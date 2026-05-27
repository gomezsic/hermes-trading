"""
backtester.py — Backtester deterministico per hermes-trading.

Simula fedelmente la strategia EMA cross 20/50 + filtro VWAP con:
  - Fee Kraken taker 0.26% per leg (entry + exit)
  - Slippage 5 basis point per market order
  - Latenza segnale di 1 candela (entra all'open della candela successiva al segnale)
  - Stop loss fisso, partial exit al 50% posizione, trailing stop con activate/tight

NON simula: Markov regime, ADX guard, news/calendar/weekend guard
(troppo dipendenti da stato esterno per essere deterministici).

Determinismo garantito: dati identici in ingresso -> output bit-perfect identico.
Il parametro `seed` e' accettato per compatibilita' API ma non altera il risultato
perche' il backtester e' deterministico per costruzione (nessun elemento stocastico).

Funzione principale: run_backtest(candles, strategy, capital, seed=42) -> dict
"""
from __future__ import annotations

import hashlib
import json
import math

from hermes_trading.score import (
    compute_calmar,
    compute_cvar,
    compute_expectancy,
    compute_max_drawdown,
    compute_sharpe,
    compute_tail_ratio,
    compute_ulcer_index,
    compute_win_stats,
)

# ---------------------------------------------------------------------------
# Costanti di costo (Kraken taker fee + slippage market order)
# ---------------------------------------------------------------------------

TAKER_FEE: float = 0.0026   # 0.26% per singola leg (entry o exit)
SLIPPAGE:  float = 0.0005   # 5 bp per market order, applicato al prezzo di fill


# ---------------------------------------------------------------------------
# Calcolo indicatori
# ---------------------------------------------------------------------------


def _compute_ema(closes: list[float], period: int) -> list[float | None]:
    """
    Calcola l'EMA (Exponential Moving Average) sulla serie di prezzi close.

    Per i primi `period - 1` indici ritorna None (dati insufficienti per il warm-up).
    A partire dall'indice `period - 1` usa la SMA come seed, poi applica lo
    smoothing esponenziale standard con alpha = 2 / (period + 1).

    Args:
        closes: serie di prezzi close (float)
        period: periodo EMA (es. 20 o 50)

    Returns:
        lista di float|None, stessa lunghezza di closes
    """
    alpha = 2.0 / (period + 1)
    ema: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return ema

    seed = sum(closes[:period]) / period
    ema[period - 1] = seed

    for i in range(period, len(closes)):
        prev = ema[i - 1]
        assert prev is not None
        ema[i] = alpha * closes[i] + (1.0 - alpha) * prev

    return ema


def _compute_vwap_rolling(candles: list[dict], window: int) -> list[float | None]:
    """
    Calcola il VWAP su finestra mobile per ogni candela della serie.

    Per ogni indice i calcola il VWAP sulle ultime `window` candele (o meno
    se i < window). Se il volume totale nella finestra e' zero ritorna None.

    Formula: typical_price = (H + L + C) / 3
             VWAP = sum(tp * v) / sum(v)

    Args:
        candles: lista OHLCV con chiavi {t, o, h, l, c, v}
        window:  dimensione della finestra rolling (numero di candele)

    Returns:
        lista di float|None, stessa lunghezza di candles
    """
    n = len(candles)
    result: list[float | None] = []

    for i in range(n):
        start = max(0, i - window + 1)
        cum_tv = 0.0
        cum_v  = 0.0
        for j in range(start, i + 1):
            c  = candles[j]
            tp = (c["h"] + c["l"] + c["c"]) / 3.0
            v  = float(c.get("v", 0.0))
            cum_tv += tp * v
            cum_v  += v
        result.append(cum_tv / cum_v if cum_v > 0 else None)

    return result


# ---------------------------------------------------------------------------
# Utilita' per prezzi e fee
# ---------------------------------------------------------------------------


def _param_hash(strategy: dict) -> str:
    """
    Calcola un hash deterministico dei parametri chiave della strategia.

    Usato per taggare ogni trade con la configurazione che lo ha generato,
    facilitando la tracciabilita' durante la reflection di Hermes.

    Args:
        strategy: dict parametri strategia (formato strategy.yaml)

    Returns:
        stringa esadecimale di 8 caratteri (prefisso SHA256)
    """
    keys = [
        "ema_fast", "ema_slow", "stop_loss_pct", "partial_exit_pct",
        "trailing_activate_pct", "trailing_stop_pct", "trailing_stop_tight_pct",
        "vwap_filter", "vwap_window", "direction",
    ]
    subset = {k: strategy.get(k) for k in keys}
    raw = json.dumps(subset, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _apply_slippage_entry(price: float, side: str) -> float:
    """
    Applica lo slippage al prezzo di entrata.

    Per ordini long (acquisto) lo slippage peggiora il prezzo verso l'alto.
    Per ordini short (vendita allo scoperto) lo slippage peggiora verso il basso.

    Args:
        price: prezzo teorico di entrata (tipicamente l'open della candela)
        side:  "long" o "short"

    Returns:
        prezzo di entrata aggiustato per slippage
    """
    if side == "long":
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def _apply_slippage_exit(price: float, side: str) -> float:
    """
    Applica lo slippage al prezzo di uscita.

    Per uscite long (vendita) lo slippage abbassa il prezzo ricevuto.
    Per uscite short (riacquisto) lo slippage alza il prezzo pagato.

    Args:
        price: prezzo teorico di uscita (stop, target, close candela)
        side:  "long" o "short"

    Returns:
        prezzo di uscita aggiustato per slippage
    """
    if side == "long":
        return price * (1.0 - SLIPPAGE)
    return price * (1.0 + SLIPPAGE)


def _gross_pnl_pct(entry: float, exit_p: float, side: str) -> float:
    """
    Calcola il PnL lordo percentuale (senza fee) rispetto al prezzo di entrata.

    Args:
        entry:  prezzo di entrata effettivo (gia' con slippage)
        exit_p: prezzo di uscita effettivo (gia' con slippage)
        side:   "long" o "short"

    Returns:
        pnl_gross come decimale (es. 0.05 = +5%, -0.03 = -3%)
    """
    if side == "long":
        return (exit_p - entry) / entry
    return (entry - exit_p) / entry


# ---------------------------------------------------------------------------
# Simulazione singolo trade
# ---------------------------------------------------------------------------


def _simulate_trade(
    candles:   list[dict],
    entry_idx: int,
    side:      str,
    strategy:  dict,
) -> dict:
    """
    Simula l'esecuzione di un singolo trade candela per candela.

    Logica intra-candela (applicata in ordine conservativo):
      1. Controlla l'estremo avverso (low per long, high per short):
         a. Stop loss fisso
         b. Trailing stop (se attivo)
      2. Controlla l'estremo favorevole (high per long, low per short):
         a. Aggiorna best_price e trailing stop level
         b. Segna partial exit se target raggiunto

    Questo ordine assume che, a parita' di candela, il movimento avverso
    avvenga prima di quello favorevole — approccio conservativo standard.

    Fee: 2 * TAKER_FEE (0.52% totale) indipendentemente dalla partial exit,
    poiche' la partial suddivide i volumi ma non le leg (entry e' sempre 1).

    Args:
        candles:   lista completa di candele OHLCV
        entry_idx: indice della candela di entrata (gia' con latenza applicata)
        side:      "long" o "short"
        strategy:  dict con i parametri della strategia

    Returns:
        dict del trade con: entry, exit, side, pnl_pct, pnl_pct_gross,
        fee_paid, reason, entry_idx, exit_idx, partial_done
    """
    sl_pct         = strategy.get("stop_loss_pct", 5.0)         / 100.0
    partial_pct    = strategy.get("partial_exit_pct", 12.0)      / 100.0
    trail_act_pct  = strategy.get("trailing_activate_pct", 6.0)  / 100.0
    trail_dist_pct = strategy.get("trailing_stop_pct", 4.0)      / 100.0
    tight_dist_pct = strategy.get("trailing_stop_tight_pct", 2.5) / 100.0

    # Prezzo di entrata: open della candela entry_idx + slippage
    entry = _apply_slippage_entry(float(candles[entry_idx]["o"]), side)

    # Livelli di stop e target (prezzi assoluti)
    if side == "long":
        sl_price      = entry * (1.0 - sl_pct)
        partial_price = entry * (1.0 + partial_pct)
    else:
        sl_price      = entry * (1.0 + sl_pct)
        partial_price = entry * (1.0 - partial_pct)

    # Stato del trailing stop
    trail_active:   bool          = False
    trail_level:    float | None  = None
    partial_done:   bool          = False
    partial_exit_p: float | None  = None

    # Miglior prezzo raggiunto dalla direzione favorevole (aggiornato su every candle)
    best_price: float = entry

    # Risultati trade
    exit_p:   float | None = None
    exit_idx: int | None   = None
    reason:   str          = "forced_close"

    n = len(candles)

    for i in range(entry_idx, n):
        c  = candles[i]
        lo = float(c["l"])
        hi = float(c["h"])

        # ----------------------------------------------------------------
        # Step 1 — Controlla estremo avverso (conservativo: worst case first)
        # ----------------------------------------------------------------
        if side == "long":
            # Stop loss fisso
            if lo <= sl_price:
                exit_p   = _apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break

            # Trailing stop
            if trail_active and trail_level is not None and lo <= trail_level:
                exit_p   = _apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break

        else:  # short
            # Stop loss fisso
            if hi >= sl_price:
                exit_p   = _apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break

            # Trailing stop
            if trail_active and trail_level is not None and hi >= trail_level:
                exit_p   = _apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break

        # ----------------------------------------------------------------
        # Step 2 — Aggiorna best price e trailing con estremo favorevole
        # ----------------------------------------------------------------
        if side == "long":
            if hi > best_price:
                best_price = hi
                gain = (best_price - entry) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 - dist)
                    trail_level = max(trail_level or 0.0, new_trail)

            # Partial exit
            if not partial_done and hi >= partial_price:
                partial_done   = True
                partial_exit_p = _apply_slippage_exit(partial_price, side)
                # Passa al trailing stretto
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 - tight_dist_pct)
                    trail_level = max(trail_level, new_trail)

        else:  # short
            if lo < best_price:
                best_price = lo
                gain = (entry - best_price) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 + dist)
                    trail_level = min(
                        trail_level if trail_level is not None else math.inf,
                        new_trail,
                    )

            # Partial exit
            if not partial_done and lo <= partial_price:
                partial_done   = True
                partial_exit_p = _apply_slippage_exit(partial_price, side)
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 + tight_dist_pct)
                    trail_level = min(trail_level, new_trail)

    # ----------------------------------------------------------------
    # Chiusura forzata sull'ultima candela disponibile
    # ----------------------------------------------------------------
    if exit_p is None:
        last     = candles[-1]
        exit_p   = _apply_slippage_exit(float(last["c"]), side)
        exit_idx = n - 1
        reason   = "forced_close"

    assert exit_idx is not None

    # ----------------------------------------------------------------
    # Calcolo PnL (con eventuale partial exit al 50%)
    # ----------------------------------------------------------------
    if partial_done and partial_exit_p is not None:
        gross_partial   = _gross_pnl_pct(entry, partial_exit_p, side)
        gross_remaining = _gross_pnl_pct(entry, exit_p, side)
        pnl_gross = 0.5 * gross_partial + 0.5 * gross_remaining
    else:
        pnl_gross = _gross_pnl_pct(entry, exit_p, side)

    # Fee totale: entry leg + exit leg = 2 * TAKER_FEE
    # (la partial suddivide i volumi ma le leg rimangono 2 in termini di costo %)
    fee_paid = 2.0 * TAKER_FEE
    pnl_net  = pnl_gross - fee_paid

    return {
        "entry":         round(entry, 6),
        "exit":          round(exit_p, 6),
        "side":          side,
        "pnl_pct":       round(pnl_net, 8),
        "pnl_pct_gross": round(pnl_gross, 8),
        "fee_paid":      round(fee_paid, 6),
        "reason":        reason,
        "entry_idx":     entry_idx,
        "exit_idx":      exit_idx,
        "partial_done":  partial_done,
    }


# ---------------------------------------------------------------------------
# Equity curve e metriche
# ---------------------------------------------------------------------------


def _build_equity_curve(
    candles: list[dict],
    trades:  list[dict],
    capital: float,
) -> list[dict]:
    """
    Costruisce la equity curve candela per candela sull'intera serie storica.

    Il capitale viene aggiornato alla candela di chiusura di ogni trade usando
    il pnl_pct netto. Tra un trade e l'altro il capitale resta invariato
    (nessun rendimento sul cash idle — approccio conservativo).

    Args:
        candles: lista completa di candele OHLCV
        trades:  lista di trade gia' simulati (output di _simulate_trade)
        capital: capitale iniziale in valuta (es. 10000.0 USD)

    Returns:
        lista di dict con {ts, equity, drawdown_pct} per ogni candela
    """
    # Mappa exit_idx -> pnl_pct accumulato (raro avere piu' trade sullo stesso idx)
    exit_map: dict[int, float] = {}
    for t in trades:
        idx = t["exit_idx"]
        exit_map[idx] = exit_map.get(idx, 0.0) + t["pnl_pct"]

    equity = float(capital)
    peak   = equity
    curve: list[dict] = []

    for i, c in enumerate(candles):
        if i in exit_map:
            equity = equity * (1.0 + exit_map[i])
        peak = max(peak, equity)
        dd   = (peak - equity) / peak * 100.0 if peak > 0.0 else 0.0
        curve.append({
            "ts":           c.get("t", i),
            "equity":       round(equity, 4),
            "drawdown_pct": round(dd, 4),
        })

    return curve


def _compute_metrics(trades: list[dict]) -> dict:
    """
    Calcola tutte le metriche di performance usando le funzioni di hermes_trading.score.

    Le metriche sono organizzate in tre livelli di priorita':
      1. Sopravvivenza: max_drawdown, cvar_5pct
      2. Robustezza:   calmar_ratio, ulcer_index, tail_ratio
      3. Efficienza:   sharpe, win_rate, n_trades, expectancy

    I pnl usati sono quelli NETTI (dopo fee) per avere una valutazione realistica.

    Args:
        trades: lista di dict trade con campo pnl_pct (netto fee)

    Returns:
        dict con tutte le metriche richieste dal contratto run_backtest
    """
    if not trades:
        return _empty_metrics()

    pnls = [t["pnl_pct"] for t in trades]
    ws   = compute_win_stats(pnls)

    return {
        "max_drawdown": round(compute_max_drawdown(pnls), 6),
        "cvar_5pct":    round(compute_cvar(pnls, 0.05), 6),
        "calmar_ratio": round(compute_calmar(pnls), 4),
        "ulcer_index":  round(compute_ulcer_index(pnls), 4),
        "tail_ratio":   round(compute_tail_ratio(pnls), 4),
        "sharpe":       round(compute_sharpe(pnls), 4),
        "win_rate":     ws["win_rate"],
        "n_trades":     len(trades),
        "expectancy":   round(compute_expectancy(pnls), 6),
    }


def _empty_metrics() -> dict:
    """
    Ritorna un dict di metriche a zero quando non ci sono trade da analizzare.

    Returns:
        dict con tutte le chiavi di metrics valorizzate a 0.0 / 0
    """
    return {
        "max_drawdown": 0.0,
        "cvar_5pct":    0.0,
        "calmar_ratio": 0.0,
        "ulcer_index":  0.0,
        "tail_ratio":   0.0,
        "sharpe":       0.0,
        "win_rate":     0.0,
        "n_trades":     0,
        "expectancy":   0.0,
    }


# ---------------------------------------------------------------------------
# Funzione principale
# ---------------------------------------------------------------------------


def run_backtest(
    candles:  list[dict],
    strategy: dict,
    capital:  float,
    seed:     int = 42,  # noqa: ARG001  — accettato per compatibilita' API
) -> dict:
    """
    Esegue il backtest deterministico della strategia EMA cross 20/50 + filtro VWAP.

    Pipeline completa:
      1. Legge parametri da strategy dict (stesso formato di state/strategy.yaml)
      2. Calcola EMA fast/slow e VWAP rolling sull'intera serie di candles
      3. Scansiona i cross EMA (golden cross = segnale long, death cross = short)
      4. Applica filtro VWAP: long solo se close > VWAP, short solo se close < VWAP
      5. Entra al segnale + 1 candela (latenza di 1 candela, all'open successivo)
      6. Simula il trade candela per candela con stop/trailing/partial
      7. Non apre nuovi trade mentre uno e' aperto (no overlapping)
      8. Costruisce equity curve e calcola metriche aggregate

    Costi simulati:
      - Fee Kraken taker: 0.26% per leg (entry + exit = 0.52% totale)
      - Slippage: 5 bp per ogni market order (entry e exit)

    Non simula: Markov regime, ADX guard, news/calendar/weekend guard.

    Determinismo: l'output e' identico bit-per-bit per gli stessi input.
    Il parametro `seed` e' accettato ma non usato (nessun elemento stocastico).

    Args:
        candles:  lista di candele OHLCV con chiavi {t, o, h, l, c, v}
        strategy: dict parametri strategia (formato state/strategy.yaml)
        capital:  capitale iniziale in valuta (es. 10000.0 USD)
        seed:     seme per compatibilita' API (non influisce sul risultato)

    Returns:
        dict con tre chiavi:
          trades: list[dict] — un dict per ogni trade eseguito con campi:
            entry, exit, side, pnl_pct (netto fee), pnl_pct_gross (lordo),
            fee_paid, reason, entry_idx, exit_idx, partial_done, param_hash
          equity_curve: list[dict] — {ts, equity, drawdown_pct} per ogni candela
          metrics: dict — metriche aggregate di performance (max_drawdown,
            cvar_5pct, calmar_ratio, ulcer_index, tail_ratio, sharpe,
            win_rate, n_trades, expectancy)
    """
    _ = seed  # deterministico per costruzione — seed non altera il risultato

    # --- Parametri strategia ---
    ema_fast    = int(strategy.get("ema_fast", 20))
    ema_slow    = int(strategy.get("ema_slow", 50))
    direction   = str(strategy.get("direction", "both"))
    vwap_filter = bool(strategy.get("vwap_filter", True))
    vwap_window = int(strategy.get("vwap_window", 200))
    phash       = _param_hash(strategy)

    n = len(candles)

    # Serve almeno ema_slow + 2 candele: ema_slow per il seed, 1 per il cross, 1 per l'entry
    min_candles = ema_slow + 2
    if n < min_candles:
        return {
            "trades":       [],
            "equity_curve": _build_equity_curve(candles, [], capital),
            "metrics":      _empty_metrics(),
        }

    # --- Calcolo indicatori ---
    closes   = [float(c["c"]) for c in candles]
    ema_f    = _compute_ema(closes, ema_fast)
    ema_s    = _compute_ema(closes, ema_slow)
    vwap_arr = _compute_vwap_rolling(candles, vwap_window)

    # --- Scansione segnali EMA cross + simulazione trade ---
    trades: list[dict]  = []
    next_free_idx: int  = ema_slow  # primo indice con entrambe le EMA disponibili

    i = ema_slow
    while i < n - 1:
        # Aspetta che la candela precedente sia oltre l'ultima chiusura di trade
        if i < next_free_idx:
            i += 1
            continue

        ef_now  = ema_f[i]
        es_now  = ema_s[i]
        ef_prev = ema_f[i - 1]
        es_prev = ema_s[i - 1]

        # EMA non ancora calcolata (warm-up insufficiente)
        if ef_now is None or es_now is None or ef_prev is None or es_prev is None:
            i += 1
            continue

        # Rilevamento cross
        signal: str | None = None
        if ef_prev <= es_prev and ef_now > es_now:
            signal = "long"   # golden cross — EMA fast supera EMA slow dal basso
        elif ef_prev >= es_prev and ef_now < es_now:
            signal = "short"  # death cross — EMA fast scende sotto EMA slow

        if signal is None:
            i += 1
            continue

        # Filtro direzione dalla strategy (long | short | both)
        if direction == "long"  and signal != "long":
            i += 1
            continue
        if direction == "short" and signal != "short":
            i += 1
            continue

        # Filtro VWAP: long solo sopra VWAP, short solo sotto
        if vwap_filter:
            vwap_val = vwap_arr[i]
            if vwap_val is not None:
                close_i = closes[i]
                if signal == "long"  and close_i < vwap_val:
                    i += 1
                    continue
                if signal == "short" and close_i > vwap_val:
                    i += 1
                    continue
            # Se VWAP non disponibile, il filtro non blocca il segnale

        # Entrata alla candela i+1 (latenza 1 candela — segnale riconosciuto a close[i])
        entry_idx = i + 1
        if entry_idx >= n:
            break

        trade = _simulate_trade(candles, entry_idx, signal, strategy)
        trade["param_hash"] = phash
        trades.append(trade)

        # Il prossimo trade non puo' aprirsi prima della chiusura di questo
        next_free_idx = trade["exit_idx"] + 1
        i = next_free_idx

    # --- Equity curve e metriche ---
    equity_curve = _build_equity_curve(candles, trades, capital)
    metrics      = _compute_metrics(trades)

    return {
        "trades":       trades,
        "equity_curve": equity_curve,
        "metrics":      metrics,
    }

from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .adapters import macro as macro_adapter
from .adapters import news as news_adapter
from .adapters import onchain as onchain_adapter
from .adapters import price as price_adapter
from .bootstrap import state_dir
from . import volume_profile as vp_calc
from . import markov_regime as markov_mod
from . import alert as alert_mod
from . import indicators as ind
from . import safe_guard as sg
from . import news_intelligence as ni
from . import sizing as sz
from . import sentiment_calibrator as scal

TICK_SECONDS = 60
MAX_CONSECUTIVE_FAILURES = 5
CIRCUIT_BREAK_SLEEP = 300       # 5 minuti
INITIAL_CAPITAL = 100_000.0     # dollari finti iniziali


class SchemaError(RuntimeError):
    """Adapter returned an unexpected schema_version."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_strategy(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Indicatori
# ---------------------------------------------------------------------------

def _compute_ema(prices: list[float], period: int) -> list[float]:
    """EMA series. Returns empty list if not enough data."""
    if len(prices) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def _ema_cross_signal(closes: list[float], fast: int, slow: int) -> str | None:
    """
    'long'  su golden cross (EMA fast supera EMA slow dal basso).
    'short' su death  cross (EMA fast scende sotto EMA slow).
    None altrimenti. Serve almeno slow+2 prezzi.
    """
    if len(closes) < slow + 2:
        return None
    ema_fast = _compute_ema(closes, fast)
    ema_slow = _compute_ema(closes, slow)
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None
    fast_now, fast_prev = ema_fast[-1], ema_fast[-2]
    slow_now, slow_prev = ema_slow[-1], ema_slow[-2]
    if fast_prev <= slow_prev and fast_now > slow_now:
        return "long"
    if fast_prev >= slow_prev and fast_now < slow_now:
        return "short"
    return None


# ---------------------------------------------------------------------------
# Confidence Score — quanto siamo sicuri del trade
# ---------------------------------------------------------------------------

def _compute_confidence(
    side: str,
    closes: list[float],
    fast: int,
    slow: int,
    vp: dict,
    poc: float | None,
    regime: dict,
    current_price: float,
    candles_1m: list[dict] | None = None,    # per VWAP
    candles_15m: list[dict] | None = None,   # per VWMA (volumi piu' stabili)
    candles: list[dict] | None = None,       # fallback legacy
) -> tuple[float, dict]:
    # Normalizza: se passato il vecchio parametro "candles", usalo come 1m
    if candles_1m is None and candles is not None:
        candles_1m = candles
    if candles_15m is None:
        candles_15m = candles_1m   # fallback a 1m se 15m non disponibile
    """
    Ritorna (confidence 0.0-1.0, breakdown dict per logging/Telegram).

    Fattori (trend-follower oriented):
      1. Markov alignment  30% — regime giornaliero conferma la direzione
      2. EMA spread        20% — forza del segnale EMA (distanza tra le due medie)
      3. VWAP alignment    20% — prezzo nel lato giusto del VWAP (filtro istituzionale)
      4. VWMA alignment    15% — cross VWMA conferma volumetricamente il segnale
      5. Price momentum    15% — % candele che vanno nella direzione giusta (ultime 10)

    Filosofia: non prevediamo il futuro. Misuriamo QUANTI indicatori indipendenti
    concordano sul trend in atto. Piu' concordano, piu' investiamo.
    """
    scores: dict[str, float] = {}

    # --- 1. Markov alignment (30%) ---
    reg_label  = regime.get("label", "Sideways")
    reg_signal = abs(float(regime.get("signal", 0.0)))
    if side == "long":
        markov_score = min(1.0, reg_signal) if reg_label == "Bull" else (0.4 if reg_label == "Sideways" else 0.1)
    else:
        markov_score = min(1.0, reg_signal) if reg_label == "Bear" else (0.4 if reg_label == "Sideways" else 0.1)
    scores["markov"] = round(markov_score, 3)

    # --- 2. EMA spread (20%) ---
    ema_fast = _compute_ema(closes, fast)
    ema_slow  = _compute_ema(closes, slow)
    ema_score = 0.3
    if ema_fast and ema_slow and ema_slow[-1] > 0:
        spread_pct = abs(ema_fast[-1] - ema_slow[-1]) / ema_slow[-1] * 100
        ema_score  = min(1.0, spread_pct / 1.0)
    scores["ema_spread"] = round(ema_score, 3)

    # --- 3. VWAP alignment (20%) ---
    vwap_score = 0.3
    if candles_1m:
        va = ind.vwap_analysis(candles_1m, current_price)
        if va["vwap"] is not None:
            # Score pieno se siamo dalla parte giusta e abbastanza distanti
            correct_side = (side == "long" and va["above_vwap"]) or \
                           (side == "short" and not va["above_vwap"])
            if correct_side:
                # Piu' siamo distanti dal VWAP nella direzione giusta, piu' il trend e' confermato
                # strength gia' normalizzato 0-1 in vwap_analysis
                vwap_score = 0.4 + va["strength"] * 0.6   # range [0.4, 1.0]
            else:
                vwap_score = 0.1   # lato sbagliato del VWAP
    scores["vwap"] = round(vwap_score, 3)

    # --- 4. VWMA alignment (15%) ---
    vwma_score = 0.3
    if candles_15m:
        vm = ind.vwma_analysis(candles_15m, fast, slow)
        if vm["vwma_fast"] is not None:
            if (side == "long"  and vm["confirms_long"]) or \
               (side == "short" and vm["confirms_short"]):
                # Spread VWMA: piu' e' ampio, piu' e' forte
                spread = abs(vm["spread_pct"])
                vwma_score = min(1.0, 0.5 + spread / 0.5)
            else:
                vwma_score = 0.1
    scores["vwma"] = round(vwma_score, 3)

    # --- 5. Price momentum (15%) ---
    momentum_score = 0.4
    if len(closes) >= 11:
        last = closes[-11:]
        if side == "long":
            ok = sum(1 for i in range(1, len(last)) if last[i] > last[i - 1])
        else:
            ok = sum(1 for i in range(1, len(last)) if last[i] < last[i - 1])
        momentum_score = ok / 10.0
    scores["momentum"] = round(momentum_score, 3)

    # --- Punteggio finale pesato ---
    confidence = (
        scores["markov"]     * 0.30 +
        scores["ema_spread"] * 0.20 +
        scores["vwap"]       * 0.20 +
        scores["vwma"]       * 0.15 +
        scores["momentum"]   * 0.15
    )
    return round(confidence, 3), scores


def _size_from_confidence(
    confidence: float,
    min_size: float = 0.50,
    max_size: float = 1.00,
) -> float:
    """
    Mappa confidence [0,1] → position_size_r [min_size, max_size].
    Arrotonda al 5% più vicino per pulizia.
    """
    raw = min_size + confidence * (max_size - min_size)
    rounded = round(raw / 0.05) * 0.05
    return round(max(min_size, min(max_size, rounded)), 2)


# ---------------------------------------------------------------------------
# Drawdown protection
# ---------------------------------------------------------------------------

def _check_drawdown(portfolio: dict, strategy: dict) -> tuple[bool, float]:
    """
    Controlla se possiamo aprire nuovi trade.
    Ritorna (can_trade, current_drawdown_pct).

    Regole:
      drawdown < 5%   → size piena (nessuna penalità)
      5-10%           → cap size al 75%
      10-15%          → cap size al 60%
      15-max%         → cap size al 50% (minimo assoluto)
      >= max_drawdown → CIRCUIT BREAKER — nessun nuovo trade
    """
    peak = portfolio.get("peak_balance", portfolio["initial_capital"])
    balance = portfolio["balance"]
    if peak <= 0:
        return True, 0.0
    dd = max(0.0, (peak - balance) / peak * 100)
    max_dd = float(strategy.get("max_drawdown_pct", 20.0))
    can_trade = dd < max_dd
    return can_trade, round(dd, 2)


def _cap_size_for_drawdown(size: float, drawdown_pct: float) -> float:
    """Riduce progressivamente la size in funzione del drawdown corrente."""
    if drawdown_pct >= 15.0:
        return min(size, 0.50)
    if drawdown_pct >= 10.0:
        return min(size, 0.60)
    if drawdown_pct >= 5.0:
        return min(size, 0.75)
    return size


# ---------------------------------------------------------------------------
# Retry / schema
# ---------------------------------------------------------------------------

async def _with_retry(name: str, fn: Callable[[], Awaitable[dict]]) -> dict:
    """3 tentativi con backoff esponenziale 1s/2s/4s."""
    backoffs = [1, 2, 4]
    last_err: Exception | None = None
    for attempt, delay in enumerate(backoffs, start=1):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < len(backoffs):
                await asyncio.sleep(delay)
    raise RuntimeError(f"adapter '{name}' failed after retries: {last_err}")


def _validate_schema(name: str, payload: dict) -> dict:
    v = payload.get("schema_version")
    if v != 1:
        raise SchemaError(f"{name} returned schema_version={v}, expected 1")
    return payload


# ---------------------------------------------------------------------------
# Portfolio — capitale virtuale $100k
# ---------------------------------------------------------------------------

def _load_portfolio(state: Path) -> dict:
    pf = state / "portfolio.json"
    if not pf.exists():
        p = {
            "initial_capital": INITIAL_CAPITAL,
            "balance":         INITIAL_CAPITAL,
            "peak_balance":    INITIAL_CAPITAL,
        }
        pf.write_text(json.dumps(p, indent=2))
        return p
    return json.loads(pf.read_text())


def _save_portfolio(state: Path, portfolio: dict) -> None:
    if portfolio["balance"] > portfolio.get("peak_balance", INITIAL_CAPITAL):
        portfolio["peak_balance"] = portfolio["balance"]
    (state / "portfolio.json").write_text(json.dumps(portfolio, indent=2))


def _update_portfolio_on_close(
    state: Path,
    portfolio: dict,
    pnl_pct: float,
    position_size_r: float,
    size_fraction: float,
    capital_at_open: float,
) -> dict:
    """
    Aggiorna il saldo dopo chiusura totale o parziale.
    Usa capital_at_open (snapshot al momento dell'apertura) per
    garantire coerenza tra partial e full close dello stesso trade.
    """
    invested   = capital_at_open * position_size_r * size_fraction
    pnl_dollar = round(invested * pnl_pct, 2)
    portfolio["balance"] = round(portfolio["balance"] + pnl_dollar, 2)
    _save_portfolio(state, portfolio)
    return portfolio


# ---------------------------------------------------------------------------
# Position I/O
# ---------------------------------------------------------------------------

def _open_position(
    state: Path,
    asset: str,
    price: float,
    strategy: dict,
    side: str,
    portfolio: dict,
    size_r: float,
    confidence: float,
    confidence_breakdown: dict,
) -> dict:
    pos = {
        "id":              str(uuid.uuid4()),
        "asset":           asset,
        "side":            side,
        "entry":           price,
        "entry_time":      _now_iso(),
        # risk params (snapshot)
        "stop_loss_pct":           float(strategy["stop_loss_pct"]),
        "trailing_stop_pct":       float(strategy["trailing_stop_pct"]),
        "trailing_activate_pct":   float(strategy["trailing_activate_pct"]),
        "partial_exit_pct":        float(strategy["partial_exit_pct"]),
        "trailing_stop_tight_pct": float(strategy["trailing_stop_tight_pct"]),
        "position_size_r":         size_r,
        "strategy_version":        strategy.get("version", "??"),
        # confidence
        "confidence":              confidence,
        "confidence_breakdown":    confidence_breakdown,
        # capitale snapshot
        "capital_at_open":         portfolio["balance"],
        # runtime state
        "extreme_price":   price,   # max per long, min per short
        "trailing_active": False,
        "trailing_price":  None,
        "half_closed":     False,
        "remaining_size":  1.0,
    }
    (state / "position.json").write_text(json.dumps(pos, indent=2))
    return pos


def _save_position(state: Path, pos: dict) -> None:
    (state / "position.json").write_text(json.dumps(pos, indent=2))


import hashlib as _hashlib


def _params_hash(strategy: dict) -> str:
    """SHA256 dei parametri rilevanti — identifica univocamente ogni versione della strategy."""
    relevant = {k: strategy.get(k) for k in [
        "stop_loss_pct", "partial_exit_pct", "trailing_activate_pct",
        "trailing_stop_pct", "trailing_stop_tight_pct",
        "entry",
    ]}
    sizing = strategy.get("sizing", {})
    if sizing:
        relevant["sizing_sigma_target"] = sizing.get("vol_target", {}).get("sigma_target_annual")
        relevant["sizing_kelly_frac"]   = sizing.get("kelly", {}).get("fraction_significant")
    raw = json.dumps(relevant, sort_keys=True)
    return "sha256:" + _hashlib.sha256(raw.encode()).hexdigest()[:16]


def _params_snapshot(strategy: dict) -> dict:
    """Snapshot dei parametri ottimizzabili per il walk-forward."""
    s = strategy
    return {
        "stop_loss_pct":           s.get("stop_loss_pct"),
        "partial_exit_pct":        s.get("partial_exit_pct"),
        "trailing_activate_pct":   s.get("trailing_activate_pct"),
        "trailing_stop_pct":       s.get("trailing_stop_pct"),
        "trailing_stop_tight_pct": s.get("trailing_stop_tight_pct"),
        "ema_fast":                s.get("entry", {}).get("ema_fast"),
        "ema_slow":                s.get("entry", {}).get("ema_slow"),
        "adx_min":                 s.get("safe_guard", {}).get("adx_min_threshold"),
        "sigma_target_annual":     s.get("sizing", {}).get("vol_target", {}).get("sigma_target_annual"),
        "kelly_fraction_sig":      s.get("sizing", {}).get("kelly", {}).get("fraction_significant"),
        "strategy_version":        s.get("version"),
    }


def _log_trade(state: Path, trade: dict) -> None:
    with (state / "trades.jsonl").open("a") as f:
        f.write(json.dumps(trade) + "\n")


def _build_trade_record(
    pos: dict,
    exit_price: float,
    reason: str,
    size: float,
    portfolio: dict,
    strategy: dict | None = None,
) -> dict:
    skip = {"extreme_price", "trailing_active", "trailing_price", "half_closed", "remaining_size",
            "confidence_breakdown"}
    direction = pos["side"]
    if direction == "long":
        pnl_pct = (exit_price - pos["entry"]) / pos["entry"]
    else:
        pnl_pct = (pos["entry"] - exit_price) / pos["entry"]

    invested   = pos["capital_at_open"] * float(pos.get("position_size_r", 0.5)) * size
    pnl_dollar = round(invested * pnl_pct, 2)

    record = {
        **{k: v for k, v in pos.items() if k not in skip},
        "exit":          exit_price,
        "exit_time":     _now_iso(),
        "pnl_pct":       round(pnl_pct, 6),
        "pnl_dollar":    pnl_dollar,
        "size":          size,
        "reason":        reason,
        "balance_after": round(portfolio["balance"] + pnl_dollar, 2),
    }
    # Provenance per walk-forward — aggiunta dal giorno del deploy
    if strategy is not None:
        record["param_hash"]      = _params_hash(strategy)
        record["params_snapshot"] = _params_snapshot(strategy)
    else:
        record["param_hash"]      = None
        record["params_snapshot"] = None
    return record


def _full_close(
    state: Path,
    pos: dict,
    exit_price: float,
    reason: str,
    portfolio: dict,
    strategy: dict | None = None,
) -> tuple[dict, dict]:
    size  = float(pos.get("remaining_size", 1.0))
    trade = _build_trade_record(pos, exit_price, reason, size, portfolio, strategy)
    _log_trade(state, trade)
    (state / "position.json").unlink(missing_ok=True)
    portfolio = _update_portfolio_on_close(
        state, portfolio,
        pnl_pct        = trade["pnl_pct"],
        position_size_r= float(pos.get("position_size_r", 0.5)),
        size_fraction  = size,
        capital_at_open= float(pos.get("capital_at_open", INITIAL_CAPITAL)),
    )
    return trade, portfolio


def _partial_close(
    state: Path,
    pos: dict,
    exit_price: float,
    reason: str,
    portfolio: dict,
    strategy: dict | None = None,
) -> tuple[dict, dict]:
    trade = _build_trade_record(pos, exit_price, reason, 0.5, portfolio, strategy)
    _log_trade(state, trade)
    portfolio = _update_portfolio_on_close(
        state, portfolio,
        pnl_pct        = trade["pnl_pct"],
        position_size_r= float(pos.get("position_size_r", 0.5)),
        size_fraction  = 0.5,
        capital_at_open= float(pos.get("capital_at_open", INITIAL_CAPITAL)),
    )
    return trade, portfolio


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _heartbeat(
    state: Path,
    trades_total: int,
    position_open: bool,
    err: str | None,
    consec_fail: int,
    portfolio: dict | None = None,
) -> None:
    data: dict = {
        "last_tick":            _now_iso(),
        "trades_total":         trades_total,
        "trades_open":          1 if position_open else 0,
        "last_error":           err,
        "consecutive_failures": consec_fail,
    }
    if portfolio:
        initial = portfolio["initial_capital"]
        balance = portfolio["balance"]
        peak    = portfolio.get("peak_balance", initial)
        data["balance"]         = balance
        data["initial_capital"] = initial
        data["pnl_total_pct"]   = round((balance - initial) / initial * 100, 2)
        data["pnl_total_dollar"]= round(balance - initial, 2)
        data["drawdown_pct"]    = round(max(0.0, (peak - balance) / peak * 100), 2) if peak > 0 else 0.0
    (state / "heartbeat.json").write_text(json.dumps(data, indent=2))


def _load_all_trades(state: Path) -> list[dict]:
    """Carica tutti i trade chiusi da trades.jsonl."""
    tf = state / "trades.jsonl"
    if not tf.exists():
        return []
    return [json.loads(l) for l in tf.read_text().splitlines() if l.strip()]


def _count_trades(state: Path) -> int:
    tf = state / "trades.jsonl"
    if not tf.exists():
        return 0
    return sum(1 for _ in tf.open())


def _load_position(state: Path) -> dict | None:
    pf = state / "position.json"
    if not pf.exists():
        return None
    return json.loads(pf.read_text())


# ---------------------------------------------------------------------------
# Gain helper — funziona per long e short
# ---------------------------------------------------------------------------

def _compute_sma50d(candles_1d: list[dict]) -> float | None:
    """
    SMA50 daily: media semplice degli ultimi 50 close giornalieri.
    Gate di regime fondamentale: il backtest su 18 mesi dimostra che
    l'EMA cross ha edge positivo SOLO quando il prezzo e' sopra la SMA50d.
    Sotto SMA50d: Sharpe -1.05, E/trade -0.24% (negativo).
    Sopra SMA50d: Sharpe +0.80, E/trade +0.23% (positivo).
    Ritorna None se dati insufficienti (non blocca il trade — fallback safe).
    """
    if len(candles_1d) < 50:
        return None
    closes = [c.get("c", (c["h"] + c["l"]) / 2) for c in candles_1d[-50:]]
    return sum(closes) / 50


def _gain_pct(side: str, entry: float, current_price: float) -> float:
    """Gain % dalla direzione della posizione. Positivo = in profitto."""
    if side == "long":
        return (current_price - entry) / entry * 100
    return (entry - current_price) / entry * 100


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_loop(asset: str) -> None:
    state = state_dir()
    closes: list[float] = []
    consec_fail = 0

    while True:
        tick_started = time.time()
        try:
            strategy          = _read_strategy(state / "strategy.yaml")
            fast              = int(strategy["entry"].get("ema_fast", 20))
            slow              = int(strategy["entry"].get("ema_slow", 50))
            allowed_direction = strategy["entry"].get("direction", "both")
            min_size_r        = float(strategy.get("min_position_size", 0.50))
            max_size_r        = float(strategy.get("max_position_size", 1.00))

            portfolio = _load_portfolio(state)

            # --- fetch adapters ---
            results: dict = {}
            for name, adapter, args in [
                ("price",   price_adapter,   (asset,)),
                ("onchain", onchain_adapter, (asset,)),
                ("news",    news_adapter,    ()),
                ("macro",   macro_adapter,   ()),
            ]:
                try:
                    payload = await _with_retry(name, lambda a=adapter, ar=args: a.fetch(*ar))
                    results[name] = _validate_schema(name, payload)
                except SchemaError:
                    raise
                except Exception as e:  # noqa: BLE001
                    print(f"adapter {name} failed: {e}", flush=True)
                    results[name] = None

            price_data = results.get("price")
            if not price_data:
                consec_fail += 1
                _heartbeat(state, _count_trades(state), _load_position(state) is not None,
                           "price adapter unavailable", consec_fail, portfolio)
                if consec_fail >= MAX_CONSECUTIVE_FAILURES:
                    print(f"FATAL: {consec_fail} consecutive failures, sleeping {CIRCUIT_BREAK_SLEEP}s", flush=True)
                    await asyncio.sleep(CIRCUIT_BREAK_SLEEP)
                    consec_fail = 0
                else:
                    await asyncio.sleep(TICK_SECONDS)
                continue

            consec_fail    = 0
            current_price  = float(price_data["price"])

            recent = price_data.get("recent_closes") or []
            if recent and len(recent) > len(closes):
                closes = recent
            closes.append(current_price)
            closes = closes[-500:]

            # --- Volume Profile e indicatori multi-timeframe ---
            # candles 1m: EMA, VWAP intraday (gia' con close reale e filtrate)
            candles_raw = price_data.get("candles") or []
            candles     = candles_raw   # gia' {h,l,c,v} pulite dal price adapter

            # 15m: Volume Profile intraday (24h), VWMA
            candles_15m = price_data.get("candles_15m") or []
            # 1h: Volume Profile swing (1 settimana), ADX
            candles_1h  = price_data.get("candles_1h")  or []
            # 1d: ATR daily per Kelly+vol sizing
            candles_1d  = price_data.get("candles_1d")  or []

            # VP su 15m (piu' stabile e significativo del VP su 1m)
            vp  = vp_calc.build(candles_15m) if candles_15m else vp_calc.build(candles) if candles else {}
            poc = vp.get("poc")

            # --- SMA50 daily — gate di regime (backtest-validated) ---
            # Edge positivo EMA cross SOLO sopra SMA50d (18 mesi dati reali):
            #   sopra: Sharpe=+0.80, E=+0.23%/trade
            #   sotto: Sharpe=-1.05, E=-0.24%/trade
            sma50d = _compute_sma50d(candles_1d)

            # --- Markov daily regime (cached, refresh ogni 60 min) ---
            regime       = await markov_mod.get_regime(asset, state)
            regime_label = regime.get("label", "?")

            # --- News intelligence (RSS + sentiment, cache 5min) ---
            news = ni.news_sentiment(now_utc=datetime.now(timezone.utc))

            position = _load_position(state)

            # -------------------------------------------------------------------
            # SAFE & GUARD — quando restare fuori dal mercato
            # -------------------------------------------------------------------
            now_utc  = datetime.now(timezone.utc)
            sg_cfg   = strategy.get("safe_guard", {})
            sg_result = sg.check(now_utc=now_utc, candles=candles_1h or candles, cfg=sg_cfg)

            # Chiudi posizione aperta se richiesto (es. venerdi sera, evento imminente)
            if sg_result["close_position"] and position is not None:
                trade, portfolio = _full_close(
                    state, position, current_price, "safe_guard_close", portfolio, strategy
                )
                print(
                    f"SAFE_GUARD CLOSE [{position['side']}] @ {current_price:.2f}  "
                    f"pnl={trade['pnl_pct']:+.4f}  pnl_$={trade['pnl_dollar']:+,.0f}  "
                    f"balance=${portfolio['balance']:,.0f}  "
                    f"reason: {sg_result['reason']}",
                    flush=True,
                )
                asyncio.ensure_future(
                    alert_mod.safe_guard_event(sg_result, trade, portfolio)
                )
                position = None

            # Traccia stato SAFE & GUARD per notifica cambio
            _last_sg = getattr(run_loop, "_last_sg_blocked", False)
            if sg_result["blocked"] and not _last_sg:
                print(f"SAFE_GUARD ATTIVO  {sg_result['reason']}", flush=True)
                asyncio.ensure_future(
                    alert_mod.safe_guard_event(sg_result, None, portfolio)
                )
            elif not sg_result["blocked"] and _last_sg:
                print("SAFE_GUARD DISATTIVATO — riprende operativita' normale", flush=True)
                asyncio.ensure_future(
                    alert_mod.safe_guard_cleared(portfolio)
                )
            run_loop._last_sg_blocked = sg_result["blocked"]   # type: ignore[attr-defined]

            # Blocco aggiuntivo per news shock (bearish_shock da sentiment RSS)
            news_blocked = False
            if news.get("safe_guard") and not sg_result["blocked"]:
                news_blocked = True
                _last_news = getattr(run_loop, "_last_news_blocked", False)
                if not _last_news:
                    print(
                        f"NEWS GUARD ATTIVO  sentiment={news['score']:+.3f}  "
                        f"signal={news['signal']}  "
                        f"'{news.get('top_bearish', ['?'])[0] if news.get('top_bearish') else '?'}'",
                        flush=True,
                    )
                    asyncio.ensure_future(alert_mod.news_guard_event(news, portfolio))
            elif not news.get("safe_guard"):
                _last_news_was = getattr(run_loop, "_last_news_blocked", False)
                if _last_news_was:
                    print("NEWS GUARD DISATTIVATO — sentiment normalizzato", flush=True)
            run_loop._last_news_blocked = news_blocked   # type: ignore[attr-defined]

            # -------------------------------------------------------------------
            # ENTRY — EMA cross + VP filter + Markov filter + confidence sizing
            # -------------------------------------------------------------------
            if position is None and not sg_result["blocked"] and not news_blocked:
                signal = _ema_cross_signal(closes, fast, slow)
                if signal == "long"  and allowed_direction == "short": signal = None
                if signal == "short" and allowed_direction == "long":  signal = None

                if signal in ("long", "short"):
                    side = signal

                    # --- Drawdown circuit breaker ---
                    can_trade, dd_pct = _check_drawdown(portfolio, strategy)
                    if not can_trade:
                        max_dd = strategy.get("max_drawdown_pct", 20.0)
                        print(
                            f"ENTRY BLOCKED [{side}]  CIRCUIT BREAKER  "
                            f"drawdown={dd_pct:.1f}% >= max={max_dd}%",
                            flush=True,
                        )
                    else:
                        # --- Calcola ATR (per logging) e stops ---
                        use_atr = strategy.get("use_atr_stops", False)
                        atr_stops_data = ind.atr_stops(
                            candles, current_price, side,
                            sl_mult     = float(strategy.get("atr_sl_multiplier",    2.5)),
                            trail_mult  = float(strategy.get("atr_trail_multiplier", 1.5)),
                            tight_mult  = float(strategy.get("atr_trail_tight_mult", 1.0)),
                            partial_rr  = float(strategy.get("partial_exit_rr",      1.5)),
                            period      = int(strategy.get("atr_period", 14)),
                            sl_fallback_pct      = float(strategy.get("stop_loss_pct",           5.0)),
                            trail_fallback_pct   = float(strategy.get("trailing_stop_pct",       4.0)),
                            tight_fallback_pct   = float(strategy.get("trailing_stop_tight_pct", 2.5)),
                            partial_fallback_pct = float(strategy.get("partial_exit_pct",        12.0)),
                        ) if candles else {}

                        # Se use_atr_stops=False usa i parametri fissi della strategy
                        if use_atr and atr_stops_data:
                            strategy_for_open = dict(strategy)
                            strategy_for_open["stop_loss_pct"]           = atr_stops_data["sl_pct"]
                            strategy_for_open["partial_exit_pct"]        = atr_stops_data["partial_pct"]
                            strategy_for_open["trailing_activate_pct"]   = atr_stops_data["partial_pct"]
                            strategy_for_open["trailing_stop_pct"]       = atr_stops_data["trail_dist_pct"]
                            strategy_for_open["trailing_stop_tight_pct"] = atr_stops_data["tight_dist_pct"]
                            atr_log = f"  ATR=${atr_stops_data.get('atr_used','?')} (atr-based)"
                        else:
                            strategy_for_open = strategy
                            atr_val = ind.compute_atr(candles, int(strategy.get("atr_period", 14))) if candles else None
                            atr_stops_data = {"atr_used": round(atr_val, 2) if atr_val else None, "source": "fixed"}
                            atr_log = f"  ATR=${atr_stops_data['atr_used']} (fixed params)" if atr_stops_data["atr_used"] else ""

                        # --- VWAP ---
                        vwap_info = ind.vwap_analysis(candles, current_price) if candles else {}
                        vwap_val  = vwap_info.get("vwap")

                        # --- Filtro VWAP (filtro primario istituzionale) ---
                        vwap_blocked = False
                        use_vwap_filter = strategy.get("vwap_filter", True)
                        if use_vwap_filter and vwap_val:
                            if side == "long" and not vwap_info.get("above_vwap"):
                                print(
                                    f"ENTRY BLOCKED [LONG]   price={current_price:.0f} < VWAP={vwap_val:.0f}"
                                    f"  dist={vwap_info.get('dist_pct', 0):+.3f}%  (sotto VWAP = bearish)",
                                    flush=True,
                                )
                                vwap_blocked = True
                            elif side == "short" and vwap_info.get("above_vwap"):
                                print(
                                    f"ENTRY BLOCKED [SHORT]  price={current_price:.0f} > VWAP={vwap_val:.0f}"
                                    f"  dist={vwap_info.get('dist_pct', 0):+.3f}%  (sopra VWAP = bullish)",
                                    flush=True,
                                )
                                vwap_blocked = True

                        # --- Filtro Markov (trend follower) ---
                        markov_blocked = False
                        if not vwap_blocked:
                            if side == "long" and regime_label == "Bear":
                                print(
                                    f"ENTRY BLOCKED [LONG]   Markov=Bear {regime.get('signal', 0):+.3f}  "
                                    f"(trend negativo — no long)",
                                    flush=True,
                                )
                                markov_blocked = True
                            elif side == "short" and regime_label == "Bull":
                                print(
                                    f"ENTRY BLOCKED [SHORT]  Markov=Bull {regime.get('signal', 0):+.3f}  "
                                    f"(trend positivo — no short)",
                                    flush=True,
                                )
                                markov_blocked = True

                        # --- Filtro SMA50 daily (gate di regime backtest-validated) ---
                        # Entra long SOLO sopra SMA50d, short SOLO sotto SMA50d.
                        # Se sma50d e' None (dati 1d insufficienti) non blocca.
                        sma50d_blocked = False
                        use_sma50d = strategy.get("sma50d_gate", True)
                        if use_sma50d and sma50d is not None and not markov_blocked and not vwap_blocked:
                            if side == "long" and current_price < sma50d:
                                print(
                                    f"ENTRY BLOCKED [LONG]   SMA50d={sma50d:.0f}  "
                                    f"prezzo={current_price:.0f} sotto SMA50d  "
                                    f"(regime bear — no edge per long)",
                                    flush=True,
                                )
                                sma50d_blocked = True
                            elif side == "short" and current_price > sma50d:
                                print(
                                    f"ENTRY BLOCKED [SHORT]  SMA50d={sma50d:.0f}  "
                                    f"prezzo={current_price:.0f} sopra SMA50d  "
                                    f"(regime bull — no edge per short)",
                                    flush=True,
                                )
                                sma50d_blocked = True

                        if not vwap_blocked and not markov_blocked and not sma50d_blocked:
                            # --- Confidence score ---
                            confidence, conf_breakdown = _compute_confidence(
                                side, closes, fast, slow, vp, poc, regime, current_price,
                                candles_1m=candles,
                                candles_15m=candles_15m,
                            )

                            # --- News sentiment multiplier (calibrato dai dati) ---
                            news_signal = news.get("signal", "neutral")
                            learned_mult, mult_source = scal.get_multiplier(state, news_signal)
                            confidence_adj = round(max(0.0, min(1.0, confidence * learned_mult)), 3)

                            # --- Kelly + Vol Targeting sizing ---
                            can_trade, dd_pct = _check_drawdown(portfolio, strategy)
                            dd_penalty = _cap_size_for_drawdown(1.0, dd_pct)  # [0.5,1.0]

                            atr_1d  = ind.atr_14_daily(candles_1d) if candles_1d else None
                            sigma_h = ind.sigma_daily_history(candles_1d) if candles_1d else []

                            sizing_decision = sz.compute_position_size(
                                capitale          = portfolio["balance"],
                                confidence        = confidence_adj,
                                dd_penalty        = dd_penalty,
                                atr_14_d          = atr_1d,
                                prezzo            = current_price,
                                sigma_30d_history = sigma_h,
                                trades_history    = _load_all_trades(state),
                                state_dir         = state,
                                config            = strategy.get("sizing", {}),
                                stop_loss_pct     = float(strategy.get("stop_loss_pct", 5.0)) / 100,
                            )

                            if sizing_decision.action == "PAUSE_SYSTEM":
                                print(
                                    f"SIZING PAUSE_SYSTEM  reason={sizing_decision.reason}  "
                                    f"until={sizing_decision.pause_until}",
                                    flush=True,
                                )
                                asyncio.ensure_future(
                                    alert_mod.sizing_pause_event(sizing_decision, portfolio)
                                )
                                continue  # salta questo tick, non aprire trade

                            if sizing_decision.action == "SKIP_TRADE":
                                print(
                                    f"SIZING SKIP_TRADE  reason={sizing_decision.reason}  "
                                    f"size={sizing_decision.size_pct:.3f}",
                                    flush=True,
                                )
                                # Non e' un errore — size troppo piccola per essere utile
                            else:
                                # action == "OPEN_TRADE"
                                size_r = sizing_decision.size_pct

                                pos = _open_position(
                                    state, asset, current_price, strategy_for_open,
                                    side, portfolio, size_r, confidence, conf_breakdown,
                                )
                                pos["poc_at_entry"]         = poc
                                pos["vah_at_entry"]         = vp.get("vah")
                                pos["val_at_entry"]         = vp.get("val")
                                pos["vwap_at_entry"]        = vwap_val
                                pos["atr_at_entry"]         = atr_stops_data.get("atr_used")
                                pos["atr_source"]           = atr_stops_data.get("source", "fixed")
                                pos["news_signal_at_entry"] = news_signal
                                pos["news_score_at_entry"]  = news.get("score", 0.0)
                                pos["news_mult_at_entry"]   = round(learned_mult, 3)
                                pos["news_mult_source"]     = mult_source
                                pos["sizing_flag"]          = sizing_decision.debug.get("flag", "")
                                pos["sizing_kelly"]         = sizing_decision.debug.get("size_kelly", 0.0)
                                pos["sizing_vol"]           = sizing_decision.debug.get("size_vol", 0.0)
                                pos["risk_at_sl_usd"]       = sizing_decision.risk_at_sl
                                _save_position(state, pos)

                                sl_dist  = float(pos["stop_loss_pct"])
                                sl_price = (current_price * (1 - sl_dist / 100) if side == "long"
                                            else current_price * (1 + sl_dist / 100))
                                vwap_str = f"  VWAP={vwap_val:.0f}" if vwap_val else ""
                                poc_str  = f"  POC={poc:.0f}" if poc else ""
                                news_str = (
                                    f"  news:{news_signal}({news['score']:+.2f})"
                                    f"x{learned_mult:.2f}[{mult_source[:1]}]"
                                    if news_signal != "neutral" or mult_source == "learned"
                                    else ""
                                )
                                invested_k = portfolio["balance"] * size_r / 1000
                                kelly_flag = sizing_decision.debug.get("flag", "")
                                print(
                                    f"OPEN {side} {asset} @ {current_price:.2f}  "
                                    f"SL={sl_price:.2f} (-{sl_dist:.2f}%)  "
                                    f"partial=+{pos['partial_exit_pct']:.1f}%"
                                    f"{atr_log}{vwap_str}{poc_str}{news_str}  "
                                    f"conf={confidence:.2f}(adj={confidence_adj:.2f})  "
                                    f"kelly={kelly_flag}  "
                                    f"size={size_r:.0%}(${invested_k:.1f}k)  "
                                    f"risk=${sizing_decision.risk_at_sl:,.0f}  "
                                    f"balance=${portfolio['balance']:,.0f}  dd={dd_pct:.1f}%  "
                                    f"v{pos['strategy_version']}",
                                    flush=True,
                                )
                                asyncio.ensure_future(
                                    alert_mod.trade_opened(
                                        pos, vp, regime, portfolio,
                                        confidence, conf_breakdown
                                    )
                                )
                                position = pos
            if position is not None:
                side    = position["side"]
                entry   = float(position["entry"])
                extreme = float(position["extreme_price"])  # max per long, min per short

                sl_pct                = float(position["stop_loss_pct"])
                trailing_pct          = float(position["trailing_stop_pct"])
                trailing_activate_pct = float(position["trailing_activate_pct"])
                partial_exit_pct      = float(position["partial_exit_pct"])
                tight_pct             = float(position["trailing_stop_tight_pct"])

                gain_pct = _gain_pct(side, entry, current_price)

                # 1. Aggiorna prezzo estremo
                if side == "long"  and current_price > extreme:
                    position["extreme_price"] = current_price; extreme = current_price
                elif side == "short" and current_price < extreme:
                    position["extreme_price"] = current_price; extreme = current_price

                # 2. Hard stop loss (priorità massima)
                if side == "long":
                    hard_stop = entry * (1 - sl_pct / 100)
                    stop_hit  = current_price <= hard_stop
                else:
                    hard_stop = entry * (1 + sl_pct / 100)
                    stop_hit  = current_price >= hard_stop

                if stop_hit:
                    trade, portfolio = _full_close(state, position, current_price, "stop_loss", portfolio, strategy)
                    scal.update(state, trade)
                    print(
                        f"CLOSE [{side}] stop_loss @ {current_price:.2f}  "
                        f"pnl={trade['pnl_pct']:+.4f}  pnl_$={trade['pnl_dollar']:+,.0f}  "
                        f"balance=${portfolio['balance']:,.0f}",
                        flush=True,
                    )
                    asyncio.ensure_future(alert_mod.trade_closed(trade, "stop_loss", portfolio))
                    position = None

                # 3. Attiva trailing stop
                if position is not None and not position.get("trailing_active") and gain_pct >= trailing_activate_pct:
                    t_pct = tight_pct if position.get("half_closed") else trailing_pct
                    position["trailing_active"] = True
                    if side == "long":
                        position["trailing_price"] = extreme * (1 - t_pct / 100)
                    else:
                        position["trailing_price"] = extreme * (1 + t_pct / 100)
                    print(
                        f"TRAILING STOP activated [{side}]  "
                        f"trail={position['trailing_price']:.2f}  gain={gain_pct:.2f}%",
                        flush=True,
                    )
                    asyncio.ensure_future(alert_mod.trailing_activated(
                        current_price, float(position["trailing_price"]), gain_pct, side
                    ))
                    _save_position(state, position)

                # 4. Aggiorna trailing stop (ratchet)
                if position is not None and position.get("trailing_active"):
                    t_pct = tight_pct if position.get("half_closed") else trailing_pct
                    if side == "long":
                        new_trail = extreme * (1 - t_pct / 100)
                        if new_trail > (position.get("trailing_price") or 0.0):
                            position["trailing_price"] = new_trail
                    else:
                        new_trail = extreme * (1 + t_pct / 100)
                        if new_trail < (position.get("trailing_price") or float("inf")):
                            position["trailing_price"] = new_trail
                    _save_position(state, position)

                # 5. Partial exit dinamico su HVN o % fissa
                if position is not None and not position.get("half_closed"):
                    hvn_target:    float | None = None
                    hvn_gain_pct:  float | None = None
                    use_hvn = False

                    if side == "long":
                        hvn_target = vp_calc.next_hvn_above(vp, entry) if vp else None
                        if hvn_target is not None:
                            hvn_gain_pct = (hvn_target - entry) / entry * 100
                            use_hvn = 1.0 <= hvn_gain_pct <= 10.0
                    else:
                        hvn_target = vp_calc.next_hvn_below(vp, entry) if vp else None
                        if hvn_target is not None:
                            hvn_gain_pct = (entry - hvn_target) / entry * 100
                            use_hvn = 1.0 <= hvn_gain_pct <= 10.0

                    partial_trigger: float = hvn_gain_pct if (use_hvn and hvn_gain_pct is not None) else partial_exit_pct

                    if gain_pct >= partial_trigger:
                        reason = f"partial_tp_hvn_{hvn_target:.0f}" if use_hvn else "partial_take_profit_50pct"
                        trade, portfolio = _partial_close(state, position, current_price, reason, portfolio, strategy)
                        position["half_closed"]     = True
                        position["remaining_size"]  = 0.5
                        position["trailing_active"] = True
                        if side == "long":
                            position["trailing_price"] = extreme * (1 - tight_pct / 100)
                        else:
                            position["trailing_price"] = extreme * (1 + tight_pct / 100)
                        _save_position(state, position)
                        hvn_str = f"  HVN={hvn_target:.0f}" if use_hvn else ""
                        print(
                            f"PARTIAL CLOSE 50% [{side}] @ {current_price:.2f}  "
                            f"pnl={trade['pnl_pct']:+.4f}  gain={gain_pct:.2f}%{hvn_str}  "
                            f"pnl_$={trade['pnl_dollar']:+,.0f}  balance=${portfolio['balance']:,.0f}  "
                            f"-> trail tightened to {position['trailing_price']:.2f} (-{tight_pct}%)",
                            flush=True,
                        )
                        asyncio.ensure_future(alert_mod.trade_closed(trade, reason, portfolio))

                # 6. Trailing stop colpito
                if (
                    position is not None
                    and position.get("trailing_active")
                    and position.get("trailing_price") is not None
                ):
                    trail     = float(position["trailing_price"])
                    trail_hit = (side == "long"  and current_price <= trail) or \
                                (side == "short" and current_price >= trail)
                    if trail_hit:
                        trade, portfolio = _full_close(state, position, current_price, "trailing_stop", portfolio, strategy)
                        scal.update(state, trade)
                        print(
                            f"CLOSE [{side}] trailing_stop @ {current_price:.2f}  "
                            f"pnl={trade['pnl_pct']:+.4f}  pnl_$={trade['pnl_dollar']:+,.0f}  "
                            f"balance=${portfolio['balance']:,.0f}",
                            flush=True,
                        )
                        asyncio.ensure_future(alert_mod.trade_closed(trade, "trailing_stop", portfolio))
                        position = None

            # --- heartbeat + tick log ---
            _heartbeat(state, _count_trades(state), position is not None, None, consec_fail, portfolio)

            # --- tick summary ---
            ema_fast_v = _compute_ema(closes, fast)
            ema_slow_v = _compute_ema(closes, slow)
            ema_info = ""
            if ema_fast_v and ema_slow_v:
                spread = ema_fast_v[-1] - ema_slow_v[-1]
                ema_info = f"  EMA{fast}={ema_fast_v[-1]:.0f} EMA{slow}={ema_slow_v[-1]:.0f} spread={spread:+.0f}"

            pos_info = ""
            if position is not None:
                side_p = position["side"]
                g      = _gain_pct(side_p, float(position["entry"]), current_price)
                conf_p = position.get("confidence", 0.0)
                sz_p   = position.get("position_size_r", 0.5)
                t_str  = f"  trail={position['trailing_price']:.0f}" if position.get("trailing_price") else ""
                h_str  = " [50%left]" if position.get("half_closed") else ""
                ta_str = " T" if position.get("trailing_active") else ""
                pos_info = f"  |  POS [{side_p}] gain={g:+.2f}%{ta_str}{t_str}{h_str} conf={conf_p:.2f} sz={sz_p:.0%}"

            warmup     = f" (warmup {len(closes)}/{slow + 2})" if len(closes) < slow + 2 else ""
            vp_info    = f"  POC={poc:.0f}" if poc else ""
            hvn_a      = vp_calc.next_hvn_above(vp, current_price) if vp else None
            if hvn_a:
                vp_info += f"  HVN>{hvn_a:.0f}"
            vwap_tick  = ind.vwap_analysis(candles, current_price) if candles else {}
            if vwap_tick.get("vwap"):
                vwap_side = "^" if vwap_tick.get("above_vwap") else "v"
                vp_info  += f"  VWAP{vwap_side}{vwap_tick['vwap']:.0f}({vwap_tick['dist_pct']:+.2f}%)"
            if sma50d:
                sma50d_side = "^" if current_price > sma50d else "v"
                vp_info += f"  SMA50d{sma50d_side}{sma50d:.0f}"

            reg_sig     = regime.get("signal", 0.0)
            fresh_mark  = "" if regime.get("fresh", True) else "~"
            regime_info = f"  [{fresh_mark}{regime_label} {reg_sig:+.2f}]"

            _, dd_pct = _check_drawdown(portfolio, strategy)
            pnl_total = round((portfolio["balance"] - portfolio["initial_capital"]) / portfolio["initial_capital"] * 100, 2)
            balance_info = (
                f"  💰${portfolio['balance']:,.0f}"
                f"  pnl={pnl_total:+.2f}%"
                f"  dd={dd_pct:.1f}%"
            )
            sg_tag   = f"  {sg.status_line(now_utc, sg_cfg)}" if sg_result["blocked"] else ""
            news_tag = f"  {ni.news_status_line(now_utc)}"
            print(
                f"tick price={current_price:.2f}{ema_info}{vp_info}{regime_info}"
                f"{balance_info}{news_tag}{pos_info}{sg_tag}{warmup}",
                flush=True,
            )

        except SchemaError as e:
            print(f"FATAL SCHEMA ERROR: {e}", flush=True)
            raise
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            tb = traceback.format_exc()
            print(f"loop error: {e}\n{tb}", flush=True)
            _heartbeat(
                state, _count_trades(state),
                _load_position(state) is not None,
                str(e), consec_fail,
            )
            asyncio.ensure_future(alert_mod.worker_error(str(e), consec_fail))

        elapsed = time.time() - tick_started
        await asyncio.sleep(max(0.0, TICK_SECONDS - elapsed))

"""
indicators.py — Indicatori tecnici per hermes-trading.

Tutto calcolato offline sui 200 candles OHLCV gia' disponibili.
Nessuna chiamata API extra.

Formato candles: [{h, l, c, v}, ...]  (c = close, v = volume)
Il prezzo adapter fornisce {h, l, v} — usa enrich_candles() per aggiungere c.

Indicatori:
  ATR(n, Wilder)  — volatilita' reale. Dimensiona stop e trailing.
  VWAP            — prezzo medio ponderato per volume. Riferimento istituzionale.
  VWMA(n)         — media mobile pesata per volume. Conferma volumetrica del cross.
  ADX(n)          — forza del trend (non direzione). >25 = trend solido.
"""
from __future__ import annotations
import math


# ---------------------------------------------------------------------------
# Utilita'
# ---------------------------------------------------------------------------

def enrich_candles(candles: list[dict]) -> list[dict]:
    """
    Aggiunge campo 'c' (close) se mancante, usando (h+l)/2.
    Compatibile con il formato {h, l, v} del price adapter.
    """
    out = []
    for c in candles:
        if "c" not in c:
            c = {**c, "c": (c["h"] + c["l"]) / 2.0}
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# ATR — Average True Range (Wilder smoothing)
# ---------------------------------------------------------------------------

def compute_atr(candles: list[dict], period: int = 14) -> float | None:
    """
    ATR con smoothing di Wilder (standard industriale).
    True Range(i) = max(H-L, |H-prevC|, |L-prevC|)

    Ritorna l'ATR in punti assoluti (es. $111 su BTC).
    None se dati insufficienti.
    """
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h     = candles[i]["h"]
        l     = candles[i]["l"]
        prev_c= candles[i - 1].get("c", (candles[i-1]["h"] + candles[i-1]["l"]) / 2)
        tr    = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    # Wilder: inizializza con SMA, poi smoothing esponenziale
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def atr_pct(candles: list[dict], period: int = 14) -> float | None:
    """ATR come % del prezzo corrente. Es: 0.146 significa 0.146%."""
    atr = compute_atr(candles, period)
    if atr is None or not candles:
        return None
    price = candles[-1].get("c", (candles[-1]["h"] + candles[-1]["l"]) / 2)
    if price <= 0:
        return None
    return round(atr / price * 100, 6)


def atr_14_daily(candles_1d: list[dict]) -> float | None:
    """
    ATR(14) giornaliero in valore assoluto ($).
    Input: candles_1d dal price adapter (candele giornaliere).
    Usato dal sizing Kelly-Vol per calcolare sigma_daily.
    """
    return compute_atr(candles_1d, period=14)


def sigma_daily_history(candles_1d: list[dict], lookback: int = 30) -> list[float]:
    """
    Serie storica di sigma_daily = ATR_1d / prezzo_close per ogni giorno.
    Usato dal sizing per rilevare vol shock (sigma_daily > 2x media 30gg).
    Ritorna lista di float (% decimale, es 0.025 = 2.5% di sigma giornaliero).
    """
    if len(candles_1d) < 2:
        return []
    result = []
    window = min(lookback, len(candles_1d))
    for i in range(max(1, len(candles_1d) - window), len(candles_1d)):
        sub = candles_1d[:i + 1]
        atr = compute_atr(sub, period=min(14, len(sub) - 1))
        if atr is None:
            continue
        price = candles_1d[i].get("c", (candles_1d[i]["h"] + candles_1d[i]["l"]) / 2)
        if price > 0:
            result.append(atr / price)
    return result


def atr_stops(
    candles:      list[dict],
    entry_price:  float,
    side:         str,
    sl_mult:      float = 2.5,
    trail_mult:   float = 1.5,
    tight_mult:   float = 1.0,
    partial_rr:   float = 1.5,
    period:       int   = 14,
    # fallback % se ATR non disponibile
    sl_fallback_pct:     float = 0.37,
    trail_fallback_pct:  float = 0.22,
    tight_fallback_pct:  float = 0.15,
    partial_fallback_pct:float = 0.55,
) -> dict:
    """
    Calcola tutti i livelli di rischio per un trade.

    Ritorna:
      sl_price        — prezzo di stop loss
      sl_pct          — distanza SL in %
      partial_price   — prezzo di partial exit (prima uscita)
      partial_pct     — distanza partial in %
      trail_dist_pct  — distanza trailing stop prima della partial
      tight_dist_pct  — distanza trailing stop dopo la partial
      atr_used        — ATR usato (None se fallback)
      source          — "atr" o "fallback"
    """
    atr_val = compute_atr(candles, period)

    if atr_val is not None and entry_price > 0:
        sl_dist      = atr_val * sl_mult
        trail_dist   = atr_val * trail_mult
        tight_dist   = atr_val * tight_mult
        partial_dist = sl_dist * partial_rr
        source       = "atr"
    else:
        # fallback a % fisse
        sl_dist      = entry_price * sl_fallback_pct      / 100
        trail_dist   = entry_price * trail_fallback_pct   / 100
        tight_dist   = entry_price * tight_fallback_pct   / 100
        partial_dist = entry_price * partial_fallback_pct / 100
        atr_val      = None
        source       = "fallback"

    if side == "long":
        sl_price      = entry_price - sl_dist
        partial_price = entry_price + partial_dist
    else:
        sl_price      = entry_price + sl_dist
        partial_price = entry_price - partial_dist

    return {
        "sl_price":       round(sl_price,      2),
        "sl_pct":         round(sl_dist / entry_price * 100, 4),
        "partial_price":  round(partial_price, 2),
        "partial_pct":    round(partial_dist / entry_price * 100, 4),
        "trail_dist_pct": round(trail_dist / entry_price * 100, 4),
        "tight_dist_pct": round(tight_dist / entry_price * 100, 4),
        "atr_used":       round(atr_val, 2) if atr_val else None,
        "source":         source,
    }


# ---------------------------------------------------------------------------
# VWAP — Volume Weighted Average Price
# ---------------------------------------------------------------------------

def compute_vwap(candles: list[dict]) -> float | None:
    """
    VWAP = SUM(typical_price * volume) / SUM(volume)
    typical_price = (H + L + C) / 3

    E' il "prezzo giusto" ponderato per i volumi reali scambiati.
    Istituti e market maker usano il VWAP come riferimento:
      - Prezzo > VWAP → gli acquirenti sono in controllo (bullish)
      - Prezzo < VWAP → i venditori sono in controllo (bearish)

    Calcolato sull'intera finestra fornita (default: ultimi 200 candles = ~3.3h).
    """
    if not candles:
        return None
    cum_tv = 0.0
    cum_v  = 0.0
    for c in candles:
        h = c["h"]; l = c["l"]
        close = c.get("c", (h + l) / 2)
        tp    = (h + l + close) / 3.0
        v     = c.get("v", 0.0)
        cum_tv += tp * v
        cum_v  += v
    if cum_v <= 0:
        return None
    return round(cum_tv / cum_v, 6)


def vwap_analysis(candles: list[dict], current_price: float) -> dict:
    """
    Analisi completa VWAP.

    Ritorna:
      vwap          — valore VWAP
      above_vwap    — True se prezzo > VWAP
      dist_pct      — distanza % (positivo = sopra VWAP = bullish)
      signal        — "bullish" | "bearish" | "neutral"
      strength      — 0-1: quanto siamo distanti dal VWAP (0=vicino, 1=molto distante)
    """
    vwap = compute_vwap(candles)
    if vwap is None or vwap <= 0:
        return {
            "vwap": None, "above_vwap": None,
            "dist_pct": 0.0, "signal": "neutral", "strength": 0.0,
        }

    dist_pct = (current_price - vwap) / vwap * 100
    above    = current_price > vwap

    # Strength: normalizza su scala 0-1 rispetto alla distanza tipica
    # (basata su osservazioni reali: dist media ~0.17%, p90 ~0.32%)
    strength = min(1.0, abs(dist_pct) / 0.32)

    if dist_pct > 0.05:
        signal = "bullish"
    elif dist_pct < -0.05:
        signal = "bearish"
    else:
        signal = "neutral"

    return {
        "vwap":       round(vwap, 2),
        "above_vwap": above,
        "dist_pct":   round(dist_pct, 4),
        "signal":     signal,
        "strength":   round(strength, 3),
    }


# ---------------------------------------------------------------------------
# VWMA — Volume Weighted Moving Average
# ---------------------------------------------------------------------------

def compute_vwma(candles: list[dict], period: int) -> float | None:
    """
    VWMA(n) = SUM(close * volume, ultimi n) / SUM(volume, ultimi n)

    Diversamente dall'EMA standard, le candele ad alto volume pesano di piu'.
    Un cross EMA + VWMA e' piu' solido perche' richiede conferma volumetrica:
    se i grandi volumi non confermano il cross, il segnale e' piu' debole.
    """
    if len(candles) < period:
        return None
    recent = candles[-period:]
    num = 0.0
    den = 0.0
    for c in recent:
        close = c.get("c", (c["h"] + c["l"]) / 2)
        v     = c.get("v", 1.0)
        num  += close * v
        den  += v
    if den <= 0:
        return None
    return round(num / den, 6)


def vwma_analysis(candles: list[dict], fast: int = 20, slow: int = 50) -> dict:
    """
    Analisi VWMA fast/slow.

    Ritorna:
      vwma_fast       — VWMA periodo fast
      vwma_slow       — VWMA periodo slow
      signal          — "bullish" (fast > slow) | "bearish" | "neutral"
      cross           — "golden" | "death" | None (solo sul tick corrente)
      spread_pct      — distanza % tra fast e slow (forza del segnale)
      confirms_long   — True se VWMA conferma direzione long
      confirms_short  — True se VWMA conferma direzione short
    """
    vf_now  = compute_vwma(candles,      fast)
    vs_now  = compute_vwma(candles,      slow)
    vf_prev = compute_vwma(candles[:-1], fast) if len(candles) > 1 else None
    vs_prev = compute_vwma(candles[:-1], slow) if len(candles) > 1 else None

    if vf_now is None or vs_now is None:
        return {
            "vwma_fast": None, "vwma_slow": None,
            "signal": "neutral", "cross": None,
            "spread_pct": 0.0, "confirms_long": False, "confirms_short": False,
        }

    spread_pct = (vf_now - vs_now) / vs_now * 100 if vs_now > 0 else 0.0
    signal = "bullish" if vf_now > vs_now else "bearish" if vf_now < vs_now else "neutral"

    cross = None
    if vf_prev is not None and vs_prev is not None:
        if vf_prev <= vs_prev and vf_now > vs_now:
            cross = "golden"
        elif vf_prev >= vs_prev and vf_now < vs_now:
            cross = "death"

    return {
        "vwma_fast":      round(vf_now, 2),
        "vwma_slow":      round(vs_now, 2),
        "signal":         signal,
        "cross":          cross,
        "spread_pct":     round(spread_pct, 4),
        "confirms_long":  vf_now > vs_now,
        "confirms_short": vf_now < vs_now,
    }


# ---------------------------------------------------------------------------
# ADX — Average Directional Index (forza del trend)
# ---------------------------------------------------------------------------

def compute_adx(candles: list[dict], period: int = 14) -> dict | None:
    """
    ADX misura la FORZA del trend, non la direzione.
    Interpretazione standard:
      ADX < 20  → mercato laterale / rumore — NON entrare in trend following
      ADX 20-25 → trend debole in formazione
      ADX > 25  → trend solido — momento ideale per trend following
      ADX > 40  → trend molto forte (attenzione a non inseguire)

    +DI > -DI = trend rialzista
    -DI > +DI = trend ribassista
    """
    if len(candles) < period * 2 + 1:
        return None

    plus_dm_list:  list[float] = []
    minus_dm_list: list[float] = []
    tr_list:       list[float] = []

    for i in range(1, len(candles)):
        h_now  = candles[i]["h"];     l_now  = candles[i]["l"]
        h_prev = candles[i-1]["h"];   l_prev = candles[i-1]["l"]
        c_prev = candles[i-1].get("c", (h_prev + l_prev) / 2)

        tr   = max(h_now - l_now, abs(h_now - c_prev), abs(l_now - c_prev))
        up   = h_now - h_prev
        down = l_prev - l_now

        plus_dm  = up   if (up   > down and up   > 0) else 0.0
        minus_dm = down if (down > up   and down > 0) else 0.0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    def _wilder(values: list[float], n: int) -> list[float]:
        s = [sum(values[:n])]
        for v in values[n:]:
            s.append(s[-1] - s[-1] / n + v)
        return s

    atr_s      = _wilder(tr_list,        period)
    plus_dm_s  = _wilder(plus_dm_list,   period)
    minus_dm_s = _wilder(minus_dm_list,  period)

    dx_list: list[float] = []
    plus_di_last = minus_di_last = 0.0

    for i in range(len(atr_s)):
        if atr_s[i] <= 0:
            continue
        pdi  = 100 * plus_dm_s[i]  / atr_s[i]
        mdi  = 100 * minus_dm_s[i] / atr_s[i]
        dsum = pdi + mdi
        dx   = 100 * abs(pdi - mdi) / dsum if dsum > 0 else 0.0
        dx_list.append(dx)
        plus_di_last  = pdi
        minus_di_last = mdi

    if not dx_list:
        return None

    recent_dx = dx_list[-period:]
    adx_val   = sum(recent_dx) / len(recent_dx)

    trend_side = "bull" if plus_di_last > minus_di_last else "bear"
    trend_strength = (
        "strong"  if adx_val >= 35 else
        "moderate" if adx_val >= 25 else
        "weak"    if adx_val >= 20 else
        "ranging"
    )

    return {
        "adx":            round(adx_val, 2),
        "plus_di":        round(plus_di_last, 2),
        "minus_di":       round(minus_di_last, 2),
        "trend_side":     trend_side,
        "trend_strength": trend_strength,
        "tradeable":      adx_val >= 20,   # False = laterale, evitare trend following
    }

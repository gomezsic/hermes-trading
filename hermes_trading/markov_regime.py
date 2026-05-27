"""
markov_regime.py — Markov daily regime filter per il loop 1m.

Calcola il regime (Bear / Sideways / Bull) sui dati daily via yfinance.
Salva la cache in state/markov_regime.json, refresh ogni REFRESH_MINUTES.

Uso nel loop:
    from .markov_regime import get_regime
    regime = await get_regime(asset, state)
    # regime["label"]  -> "Bear" | "Sideways" | "Bull"
    # regime["signal"] -> float in [-1, +1]
    # regime["next_probs"] -> {"Bear": float, "Sideways": float, "Bull": float}
    # regime["fresh"]  -> bool (False se yfinance ha fallito, usa cache vecchia)
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------
REFRESH_MINUTES = 60        # ricalcola max ogni ora
LOOKBACK_YEARS  = 2         # quanti anni di dati daily
WINDOW          = 20        # rolling window per log-return
THRESHOLD       = 0.08      # soglia regime crypto (non 0.05 — troppo tight per BTC)
MIN_TRAIN       = 30        # minimo giorni per avere la matrice

STATES      = ["Bear", "Sideways", "Bull"]   # indici 0, 1, 2
CACHE_FILE  = "markov_regime.json"

# -----------------------------------------------------------------------
# Markov helpers (indipendenti dallo skill ~/.claude)
# -----------------------------------------------------------------------

def _label_regimes(close: pd.Series, window: int, threshold: float) -> pd.Series:
    log_close = np.log(close)
    rolling   = log_close - log_close.shift(window)
    labels    = pd.Series(1, index=close.index, dtype=int)
    labels[rolling >  threshold] = 2  # Bull
    labels[rolling < -threshold] = 0  # Bear
    return labels.dropna()


def _build_matrix(labels: pd.Series) -> np.ndarray:
    counts = np.zeros((3, 3), dtype=float)
    arr = labels.to_numpy()
    for i in range(len(arr) - 1):
        counts[arr[i], arr[i + 1]] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return counts / row_sums


def _signal(P: np.ndarray, state: int) -> float:
    """P(Bull|state) - P(Bear|state) in [-1, +1]."""
    return float(np.clip(P[state, 2] - P[state, 0], -1.0, 1.0))


# -----------------------------------------------------------------------
# yfinance fetch (sync — verrà chiamato in thread executor)
# -----------------------------------------------------------------------

def _fetch_daily(ticker: str) -> pd.Series:
    """Ritorna la serie Close daily. Lancia eccezione se fallisce."""
    import yfinance as yf
    end   = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = end - pd.DateOffset(years=LOOKBACK_YEARS)
    df = yf.download(
        ticker,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned empty for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df["Close"].dropna()


# -----------------------------------------------------------------------
# Cache I/O
# -----------------------------------------------------------------------

def _load_cache(state: Path) -> dict | None:
    f = state / CACHE_FILE
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _save_cache(state: Path, data: dict) -> None:
    (state / CACHE_FILE).write_text(json.dumps(data, indent=2))


def _is_stale(cache: dict) -> bool:
    ts = cache.get("computed_at")
    if not ts:
        return True
    try:
        computed = datetime.fromisoformat(ts)
        if computed.tzinfo is None:
            computed = computed.replace(tzinfo=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - computed).total_seconds() / 60
        return age_minutes >= REFRESH_MINUTES
    except Exception:
        return True


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

async def get_regime(asset: str, state: Path) -> dict:
    """
    Ritorna il regime corrente. Usa cache se fresca, altrimenti ricalcola.
    Non lancia mai eccezioni — in caso di errore ritorna l'ultima cache o
    un default neutro (Sideways, signal=0).
    """
    cache = _load_cache(state)

    if cache and not _is_stale(cache):
        return {**cache, "fresh": True, "from_cache": True}

    # Ricalcola in thread per non bloccare l'event loop
    try:
        ticker = asset.replace("/", "-")   # BTC/USDT → BTC-USDT, poi yfinance accetta BTC-USD
        # Kraken usa BTC/USDT, yfinance vuole BTC-USD — mappa i più comuni
        yf_ticker = _map_ticker(ticker)
        close = await asyncio.to_thread(_fetch_daily, yf_ticker)

        labels = _label_regimes(close, WINDOW, THRESHOLD)
        if len(labels) < MIN_TRAIN:
            raise RuntimeError(f"Troppo pochi dati ({len(labels)} righe)")

        P             = _build_matrix(labels)
        current_state = int(labels.iloc[-1])
        sig           = _signal(P, current_state)
        next_probs    = {STATES[i]: round(float(P[current_state, i]) * 100, 1) for i in range(3)}

        # Mix storico
        bear_pct = round(float((labels == 0).mean()) * 100, 1)
        side_pct = round(float((labels == 1).mean()) * 100, 1)
        bull_pct = round(float((labels == 2).mean()) * 100, 1)

        # Persistenza diagonale
        persistence = {STATES[i]: round(float(P[i, i]) * 100, 1) for i in range(3)}

        result = {
            "label":        STATES[current_state],
            "state_index":  current_state,
            "signal":       round(sig, 4),
            "next_probs":   next_probs,
            "persistence":  persistence,
            "mix":          {"Bear": bear_pct, "Sideways": side_pct, "Bull": bull_pct},
            "last_close":   round(float(close.iloc[-1]), 2),
            "last_date":    close.index[-1].strftime("%Y-%m-%d"),
            "rows":         len(labels),
            "ticker":       yf_ticker,
            "computed_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fresh":        True,
            "from_cache":   False,
        }
        _save_cache(state, result)
        return result

    except Exception as e:
        # Fallback: usa cache vecchia se disponibile, altrimenti Sideways neutro
        if cache:
            return {**cache, "fresh": False, "from_cache": True, "error": str(e)}
        return {
            "label":       "Sideways",
            "state_index": 1,
            "signal":      0.0,
            "next_probs":  {"Bear": 33.3, "Sideways": 33.3, "Bull": 33.3},
            "fresh":       False,
            "from_cache":  False,
            "error":       str(e),
        }


def _map_ticker(ticker: str) -> str:
    """Mappa ticker ccxt → yfinance."""
    mapping = {
        "BTC-USDT": "BTC-USD",
        "ETH-USDT": "ETH-USD",
        "SOL-USDT": "SOL-USD",
        "BNB-USDT": "BNB-USD",
        "XRP-USDT": "XRP-USD",
    }
    return mapping.get(ticker.upper(), ticker)

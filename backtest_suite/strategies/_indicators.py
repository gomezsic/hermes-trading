"""Indicatori condivisi tra le strategie del backtest_suite.

Funzioni pure su liste di float / candele dict {t,o,h,l,c,v}. Nessuno stato.
Ogni funzione ritorna una lista della stessa lunghezza dell'input, con None
nelle posizioni in cui l'indicatore non è ancora calcolabile.
"""
from __future__ import annotations


def compute_sma(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def compute_ema(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def compute_atr(candles: list[dict], period: int) -> list[float | None]:
    """ATR di Wilder. True Range = max(h-l, |h-prev_c|, |l-prev_c|)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n <= period:
        return out
    trs: list[float] = [0.0] * n
    trs[0] = float(candles[0]["h"]) - float(candles[0]["l"])
    for i in range(1, n):
        h = float(candles[i]["h"])
        l = float(candles[i]["l"])
        pc = float(candles[i - 1]["c"])
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    # seed = media dei primi `period` TR (indici 1..period)
    atr = sum(trs[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out

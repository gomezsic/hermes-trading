"""SupertrendStrategy — trend follower ATR-based (Supertrend classico).

Calcola la linea Supertrend in un'unica passata cachata sulle candele e
emette un segnale solo sulla barra in cui il trend cambia direzione.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_atr


class SupertrendStrategy:
    strategy_id:  ClassVar[str]                 = "supertrend"
    display_name: ClassVar[str]                 = "Supertrend"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("atr_period", 5, 30, 1, is_int=True),
        ParamSpec("multiplier", 1.0, 5.0, None),
        ParamSpec("direction",  0,  2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.atr_period = int(params["atr_period"])
        self.multiplier = float(params["multiplier"])
        self.direction  = int(params.get("direction", 2))
        # trend[idx] = +1 (rialzo) / -1 (ribasso) / 0 (non pronto)
        self._trend_cache: list[int] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.atr_period + 1

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        n = len(candles)
        atr = compute_atr(candles, self.atr_period)
        trend: list[int] = [0] * n
        final_upper = [0.0] * n
        final_lower = [0.0] * n
        prev_trend = 1
        for i in range(n):
            if atr[i] is None:
                trend[i] = 0
                continue
            hl2 = (float(candles[i]["h"]) + float(candles[i]["l"])) / 2.0
            basic_upper = hl2 + self.multiplier * atr[i]
            basic_lower = hl2 - self.multiplier * atr[i]
            c_prev = float(candles[i - 1]["c"]) if i > 0 else float(candles[i]["c"])

            if i == 0 or final_upper[i - 1] == 0.0:
                final_upper[i] = basic_upper
                final_lower[i] = basic_lower
            else:
                final_upper[i] = (basic_upper
                                  if (basic_upper < final_upper[i - 1] or c_prev > final_upper[i - 1])
                                  else final_upper[i - 1])
                final_lower[i] = (basic_lower
                                  if (basic_lower > final_lower[i - 1] or c_prev < final_lower[i - 1])
                                  else final_lower[i - 1])

            c_now = float(candles[i]["c"])
            if c_now > final_upper[i]:
                cur = 1
            elif c_now < final_lower[i]:
                cur = -1
            else:
                cur = prev_trend
            trend[i] = cur
            prev_trend = cur
        self._trend_cache = trend
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._trend_cache is not None
        if idx < self.warmup_bars():
            return Signal(side=None)
        cur  = self._trend_cache[idx]
        prev = self._trend_cache[idx - 1]
        if cur == 0 or prev == 0:
            return Signal(side=None)

        side: str | None = None
        if prev <= 0 and cur > 0:
            side = "long"
        elif prev >= 0 and cur < 0:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

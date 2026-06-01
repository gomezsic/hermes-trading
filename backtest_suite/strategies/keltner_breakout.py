"""KeltnerBreakoutStrategy — rottura del canale di Keltner (EMA ± k·ATR).

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_ema, compute_atr


class KeltnerBreakoutStrategy:
    strategy_id:  ClassVar[str]                 = "keltner_breakout"
    display_name: ClassVar[str]                 = "Keltner Breakout"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_period", 10, 50, 1, is_int=True),
        ParamSpec("atr_period",  5, 30, 1, is_int=True),
        ParamSpec("multiplier",  1.0, 3.0, None),
        ParamSpec("direction",   0,  2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_period = int(params["ema_period"])
        self.atr_period = int(params["atr_period"])
        self.multiplier = float(params["multiplier"])
        self.direction  = int(params.get("direction", 2))
        self._ema_cache: list[float | None] | None = None
        self._atr_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return max(self.ema_period, self.atr_period + 1)

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._ema_cache = compute_ema(closes, self.ema_period)
        self._atr_cache = compute_atr(candles, self.atr_period)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._ema_cache is not None and self._atr_cache is not None
        mid = self._ema_cache[idx]
        atr = self._atr_cache[idx]
        if mid is None or atr is None:
            return Signal(side=None)
        upper = mid + self.multiplier * atr
        lower = mid - self.multiplier * atr
        c_now = float(candles[idx]["c"])

        side: str | None = None
        if c_now > upper:
            side = "long"
        elif c_now < lower:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

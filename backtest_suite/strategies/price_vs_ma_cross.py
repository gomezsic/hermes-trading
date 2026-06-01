"""PriceVsMaCrossStrategy — il close reale incrocia una media mobile (SMA o EMA).

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_sma, compute_ema


class PriceVsMaCrossStrategy:
    strategy_id:  ClassVar[str]                 = "price_vs_ma_cross"
    display_name: ClassVar[str]                 = "Price vs MA Cross"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ma_period", 5, 200, 1, is_int=True),
        ParamSpec("ma_type",   0,   1, 1, is_int=True, description="0=SMA, 1=EMA"),
        ParamSpec("direction", 0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ma_period = int(params["ma_period"])
        self.ma_type   = int(params.get("ma_type", 0))
        self.direction = int(params.get("direction", 2))
        self._ma_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.ma_period

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._ma_cache = (compute_ema(closes, self.ma_period)
                          if self.ma_type == 1
                          else compute_sma(closes, self.ma_period))
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._ma_cache is not None
        if idx < self.ma_period:
            return Signal(side=None)
        ma_now  = self._ma_cache[idx]
        ma_prev = self._ma_cache[idx - 1]
        if ma_now is None or ma_prev is None:
            return Signal(side=None)
        c_now  = float(candles[idx]["c"])
        c_prev = float(candles[idx - 1]["c"])

        side: str | None = None
        if c_prev <= ma_prev and c_now > ma_now:
            side = "long"
        elif c_prev >= ma_prev and c_now < ma_now:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

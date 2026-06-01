"""DonchianBreakoutStrategy — rottura del canale di Donchian a N periodi.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


class DonchianBreakoutStrategy:
    strategy_id:  ClassVar[str]                 = "donchian_breakout"
    display_name: ClassVar[str]                 = "Donchian Breakout"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("channel_period", 5, 100, 1, is_int=True),
        ParamSpec("direction",      0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.channel_period = int(params["channel_period"])
        self.direction      = int(params.get("direction", 2))

    def warmup_bars(self) -> int:
        return self.channel_period

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        if idx < self.channel_period:
            return Signal(side=None)
        window = candles[idx - self.channel_period: idx]   # barre PRECEDENTI
        hi = max(float(c["h"]) for c in window)
        lo = min(float(c["l"]) for c in window)
        c_now = float(candles[idx]["c"])

        side: str | None = None
        if c_now > hi:
            side = "long"
        elif c_now < lo:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

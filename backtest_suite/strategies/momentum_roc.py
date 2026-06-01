"""MomentumRocStrategy — Rate-of-Change oltre soglia.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


class MomentumRocStrategy:
    strategy_id:  ClassVar[str]                 = "momentum_roc"
    display_name: ClassVar[str]                 = "Momentum ROC"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("roc_period",    5, 50, 1, is_int=True),
        ParamSpec("threshold_pct", 1.0, 20.0, None, description="soglia % ROC"),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.roc_period    = int(params["roc_period"])
        self.threshold_pct = float(params["threshold_pct"])
        self.direction     = int(params.get("direction", 2))

    def warmup_bars(self) -> int:
        return self.roc_period

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        if idx < self.roc_period:
            return Signal(side=None)
        c_now  = float(candles[idx]["c"])
        c_past = float(candles[idx - self.roc_period]["c"])
        if c_past == 0:
            return Signal(side=None)
        roc = (c_now / c_past - 1.0) * 100.0

        side: str | None = None
        if roc > self.threshold_pct:
            side = "long"
        elif roc < -self.threshold_pct:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

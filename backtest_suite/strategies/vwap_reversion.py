"""VwapReversionStrategy — rientro verso il VWAP rolling.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _rolling_vwap(candles: list[dict], window: int) -> list[float | None]:
    """VWAP su finestra mobile di `window` barre. typical price = (h+l+c)/3."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if window <= 0 or n < window:
        return out
    tp = [(float(c["h"]) + float(c["l"]) + float(c["c"])) / 3.0 for c in candles]
    vol = [float(c["v"]) for c in candles]
    for i in range(window - 1, n):
        num = 0.0
        den = 0.0
        for j in range(i - window + 1, i + 1):
            num += tp[j] * vol[j]
            den += vol[j]
        out[i] = (num / den) if den > 0 else None
    return out


class VwapReversionStrategy:
    strategy_id:  ClassVar[str]                 = "vwap_reversion"
    display_name: ClassVar[str]                 = "VWAP Reversion"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("vwap_window",   10, 200, 1, is_int=True),
        ParamSpec("threshold_pct", 0.5, 10.0, None, description="distanza % dal VWAP"),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.vwap_window   = int(params["vwap_window"])
        self.threshold_pct = float(params["threshold_pct"])
        self.direction     = int(params.get("direction", 2))
        self._vwap_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.vwap_window

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        self._vwap_cache = _rolling_vwap(candles, self.vwap_window)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._vwap_cache is not None
        vwap = self._vwap_cache[idx]
        if vwap is None or vwap == 0:
            return Signal(side=None)
        c_now = float(candles[idx]["c"])
        dist = (c_now - vwap) / vwap * 100.0

        side: str | None = None
        if dist < -self.threshold_pct:
            side = "long"
        elif dist > self.threshold_pct:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

"""MacdSignalStrategy — cross MACD / signal line.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_ema


def _ema_of_series(series: list[float | None], period: int) -> list[float | None]:
    """EMA su una serie che può contenere None all'inizio (es. la MACD line)."""
    n = len(series)
    out: list[float | None] = [None] * n
    # indice del primo valore non-None
    start = next((i for i, v in enumerate(series) if v is not None), None)
    if start is None or n - start < period:
        return out
    vals = [float(v) for v in series[start:]]   # type: ignore[arg-type]
    sub = compute_ema(vals, period)
    for j, v in enumerate(sub):
        out[start + j] = v
    return out


class MacdSignalStrategy:
    strategy_id:  ClassVar[str]                 = "macd_signal"
    display_name: ClassVar[str]                 = "MACD Signal Cross"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_fast",      5,  20, 1, is_int=True),
        ParamSpec("ema_slow",     20,  50, 1, is_int=True),
        ParamSpec("signal_period", 5,  15, 1, is_int=True),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_fast      = int(params["ema_fast"])
        self.ema_slow      = int(params["ema_slow"])
        self.signal_period = int(params["signal_period"])
        self.direction     = int(params.get("direction", 2))
        self._macd_cache:   list[float | None] | None = None
        self._signal_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.ema_slow + self.signal_period

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        ema_f = compute_ema(closes, self.ema_fast)
        ema_s = compute_ema(closes, self.ema_slow)
        macd: list[float | None] = [
            (f - s) if (f is not None and s is not None) else None
            for f, s in zip(ema_f, ema_s)
        ]
        self._macd_cache   = macd
        self._signal_cache = _ema_of_series(macd, self.signal_period)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._macd_cache is not None and self._signal_cache is not None
        if idx < 1:
            return Signal(side=None)
        m_now,  m_prev = self._macd_cache[idx],   self._macd_cache[idx - 1]
        s_now,  s_prev = self._signal_cache[idx], self._signal_cache[idx - 1]
        if None in (m_now, m_prev, s_now, s_prev):
            return Signal(side=None)

        side: str | None = None
        if m_prev <= s_prev and m_now > s_now:
            side = "long"
        elif m_prev >= s_prev and m_now < s_now:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)

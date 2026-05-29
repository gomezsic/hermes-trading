"""
BollingerBreakoutStrategy — long se close > upper band per N bar consecutivi,
short se close < lower band per N bar.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _compute_bands(closes: list[float], period: int, std_mult: float):
    """Calcola upper/lower band a ogni indice (None nei primi period-1)."""
    n = len(closes)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period:
        return upper, lower
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        m  = mean(window)
        sd = pstdev(window)
        upper[i] = m + std_mult * sd
        lower[i] = m - std_mult * sd
    return upper, lower


class BollingerBreakoutStrategy:
    strategy_id:  ClassVar[str]                  = "bb_breakout"
    display_name: ClassVar[str]                  = "Bollinger Breakout"
    timeframes:   ClassVar[tuple[str, ...]]      = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("bb_period",         10,  40,  1,   is_int=True),
        ParamSpec("bb_std",            1.5, 3.0, 0.1),
        ParamSpec("confirmation_bars", 1,   5,   1,   is_int=True),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.period   = int(params["bb_period"])
        self.std_mult = float(params["bb_std"])
        self.confirm  = int(params["confirmation_bars"])

        self._upper: list[float | None] | None = None
        self._lower: list[float | None] | None = None
        # Cache tramite reference-identity: si ricalcola solo se la lista cambia
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.period

    def _ensure_cache(self, candles: list[dict]) -> None:
        # Usa `is` per confronto identità: evita ricalcolo sulla stessa lista
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._upper, self._lower = _compute_bands(closes, self.period, self.std_mult)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._upper is not None and self._lower is not None

        if idx < self.period + self.confirm - 1:
            return Signal(side=None)

        # Verifica confirmation_bars barre consecutive sopra/sotto la banda
        long_ok = True
        short_ok = True
        for j in range(idx - self.confirm + 1, idx + 1):
            u = self._upper[j]
            l = self._lower[j]
            c = float(candles[j]["c"])
            if u is None or l is None:
                return Signal(side=None)
            if c <= u:
                long_ok = False
            if c >= l:
                short_ok = False

        if long_ok:
            return Signal(side="long")
        if short_ok:
            return Signal(side="short")
        return Signal(side=None)

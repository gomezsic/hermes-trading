"""
RsiMeanReversionStrategy — RSI(n) classico: long se RSI < oversold,
short se RSI > overbought.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _compute_rsi(closes: list[float], period: int) -> list[float | None]:
    """RSI di Wilder (smoothing esponenziale)."""
    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi

    gains  = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains  / period
    avg_loss = losses / period
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)

    return rsi


class RsiMeanReversionStrategy:
    strategy_id:  ClassVar[str]                 = "rsi_mr"
    display_name: ClassVar[str]                 = "RSI Mean Reversion"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("rsi_period", 7,  21, 1, is_int=True),
        ParamSpec("oversold",   15, 35, 1, is_int=True),
        ParamSpec("overbought", 65, 85, 1, is_int=True),
        ParamSpec("exit_mid",   40, 60, 1, is_int=True),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.period     = int(params["rsi_period"])
        self.oversold   = int(params["oversold"])
        self.overbought = int(params["overbought"])
        self.exit_mid   = int(params["exit_mid"])

        self._rsi_cache: list[float | None] | None = None
        # Riferimento alla lista candles usata per popolare la cache.
        # Guard con identità (is) — non con id() — per evitare falsi hit
        # quando GA riutilizza l'istanza su finestre diverse con lo stesso id.
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.period + 1

    def _ensure_cache(self, candles: list[dict]) -> None:
        # Se punta allo stesso oggetto lista, la cache è ancora valida
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._rsi_cache  = _compute_rsi(closes, self.period)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._rsi_cache is not None

        if idx < self.warmup_bars():
            return Signal(side=None)

        rsi = self._rsi_cache[idx]
        if rsi is None:
            return Signal(side=None)

        if rsi <= self.oversold:
            return Signal(side="long",  confidence=(self.oversold   - rsi) / self.oversold)
        if rsi >= self.overbought:
            return Signal(side="short", confidence=(rsi - self.overbought) / (100 - self.overbought))
        return Signal(side=None)

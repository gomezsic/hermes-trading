"""
EmaCrossStrategy — wrap della logica EMA cross 20/50 + filtro VWAP esistente.

Riusa _compute_ema e _compute_vwap_rolling da hermes_trading.backtester per
garantire equivalenza bit-perfect col backtester legacy.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5, §15.
"""
from __future__ import annotations

from typing import ClassVar

from hermes_trading.backtester import _compute_ema, _compute_vwap_rolling

from backtest_suite.strategies.base import ParamSpec, Signal


class EmaCrossStrategy:
    """EMA cross fast/slow con filtro VWAP opzionale. direction codificato come int."""

    strategy_id:  ClassVar[str]                = "ema_cross"
    display_name: ClassVar[str]                = "EMA Cross"
    timeframes:   ClassVar[tuple[str, ...]]    = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_fast",    5,  30,  1, is_int=True),
        ParamSpec("ema_slow",   20, 100,  1, is_int=True),
        ParamSpec("vwap_window", 50, 400, 10, is_int=True),
        ParamSpec("vwap_filter",  0,   1,  1, is_int=True, description="0|1"),
        ParamSpec("direction",    0,   2,  1, is_int=True,
                  description="0=long, 1=short, 2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_fast    = int(params["ema_fast"])
        self.ema_slow    = int(params["ema_slow"])
        self.vwap_window = int(params.get("vwap_window", 200))
        self.vwap_filter = bool(int(params.get("vwap_filter", 0)))
        self.direction   = int(params.get("direction", 2))

        self._ema_f_cache: list[float | None] | None = None
        self._ema_s_cache: list[float | None] | None = None
        self._vwap_cache: list[float | None] | None  = None
        self._candles_id: int | None = None

    def warmup_bars(self) -> int:
        return self.ema_slow

    def _ensure_caches(self, candles: list[dict]) -> None:
        if self._candles_id == id(candles):
            return
        closes = [float(c["c"]) for c in candles]
        self._ema_f_cache = _compute_ema(closes, self.ema_fast)
        self._ema_s_cache = _compute_ema(closes, self.ema_slow)
        self._vwap_cache  = _compute_vwap_rolling(candles, self.vwap_window) \
            if self.vwap_filter else None
        self._candles_id  = id(candles)

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_caches(candles)
        assert self._ema_f_cache is not None and self._ema_s_cache is not None

        if idx < self.ema_slow:
            return Signal(side=None)

        ef_now  = self._ema_f_cache[idx]
        es_now  = self._ema_s_cache[idx]
        ef_prev = self._ema_f_cache[idx - 1]
        es_prev = self._ema_s_cache[idx - 1]

        if ef_now is None or es_now is None or ef_prev is None or es_prev is None:
            return Signal(side=None)

        side: str | None = None
        if ef_prev <= es_prev and ef_now > es_now:
            side = "long"
        elif ef_prev >= es_prev and ef_now < es_now:
            side = "short"

        if side is None:
            return Signal(side=None)

        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)

        if self.vwap_filter and self._vwap_cache is not None:
            vwap_val = self._vwap_cache[idx]
            if vwap_val is not None:
                close_i = float(candles[idx]["c"])
                if side == "long"  and close_i < vwap_val:
                    return Signal(side=None)
                if side == "short" and close_i > vwap_val:
                    return Signal(side=None)

        return Signal(side=side)

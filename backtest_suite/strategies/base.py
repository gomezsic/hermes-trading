"""
Contratto base per le Strategy della backtest_suite.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True)
class ParamSpec:
    """Definisce un parametro tunabile di una strategy."""
    name: str
    low: float
    high: float
    step: float | None = None       # None = continuo; valore = discretizzato (per grid)
    is_int: bool = False
    description: str = ""


@dataclass
class Signal:
    """Output del segnale a una candela."""
    side: str | None                # "long" | "short" | None
    confidence: float = 1.0


@runtime_checkable
class Strategy(Protocol):
    """Contratto che ogni strategia deve rispettare."""

    strategy_id:  ClassVar[str]
    display_name: ClassVar[str]
    timeframes:   ClassVar[tuple[str, ...]]
    param_specs:  ClassVar[tuple[ParamSpec, ...]]

    def __init__(self, params: dict[str, float]) -> None: ...

    def warmup_bars(self) -> int: ...

    def on_bar(self, idx: int, candles: list[dict]) -> Signal: ...

"""
execution — re-export di helper di esecuzione dal modulo condiviso _engine_core.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6, §15.
"""
from hermes_trading._engine_core import (
    SLIPPAGE,
    TAKER_FEE,
    apply_slippage_entry,
    apply_slippage_exit,
    build_equity_curve,
    gross_pnl_pct,
)

__all__ = [
    "SLIPPAGE", "TAKER_FEE",
    "apply_slippage_entry", "apply_slippage_exit",
    "gross_pnl_pct", "build_equity_curve",
]

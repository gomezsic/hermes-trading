"""
_engine_core.py — Helper puri condivisi tra backtester legacy e backtest_suite engine.

Estratto da backtester.py durante il refactor non-distruttivo.
Stesse costanti e semantica; nessun cambiamento di comportamento.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §15.
"""
from __future__ import annotations

from dataclasses import dataclass

# Costanti di costo (Kraken taker fee + slippage market order) — invariate.
TAKER_FEE: float = 0.0026
SLIPPAGE:  float = 0.0005


@dataclass(frozen=True)
class RiskConfig:
    """Parametri di risk management usati dall'engine (decimali, non percentuali)."""
    stop_loss_pct: float
    partial_exit_pct: float
    trailing_activate_pct: float
    trailing_stop_pct: float
    trailing_stop_tight_pct: float


def apply_slippage_entry(price: float, side: str) -> float:
    """Slippage entry: long peggiora verso l'alto, short verso il basso."""
    if side == "long":
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def apply_slippage_exit(price: float, side: str) -> float:
    """Slippage exit: long abbassa prezzo, short alza prezzo."""
    if side == "long":
        return price * (1.0 - SLIPPAGE)
    return price * (1.0 + SLIPPAGE)


def gross_pnl_pct(entry: float, exit_p: float, side: str) -> float:
    """PnL lordo decimale (es. 0.05 = +5%)."""
    if side == "long":
        return (exit_p - entry) / entry
    return (entry - exit_p) / entry

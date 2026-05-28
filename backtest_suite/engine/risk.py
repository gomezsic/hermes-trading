"""
risk — re-export di simulate_trade e RiskConfig dal modulo condiviso _engine_core.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6, §15.
"""
from hermes_trading._engine_core import RiskConfig, simulate_trade

__all__ = ["RiskConfig", "simulate_trade"]

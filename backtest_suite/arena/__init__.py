# backtest_suite/arena/__init__.py
"""arena — 10 archetipi che competono su backtest walk-forward OOS.

Invariante di import: arena importa da backtest_suite, MAI il contrario.
Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §3.
"""
from backtest_suite.arena.types import (
    Attempt, CandidateMetrics, Weights, ArenaConfig,
    LeaderboardRow, Leaderboard,
)

__all__ = [
    "Attempt", "CandidateMetrics", "Weights", "ArenaConfig",
    "LeaderboardRow", "Leaderboard",
]

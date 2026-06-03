# backtest_suite/arena/__init__.py
"""arena — 10 archetipi che competono su backtest walk-forward OOS.

Invariante di import: arena importa da backtest_suite, MAI il contrario.
Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §3.
"""
from backtest_suite.arena.types import (
    Attempt, CandidateMetrics, Weights, ArenaConfig,
    LeaderboardRow, Leaderboard,
)
from backtest_suite.arena.fitness import (
    evaluate, composite_scores, validation_verdict, robust_zscore,
)
from backtest_suite.arena.proposer import Proposer, RandomGAProposer
from backtest_suite.arena.agent import Agent
from backtest_suite.arena.tournament import run_tournament
from backtest_suite.arena.store import ArenaStore

__all__ = [
    "Attempt", "CandidateMetrics", "Weights", "ArenaConfig",
    "LeaderboardRow", "Leaderboard",
    "evaluate", "composite_scores", "validation_verdict", "robust_zscore",
    "Proposer", "RandomGAProposer", "Agent", "run_tournament", "ArenaStore",
]

# tests/suite/test_arena_tournament.py
"""Test end-to-end della mini-arena (solo GA, candele sintetiche)."""
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import WalkForwardConfig
from backtest_suite.arena.types import ArenaConfig, Weights, Leaderboard
from backtest_suite.arena.tournament import run_tournament


def _sine_candles(n=400):
    out = []
    for i in range(n):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        out.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    return out


def _cfg(generations=2):
    return ArenaConfig(
        archetype_ids=("ema_cross", "rsi_mr"),
        proposer_ids=("ga",),
        n_generations=generations,
        seed=123,
        wf=WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                             min_trades_oos=1, max_drawdown_per_window=1.0),
        execution=ExecutionConfig(),
        weights=Weights(),
    )


def test_tournament_returns_one_row_per_agent():
    lb = run_tournament(_cfg(), _sine_candles())
    assert isinstance(lb, Leaderboard)
    agent_ids = {r.agent_id for r in lb.rows}
    assert agent_ids == {"ema_cross/ga", "rsi_mr/ga"}


def test_tournament_rows_sorted_by_score_desc():
    lb = run_tournament(_cfg(), _sine_candles())
    scores = [r.composite_score for r in lb.rows]
    assert scores == sorted(scores, reverse=True)


def test_tournament_verdicts_are_valid_labels():
    lb = run_tournament(_cfg(), _sine_candles())
    for r in lb.rows:
        assert r.verdict in {"robust", "weak", "likely_overfit"}


def test_tournament_is_deterministic():
    lb1 = run_tournament(_cfg(), _sine_candles())
    lb2 = run_tournament(_cfg(), _sine_candles())
    best1 = {r.agent_id: r.candidate.individual for r in lb1.rows}
    best2 = {r.agent_id: r.candidate.individual for r in lb2.rows}
    assert best1 == best2

# tests/suite/test_arena_store.py
"""Test di persistenza dell'arena su SQLite temporaneo."""
import json

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import IndividualConfig, WalkForwardConfig
from backtest_suite.arena.store import ArenaStore
from backtest_suite.arena.types import (
    ArenaConfig, Weights, CandidateMetrics, LeaderboardRow,
)


def _cfg():
    return ArenaConfig(
        archetype_ids=("ema_cross",), proposer_ids=("ga",),
        n_generations=1, seed=1,
        wf=WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                             min_trades_oos=1, max_drawdown_per_window=1.0),
        execution=ExecutionConfig(), weights=Weights(),
    )


def _row(agent_id, score, gen=1):
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5.0},
        risk_params={"stop_loss_pct": 0.05},
    )
    cm = CandidateMetrics(
        individual=ind, fitness=score, quality=0.4, consistency=-0.1,
        drawdown=0.1, n_trades=12, failed=False,
    )
    return LeaderboardRow(
        agent_id=agent_id, archetype_id="ema_cross", proposer_id="ga",
        generation=gen, composite_score=score, verdict="robust", candidate=cm,
    )


def test_create_run_returns_incrementing_id(tmp_path):
    store = ArenaStore(tmp_path / "catalog.db", tmp_path / "runs")
    r1 = store.create_run(_cfg())
    r2 = store.create_run(_cfg())
    assert isinstance(r1, int) and r2 == r1 + 1


def test_insert_and_query_top_individuals(tmp_path):
    store = ArenaStore(tmp_path / "catalog.db", tmp_path / "runs")
    run_id = store.create_run(_cfg())
    store.insert_generation(run_id, 1, [
        _row("ema_cross/ga", 0.20),
        _row("rsi_mr/ga", 0.90),
    ])
    top = store.top_individuals(run_id, k=1)
    assert len(top) == 1
    assert top[0]["agent_id"] == "rsi_mr/ga"
    assert top[0]["composite_score"] == 0.90
    assert top[0]["verdict"] == "robust"
    genome = json.loads(top[0]["params_json"])
    assert genome["risk_params"] == {"stop_loss_pct": 0.05}


def test_finish_run_sets_status_and_best(tmp_path):
    store = ArenaStore(tmp_path / "catalog.db", tmp_path / "runs")
    run_id = store.create_run(_cfg())
    store.finish_run(run_id, [_row("rsi_mr/ga", 0.90), _row("ema_cross/ga", 0.20)])
    run = store.get_run(run_id)
    assert run["status"] == "finished"
    assert run["best_score"] == 0.90
    assert run["finished_at"] is not None

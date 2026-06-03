# tests/suite/test_arena_e2e_persisted.py
"""E2E: torneo che persiste su ArenaStore."""
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import WalkForwardConfig
from backtest_suite.arena import run_tournament, ArenaStore
from backtest_suite.arena.types import ArenaConfig, Weights


def _sine_candles(n=400):
    out = []
    for i in range(n):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        out.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    return out


def test_tournament_persists_run_and_individuals(tmp_path):
    store = ArenaStore(tmp_path / "catalog.db", tmp_path / "runs")
    cfg = ArenaConfig(
        archetype_ids=("ema_cross", "rsi_mr"), proposer_ids=("ga",),
        n_generations=2, seed=99,
        wf=WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                             min_trades_oos=1, max_drawdown_per_window=1.0),
        execution=ExecutionConfig(), weights=Weights(),
    )
    lb = run_tournament(cfg, _sine_candles(), store=store)

    assert lb.run_id is not None
    run = store.get_run(lb.run_id)
    assert run["status"] == "finished"
    # 2 agenti × 2 generazioni = 4 righe individuo
    top = store.top_individuals(lb.run_id, k=99)
    assert len(top) == 4
    assert run["best_score"] == max(r["composite_score"] for r in top)

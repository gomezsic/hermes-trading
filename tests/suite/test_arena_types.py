# tests/suite/test_arena_types.py
"""Test dei tipi base dell'arena."""
from backtest_suite.optimizer.types import IndividualConfig
from backtest_suite.arena.types import Attempt, CandidateMetrics, Weights


def _ind():
    return IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5.0},
        risk_params={"stop_loss_pct": 0.05},
    )


def test_attempt_holds_individual_and_fitness():
    a = Attempt(individual=_ind(), fitness=0.5, rationale="ga")
    assert a.individual.strategy_id == "ema_cross"
    assert a.fitness == 0.5
    assert a.rationale == "ga"


def test_weights_default_is_unit():
    w = Weights()
    assert (w.quality, w.consistency, w.drawdown) == (1.0, 1.0, 1.0)


def test_candidate_metrics_exposes_strategy_id_and_failed():
    cm = CandidateMetrics(
        individual=_ind(), fitness=float("-inf"), quality=0.0,
        consistency=0.0, drawdown=0.0, n_trades=0,
        failed=True, failure_reason="min_trades_oos",
    )
    assert cm.strategy_id == "ema_cross"
    assert cm.failed is True
    assert cm.failure_reason == "min_trades_oos"


def test_arena_config_constructs_with_defaults():
    from backtest_suite.engine.types import ExecutionConfig
    from backtest_suite.optimizer.types import WalkForwardConfig
    from backtest_suite.arena.types import ArenaConfig
    cfg = ArenaConfig(
        archetype_ids=("ema_cross",), proposer_ids=("ga",),
        n_generations=2, seed=1,
        wf=WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                             min_trades_oos=1, max_drawdown_per_window=1.0),
        execution=ExecutionConfig(),
    )
    assert cfg.symbol == "BTCUSDT"
    assert cfg.weights.quality == 1.0


def test_leaderboard_rows_are_mutable_and_default_empty():
    from backtest_suite.arena.types import Leaderboard, LeaderboardRow, CandidateMetrics
    from backtest_suite.optimizer.types import IndividualConfig
    lb = Leaderboard()
    assert lb.rows == [] and lb.run_id is None
    cm = CandidateMetrics(
        individual=IndividualConfig(strategy_id="ema_cross", strategy_params={}, risk_params={}),
        fitness=0.1, quality=0.1, consistency=0.0, drawdown=0.1, n_trades=1, failed=False,
    )
    lb.rows.append(LeaderboardRow(agent_id="ema_cross/ga", archetype_id="ema_cross",
                                  proposer_id="ga", generation=1, composite_score=0.1,
                                  verdict="robust", candidate=cm))
    assert len(lb.rows) == 1

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

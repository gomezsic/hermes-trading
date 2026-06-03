# tests/suite/test_arena_fitness_pure.py
"""Test delle funzioni pure di scoring dell'arena (no backtest)."""
import math

from backtest_suite.optimizer.types import IndividualConfig
from backtest_suite.arena.types import CandidateMetrics, Weights
from backtest_suite.arena.fitness import (
    robust_zscore, composite_scores, validation_verdict,
)


def _cm(quality, consistency, drawdown, *, failed=False, fitness=0.0):
    ind = IndividualConfig(strategy_id="x", strategy_params={}, risk_params={})
    return CandidateMetrics(
        individual=ind, fitness=fitness,
        quality=quality, consistency=consistency, drawdown=drawdown,
        n_trades=10, failed=failed,
    )


def test_robust_zscore_centers_on_median():
    z = robust_zscore([1.0, 2.0, 3.0])
    assert z[1] == 0.0          # la mediana ha z = 0
    assert z[0] < 0 < z[2]      # ordinati come i valori


def test_robust_zscore_constant_input_is_all_zero():
    assert robust_zscore([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_robust_zscore_ignores_non_finite_for_scale():
    z = robust_zscore([1.0, 2.0, 3.0, float("-inf")])
    assert z[3] == 0.0          # i non-finiti mappano a 0
    assert math.isfinite(z[0])


def test_composite_rewards_quality_penalizes_drawdown():
    cms = [
        _cm(quality=0.8, consistency=0.0, drawdown=0.10),   # buona
        _cm(quality=0.1, consistency=0.0, drawdown=0.40),   # scarsa
    ]
    scores = composite_scores(cms, Weights())
    assert scores[0] > scores[1]


def test_composite_failed_is_minus_inf():
    cms = [_cm(0.5, 0.0, 0.1), _cm(0.0, 0.0, 0.0, failed=True)]
    scores = composite_scores(cms, Weights())
    assert scores[1] == float("-inf")


def test_verdict_likely_overfit_when_quality_non_positive():
    assert validation_verdict(_cm(quality=0.0, consistency=0.0, drawdown=0.1)) == "likely_overfit"
    assert validation_verdict(_cm(0.5, 0.0, 0.1, failed=True)) == "likely_overfit"


def test_verdict_robust_when_high_quality_low_dispersion():
    assert validation_verdict(_cm(quality=0.6, consistency=-0.1, drawdown=0.1)) == "robust"


def test_verdict_weak_when_high_dispersion():
    assert validation_verdict(_cm(quality=0.3, consistency=-0.5, drawdown=0.1)) == "weak"

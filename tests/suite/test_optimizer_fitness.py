"""Test dei tipi dell'optimizer."""
from backtest_suite.optimizer.types import (
    IndividualConfig,
    WalkForwardConfig,
    FitnessResult,
    GAConfig,
    GridConfig,
    GenerationEvent,
    EvolutionResult,
    Scored,
)


def test_individual_config_holds_strategy_and_params():
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 10, "ema_slow": 30, "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    assert ind.strategy_id == "ema_cross"


def test_walk_forward_config_required_fields():
    wf = WalkForwardConfig(
        is_months=6, oos_months=2, step_months=2,
        min_trades_oos=20, max_drawdown_per_window=0.30,
        variance_lambda=0.5,
    )
    assert wf.variance_lambda == 0.5


def test_fitness_result_supports_failed():
    fr = FitnessResult(
        fitness=float("-inf"),
        per_window_scores=[],
        mean_score=0.0,
        stdev_score=0.0,
        max_drawdown_observed=0.0,
        n_trades_total=0,
        failed=True,
        failure_reason="min_trades_oos",
    )
    assert fr.failed is True

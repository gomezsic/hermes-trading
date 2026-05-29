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


from hermes_trading._engine_core import RiskConfig
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import (
    generate_walk_forward_windows,
    score_individual,
    _build_risk_config,
)
from backtest_suite.optimizer.types import IndividualConfig, WalkForwardConfig


def test_generate_windows_basic():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(365)]
    wf = WalkForwardConfig(is_months=6, oos_months=2, step_months=2,
                           min_trades_oos=20, max_drawdown_per_window=0.3)
    windows = generate_walk_forward_windows(candles, wf)
    assert len(windows) >= 1
    for is_w, oos_w in windows:
        assert len(is_w) == 6 * 30   # is_months * 30
        assert len(oos_w) == 2 * 30


def test_build_risk_config_from_dict():
    rc = _build_risk_config({
        "stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
        "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
        "trailing_stop_tight_pct": 0.025,
    })
    assert isinstance(rc, RiskConfig)
    assert rc.stop_loss_pct == 0.05


def test_score_individual_returns_fitness_result():
    import math
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})

    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=1, max_drawdown_per_window=1.0)
    res = score_individual(ind, candles, wf, ExecutionConfig())
    assert isinstance(res.fitness, float)
    # Almeno una finestra deve essere valutata
    assert len(res.per_window_scores) >= 1


def test_score_individual_fails_filter_when_dd_too_high():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(200)]
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=100, max_drawdown_per_window=0.01)
    res = score_individual(ind, candles, wf, ExecutionConfig())
    # Niente trade su flat → min_trades_oos non raggiunto
    assert res.failed

"""Test grid search."""
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.grid import grid_search, _generate_combos
from backtest_suite.optimizer.types import GridConfig, WalkForwardConfig


def test_generate_combos_uses_strategy_grid_when_provided():
    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={"stop_loss_pct": [0.03, 0.05]},
        strategy_params_grid={
            "ema_cross": {
                "ema_fast":   [5, 10],
                "ema_slow":   [20, 30],
                "vwap_window": [100],
                "vwap_filter": [0],
                "direction":   [2],
            }
        },
        max_combos=100,
    )
    combos = list(_generate_combos(cfg))
    assert len(combos) >= 2 * 2 * 2
    for ind in combos:
        assert ind.strategy_id == "ema_cross"


def test_generate_combos_caps_at_max_combos():
    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={"stop_loss_pct": [0.03, 0.05]},
        strategy_params_grid={
            "ema_cross": {
                "ema_fast":  [5, 10, 15, 20, 25, 30],
                "ema_slow":  [20, 30, 40, 50, 60, 70, 80, 90, 100],
                "vwap_window": [50, 100, 200, 300],
                "vwap_filter": [0, 1],
                "direction":   [0, 1, 2],
            }
        },
        max_combos=10,
    )
    try:
        list(_generate_combos(cfg))
    except ValueError as e:
        assert "max_combos" in str(e)
        return
    raise AssertionError("ValueError atteso")


def test_grid_search_runs_and_returns_best():
    candles = []
    for i in range(300):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1.0, "l": p - 1.0,
                        "c": p, "v": 100.0})

    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={
            "stop_loss_pct":           [0.05],
            "partial_exit_pct":        [0.10],
            "trailing_activate_pct":   [0.06],
            "trailing_stop_pct":       [0.04],
            "trailing_stop_tight_pct": [0.025],
        },
        strategy_params_grid={
            "ema_cross": {
                "ema_fast": [5, 10], "ema_slow": [20, 30],
                "vwap_window": [100], "vwap_filter": [0], "direction": [2],
            }
        },
        max_combos=50,
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=1, max_drawdown_per_window=1.0)
    progress = []
    result = grid_search(cfg, candles, wf, ExecutionConfig(),
                         stop_flag=lambda: False,
                         progress_callback=progress.append, n_workers=1)
    assert len(result.all_scored) == 4
    assert result.best_fitness is not None

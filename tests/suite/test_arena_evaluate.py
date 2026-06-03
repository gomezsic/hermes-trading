# tests/suite/test_arena_evaluate.py
"""Test di evaluate(): wrapping di score_individual su candele reali."""
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import IndividualConfig, WalkForwardConfig
from backtest_suite.arena.fitness import evaluate
from backtest_suite.arena.types import CandidateMetrics


def _sine_candles(n=400):
    out = []
    for i in range(n):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        out.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    return out


def _wf():
    return WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                             min_trades_oos=1, max_drawdown_per_window=1.0)


def _ind(strategy_params):
    return IndividualConfig(
        strategy_id="ema_cross",
        strategy_params=strategy_params,
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )


def test_evaluate_returns_candidate_metrics():
    candles = _sine_candles()
    ind = _ind({"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                "vwap_filter": 0, "direction": 2})
    cm = evaluate(ind, candles, _wf(), ExecutionConfig())
    assert isinstance(cm, CandidateMetrics)
    assert cm.strategy_id == "ema_cross"
    assert cm.individual is ind
    assert isinstance(cm.fitness, float)
    assert cm.consistency <= 0.0


def test_evaluate_marks_failed_on_flat_market():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(200)]
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=100, max_drawdown_per_window=0.01)
    ind = _ind({"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                "vwap_filter": 0, "direction": 2})
    cm = evaluate(ind, candles, wf, ExecutionConfig())
    assert cm.failed is True
    assert cm.fitness == float("-inf")

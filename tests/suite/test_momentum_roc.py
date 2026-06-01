"""Test MomentumRocStrategy."""
from backtest_suite.strategies.momentum_roc import MomentumRocStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"roc_period": 10, "threshold_pct": 5.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_roc_period():
    s = MomentumRocStrategy(_params(roc_period=14))
    assert s.warmup_bars() == 14


def test_no_signal_before_warmup():
    s = MomentumRocStrategy(_params(roc_period=10))
    candles = _candles([100.0] * 20)
    assert s.on_bar(5, candles).side is None


def test_long_when_roc_above_threshold():
    values = [100.0] * 11 + [120.0]   # +20% rispetto a 10 barre prima
    candles = _candles(values)
    s = MomentumRocStrategy(_params(roc_period=10, threshold_pct=5.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_when_roc_below_negative_threshold():
    values = [100.0] * 11 + [80.0]    # −20%
    candles = _candles(values)
    s = MomentumRocStrategy(_params(roc_period=10, threshold_pct=5.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"

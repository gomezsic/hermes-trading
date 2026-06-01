"""Test KeltnerBreakoutStrategy."""
from backtest_suite.strategies.keltner_breakout import KeltnerBreakoutStrategy


def _candles(rows):
    # rows = (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"ema_period": 10, "atr_period": 5, "multiplier": 1.5, "direction": 2}
    base.update(kw)
    return base


def test_warmup_is_max_of_periods():
    s = KeltnerBreakoutStrategy(_params(ema_period=20, atr_period=14))
    assert s.warmup_bars() == 20


def test_no_signal_before_warmup():
    s = KeltnerBreakoutStrategy(_params())
    candles = _candles([(101.0, 99.0, 100.0)] * 30)
    assert s.on_bar(3, candles).side is None


def test_long_on_break_above_upper():
    rows = [(101.0, 99.0, 100.0)] * 25 + [(140.0, 130.0, 138.0)]
    candles = _candles(rows)
    s = KeltnerBreakoutStrategy(_params(ema_period=10, atr_period=5, multiplier=1.5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_on_break_below_lower():
    rows = [(101.0, 99.0, 100.0)] * 25 + [(70.0, 60.0, 62.0)]
    candles = _candles(rows)
    s = KeltnerBreakoutStrategy(_params(ema_period=10, atr_period=5, multiplier=1.5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"

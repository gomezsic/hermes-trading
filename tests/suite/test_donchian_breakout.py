"""Test DonchianBreakoutStrategy."""
from backtest_suite.strategies.donchian_breakout import DonchianBreakoutStrategy


def _candles(rows):
    # rows = list di (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"channel_period": 5, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_channel_period():
    s = DonchianBreakoutStrategy(_params(channel_period=20))
    assert s.warmup_bars() == 20


def test_no_signal_before_warmup():
    s = DonchianBreakoutStrategy(_params(channel_period=5))
    candles = _candles([(101.0, 99.0, 100.0)] * 20)
    assert s.on_bar(3, candles).side is None


def test_long_on_breakout_up():
    rows = [(101.0, 99.0, 100.0)] * 10 + [(120.0, 110.0, 119.0)]
    candles = _candles(rows)
    s = DonchianBreakoutStrategy(_params(channel_period=5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_on_breakout_down():
    rows = [(101.0, 99.0, 100.0)] * 10 + [(90.0, 80.0, 81.0)]
    candles = _candles(rows)
    s = DonchianBreakoutStrategy(_params(channel_period=5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"

"""Test SupertrendStrategy."""
from backtest_suite.strategies.supertrend import SupertrendStrategy


def _candles(rows):
    # rows = (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"atr_period": 5, "multiplier": 2.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_positive():
    s = SupertrendStrategy(_params(atr_period=10))
    assert s.warmup_bars() == 11


def test_no_signal_before_warmup():
    s = SupertrendStrategy(_params(atr_period=5))
    candles = _candles([(101.0, 99.0, 100.0)] * 30)
    assert s.on_bar(2, candles).side is None


def test_long_appears_when_trend_flips_up():
    # discesa stabile poi forte salita: il flip a rialzo deve emettere un long
    down = [(100.0 - i + 1, 100.0 - i - 1, 100.0 - i) for i in range(20)]
    up   = [(85.0 + i * 3 + 1, 85.0 + i * 3 - 1, 85.0 + i * 3) for i in range(20)]
    candles = _candles(down + up)
    s = SupertrendStrategy(_params(atr_period=5, multiplier=2.0, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long

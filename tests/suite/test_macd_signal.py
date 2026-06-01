"""Test MacdSignalStrategy."""
from backtest_suite.strategies.macd_signal import MacdSignalStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"ema_fast": 12, "ema_slow": 26, "signal_period": 9, "direction": 2}
    base.update(kw)
    return base


def test_warmup_covers_slow_plus_signal():
    s = MacdSignalStrategy(_params(ema_slow=26, signal_period=9))
    assert s.warmup_bars() == 26 + 9


def test_no_signal_before_warmup():
    s = MacdSignalStrategy(_params())
    candles = _candles([100.0] * 10)
    assert s.on_bar(5, candles).side is None


def test_long_appears_on_uptrend_reversal():
    # discesa poi salita decisa: la MACD incrocia sopra la signal almeno una volta
    values = [100.0 - i for i in range(30)] + [70.0 + i * 2 for i in range(30)]
    candles = _candles(values)
    s = MacdSignalStrategy(_params(ema_fast=5, ema_slow=13, signal_period=5, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long

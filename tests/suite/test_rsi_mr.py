"""Test RsiMeanReversionStrategy."""
from backtest_suite.strategies.rsi_mr import RsiMeanReversionStrategy


def _candles(values: list[float]) -> list[dict]:
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def test_rsi_mr_warmup_equals_period_plus_one():
    s = RsiMeanReversionStrategy({"rsi_period": 14, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})
    assert s.warmup_bars() == 15


def test_rsi_mr_no_signal_before_warmup():
    s = RsiMeanReversionStrategy({"rsi_period": 14, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})
    candles = _candles([100.0] * 50)
    sig = s.on_bar(5, candles)
    assert sig.side is None


def test_rsi_mr_long_when_oversold():
    values = [100.0] * 5 + [100.0 - i * 1.0 for i in range(1, 30)] + [70.0] * 5
    candles = _candles(values)
    s = RsiMeanReversionStrategy({"rsi_period": 7, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})

    seen_long = False
    for i in range(s.warmup_bars(), len(candles)):
        if s.on_bar(i, candles).side == "long":
            seen_long = True
            break
    assert seen_long

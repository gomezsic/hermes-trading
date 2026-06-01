"""Test PriceVsMaCrossStrategy."""
from backtest_suite.strategies.price_vs_ma_cross import PriceVsMaCrossStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"ma_period": 5, "ma_type": 0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_ma_period():
    s = PriceVsMaCrossStrategy(_params(ma_period=10))
    assert s.warmup_bars() == 10


def test_no_signal_before_warmup():
    s = PriceVsMaCrossStrategy(_params(ma_period=5))
    candles = _candles([100.0] * 20)
    assert s.on_bar(2, candles).side is None


def test_long_on_cross_up():
    # prezzo piatto sotto, poi sale sopra la MA -> golden cross prezzo/MA
    values = [100.0] * 10 + [101.0, 103.0, 106.0, 110.0]
    candles = _candles(values)
    s = PriceVsMaCrossStrategy(_params(ma_period=5, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long


def test_direction_long_only_blocks_short():
    values = [110.0, 108.0, 105.0, 101.0] + [100.0] * 6 + [90.0, 80.0]
    candles = _candles(values)
    s = PriceVsMaCrossStrategy(_params(ma_period=5, direction=0))
    sides = {s.on_bar(i, candles).side for i in range(s.warmup_bars(), len(candles))}
    assert "short" not in sides

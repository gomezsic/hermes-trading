"""Test VwapReversionStrategy."""
from backtest_suite.strategies.vwap_reversion import VwapReversionStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"vwap_window": 20, "threshold_pct": 2.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_window():
    s = VwapReversionStrategy(_params(vwap_window=50))
    assert s.warmup_bars() == 50


def test_no_signal_before_warmup():
    s = VwapReversionStrategy(_params(vwap_window=20))
    candles = _candles([100.0] * 30)
    assert s.on_bar(5, candles).side is None


def test_long_when_price_far_below_vwap():
    values = [100.0] * 20 + [90.0]   # close ben sotto il VWAP ~100
    candles = _candles(values)
    s = VwapReversionStrategy(_params(vwap_window=20, threshold_pct=2.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_when_price_far_above_vwap():
    values = [100.0] * 20 + [110.0]
    candles = _candles(values)
    s = VwapReversionStrategy(_params(vwap_window=20, threshold_pct=2.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"

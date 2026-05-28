"""Test EmaCrossStrategy — replica la logica esistente di backtester.py."""
from backtest_suite.strategies.base import Signal
from backtest_suite.strategies.ema_cross import EmaCrossStrategy


def _candles_with_golden_cross() -> list[dict]:
    candles = []
    price = 100.0
    for i in range(30):
        price -= 0.2
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    for i in range(30, 80):
        price += 0.5
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    return candles


def test_ema_cross_warmup_equals_ema_slow():
    s = EmaCrossStrategy({"ema_fast": 20, "ema_slow": 50,
                          "vwap_filter": 0, "direction": 2})
    assert s.warmup_bars() == 50


def test_ema_cross_emits_long_after_golden_cross():
    candles = _candles_with_golden_cross()
    s = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 15,
                          "vwap_filter": 0, "direction": 2})

    seen_long = False
    for i in range(s.warmup_bars(), len(candles)):
        sig = s.on_bar(i, candles)
        if sig.side == "long":
            seen_long = True
            break
    assert seen_long


def test_ema_cross_direction_long_only_filters_short():
    candles = _candles_with_golden_cross()[::-1]
    s = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 15,
                          "vwap_filter": 0, "direction": 0})

    for i in range(s.warmup_bars(), len(candles)):
        sig = s.on_bar(i, candles)
        assert sig.side != "short"

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


def _candles_with_death_cross() -> list[dict]:
    """Inverted series: starts rising then falls — produces a death cross."""
    candles = []
    price = 100.0
    for i in range(30):
        price += 0.5
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    for i in range(30, 80):
        price -= 0.2
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    return candles


def test_cache_reuse_across_different_candle_lists():
    """
    Regression for the id()-based cache bug: reusing ONE strategy instance across
    TWO different candle lists must recompute indicators for the second list.

    If the old id()-based check were used and the second list happened to land at
    the same address as the (now-GC'd) first list, the strategy would silently
    serve stale caches — leading to wrong signals. This test verifies that the
    identity-based cache correctly detects the list change.
    """
    s = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 15,
                          "vwap_filter": 0, "direction": 2})

    # First candle list: golden-cross pattern → should emit at least one "long"
    candles_a = _candles_with_golden_cross()
    signals_a: list[str | None] = []
    for i in range(s.warmup_bars(), len(candles_a)):
        sig = s.on_bar(i, candles_a)
        signals_a.append(sig.side)

    # Second candle list (different object): death-cross pattern → should emit "short"
    candles_b = _candles_with_death_cross()
    signals_b: list[str | None] = []
    for i in range(s.warmup_bars(), len(candles_b)):
        sig = s.on_bar(i, candles_b)
        signals_b.append(sig.side)

    # The golden-cross list should have produced at least one "long"
    assert "long" in signals_a, "expected a long signal on golden-cross series"
    # The death-cross list should have produced at least one "short"
    assert "short" in signals_b, "expected a short signal on death-cross series"
    # Crucially, signals differ — proves caches were rebuilt for the second list,
    # not re-used from the first (which would have yielded the same signals)
    assert signals_a != signals_b, "signals must differ across two distinct candle lists"

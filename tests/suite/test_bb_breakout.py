"""Test BollingerBreakoutStrategy."""
from backtest_suite.strategies.bb_breakout import BollingerBreakoutStrategy


def test_bb_breakout_warmup_equals_period():
    s = BollingerBreakoutStrategy({"bb_period": 20, "bb_std": 2.0,
                                   "confirmation_bars": 1})
    assert s.warmup_bars() == 20


def test_bb_breakout_long_on_upper_band_break():
    base = [100.0] * 25 + [115.0, 117.0, 118.0]
    candles = [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
               for i, v in enumerate(base)]
    s = BollingerBreakoutStrategy({"bb_period": 20, "bb_std": 2.0,
                                   "confirmation_bars": 1})
    seen_long = False
    for i in range(s.warmup_bars(), len(candles)):
        if s.on_bar(i, candles).side == "long":
            seen_long = True
            break
    assert seen_long


def test_bb_breakout_no_signal_inside_bands():
    base = [100.0 + (i % 3 - 1) * 0.1 for i in range(30)]
    candles = [{"t": i, "o": v, "h": v + 0.05, "l": v - 0.05, "c": v, "v": 100.0}
               for i, v in enumerate(base)]
    s = BollingerBreakoutStrategy({"bb_period": 20, "bb_std": 2.0,
                                   "confirmation_bars": 1})
    sides = {s.on_bar(i, candles).side for i in range(s.warmup_bars(), len(candles))}
    assert sides == {None}

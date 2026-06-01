"""Test helper indicatori condivisi."""
from backtest_suite.strategies._indicators import (
    compute_sma, compute_ema, compute_atr,
)


def _candles(values):
    return [{"t": i, "o": v, "h": v + 1.0, "l": v - 1.0, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def test_sma_simple():
    out = compute_sma([1.0, 2.0, 3.0, 4.0], 2)
    assert out[0] is None
    assert out[1] == 1.5
    assert out[2] == 2.5
    assert out[3] == 3.5


def test_ema_first_value_is_sma_seed():
    out = compute_ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    # primi (period-1) None, seed = SMA dei primi 3 = 2.0
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0
    assert out[3] is not None and out[3] > 2.0


def test_atr_length_and_positive():
    candles = _candles([10.0, 11.0, 12.0, 11.0, 13.0, 12.0, 14.0])
    out = compute_atr(candles, 3)
    assert len(out) == len(candles)
    assert out[2] is None          # non ancora pronto
    assert out[3] is not None and out[3] > 0

"""Test kraken_source con ccxt mockato (no chiamate di rete)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backtest_suite.data_lake.kraken_source import (
    fetch_ohlcv_range,
    _normalize_symbol,
)


def test_normalize_symbol_btcusdt_to_kraken_pair():
    assert _normalize_symbol("BTCUSDT") == "BTC/USDT"
    assert _normalize_symbol("ETHUSDT") == "ETH/USDT"


def test_normalize_symbol_already_canonical():
    assert _normalize_symbol("BTC/USDT") == "BTC/USDT"


@patch("backtest_suite.data_lake.kraken_source._build_exchange")
def test_fetch_ohlcv_range_paginates_until_target(mock_build):
    mock_ex = MagicMock()
    t0_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    step_ms = 3600 * 1000

    calls = [
        [[t0_ms,        100.0, 101.0, 99.0, 100.5, 50.0],
         [t0_ms + step_ms, 100.5, 102.0, 100.0, 101.5, 60.0]],
        [[t0_ms + 2 * step_ms, 101.5, 103.0, 101.0, 102.5, 70.0],
         [t0_ms + 3 * step_ms, 102.5, 104.0, 102.0, 103.5, 80.0]],
        [],   # fine
    ]
    mock_ex.fetch_ohlcv.side_effect = calls
    mock_build.return_value = mock_ex

    since = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    until = int(datetime(2024, 1, 1, 4, tzinfo=timezone.utc).timestamp())

    out = fetch_ohlcv_range("BTCUSDT", "1h", since, until,
                            sleep_seconds=0.0)
    assert len(out) == 4
    assert out[0]["t"] == since
    assert out[0]["o"] == 100.0
    assert out[0]["n_trades"] == 0   # ccxt non lo fornisce: default 0


@patch("backtest_suite.data_lake.kraken_source.time.sleep")
@patch("backtest_suite.data_lake.kraken_source._build_exchange")
def test_fetch_ohlcv_range_retries_once_on_exception(mock_build, mock_sleep):
    # La prima fetch solleva, la seconda (retry) consegna i dati, poi fine.
    mock_ex = MagicMock()
    t0_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    step_ms = 3600 * 1000

    mock_ex.fetch_ohlcv.side_effect = [
        ConnectionError("kraken timeout"),                       # 1ª call: errore
        [[t0_ms,           100.0, 101.0, 99.0, 100.5, 50.0],     # retry: dati
         [t0_ms + step_ms, 100.5, 102.0, 100.0, 101.5, 60.0]],
        [],                                                       # fine
    ]
    mock_build.return_value = mock_ex

    since = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    until = int(datetime(2024, 1, 1, 4, tzinfo=timezone.utc).timestamp())

    out = fetch_ohlcv_range("BTCUSDT", "1h", since, until, sleep_seconds=0.0)

    # Il retry ha recuperato: i dati sono comunque consegnati.
    assert len(out) == 2
    assert out[0]["t"] == since
    # Il backoff su errore deve aver dormito almeno una volta.
    assert mock_sleep.called

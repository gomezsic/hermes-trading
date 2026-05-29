"""Test del modulo parquet_store: schema, validation, gap detection."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtest_suite.data_lake.parquet_store import (
    OHLCV_SCHEMA,
    align_timestamp,
    write_year_file,
    read_range,
    detect_gaps,
    coverage_report,
)


def _candle(ts: int, c: float = 100.0) -> dict:
    return {"t": ts, "o": c, "h": c + 1.0, "l": c - 1.0, "c": c,
            "v": 100.0, "n_trades": 10}


def test_align_timestamp_1h_round_down():
    ts = int(datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc).timestamp())
    aligned = align_timestamp(ts, "1h")
    assert aligned == int(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp())


def test_align_timestamp_1d_round_down():
    ts = int(datetime(2024, 6, 15, 13, 45, tzinfo=timezone.utc).timestamp())
    aligned = align_timestamp(ts, "1d")
    assert aligned == int(datetime(2024, 6, 15, 0, 0, tzinfo=timezone.utc).timestamp())


def test_write_and_read_roundtrip(tmp_path: Path):
    base_dir = tmp_path / "BTCUSDT" / "1h"
    base_dir.mkdir(parents=True)

    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [_candle(t0 + i * 3600) for i in range(24)]

    write_year_file(base_dir, year=2024, candles=candles)
    out = read_range(base_dir, since=None, until=None)

    assert len(out) == 24
    assert out[0]["t"] == t0
    assert out[-1]["t"] == t0 + 23 * 3600
    assert out[0]["v"] == 100.0


def test_write_dedupes_and_sorts(tmp_path: Path):
    base_dir = tmp_path / "BTCUSDT" / "1h"
    base_dir.mkdir(parents=True)
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [_candle(t0 + 3600), _candle(t0), _candle(t0 + 3600), _candle(t0 + 7200)]
    write_year_file(base_dir, year=2024, candles=candles)
    out = read_range(base_dir, since=None, until=None)
    assert len(out) == 3
    ts_list = [r["t"] for r in out]
    assert ts_list == sorted(ts_list)


def test_detect_gaps_finds_missing_intervals():
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [
        _candle(t0),
        _candle(t0 + 3600),
        _candle(t0 + 4 * 3600),
        _candle(t0 + 5 * 3600),
    ]
    gaps = detect_gaps(candles, timeframe="1h")
    assert len(gaps) == 1
    assert gaps[0] == (t0 + 2 * 3600, t0 + 4 * 3600)


def test_coverage_report_empty_dir(tmp_path: Path):
    rep = coverage_report(tmp_path, timeframe="1h")
    assert rep["n_candles"] == 0
    assert rep["years"] == []


def test_coverage_report_with_data(tmp_path: Path):
    base_dir = tmp_path / "BTCUSDT" / "1h"
    base_dir.mkdir(parents=True)
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [_candle(t0 + i * 3600) for i in range(48)]
    write_year_file(base_dir, year=2024, candles=candles)

    rep = coverage_report(base_dir, timeframe="1h")
    assert rep["n_candles"] == 48
    assert 2024 in rep["years"]
    assert rep["since"] == t0
    assert rep["until"] == t0 + 47 * 3600


from unittest.mock import patch

from backtest_suite.data_lake import fetch, load, coverage


@patch("backtest_suite.data_lake.kraken_source.fetch_ohlcv_range")
def test_fetch_writes_parquet_and_load_reads_back(mock_fetch, tmp_path):
    from datetime import datetime, timezone
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [
        {"t": t0 + i * 3600, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0,
         "v": 100.0, "n_trades": 5}
        for i in range(10)
    ]
    mock_fetch.return_value = candles

    n = fetch("BTCUSDT", "1h",
              since=datetime(2024, 1, 1, tzinfo=timezone.utc),
              until=datetime(2024, 1, 1, 9, tzinfo=timezone.utc),
              root=tmp_path)
    assert n == 10

    out = load("BTCUSDT", "1h", root=tmp_path)
    assert len(out) == 10
    assert out[0]["t"] == t0


@patch("backtest_suite.data_lake.kraken_source.fetch_ohlcv_range")
def test_fetch_idempotent_skips_existing_ranges(mock_fetch, tmp_path):
    from datetime import datetime, timezone
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = [
        {"t": t0 + i * 3600, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0,
         "v": 100.0, "n_trades": 5}
        for i in range(10)
    ]
    mock_fetch.return_value = candles

    fetch("BTCUSDT", "1h",
          since=datetime(2024, 1, 1, tzinfo=timezone.utc),
          until=datetime(2024, 1, 1, 9, tzinfo=timezone.utc),
          root=tmp_path)

    # Seconda chiamata sullo stesso range con force_refresh=False
    mock_fetch.reset_mock()
    fetch("BTCUSDT", "1h",
          since=datetime(2024, 1, 1, tzinfo=timezone.utc),
          until=datetime(2024, 1, 1, 9, tzinfo=timezone.utc),
          root=tmp_path,
          force_refresh=False)
    mock_fetch.assert_not_called()


def test_coverage_returns_dict(tmp_path):
    rep = coverage("BTCUSDT", "1h", root=tmp_path)
    assert "n_candles" in rep
    assert "gaps" in rep


def test_read_range_ignores_non_year_parquet(tmp_path: Path):
    # Un file .parquet con nome non-anno non deve far crashare read_range.
    base_dir = tmp_path / "BTCUSDT" / "1h"
    base_dir.mkdir(parents=True)
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    write_year_file(base_dir, year=2024, candles=[_candle(t0 + i * 3600)
                                                  for i in range(5)])
    # File estraneo con stem non intero
    (base_dir / "scratch.parquet").write_bytes(b"")

    out = read_range(base_dir, since=None, until=None)
    assert len(out) == 5   # solo le candele valide del 2024, il file estraneo è ignorato

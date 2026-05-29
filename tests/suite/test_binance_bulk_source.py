"""Test binance_bulk_source con download mockato (no rete)."""
import io
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock

from backtest_suite.data_lake.binance_bulk_source import (
    _normalize_symbol,
    _month_zip_url,
    _parse_ts,
    _parse_csv_bytes,
    fetch_ohlcv_bulk,
)


def test_normalize_symbol():
    assert _normalize_symbol("BTC/USDT") == "BTCUSDT"
    assert _normalize_symbol("btcusdt") == "BTCUSDT"
    assert _normalize_symbol("BTCUSDT") == "BTCUSDT"


def test_month_zip_url():
    url = _month_zip_url("BTCUSDT", "1h", 2024, 3)
    assert url.endswith("/BTCUSDT/1h/BTCUSDT-1h-2024-03.zip")
    assert url.startswith("https://data.binance.vision/data/spot/monthly/klines/")


def test_parse_ts_detects_unit():
    assert _parse_ts(1_700_000_000) == 1_700_000_000              # secondi
    assert _parse_ts(1_700_000_000_000) == 1_700_000_000          # millisecondi
    assert _parse_ts(1_700_000_000_000_000) == 1_700_000_000      # microsecondi


def test_parse_csv_skips_header_and_reads_n_trades():
    csv_with_header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,a,b,c\n"
        "1704067200000,100.0,101.0,99.0,100.5,12.0,1704070799999,1200.0,42,0,0,0\n"
        "1704070800000,100.5,102.0,100.0,101.5,8.0,1704074399999,800.0,30,0,0,0\n"
    ).encode("utf-8")
    rows = _parse_csv_bytes(csv_with_header)
    assert len(rows) == 2                                # header saltato
    assert rows[0]["t"] == 1704067200                    # ms → s
    assert rows[0]["o"] == 100.0
    assert rows[0]["n_trades"] == 42                     # colonna count
    assert rows[1]["c"] == 101.5


def test_parse_csv_without_header():
    csv_no_header = (
        "1704067200000,100.0,101.0,99.0,100.5,12.0,1704070799999,1200.0,7,0,0,0\n"
    ).encode("utf-8")
    rows = _parse_csv_bytes(csv_no_header)
    assert len(rows) == 1
    assert rows[0]["n_trades"] == 7


def _make_zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-1h-2024-01.csv", csv_text)
    return buf.getvalue()


def test_fetch_ohlcv_bulk_downloads_parses_and_filters():
    # Gennaio: 3 candele orarie. Mock: 200 per 2024-01, 404 per gli altri mesi.
    t0 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    step = 3600
    csv_text = "".join(
        f"{(t0 + i*step)*1000},{100+i},{101+i},{99+i},{100.5+i},10,0,0,{5+i},0,0,0\n"
        for i in range(3)
    )
    zip_bytes = _make_zip(csv_text)

    def _resp(status, content=b""):
        m = MagicMock()
        m.status_code = status
        m.content = content
        m.raise_for_status = MagicMock()
        return m

    client = MagicMock()
    client.get.side_effect = lambda url: (
        _resp(200, zip_bytes) if "2024-01" in url else _resp(404)
    )

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    until = datetime(2024, 2, 28, tzinfo=timezone.utc)
    out = fetch_ohlcv_bulk("BTCUSDT", "1h", since, until, client=client)

    assert len(out) == 3
    assert out[0]["t"] == t0
    assert out[0]["n_trades"] == 5
    assert [r["t"] for r in out] == sorted(r["t"] for r in out)   # ordinato


def test_fetch_ohlcv_bulk_filters_out_of_range():
    # Candela fuori range (prima di since) deve essere scartata.
    t_in = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
    t_out = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    csv_text = (
        f"{t_out*1000},1,1,1,1,1,0,0,1,0,0,0\n"
        f"{t_in*1000},2,2,2,2,2,0,0,2,0,0,0\n"
    )
    zip_bytes = _make_zip(csv_text)

    def _resp(status, content=b""):
        m = MagicMock(); m.status_code = status; m.content = content
        m.raise_for_status = MagicMock(); return m

    client = MagicMock()
    client.get.side_effect = lambda url: _resp(200, zip_bytes) if "2024-01" in url else _resp(404)

    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    until = datetime(2024, 1, 31, tzinfo=timezone.utc)
    out = fetch_ohlcv_bulk("BTCUSDT", "1h", since, until, client=client)
    assert len(out) == 1
    assert out[0]["t"] == t_in

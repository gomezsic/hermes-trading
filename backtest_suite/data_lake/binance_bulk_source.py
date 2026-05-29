"""
binance_bulk_source — storia OHLCV profonda dagli archivi pubblici
data.binance.vision (zip mensili di kline). Niente auth, anni di storia,
con n_trades reale.

Layout archivio:
  https://data.binance.vision/data/spot/monthly/klines/<SYM>/<TF>/<SYM>-<TF>-<YYYY>-<MM>.zip

Ogni zip contiene un CSV (storicamente senza header, dal 2025 con header) con colonne:
  open_time, open, high, low, close, volume, close_time, quote_volume,
  n_trades, taker_buy_base, taker_buy_quote, ignore

open_time è in millisecondi (storico) o microsecondi (dal 2025): rilevato per magnitudine.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

_BASE = "https://data.binance.vision/data/spot/monthly/klines"

# Timeframe supportati dagli archivi Binance (sottoinsieme utile).
_INTERVALS: set[str] = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}


def _normalize_symbol(symbol: str) -> str:
    """Binance usa simboli senza slash, maiuscoli: BTC/USDT → BTCUSDT."""
    return symbol.replace("/", "").upper()


def _month_zip_url(symbol: str, timeframe: str, year: int, month: int) -> str:
    sym = _normalize_symbol(symbol)
    return f"{_BASE}/{sym}/{timeframe}/{sym}-{timeframe}-{year:04d}-{month:02d}.zip"


def _parse_ts(raw: int) -> int:
    """open_time → unix seconds. Rileva l'unità per magnitudine (us|ms|s)."""
    if raw >= 10**14:        # microsecondi (es. 1.7e15)
        return raw // 1_000_000
    if raw >= 10**11:        # millisecondi (es. 1.7e12)
        return raw // 1000
    return raw               # già in secondi


def _parse_csv_bytes(data: bytes) -> list[dict]:
    """Parsa il CSV kline. Salta un'eventuale riga header (primo campo non numerico)."""
    out: list[dict] = []
    reader = csv.reader(io.StringIO(data.decode("utf-8")))
    for row in reader:
        if not row:
            continue
        try:
            ts = int(row[0])
        except ValueError:
            continue          # header o riga non valida
        n_trades = 0
        if len(row) > 8 and row[8] not in ("", "None"):
            try:
                n_trades = int(float(row[8]))
            except ValueError:
                n_trades = 0
        out.append({
            "t":        _parse_ts(ts),
            "o":        float(row[1]),
            "h":        float(row[2]),
            "l":        float(row[3]),
            "c":        float(row[4]),
            "v":        float(row[5]),
            "n_trades": n_trades,
        })
    return out


def _extract_csv_from_zip(zip_bytes: bytes) -> bytes:
    """Estrae l'unico CSV contenuto nello zip."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise ValueError("zip Binance senza CSV")
        return zf.read(names[0])


def _iter_months(since: datetime, until: datetime):
    """Genera (anno, mese) da since a until inclusi."""
    y, m = since.year, since.month
    while (y, m) <= (until.year, until.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _build_client() -> httpx.Client:
    """Factory httpx — separata per facilitare il mocking nei test."""
    return httpx.Client(timeout=60.0, follow_redirects=True)


def _download_month(symbol: str, timeframe: str, year: int, month: int,
                    client: httpx.Client) -> bytes | None:
    """Scarica lo zip mensile. Ritorna None se non esiste (404)."""
    url = _month_zip_url(symbol, timeframe, year, month)
    r = client.get(url)
    if r.status_code == 404:
        log.info("[binance_bulk] mese non disponibile: %s", url)
        return None
    r.raise_for_status()
    return r.content


def fetch_ohlcv_bulk(
    symbol:    str,
    timeframe: str,
    since:     datetime,
    until:     datetime,
    client:    httpx.Client | None = None,
) -> list[dict]:
    """
    Scarica OHLCV profondo dagli archivi mensili Binance per [since, until].

    Itera mese per mese, scarica lo zip, estrae e parsa il CSV, filtra al range,
    deduplica per `t` e ordina. I mesi mancanti (404) vengono saltati.
    Ritorna list[dict] con chiavi {t, o, h, l, c, v, n_trades}.
    """
    if timeframe not in _INTERVALS:
        raise ValueError(f"timeframe non supportato da Binance bulk: {timeframe}")

    since_ts = int(since.timestamp())
    until_ts = int(until.timestamp())

    owns_client = client is None
    client = client or _build_client()
    rows: dict[int, dict] = {}
    try:
        for year, month in _iter_months(since, until):
            zip_bytes = _download_month(symbol, timeframe, year, month, client)
            if zip_bytes is None:
                continue
            csv_bytes = _extract_csv_from_zip(zip_bytes)
            for c in _parse_csv_bytes(csv_bytes):
                if since_ts <= c["t"] <= until_ts:
                    rows[c["t"]] = c
    finally:
        if owns_client:
            client.close()

    return [rows[t] for t in sorted(rows.keys())]

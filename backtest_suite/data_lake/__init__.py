"""
data_lake — API pubblica per cache OHLCV parquet.

Layout disco: <root>/<exchange>/<symbol>/<timeframe>/<YYYY>.parquet

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from backtest_suite.data_lake import kraken_source
from backtest_suite.data_lake.parquet_store import (
    coverage_report,
    detect_gaps,
    read_range,
    write_year_file,
    align_timestamp,
)

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path("data/ohlcv")
EXCHANGE = "kraken"


def _symbol_dir(root: Path, symbol: str, timeframe: str) -> Path:
    # Coercizione a Path: fetch/load/coverage accettano root anche come str.
    return Path(root) / EXCHANGE / symbol / timeframe


def _to_unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch(
    symbol:        str,
    timeframe:     str,
    since:         datetime,
    until:         datetime,
    force_refresh: bool = False,
    root:          Path = DEFAULT_ROOT,
) -> int:
    """
    Scarica e cachea candele OHLCV.

    Idempotente: se force_refresh=False e il range richiesto è già coperto, ritorna 0
    senza chiamare l'exchange. Altrimenti scarica i buchi.

    Returns: numero di candele effettivamente scaricate/scritte.
    """
    base_dir = _symbol_dir(root, symbol, timeframe)
    since_ts = align_timestamp(_to_unix(since), timeframe)
    until_ts = align_timestamp(_to_unix(until), timeframe)

    if not force_refresh:
        existing = read_range(base_dir, since=since_ts, until=until_ts)
        if existing:
            gaps = detect_gaps(existing, timeframe)
            if not gaps and existing[0]["t"] == since_ts and existing[-1]["t"] == until_ts:
                log.info("[data_lake] %s %s [%d-%d] già coperto",
                         symbol, timeframe, since_ts, until_ts)
                return 0

    candles = kraken_source.fetch_ohlcv_range(symbol, timeframe, since_ts, until_ts)
    if not candles:
        return 0

    # Raggruppa per anno e scrivi
    by_year: dict[int, list[dict]] = {}
    for c in candles:
        y = datetime.fromtimestamp(c["t"], tz=timezone.utc).year
        by_year.setdefault(y, []).append(c)

    n_written = 0
    for year, items in sorted(by_year.items()):
        write_year_file(base_dir, year, items)
        n_written += len(items)

    log.info("[data_lake] %s %s scritte %d candele in %s",
             symbol, timeframe, n_written, base_dir)
    return n_written


def load(
    symbol:    str,
    timeframe: str,
    since:     datetime | None = None,
    until:     datetime | None = None,
    root:      Path = DEFAULT_ROOT,
) -> list[dict]:
    """Carica candele dal parquet locale. Errore se la directory non esiste."""
    base_dir = _symbol_dir(root, symbol, timeframe)
    if not base_dir.exists():
        raise FileNotFoundError(
            f"Nessun dato per {symbol} {timeframe}. Esegui: "
            f"hermes-bt fetch {symbol} {timeframe} --since <data>"
        )
    s = _to_unix(since) if since else None
    u = _to_unix(until) if until else None
    return read_range(base_dir, since=s, until=u)


def coverage(symbol: str, timeframe: str, root: Path = DEFAULT_ROOT) -> dict:
    """Riporta la coverage map per la coppia (symbol, timeframe)."""
    base_dir = _symbol_dir(root, symbol, timeframe)
    rep = coverage_report(base_dir, timeframe)
    rep["symbol"]    = symbol
    rep["timeframe"] = timeframe
    rep["exchange"]  = EXCHANGE
    return rep

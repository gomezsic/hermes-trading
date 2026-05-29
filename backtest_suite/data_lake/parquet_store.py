"""
parquet_store — read/write/validate dei file OHLCV in parquet.

Schema: t (int64), o/h/l/c/v (float64), n_trades (int32).
Layout: <base_dir>/<YYYY>.parquet — un file per anno-timeframe.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

OHLCV_SCHEMA = pa.schema([
    pa.field("t",        pa.int64()),
    pa.field("o",        pa.float64()),
    pa.field("h",        pa.float64()),
    pa.field("l",        pa.float64()),
    pa.field("c",        pa.float64()),
    pa.field("v",        pa.float64()),
    pa.field("n_trades", pa.int32()),
])

_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m":   60,
    "5m":   300,
    "15m":  900,
    "1h":   3600,
    "4h":   14400,
    "1d":   86400,
}


def _tf_seconds(timeframe: str) -> int:
    if timeframe not in _TIMEFRAME_SECONDS:
        raise ValueError(f"timeframe non supportato: {timeframe}")
    return _TIMEFRAME_SECONDS[timeframe]


def align_timestamp(ts: int, timeframe: str) -> int:
    """Allinea ts (unix seconds UTC) all'inizio della finestra del timeframe."""
    step = _tf_seconds(timeframe)
    return (ts // step) * step


def _year_of(ts: int) -> int:
    return datetime.fromtimestamp(ts, tz=timezone.utc).year


def _year_path(base_dir: Path, year: int) -> Path:
    return base_dir / f"{year}.parquet"


def write_year_file(base_dir: Path, year: int, candles: list[dict]) -> Path:
    """
    Scrive (o sostituisce, dopo merge dedup+sort) un file parquet per l'anno.

    Se il file esiste già: legge contenuto esistente, fa union con i nuovi candles,
    dedup per `t` (last write wins), sort per `t`, scrive intero file di nuovo.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    path = _year_path(base_dir, year)

    # Merge col contenuto esistente
    rows: dict[int, dict] = {}
    if path.exists():
        existing = read_range(base_dir, since=None, until=None,
                              year_filter={year})
        for r in existing:
            rows[r["t"]] = r
    for c in candles:
        rows[c["t"]] = {
            "t":        int(c["t"]),
            "o":        float(c["o"]),
            "h":        float(c["h"]),
            "l":        float(c["l"]),
            "c":        float(c["c"]),
            "v":        float(c["v"]),
            "n_trades": int(c.get("n_trades", 0)),
        }

    sorted_rows = [rows[t] for t in sorted(rows.keys())]
    table = pa.Table.from_pylist(sorted_rows, schema=OHLCV_SCHEMA)
    # Scrittura atomica: scrivi su file temporaneo nella stessa dir, poi rename.
    # os.replace è atomico sullo stesso filesystem → niente file parziali su crash.
    tmp_path = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp_path, compression="snappy")
    os.replace(tmp_path, path)
    return path


def read_range(
    base_dir: Path,
    since: int | None,
    until: int | None,
    year_filter: set[int] | None = None,
) -> list[dict]:
    """
    Legge tutti i file parquet sotto base_dir (uno per anno) e filtra per t.

    since/until in unix seconds (estremi inclusi se non None).
    year_filter: se non None, considera solo gli anni specificati.
    """
    if not base_dir.exists():
        return []

    out: list[dict] = []
    files = sorted(base_dir.glob("*.parquet"))
    for f in files:
        # Ignora file parquet con nome non-anno (es. residui o file estranei):
        # il layout prevede esattamente <YYYY>.parquet.
        try:
            year = int(f.stem)
        except ValueError:
            continue
        if year_filter is not None and year not in year_filter:
            continue
        table = pq.read_table(f, schema=OHLCV_SCHEMA)
        for row in table.to_pylist():
            if since is not None and row["t"] < since:
                continue
            if until is not None and row["t"] > until:
                continue
            out.append(row)
    out.sort(key=lambda r: r["t"])
    return out


def detect_gaps(candles: list[dict], timeframe: str) -> list[tuple[int, int]]:
    """
    Trova i gap nella serie. Ritorna lista di tuple (gap_start, gap_end)
    dove gap_start è il primo timestamp mancante e gap_end è il primo presente
    dopo il gap. Se nessun gap, ritorna [].
    """
    if len(candles) < 2:
        return []
    step = _tf_seconds(timeframe)
    gaps: list[tuple[int, int]] = []
    for prev, curr in zip(candles, candles[1:]):
        expected = prev["t"] + step
        if curr["t"] > expected:
            gaps.append((expected, curr["t"]))
    return gaps


def coverage_report(base_dir: Path, timeframe: str) -> dict:
    """
    Riporta lo stato di copertura: numero candele, anni presenti,
    intervallo since/until, conteggio gap.
    """
    if not base_dir.exists():
        return {"n_candles": 0, "years": [], "since": None, "until": None, "gaps": 0}

    candles = read_range(base_dir, since=None, until=None)
    if not candles:
        return {"n_candles": 0, "years": [], "since": None, "until": None, "gaps": 0}

    years = sorted({_year_of(r["t"]) for r in candles})
    gaps  = detect_gaps(candles, timeframe)
    return {
        "n_candles": len(candles),
        "years":     years,
        "since":     candles[0]["t"],
        "until":     candles[-1]["t"],
        "gaps":      len(gaps),
    }

# Backtest Suite — Plan B: Data Lake + Strategie + Optimizer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere il data lake parquet locale con downloader Kraken (`ccxt`), implementare le altre due strategie del start kit (RSI mean-reversion, Bollinger breakout) e costruire l'optimizer (fitness OOS aggregata + GA + Grid) con parallelizzazione `multiprocessing`.

**Architecture:** Data lake con un file parquet per anno-timeframe sotto `data/ohlcv/kraken/{symbol}/{timeframe}/`. Optimizer in moduli puri (no I/O), riusa `walk_forward._generate_windows` per generare finestre IS/OOS. Fitness deterministica calcolata sulle finestre OOS aggregate. GA con genoma `(strategy_id, strategy_params, risk_params)` + speciation per strategy_id. Pool `multiprocessing` con `initializer` che carica le candele una volta per worker.

**Tech Stack:** Python 3.11, pytest, pyarrow, ccxt, stdlib multiprocessing, itertools.

**Spec:** `docs/superpowers/specs/2026-05-27-backtest-suite-design.md` §§ 5, 7, 9.

**Prerequisito:** Plan A completato (engine generico + EmaCrossStrategy + regression gate verde).

---

## File Structure

**Files to create:**
- `backtest_suite/data_lake/__init__.py` — API pubblica (fetch, load, coverage)
- `backtest_suite/data_lake/parquet_store.py` — read/write/validate parquet
- `backtest_suite/data_lake/kraken_source.py` — downloader ccxt + rate limit
- `backtest_suite/strategies/rsi_mr.py` — RsiMeanReversionStrategy
- `backtest_suite/strategies/bb_breakout.py` — BollingerBreakoutStrategy
- `backtest_suite/optimizer/__init__.py`
- `backtest_suite/optimizer/types.py` — IndividualConfig, Scored, GAConfig, GridConfig, WalkForwardConfig, FitnessResult, GenerationEvent, EvolutionResult, GridResult, GridProgressEvent
- `backtest_suite/optimizer/fitness.py`
- `backtest_suite/optimizer/ga.py`
- `backtest_suite/optimizer/grid.py`
- `tests/suite/test_parquet_store.py`
- `tests/suite/test_kraken_source.py`
- `tests/suite/test_rsi_mr.py`
- `tests/suite/test_bb_breakout.py`
- `tests/suite/test_optimizer_fitness.py`
- `tests/suite/test_optimizer_ga.py`
- `tests/suite/test_optimizer_grid.py`

**Files to modify:**
- `backtest_suite/strategies/__init__.py` — aggiungere RSI + BB al `STRATEGY_REGISTRY`

---

## Task 1: parquet_store — schema, write, read, validate, coverage

**Files:**
- Create: `backtest_suite/data_lake/parquet_store.py`
- Test: `tests/suite/test_parquet_store.py`

- [ ] **Step 1: Write failing tests for parquet_store**

Create `tests/suite/test_parquet_store.py`:

```python
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
    # 2024-01-01 00:30:00 UTC → round down to 2024-01-01 00:00:00
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
    # Duplicate + out of order
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
        # gap di 2 ore
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
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_parquet_store.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement parquet_store.py**

Create `backtest_suite/data_lake/parquet_store.py`:

```python
"""
parquet_store — read/write/validate dei file OHLCV in parquet.

Schema: t (int64), o/h/l/c/v (float64), n_trades (int32).
Layout: <base_dir>/<YYYY>.parquet — un file per anno-timeframe.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

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
    pq.write_table(table, path, compression="snappy")
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
        year = int(f.stem)
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
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_parquet_store.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/data_lake/parquet_store.py tests/suite/test_parquet_store.py
git commit -m "feat(data_lake): parquet_store con write/read/dedup/coverage"
```

---

## Task 2: kraken_source — downloader OHLCV via ccxt

**Files:**
- Create: `backtest_suite/data_lake/kraken_source.py`
- Test: `tests/suite/test_kraken_source.py`

- [ ] **Step 1: Write failing tests with mocked ccxt**

Create `tests/suite/test_kraken_source.py`:

```python
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
    # Mocka ccxt: ogni call ritorna 2 candele, fino al target
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
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_kraken_source.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement kraken_source.py**

Create `backtest_suite/data_lake/kraken_source.py`:

```python
"""
kraken_source — downloader OHLCV da Kraken via ccxt con paginazione e rate limit.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

import logging
import time

import ccxt

log = logging.getLogger(__name__)


_TIMEFRAME_TO_MS: dict[str, int] = {
    "1m":   60_000,
    "5m":   300_000,
    "15m":  900_000,
    "1h":   3_600_000,
    "4h":   14_400_000,
    "1d":   86_400_000,
}


def _normalize_symbol(symbol: str) -> str:
    """Converti BTCUSDT in BTC/USDT (canonico per ccxt)."""
    if "/" in symbol:
        return symbol
    # Heuristica semplice: split sulle quote più comuni
    for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}/{quote}"
    return symbol


def _build_exchange() -> ccxt.Exchange:
    """Factory ccxt Kraken — separato per facilitare mocking nei test."""
    return ccxt.kraken({"enableRateLimit": True, "timeout": 30000})


def fetch_ohlcv_range(
    symbol:       str,
    timeframe:    str,
    since:        int,           # unix seconds UTC inclusivo
    until:        int,            # unix seconds UTC inclusivo
    sleep_seconds: float = 1.0,   # rate-limit Kraken (override a 0 nei test)
    page_limit:   int = 720,      # max candele per request (Kraken default)
) -> list[dict]:
    """
    Scarica OHLCV per il range [since, until].

    Itera con paginazione: ogni richiesta ritorna fino a page_limit candele.
    Aspetta sleep_seconds tra le request per rispettare il rate limit.
    Ritorna list[dict] con chiavi {t, o, h, l, c, v, n_trades}.
    """
    if timeframe not in _TIMEFRAME_TO_MS:
        raise ValueError(f"timeframe non supportato: {timeframe}")

    step_ms = _TIMEFRAME_TO_MS[timeframe]
    since_ms = since * 1000
    until_ms = until * 1000

    pair = _normalize_symbol(symbol)
    ex   = _build_exchange()

    out: list[dict] = []
    cursor_ms = since_ms

    while cursor_ms <= until_ms:
        try:
            batch = ex.fetch_ohlcv(pair, timeframe=timeframe,
                                   since=cursor_ms, limit=page_limit)
        except Exception as exc:
            log.warning("[kraken_source] fetch failed at %d: %s — retrying once", cursor_ms, exc)
            time.sleep(max(sleep_seconds, 2.0))
            batch = ex.fetch_ohlcv(pair, timeframe=timeframe,
                                   since=cursor_ms, limit=page_limit)

        if not batch:
            break

        for row in batch:
            t_ms = int(row[0])
            if t_ms > until_ms:
                continue
            out.append({
                "t":        t_ms // 1000,
                "o":        float(row[1]),
                "h":        float(row[2]),
                "l":        float(row[3]),
                "c":        float(row[4]),
                "v":        float(row[5]),
                "n_trades": 0,        # ccxt non lo espone uniformemente
            })

        last_ms = int(batch[-1][0])
        if last_ms <= cursor_ms:
            # Niente progresso → exit per evitare loop infinito
            break
        cursor_ms = last_ms + step_ms

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return out
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_kraken_source.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/data_lake/kraken_source.py tests/suite/test_kraken_source.py
git commit -m "feat(data_lake): kraken_source downloader con paginazione + rate limit"
```

---

## Task 3: data_lake/__init__.py — API pubblica fetch/load/coverage

**Files:**
- Modify: `backtest_suite/data_lake/__init__.py` (replace skeleton)
- Modify: `tests/suite/test_parquet_store.py` (aggiungi test fetch/load idempotency)

- [ ] **Step 1: Add failing test for API pubblica**

Append to `tests/suite/test_parquet_store.py`:

```python
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
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_parquet_store.py -v -k "fetch or coverage_returns"`
Expected: ImportError.

- [ ] **Step 3: Implement data_lake/__init__.py**

Replace `backtest_suite/data_lake/__init__.py`:

```python
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
    return root / EXCHANGE / symbol / timeframe


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
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_parquet_store.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/data_lake/__init__.py tests/suite/test_parquet_store.py
git commit -m "feat(data_lake): API pubblica fetch/load/coverage idempotente"
```

---

## Task 4: RsiMeanReversionStrategy

**Files:**
- Create: `backtest_suite/strategies/rsi_mr.py`
- Test: `tests/suite/test_rsi_mr.py`

- [ ] **Step 1: Write failing test**

Create `tests/suite/test_rsi_mr.py`:

```python
"""Test RsiMeanReversionStrategy."""
from backtest_suite.strategies.rsi_mr import RsiMeanReversionStrategy


def _candles(values: list[float]) -> list[dict]:
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def test_rsi_mr_warmup_equals_period_plus_one():
    s = RsiMeanReversionStrategy({"rsi_period": 14, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})
    assert s.warmup_bars() == 15


def test_rsi_mr_no_signal_before_warmup():
    s = RsiMeanReversionStrategy({"rsi_period": 14, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})
    candles = _candles([100.0] * 50)
    sig = s.on_bar(5, candles)
    assert sig.side is None


def test_rsi_mr_long_when_oversold():
    # Costruisci candele con crollo poi stabilizzazione → RSI deve scendere
    values = [100.0] * 5 + [100.0 - i * 1.0 for i in range(1, 30)] + [70.0] * 5
    candles = _candles(values)
    s = RsiMeanReversionStrategy({"rsi_period": 7, "oversold": 30,
                                  "overbought": 70, "exit_mid": 50})

    seen_long = False
    for i in range(s.warmup_bars(), len(candles)):
        if s.on_bar(i, candles).side == "long":
            seen_long = True
            break
    assert seen_long
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_rsi_mr.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement RsiMeanReversionStrategy**

Create `backtest_suite/strategies/rsi_mr.py`:

```python
"""
RsiMeanReversionStrategy — RSI(n) classico: long se RSI < oversold,
short se RSI > overbought.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _compute_rsi(closes: list[float], period: int) -> list[float | None]:
    """RSI di Wilder (smoothing esponenziale)."""
    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi

    gains  = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains  / period
    avg_loss = losses / period
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)

    return rsi


class RsiMeanReversionStrategy:
    strategy_id:  ClassVar[str]                 = "rsi_mr"
    display_name: ClassVar[str]                 = "RSI Mean Reversion"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("rsi_period", 7,  21, 1, is_int=True),
        ParamSpec("oversold",   15, 35, 1, is_int=True),
        ParamSpec("overbought", 65, 85, 1, is_int=True),
        ParamSpec("exit_mid",   40, 60, 1, is_int=True),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.period     = int(params["rsi_period"])
        self.oversold   = int(params["oversold"])
        self.overbought = int(params["overbought"])
        self.exit_mid   = int(params["exit_mid"])

        self._rsi_cache: list[float | None] | None = None
        self._candles_id: int | None = None

    def warmup_bars(self) -> int:
        return self.period + 1

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._candles_id == id(candles):
            return
        closes = [float(c["c"]) for c in candles]
        self._rsi_cache  = _compute_rsi(closes, self.period)
        self._candles_id = id(candles)

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._rsi_cache is not None

        if idx < self.warmup_bars():
            return Signal(side=None)

        rsi = self._rsi_cache[idx]
        if rsi is None:
            return Signal(side=None)

        if rsi <= self.oversold:
            return Signal(side="long",  confidence=(self.oversold   - rsi) / self.oversold)
        if rsi >= self.overbought:
            return Signal(side="short", confidence=(rsi - self.overbought) / (100 - self.overbought))
        return Signal(side=None)
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_rsi_mr.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/rsi_mr.py tests/suite/test_rsi_mr.py
git commit -m "feat(strategies): RsiMeanReversionStrategy con RSI di Wilder"
```

---

## Task 5: BollingerBreakoutStrategy

**Files:**
- Create: `backtest_suite/strategies/bb_breakout.py`
- Test: `tests/suite/test_bb_breakout.py`

- [ ] **Step 1: Write failing test**

Create `tests/suite/test_bb_breakout.py`:

```python
"""Test BollingerBreakoutStrategy."""
from backtest_suite.strategies.bb_breakout import BollingerBreakoutStrategy


def test_bb_breakout_warmup_equals_period():
    s = BollingerBreakoutStrategy({"bb_period": 20, "bb_std": 2.0,
                                   "confirmation_bars": 1})
    assert s.warmup_bars() == 20


def test_bb_breakout_long_on_upper_band_break():
    # Pricing piatto poi spike sopra upper band
    base = [100.0] * 25 + [115.0, 117.0, 118.0]
    candles = [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
               for i, v in enumerate(base)]
    s = BollingerBreakoutStrategy({"bb_period": 20, "bb_std": 2.0,
                                   "confirmation_bars": 1})
    # Cerca il primo segnale long dopo il warmup
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
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_bb_breakout.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement BollingerBreakoutStrategy**

Create `backtest_suite/strategies/bb_breakout.py`:

```python
"""
BollingerBreakoutStrategy — long se close > upper band per N bar consecutivi,
short se close < lower band per N bar.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _compute_bands(closes: list[float], period: int, std_mult: float):
    """Calcola upper/lower band a ogni indice (None nei primi period-1)."""
    n = len(closes)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period:
        return upper, lower
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        m  = mean(window)
        sd = pstdev(window)
        upper[i] = m + std_mult * sd
        lower[i] = m - std_mult * sd
    return upper, lower


class BollingerBreakoutStrategy:
    strategy_id:  ClassVar[str]                  = "bb_breakout"
    display_name: ClassVar[str]                  = "Bollinger Breakout"
    timeframes:   ClassVar[tuple[str, ...]]      = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("bb_period",         10,  40,  1,   is_int=True),
        ParamSpec("bb_std",            1.5, 3.0, 0.1),
        ParamSpec("confirmation_bars", 1,   5,   1,   is_int=True),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.period   = int(params["bb_period"])
        self.std_mult = float(params["bb_std"])
        self.confirm  = int(params["confirmation_bars"])

        self._upper: list[float | None] | None = None
        self._lower: list[float | None] | None = None
        self._candles_id: int | None = None

    def warmup_bars(self) -> int:
        return self.period

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._candles_id == id(candles):
            return
        closes = [float(c["c"]) for c in candles]
        self._upper, self._lower = _compute_bands(closes, self.period, self.std_mult)
        self._candles_id = id(candles)

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._upper is not None and self._lower is not None

        if idx < self.period + self.confirm - 1:
            return Signal(side=None)

        # Check confirmation_bars consecutivi
        long_ok = True
        short_ok = True
        for j in range(idx - self.confirm + 1, idx + 1):
            u = self._upper[j]
            l = self._lower[j]
            c = float(candles[j]["c"])
            if u is None or l is None:
                return Signal(side=None)
            if c <= u:
                long_ok = False
            if c >= l:
                short_ok = False

        if long_ok:
            return Signal(side="long")
        if short_ok:
            return Signal(side="short")
        return Signal(side=None)
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_bb_breakout.py -v`
Expected: 3 passed.

- [ ] **Step 5: Update STRATEGY_REGISTRY**

Replace `backtest_suite/strategies/__init__.py`:

```python
"""strategies — registry e implementazioni delle Strategy."""
from backtest_suite.strategies.base       import ParamSpec, Signal, Strategy
from backtest_suite.strategies.ema_cross  import EmaCrossStrategy
from backtest_suite.strategies.rsi_mr     import RsiMeanReversionStrategy
from backtest_suite.strategies.bb_breakout import BollingerBreakoutStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    EmaCrossStrategy.strategy_id:           EmaCrossStrategy,
    RsiMeanReversionStrategy.strategy_id:   RsiMeanReversionStrategy,
    BollingerBreakoutStrategy.strategy_id:  BollingerBreakoutStrategy,
}

__all__ = [
    "ParamSpec", "Signal", "Strategy", "STRATEGY_REGISTRY",
    "EmaCrossStrategy", "RsiMeanReversionStrategy", "BollingerBreakoutStrategy",
]
```

- [ ] **Step 6: Run full suite to confirm no regression**

Run: `uv run pytest tests/suite -v`
Expected: tutti passano.

- [ ] **Step 7: Commit**

```bash
git add backtest_suite/strategies/bb_breakout.py backtest_suite/strategies/__init__.py tests/suite/test_bb_breakout.py
git commit -m "feat(strategies): BollingerBreakoutStrategy + completa STRATEGY_REGISTRY"
```

---

## Task 6: optimizer/types.py — tutti i dataclass

**Files:**
- Create: `backtest_suite/optimizer/types.py`
- Create: `backtest_suite/optimizer/__init__.py`
- Test: `tests/suite/test_optimizer_fitness.py` (avvio file)

- [ ] **Step 1: Write failing test for types**

Create `tests/suite/test_optimizer_fitness.py`:

```python
"""Test dei tipi dell'optimizer."""
from backtest_suite.optimizer.types import (
    IndividualConfig,
    WalkForwardConfig,
    FitnessResult,
    GAConfig,
    GridConfig,
    GenerationEvent,
    EvolutionResult,
    Scored,
)


def test_individual_config_holds_strategy_and_params():
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 10, "ema_slow": 30, "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    assert ind.strategy_id == "ema_cross"


def test_walk_forward_config_required_fields():
    wf = WalkForwardConfig(
        is_months=6, oos_months=2, step_months=2,
        min_trades_oos=20, max_drawdown_per_window=0.30,
        variance_lambda=0.5,
    )
    assert wf.variance_lambda == 0.5


def test_fitness_result_supports_failed():
    fr = FitnessResult(
        fitness=float("-inf"),
        per_window_scores=[],
        mean_score=0.0,
        stdev_score=0.0,
        max_drawdown_observed=0.0,
        n_trades_total=0,
        failed=True,
        failure_reason="min_trades_oos",
    )
    assert fr.failed is True
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_optimizer_fitness.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement optimizer/__init__.py and types.py**

Create `backtest_suite/optimizer/__init__.py`:

```python
"""optimizer — fitness + GA + Grid per la backtest_suite."""
```

Create `backtest_suite/optimizer/types.py`:

```python
"""
Tipi dell'optimizer.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndividualConfig:
    """Genoma di un individuo del GA / combinazione di Grid."""
    strategy_id:     str
    strategy_params: dict[str, float]
    risk_params:     dict[str, float]


@dataclass(frozen=True)
class WalkForwardConfig:
    is_months:                int
    oos_months:               int
    step_months:              int
    min_trades_oos:           int
    max_drawdown_per_window:  float
    variance_lambda:          float = 0.5


@dataclass
class FitnessResult:
    fitness:               float
    per_window_scores:     list[float]
    mean_score:            float
    stdev_score:           float
    max_drawdown_observed: float
    n_trades_total:        int
    failed:                bool
    failure_reason:        str | None = None


@dataclass
class Scored:
    individual: IndividualConfig
    fitness:    float
    detail:     FitnessResult


@dataclass(frozen=True)
class GAConfig:
    n_generations:           int
    pop_size:                int
    elite_size:              int
    mutation_rate:           float
    crossover_rate:          float
    tournament_k:            int
    species_quotas:          dict[str, float]
    mutate_strategy_id_prob: float
    immigrants_rate:         float
    immigrants_every:        int
    seed:                    int


@dataclass(frozen=True)
class GridConfig:
    strategy_ids:        list[str]
    risk_params_grid:    dict[str, list[float]]
    strategy_params_grid: dict[str, dict[str, list[float]]] | None
    max_combos:          int = 5000


@dataclass
class GenerationEvent:
    generation:      int
    pop_size:        int
    best_fitness:    float
    mean_fitness:    float
    best_individual: IndividualConfig
    species_counts:  dict[str, int]
    elapsed_sec:     float


@dataclass
class EvolutionResult:
    best_individual:         IndividualConfig
    best_fitness:            float
    n_generations_completed: int
    history:                 list[GenerationEvent] = field(default_factory=list)
    elapsed_sec:             float = 0.0
    status:                  str = "finished"     # 'finished' | 'stopped' | 'failed'


@dataclass
class GridProgressEvent:
    processed:   int
    total:       int
    best_so_far: float
    elapsed_sec: float


@dataclass
class GridResult:
    best_individual: IndividualConfig
    best_fitness:    float
    all_scored:      list[Scored]
    elapsed_sec:     float
    status:          str = "finished"
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_optimizer_fitness.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/optimizer/__init__.py backtest_suite/optimizer/types.py tests/suite/test_optimizer_fitness.py
git commit -m "feat(optimizer): tipi (IndividualConfig, GAConfig, FitnessResult, ...)"
```

---

## Task 7: optimizer/fitness — walk-forward windows + aggregated OOS

**Files:**
- Create: `backtest_suite/optimizer/fitness.py`
- Modify: `tests/suite/test_optimizer_fitness.py`

- [ ] **Step 1: Add failing tests for fitness**

Append to `tests/suite/test_optimizer_fitness.py`:

```python
from hermes_trading._engine_core import RiskConfig
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import (
    generate_walk_forward_windows,
    score_individual,
    _build_risk_config,
)
from backtest_suite.optimizer.types import IndividualConfig, WalkForwardConfig


def test_generate_windows_basic():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(365)]
    wf = WalkForwardConfig(is_months=6, oos_months=2, step_months=2,
                           min_trades_oos=20, max_drawdown_per_window=0.3)
    windows = generate_walk_forward_windows(candles, wf)
    assert len(windows) >= 1
    for is_w, oos_w in windows:
        assert len(is_w) == 6 * 30   # is_months * 30
        assert len(oos_w) == 2 * 30


def test_build_risk_config_from_dict():
    rc = _build_risk_config({
        "stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
        "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
        "trailing_stop_tight_pct": 0.025,
    })
    assert isinstance(rc, RiskConfig)
    assert rc.stop_loss_pct == 0.05


def test_score_individual_returns_fitness_result():
    import math
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})

    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=1, max_drawdown_per_window=1.0)
    res = score_individual(ind, candles, wf, ExecutionConfig())
    assert isinstance(res.fitness, float)
    # Almeno una finestra deve essere valutata
    assert len(res.per_window_scores) >= 1


def test_score_individual_fails_filter_when_dd_too_high():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(200)]
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 5, "ema_slow": 20, "vwap_window": 50,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=100, max_drawdown_per_window=0.01)
    res = score_individual(ind, candles, wf, ExecutionConfig())
    # Niente trade su flat → min_trades_oos non raggiunto
    assert res.failed
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_optimizer_fitness.py -v -k "windows or score or risk"`
Expected: ImportError.

- [ ] **Step 3: Implement fitness.py**

Create `backtest_suite/optimizer/fitness.py`:

```python
"""
fitness — calcola fitness anti-overfit di un IndividualConfig su finestre OOS.

Approccio: fitness = mean(score_OOS) - lambda * stdev(score_OOS).
Filtri hard: min_trades_oos cumulato, max_drawdown_per_window.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.1.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev

from hermes_trading._engine_core import RiskConfig
from hermes_trading import score as score_mod
from hermes_trading.walk_forward import _DAYS_PER_MONTH, _generate_windows

from backtest_suite.engine import run_backtest
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import (
    FitnessResult,
    IndividualConfig,
    WalkForwardConfig,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


def generate_walk_forward_windows(
    candles: list[dict],
    wf:      WalkForwardConfig,
) -> list[tuple[list[dict], list[dict]]]:
    """Riusa walk_forward._generate_windows. mesi → giorni × _DAYS_PER_MONTH."""
    is_days   = wf.is_months   * _DAYS_PER_MONTH
    oos_days  = wf.oos_months  * _DAYS_PER_MONTH
    step_days = wf.step_months * _DAYS_PER_MONTH
    return _generate_windows(candles, is_days, oos_days, step_days)


def _build_risk_config(risk_params: dict[str, float]) -> RiskConfig:
    return RiskConfig(
        stop_loss_pct           = float(risk_params["stop_loss_pct"]),
        partial_exit_pct        = float(risk_params["partial_exit_pct"]),
        trailing_activate_pct   = float(risk_params["trailing_activate_pct"]),
        trailing_stop_pct       = float(risk_params["trailing_stop_pct"]),
        trailing_stop_tight_pct = float(risk_params["trailing_stop_tight_pct"]),
    )


def _composite_score(report: dict) -> float:
    return float(report.get("composite_score", 0.0))


def score_individual(
    individual: IndividualConfig,
    candles:    list[dict],
    wf:         WalkForwardConfig,
    execution:  ExecutionConfig,
) -> FitnessResult:
    """
    Valuta un individuo sulle finestre OOS aggregate.

    Per ogni finestra (IS, OOS):
      - costruisce Strategy + RiskConfig dall'individuo
      - run_backtest sulla OOS
      - calcola composite_score con score.full_report
    Aggrega: fitness = mean(scores) - variance_lambda * stdev(scores).
    Filtri hard: somma trade >= min_trades_oos, max DD per finestra <= soglia.
    """
    strategy_cls = STRATEGY_REGISTRY.get(individual.strategy_id)
    if strategy_cls is None:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=[], mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=0.0, n_trades_total=0,
            failed=True, failure_reason=f"strategy_id sconosciuto: {individual.strategy_id}",
        )

    risk = _build_risk_config(individual.risk_params)
    windows = generate_walk_forward_windows(candles, wf)
    if not windows:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=[], mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=0.0, n_trades_total=0,
            failed=True, failure_reason="nessuna finestra IS/OOS generabile",
        )

    scores: list[float]     = []
    n_trades_total: int     = 0
    max_dd_observed: float  = 0.0

    for _is_w, oos_w in windows:
        try:
            strat  = strategy_cls(individual.strategy_params)
            result = run_backtest(oos_w, strat, risk, execution)
        except Exception:
            scores.append(0.0)
            continue
        n_trades_total += len(result.trades)
        dd = float(result.metrics.get("max_drawdown", 0.0))
        if dd > max_dd_observed:
            max_dd_observed = dd
        if dd > wf.max_drawdown_per_window:
            return FitnessResult(
                fitness=float("-inf"),
                per_window_scores=scores + [0.0],
                mean_score=0.0, stdev_score=0.0,
                max_drawdown_observed=dd,
                n_trades_total=n_trades_total,
                failed=True,
                failure_reason=f"max_dd {dd:.4f} > {wf.max_drawdown_per_window}",
            )
        trade_dicts = [{"pnl_pct": t.pnl_pct} for t in result.trades]
        report = score_mod.full_report(trade_dicts, {})
        scores.append(_composite_score(report))

    if n_trades_total < wf.min_trades_oos:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=scores, mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=max_dd_observed,
            n_trades_total=n_trades_total,
            failed=True,
            failure_reason=f"n_trades_total {n_trades_total} < {wf.min_trades_oos}",
        )

    mu = mean(scores) if scores else 0.0
    sd = pstdev(scores) if len(scores) >= 2 else 0.0
    fitness = mu - wf.variance_lambda * sd
    return FitnessResult(
        fitness=fitness,
        per_window_scores=[round(s, 6) for s in scores],
        mean_score=round(mu, 6),
        stdev_score=round(sd, 6),
        max_drawdown_observed=round(max_dd_observed, 6),
        n_trades_total=n_trades_total,
        failed=False,
        failure_reason=None,
    )
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_optimizer_fitness.py -v`
Expected: tutti i nuovi passano.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/optimizer/fitness.py tests/suite/test_optimizer_fitness.py
git commit -m "feat(optimizer): fitness OOS aggregata con filtri hard"
```

---

## Task 8: optimizer/ga.py — operatori (mutate, crossover, tournament, init pop)

**Files:**
- Create: `backtest_suite/optimizer/ga.py`
- Test: `tests/suite/test_optimizer_ga.py`

- [ ] **Step 1: Write failing tests for operators**

Create `tests/suite/test_optimizer_ga.py`:

```python
"""Test operatori GA: init_population, mutate, crossover, tournament."""
import random

from backtest_suite.optimizer.ga import (
    init_population,
    mutate,
    crossover,
    tournament_select,
    _DEFAULT_RISK_RANGES,
)
from backtest_suite.optimizer.types import GAConfig, IndividualConfig, Scored, FitnessResult


def _ga_config(pop=10, seed=1) -> GAConfig:
    return GAConfig(
        n_generations=3, pop_size=pop, elite_size=1,
        mutation_rate=0.2, crossover_rate=0.7, tournament_k=3,
        species_quotas={"ema_cross": 1.0},
        mutate_strategy_id_prob=0.0, immigrants_rate=0.0, immigrants_every=999,
        seed=seed,
    )


def test_init_population_size_and_quotas():
    rng = random.Random(42)
    cfg = _ga_config(pop=20)
    pop = init_population(cfg, rng)
    assert len(pop) == 20
    assert all(ind.strategy_id == "ema_cross" for ind in pop)
    assert all("ema_fast" in ind.strategy_params for ind in pop)
    assert all("stop_loss_pct" in ind.risk_params for ind in pop)


def test_init_population_respects_param_bounds():
    rng = random.Random(7)
    cfg = _ga_config(pop=50)
    pop = init_population(cfg, rng)
    for ind in pop:
        assert 5 <= ind.strategy_params["ema_fast"] <= 30
        assert 20 <= ind.strategy_params["ema_slow"] <= 100
        sl_lo, sl_hi = _DEFAULT_RISK_RANGES["stop_loss_pct"]
        assert sl_lo <= ind.risk_params["stop_loss_pct"] <= sl_hi


def test_mutate_changes_at_least_one_param_with_high_rate():
    rng = random.Random(0)
    cfg = _ga_config(pop=1)
    pop = init_population(cfg, rng)
    original = pop[0]
    mutated = mutate(original, rate=1.0, rng=rng,
                     mutate_strategy_id_prob=0.0)
    # Almeno un parametro è cambiato
    same_strategy = all(original.strategy_params[k] == mutated.strategy_params[k]
                        for k in original.strategy_params)
    same_risk = all(original.risk_params[k] == mutated.risk_params[k]
                    for k in original.risk_params)
    assert not (same_strategy and same_risk)


def test_crossover_same_species_produces_valid_children():
    rng = random.Random(0)
    cfg = _ga_config(pop=2)
    pop = init_population(cfg, rng)
    a, b = pop
    c1, c2 = crossover(a, b, rng)
    assert c1.strategy_id == a.strategy_id
    assert c2.strategy_id == b.strategy_id
    assert set(c1.strategy_params.keys()) == set(a.strategy_params.keys())


def test_crossover_different_species_returns_unchanged():
    a = IndividualConfig("ema_cross",
                         {"ema_fast": 10, "ema_slow": 30, "vwap_window": 100,
                          "vwap_filter": 0, "direction": 2},
                         {"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                          "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                          "trailing_stop_tight_pct": 0.025})
    b = IndividualConfig("rsi_mr",
                         {"rsi_period": 14, "oversold": 30, "overbought": 70, "exit_mid": 50},
                         a.risk_params)
    rng = random.Random(0)
    c1, c2 = crossover(a, b, rng)
    assert c1 is a and c2 is b


def test_tournament_select_returns_best_of_k():
    rng = random.Random(0)
    individuals = [
        IndividualConfig(f"ema_cross", {"ema_fast": 10 + i, "ema_slow": 30,
                                        "vwap_window": 100, "vwap_filter": 0, "direction": 2},
                         {"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                          "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                          "trailing_stop_tight_pct": 0.025})
        for i in range(10)
    ]
    scored = [Scored(individual=ind, fitness=float(i),
                     detail=FitnessResult(fitness=float(i), per_window_scores=[],
                                          mean_score=0.0, stdev_score=0.0,
                                          max_drawdown_observed=0.0, n_trades_total=0,
                                          failed=False, failure_reason=None))
              for i, ind in enumerate(individuals)]
    # Su k=10 si seleziona sempre il migliore = ultimo
    chosen = tournament_select(scored, k=10, rng=rng)
    assert chosen is scored[-1].individual
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_optimizer_ga.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement ga.py operators**

Create `backtest_suite/optimizer/ga.py`:

```python
"""
ga — operatori GA + evolve loop.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.3, §7.4.
"""
from __future__ import annotations

import random
from typing import Callable

from backtest_suite.optimizer.types import (
    GAConfig,
    IndividualConfig,
    Scored,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


# Range default per i risk params (decimali).
_DEFAULT_RISK_RANGES: dict[str, tuple[float, float]] = {
    "stop_loss_pct":           (0.02,  0.08),
    "partial_exit_pct":        (0.05,  0.20),
    "trailing_activate_pct":   (0.03,  0.10),
    "trailing_stop_pct":       (0.02,  0.06),
    "trailing_stop_tight_pct": (0.01,  0.04),
}


def _sample_param_value(low: float, high: float, is_int: bool,
                        step: float | None, rng: random.Random) -> float:
    if is_int:
        return float(rng.randint(int(low), int(high)))
    if step is not None and step > 0:
        # Discretizzato
        n = int(round((high - low) / step))
        i = rng.randint(0, n)
        return round(low + i * step, 6)
    return rng.uniform(low, high)


def _random_individual(strategy_id: str, rng: random.Random) -> IndividualConfig:
    cls = STRATEGY_REGISTRY[strategy_id]
    strategy_params: dict[str, float] = {}
    for ps in cls.param_specs:
        strategy_params[ps.name] = _sample_param_value(ps.low, ps.high, ps.is_int,
                                                       ps.step, rng)

    risk_params: dict[str, float] = {}
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        risk_params[name] = round(rng.uniform(lo, hi), 6)

    return IndividualConfig(
        strategy_id=strategy_id,
        strategy_params=strategy_params,
        risk_params=risk_params,
    )


def init_population(config: GAConfig, rng: random.Random) -> list[IndividualConfig]:
    """Crea pop_size individui rispettando species_quotas."""
    pop: list[IndividualConfig] = []
    remaining = config.pop_size
    quotas = list(config.species_quotas.items())
    for i, (strategy_id, quota) in enumerate(quotas):
        if i == len(quotas) - 1:
            n = remaining
        else:
            n = max(1, int(round(config.pop_size * quota)))
            n = min(n, remaining)
        for _ in range(n):
            pop.append(_random_individual(strategy_id, rng))
        remaining -= n
        if remaining <= 0:
            break
    return pop


def _mutate_value(value: float, low: float, high: float, is_int: bool,
                  step: float | None, rng: random.Random) -> float:
    sigma = (high - low) * 0.1
    new_val = value + rng.gauss(0.0, sigma)
    new_val = max(low, min(high, new_val))
    if is_int:
        return float(int(round(new_val)))
    if step is not None and step > 0:
        # Snap al passo
        offset = round((new_val - low) / step)
        return round(low + offset * step, 6)
    return round(new_val, 6)


def mutate(ind: IndividualConfig, rate: float, rng: random.Random,
           mutate_strategy_id_prob: float = 0.0) -> IndividualConfig:
    """Gaussian mutation per parametro; opzionale flip della strategia."""
    # Eventuale flip della strategia
    if mutate_strategy_id_prob > 0 and rng.random() < mutate_strategy_id_prob:
        other_ids = [sid for sid in STRATEGY_REGISTRY if sid != ind.strategy_id]
        if other_ids:
            new_sid = rng.choice(other_ids)
            return _random_individual(new_sid, rng)

    cls = STRATEGY_REGISTRY[ind.strategy_id]
    new_strategy_params = dict(ind.strategy_params)
    for ps in cls.param_specs:
        if rng.random() < rate:
            new_strategy_params[ps.name] = _mutate_value(
                ind.strategy_params[ps.name], ps.low, ps.high,
                ps.is_int, ps.step, rng,
            )

    new_risk_params = dict(ind.risk_params)
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        if rng.random() < rate:
            new_risk_params[name] = _mutate_value(
                ind.risk_params[name], lo, hi, is_int=False, step=None, rng=rng,
            )

    return IndividualConfig(
        strategy_id=ind.strategy_id,
        strategy_params=new_strategy_params,
        risk_params=new_risk_params,
    )


def crossover(a: IndividualConfig, b: IndividualConfig,
              rng: random.Random) -> tuple[IndividualConfig, IndividualConfig]:
    """Uniform crossover. Niente crossover tra specie diverse."""
    if a.strategy_id != b.strategy_id:
        return a, b

    sp_a, sp_b = dict(a.strategy_params), dict(b.strategy_params)
    rp_a, rp_b = dict(a.risk_params),     dict(b.risk_params)
    for k in sp_a:
        if rng.random() < 0.5:
            sp_a[k], sp_b[k] = sp_b[k], sp_a[k]
    for k in rp_a:
        if rng.random() < 0.5:
            rp_a[k], rp_b[k] = rp_b[k], rp_a[k]

    c1 = IndividualConfig(a.strategy_id, sp_a, rp_a)
    c2 = IndividualConfig(b.strategy_id, sp_b, rp_b)
    return c1, c2


def tournament_select(scored: list[Scored], k: int,
                      rng: random.Random) -> IndividualConfig:
    """Tournament selection size k — ritorna l'individuo migliore di k campionati."""
    k_eff = min(k, len(scored))
    contenders = rng.sample(scored, k_eff)
    best = max(contenders, key=lambda s: s.fitness)
    return best.individual
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_optimizer_ga.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/optimizer/ga.py tests/suite/test_optimizer_ga.py
git commit -m "feat(optimizer): operatori GA (init/mutate/crossover/tournament)"
```

---

## Task 9: optimizer/ga.py — evolve loop + multiprocessing pool

**Files:**
- Modify: `backtest_suite/optimizer/ga.py`
- Modify: `tests/suite/test_optimizer_ga.py`

- [ ] **Step 1: Add failing test for evolve()**

Append to `tests/suite/test_optimizer_ga.py`:

```python
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.ga import evolve
from backtest_suite.optimizer.types import GAConfig, WalkForwardConfig


def test_evolve_terminates_and_returns_best():
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1.0, "l": p - 1.0,
                        "c": p, "v": 100.0})

    cfg = GAConfig(
        n_generations=2, pop_size=4, elite_size=1,
        mutation_rate=0.3, crossover_rate=0.7, tournament_k=2,
        species_quotas={"ema_cross": 1.0},
        mutate_strategy_id_prob=0.0, immigrants_rate=0.0, immigrants_every=999,
        seed=42,
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=1, max_drawdown_per_window=1.0)

    events: list = []
    result = evolve(
        cfg, candles, wf, ExecutionConfig(),
        stop_flag=lambda: False,
        progress_callback=events.append,
        n_workers=1,    # serial — test deterministico
    )
    assert result.n_generations_completed == 2
    assert len(events) == 2
    assert result.best_fitness is not None


def test_evolve_respects_stop_flag():
    candles = [{"t": i * 86400, "o": 100, "h": 100, "l": 100, "c": 100, "v": 0}
               for i in range(200)]
    cfg = GAConfig(
        n_generations=10, pop_size=4, elite_size=1,
        mutation_rate=0.1, crossover_rate=0.5, tournament_k=2,
        species_quotas={"ema_cross": 1.0},
        mutate_strategy_id_prob=0.0, immigrants_rate=0.0, immigrants_every=999,
        seed=1,
    )
    wf = WalkForwardConfig(is_months=1, oos_months=1, step_months=1,
                           min_trades_oos=0, max_drawdown_per_window=1.0)

    called = {"n": 0}

    def stop_after_one():
        called["n"] += 1
        return called["n"] > 1

    result = evolve(cfg, candles, wf, ExecutionConfig(),
                    stop_flag=stop_after_one,
                    progress_callback=lambda _: None,
                    n_workers=1)
    assert result.status == "stopped"
    assert result.n_generations_completed <= 2
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_optimizer_ga.py -v -k evolve`
Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement evolve loop**

Append to `backtest_suite/optimizer/ga.py`:

```python
import multiprocessing
import os
import time
from typing import Callable

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import score_individual
from backtest_suite.optimizer.types import (
    EvolutionResult,
    GenerationEvent,
    Scored,
    WalkForwardConfig,
)

# Stato globale per i worker (popolato da _init_worker via initializer del Pool)
_W_CANDLES: list[dict] | None = None
_W_WF: WalkForwardConfig | None = None
_W_EXEC: ExecutionConfig | None = None


def _init_worker(candles, wf, execution):
    global _W_CANDLES, _W_WF, _W_EXEC
    _W_CANDLES = candles
    _W_WF      = wf
    _W_EXEC    = execution


def _evaluate_one(individual: IndividualConfig) -> Scored:
    assert _W_CANDLES is not None and _W_WF is not None and _W_EXEC is not None
    detail = score_individual(individual, _W_CANDLES, _W_WF, _W_EXEC)
    return Scored(individual=individual, fitness=detail.fitness, detail=detail)


def _evaluate_serial(individual: IndividualConfig,
                     candles: list[dict],
                     wf: WalkForwardConfig,
                     execution: ExecutionConfig) -> Scored:
    detail = score_individual(individual, candles, wf, execution)
    return Scored(individual=individual, fitness=detail.fitness, detail=detail)


def _evaluate_population(
    pop: list[IndividualConfig],
    candles: list[dict],
    wf: WalkForwardConfig,
    execution: ExecutionConfig,
    n_workers: int,
) -> list[Scored]:
    if n_workers <= 1:
        return [_evaluate_serial(ind, candles, wf, execution) for ind in pop]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=n_workers,
                  initializer=_init_worker,
                  initargs=(candles, wf, execution)) as pool:
        scored = pool.map(_evaluate_one, pop)
    return scored


def _species_counts(pop: list[IndividualConfig]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ind in pop:
        counts[ind.strategy_id] = counts.get(ind.strategy_id, 0) + 1
    return counts


def evolve(
    config:    GAConfig,
    candles:   list[dict],
    wf:        WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],
    progress_callback: Callable[[GenerationEvent], None],
    n_workers: int = 0,
) -> EvolutionResult:
    """
    Evolve loop. n_workers=0 → auto (cpu_count-2); n_workers=1 → serial deterministico.
    """
    if n_workers == 0:
        n_workers = max(1, (os.cpu_count() or 2) - 2)

    rng = random.Random(config.seed)
    pop = init_population(config, rng)

    history: list[GenerationEvent] = []
    best_overall: Scored | None    = None
    t_start = time.time()
    status  = "finished"
    n_done  = 0

    for gen in range(config.n_generations):
        scored = _evaluate_population(pop, candles, wf, execution, n_workers)
        scored.sort(key=lambda s: s.fitness, reverse=True)

        if best_overall is None or scored[0].fitness > best_overall.fitness:
            best_overall = scored[0]

        valid_fitness = [s.fitness for s in scored if s.fitness != float("-inf")]
        mean_fit = sum(valid_fitness) / len(valid_fitness) if valid_fitness else float("-inf")

        event = GenerationEvent(
            generation=gen,
            pop_size=len(pop),
            best_fitness=scored[0].fitness,
            mean_fitness=mean_fit,
            best_individual=scored[0].individual,
            species_counts=_species_counts(pop),
            elapsed_sec=round(time.time() - t_start, 3),
        )
        progress_callback(event)
        history.append(event)
        n_done = gen + 1

        if stop_flag():
            status = "stopped"
            break

        # Costruisci la prossima generazione
        elites = [s.individual for s in scored[: config.elite_size]]
        next_pop: list[IndividualConfig] = list(elites)

        # Immigrants (random fresh) ogni N generazioni
        n_immigrants = 0
        if config.immigrants_every > 0 and (gen + 1) % config.immigrants_every == 0:
            n_immigrants = max(0, int(config.pop_size * config.immigrants_rate))
            for _ in range(n_immigrants):
                sid = rng.choices(
                    list(config.species_quotas.keys()),
                    weights=list(config.species_quotas.values()),
                )[0]
                next_pop.append(_random_individual(sid, rng))

        while len(next_pop) < config.pop_size:
            p1 = tournament_select(scored, config.tournament_k, rng)
            p2 = tournament_select(scored, config.tournament_k, rng)
            if rng.random() < config.crossover_rate:
                c1, c2 = crossover(p1, p2, rng)
            else:
                c1, c2 = p1, p2
            c1 = mutate(c1, config.mutation_rate, rng, config.mutate_strategy_id_prob)
            next_pop.append(c1)
            if len(next_pop) < config.pop_size:
                c2 = mutate(c2, config.mutation_rate, rng, config.mutate_strategy_id_prob)
                next_pop.append(c2)

        pop = next_pop[: config.pop_size]

    assert best_overall is not None
    return EvolutionResult(
        best_individual=best_overall.individual,
        best_fitness=best_overall.fitness,
        n_generations_completed=n_done,
        history=history,
        elapsed_sec=round(time.time() - t_start, 3),
        status=status,
    )
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_optimizer_ga.py -v -k evolve`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/optimizer/ga.py tests/suite/test_optimizer_ga.py
git commit -m "feat(optimizer): evolve loop con multiprocessing pool"
```

---

## Task 10: optimizer/grid.py — grid search

**Files:**
- Create: `backtest_suite/optimizer/grid.py`
- Test: `tests/suite/test_optimizer_grid.py`

- [ ] **Step 1: Write failing test**

Create `tests/suite/test_optimizer_grid.py`:

```python
"""Test grid search."""
import math

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.grid import grid_search, _generate_combos
from backtest_suite.optimizer.types import GridConfig, WalkForwardConfig


def test_generate_combos_uses_strategy_grid_when_provided():
    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={"stop_loss_pct": [0.03, 0.05]},
        strategy_params_grid={
            "ema_cross": {
                "ema_fast":   [5, 10],
                "ema_slow":   [20, 30],
                "vwap_window": [100],
                "vwap_filter": [0],
                "direction":   [2],
            }
        },
        max_combos=100,
    )
    combos = list(_generate_combos(cfg))
    # 2 ema_fast × 2 ema_slow × 2 stop_loss × (partial_exit, trailing_*, ... 1 valore default)
    assert len(combos) >= 2 * 2 * 2
    for ind in combos:
        assert ind.strategy_id == "ema_cross"


def test_generate_combos_caps_at_max_combos():
    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={"stop_loss_pct": [0.03, 0.05]},
        strategy_params_grid={
            "ema_cross": {
                "ema_fast":  [5, 10, 15, 20, 25, 30],
                "ema_slow":  [20, 30, 40, 50, 60, 70, 80, 90, 100],
                "vwap_window": [50, 100, 200, 300],
                "vwap_filter": [0, 1],
                "direction":   [0, 1, 2],
            }
        },
        max_combos=10,
    )
    try:
        list(_generate_combos(cfg))
    except ValueError as e:
        assert "max_combos" in str(e)
        return
    raise AssertionError("ValueError atteso")


def test_grid_search_runs_and_returns_best():
    candles = []
    for i in range(300):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1.0, "l": p - 1.0,
                        "c": p, "v": 100.0})

    cfg = GridConfig(
        strategy_ids=["ema_cross"],
        risk_params_grid={
            "stop_loss_pct":           [0.05],
            "partial_exit_pct":        [0.10],
            "trailing_activate_pct":   [0.06],
            "trailing_stop_pct":       [0.04],
            "trailing_stop_tight_pct": [0.025],
        },
        strategy_params_grid={
            "ema_cross": {
                "ema_fast": [5, 10], "ema_slow": [20, 30],
                "vwap_window": [100], "vwap_filter": [0], "direction": [2],
            }
        },
        max_combos=50,
    )
    wf = WalkForwardConfig(is_months=2, oos_months=1, step_months=1,
                           min_trades_oos=1, max_drawdown_per_window=1.0)
    progress = []
    result = grid_search(cfg, candles, wf, ExecutionConfig(),
                         stop_flag=lambda: False,
                         progress_callback=progress.append, n_workers=1)
    assert len(result.all_scored) == 4
    assert result.best_fitness is not None
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_optimizer_grid.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement grid.py**

Create `backtest_suite/optimizer/grid.py`:

```python
"""
grid — grid search sulla stessa fitness del GA.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.6.
"""
from __future__ import annotations

import itertools
import time
from typing import Callable, Iterator

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import score_individual
from backtest_suite.optimizer.ga import _DEFAULT_RISK_RANGES, _evaluate_population
from backtest_suite.optimizer.types import (
    GridConfig,
    GridProgressEvent,
    GridResult,
    IndividualConfig,
    Scored,
    WalkForwardConfig,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


def _values_from_spec(low: float, high: float, step: float | None, is_int: bool) -> list[float]:
    if is_int or (step is not None and step > 0):
        if step is None:
            step = 1.0 if is_int else (high - low) / 4.0
        out, v = [], low
        while v <= high + 1e-9:
            out.append(round(v, 6) if not is_int else float(int(round(v))))
            v += step
        return out
    return [round(low + i * (high - low) / 4.0, 6) for i in range(5)]


def _strategy_values(strategy_id: str, override: dict[str, list[float]] | None
                     ) -> dict[str, list[float]]:
    cls = STRATEGY_REGISTRY[strategy_id]
    out: dict[str, list[float]] = {}
    for ps in cls.param_specs:
        if override and ps.name in override:
            out[ps.name] = list(override[ps.name])
        else:
            out[ps.name] = _values_from_spec(ps.low, ps.high, ps.step, ps.is_int)
    return out


def _risk_values(override: dict[str, list[float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        if name in override:
            out[name] = list(override[name])
        else:
            out[name] = [round((lo + hi) / 2, 6)]
    return out


def _generate_combos(cfg: GridConfig) -> Iterator[IndividualConfig]:
    # Conta prima per validare max_combos
    total = 0
    per_strategy: list[tuple[str, dict[str, list[float]]]] = []
    risk_values = _risk_values(cfg.risk_params_grid)
    n_risk = 1
    for v in risk_values.values():
        n_risk *= max(1, len(v))

    for sid in cfg.strategy_ids:
        sp_override = (cfg.strategy_params_grid or {}).get(sid)
        sv = _strategy_values(sid, sp_override)
        per_strategy.append((sid, sv))
        n_strat = 1
        for v in sv.values():
            n_strat *= max(1, len(v))
        total += n_strat * n_risk

    if total > cfg.max_combos:
        raise ValueError(
            f"max_combos superato: {total} > {cfg.max_combos}. "
            f"Riduci i valori della grid o aumenta max_combos."
        )

    for sid, sv in per_strategy:
        sp_names = list(sv.keys())
        sp_values = [sv[k] for k in sp_names]
        rp_names = list(risk_values.keys())
        rp_values = [risk_values[k] for k in rp_names]
        for sp_combo in itertools.product(*sp_values):
            for rp_combo in itertools.product(*rp_values):
                yield IndividualConfig(
                    strategy_id=sid,
                    strategy_params=dict(zip(sp_names, sp_combo)),
                    risk_params=dict(zip(rp_names, rp_combo)),
                )


def grid_search(
    cfg:       GridConfig,
    candles:   list[dict],
    wf:        WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],
    progress_callback: Callable[[GridProgressEvent], None],
    n_workers: int = 0,
) -> GridResult:
    combos = list(_generate_combos(cfg))
    if not combos:
        raise ValueError("nessuna combinazione generata dalla GridConfig")

    t_start = time.time()
    scored_all: list[Scored] = []
    best_so_far = float("-inf")
    status = "finished"

    # Valuta in batch per supportare stop_flag e progress più granulare
    batch_size = max(1, min(50, len(combos)))
    for i in range(0, len(combos), batch_size):
        if stop_flag():
            status = "stopped"
            break
        batch = combos[i : i + batch_size]
        scored_batch = _evaluate_population(batch, candles, wf, execution, n_workers or 1)
        scored_all.extend(scored_batch)
        for s in scored_batch:
            if s.fitness > best_so_far:
                best_so_far = s.fitness
        progress_callback(GridProgressEvent(
            processed=len(scored_all),
            total=len(combos),
            best_so_far=best_so_far,
            elapsed_sec=round(time.time() - t_start, 3),
        ))

    if not scored_all:
        raise RuntimeError("grid search interrotta senza nessun individuo valutato")

    scored_all.sort(key=lambda s: s.fitness, reverse=True)
    best = scored_all[0]
    return GridResult(
        best_individual=best.individual,
        best_fitness=best.fitness,
        all_scored=scored_all,
        elapsed_sec=round(time.time() - t_start, 3),
        status=status,
    )
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_optimizer_grid.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite as smoke**

Run: `uv run pytest tests/suite -v`
Expected: tutti passano.

- [ ] **Step 6: Commit**

```bash
git add backtest_suite/optimizer/grid.py tests/suite/test_optimizer_grid.py
git commit -m "feat(optimizer): grid search con cap max_combos e batching"
```

---

## Self-Review

**Spec coverage** (Plan B):
- §5 RsiMeanReversionStrategy + BollingerBreakoutStrategy + registry aggiornato ✓
- §7.1 fitness OOS aggregata con filtri hard (`score_individual`) ✓
- §7.2 IndividualConfig + Scored ✓
- §7.3 operatori GA (mutate, crossover, tournament) con speciation ✓
- §7.4 evolve loop con stop cooperativo ✓
- §7.5 multiprocessing pool con initializer ✓
- §7.6 grid search con max_combos cap ✓
- §7.7 GenerationEvent + GridProgressEvent (callback) ✓
- §9 data lake parquet (parquet_store + kraken_source + API pubblica fetch/load/coverage) ✓

**Out of scope per Plan B** (coperti nei prossimi):
- Persistenza SQLite + parquet artifacts → Plan C
- CLI hermes-bt → Plan C
- FastAPI server + WebSocket + frontend → Plan D
- End-to-end integration test → Plan D

**Placeholder scan**: nessun TODO/TBD nelle 10 task.

**Type consistency**:
- `IndividualConfig`, `Scored`, `GAConfig`, `GridConfig`, `WalkForwardConfig`, `FitnessResult`, `GenerationEvent`, `EvolutionResult`, `GridResult`, `GridProgressEvent` definiti in `optimizer/types.py` e importati ovunque.
- `RiskConfig` resta in `hermes_trading/_engine_core` (definito in Plan A) — il dict `risk_params` viene convertito tramite `_build_risk_config`.
- Stesse 5 chiavi risk in tutti i moduli: `stop_loss_pct`, `partial_exit_pct`, `trailing_activate_pct`, `trailing_stop_pct`, `trailing_stop_tight_pct`.

**Critical path**: Task 7 (fitness OOS) → Task 9 (evolve loop). Se la fitness non valuta correttamente, il GA non converge.

---

**Plan B completo, salvato in** `docs/superpowers/plans/2026-05-27-backtest-suite-plan-B-data-optimizer.md`.

Plan C (persistenza + CLI) e Plan D (server + frontend + integration) verranno scritti successivamente.

# Backtest Suite — Plan C: Persistenza + CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere la persistenza SQLite (metadati run/individuals) + parquet (equity/trades artifacts) con write pattern batch per generazione. Costruire il config loader pydantic + YAML e la CLI `hermes-bt` con i comandi `fetch`, `run`, `grid`, `evolve` (no `ui`, quello arriva in Plan D).

**Architecture:** `persistence/catalog_db.py` wrappa SQLite con un set di metodi CRUD tipizzati; `persistence/artifact_store.py` gestisce parquet per equity e trades dei top-K. Config caricato da YAML in `config.py` con validazione pydantic. CLI in `cli.py` usa `argparse` (stdlib) per semplicità — niente click/typer.

**Tech Stack:** Python 3.11, pytest, sqlite3 (stdlib), pyarrow, pydantic v2, pyyaml, argparse.

**Spec:** `docs/superpowers/specs/2026-05-27-backtest-suite-design.md` §§ 10, 11.

**Prerequisito:** Plan A + Plan B completati (engine + strategies + optimizer funzionanti).

---

## File Structure

**Files to create:**
- `backtest_suite/persistence/__init__.py`
- `backtest_suite/persistence/catalog_db.py` — SQLite wrapper
- `backtest_suite/persistence/artifact_store.py` — parquet I/O per equity/trades
- `backtest_suite/config.py` — pydantic models + YAML loader
- `backtest_suite/cli.py` — entry point `hermes-bt`
- `tests/suite/test_catalog_db.py`
- `tests/suite/test_artifact_store.py`
- `tests/suite/test_config.py`
- `tests/suite/test_cli.py`
- `tests/suite/fixtures/example_evolve.yaml` — config esempio
- `tests/suite/fixtures/example_grid.yaml`

**Files to modify:**
- `pyproject.toml` — aggiungere `[project.scripts]` entry point

---

## Task 1: catalog_db — schema, create_run, update_run_status

**Files:**
- Create: `backtest_suite/persistence/__init__.py`
- Create: `backtest_suite/persistence/catalog_db.py`
- Test: `tests/suite/test_catalog_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/suite/test_catalog_db.py`:

```python
"""Test del SQLite wrapper CatalogDB."""
import json
from pathlib import Path

from backtest_suite.persistence.catalog_db import CatalogDB


def test_init_creates_schema(tmp_path: Path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    runs = db.list_runs()
    assert runs == []


def test_create_run_returns_id(tmp_path: Path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    run_id = db.create_run(
        kind="ga", symbol="BTCUSDT", timeframe="1h",
        config_path="runs/0001/manifest.yaml",
    )
    assert run_id == 1
    run_id2 = db.create_run(kind="grid", symbol="BTCUSDT", timeframe="4h",
                            config_path="runs/0002/manifest.yaml")
    assert run_id2 == 2


def test_update_run_status_persists_fields(tmp_path: Path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    run_id = db.create_run(kind="ga", symbol="BTCUSDT", timeframe="1h",
                           config_path="runs/0001/manifest.yaml")
    db.update_run_status(run_id, status="finished",
                         best_fitness=1.842,
                         best_individual=json.dumps({"strategy_id": "ema_cross"}),
                         n_generations=50, n_individuals=5000)

    runs = db.list_runs()
    assert len(runs) == 1
    r = runs[0]
    assert r["status"] == "finished"
    assert r["best_fitness"] == 1.842
    assert json.loads(r["best_individual"])["strategy_id"] == "ema_cross"


def test_list_runs_filters_by_status(tmp_path: Path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    r1 = db.create_run("ga", "BTCUSDT", "1h", "runs/0001/manifest.yaml")
    r2 = db.create_run("grid", "BTCUSDT", "4h", "runs/0002/manifest.yaml")
    db.update_run_status(r1, status="finished")
    db.update_run_status(r2, status="running")

    finished = db.list_runs(status="finished")
    running = db.list_runs(status="running")
    assert len(finished) == 1 and finished[0]["id"] == r1
    assert len(running) == 1 and running[0]["id"] == r2
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_catalog_db.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement catalog_db.py (schema + create/update/list)**

Create `backtest_suite/persistence/__init__.py`:

```python
"""persistence — SQLite catalog + parquet artifact store."""
```

Create `backtest_suite/persistence/catalog_db.py`:

```python
"""
catalog_db — wrapper SQLite per metadati run + individuals.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §10.2.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id              INTEGER PRIMARY KEY,
        kind            TEXT NOT NULL,
        status          TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        started_at      TEXT NOT NULL,
        finished_at     TEXT,
        config_path     TEXT NOT NULL,
        best_fitness    REAL,
        best_individual TEXT,
        n_generations   INTEGER,
        n_individuals   INTEGER,
        notes           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS individuals (
        run_id          INTEGER NOT NULL,
        generation      INTEGER NOT NULL,
        rank            INTEGER NOT NULL,
        individual_id   TEXT NOT NULL,
        strategy_id     TEXT NOT NULL,
        params_json     TEXT NOT NULL,
        fitness         REAL NOT NULL,
        mean_oos_score  REAL,
        stdev_oos_score REAL,
        max_drawdown    REAL,
        sharpe          REAL,
        n_trades        INTEGER,
        PRIMARY KEY (run_id, generation, rank),
        FOREIGN KEY (run_id) REFERENCES runs(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_individuals_fitness ON individuals(run_id, fitness DESC)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status        ON runs(status, started_at DESC)",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CatalogDB:
    """Wrapper minimale su sqlite3. Tutte le operazioni in WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    def create_run(self, kind: str, symbol: str, timeframe: str,
                   config_path: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO runs
                   (kind, status, symbol, timeframe, started_at, config_path)
                   VALUES (?, 'running', ?, ?, ?, ?)""",
                (kind, symbol, timeframe, _now_iso(), config_path),
            )
            return int(cur.lastrowid)

    def update_run_status(self, run_id: int, status: str, **fields: Any) -> None:
        cols = ["status = ?"]
        vals: list[Any] = [status]
        for k, v in fields.items():
            cols.append(f"{k} = ?")
            vals.append(v)
        if status in ("finished", "failed", "stopped"):
            cols.append("finished_at = ?")
            vals.append(_now_iso())
        vals.append(run_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", vals)

    def list_runs(self, status: str | None = None,
                  limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM runs WHERE status = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_catalog_db.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/persistence/__init__.py backtest_suite/persistence/catalog_db.py tests/suite/test_catalog_db.py
git commit -m "feat(persistence): CatalogDB schema + create/update/list runs"
```

---

## Task 2: catalog_db — insert_generation + top_individuals

**Files:**
- Modify: `backtest_suite/persistence/catalog_db.py`
- Modify: `tests/suite/test_catalog_db.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/suite/test_catalog_db.py`:

```python
import json

from backtest_suite.optimizer.types import IndividualConfig, FitnessResult, Scored


def _scored(strategy_id: str, fitness: float) -> Scored:
    ind = IndividualConfig(
        strategy_id=strategy_id,
        strategy_params={"ema_fast": 10, "ema_slow": 30, "vwap_window": 100,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.025},
    )
    detail = FitnessResult(
        fitness=fitness, per_window_scores=[fitness], mean_score=fitness,
        stdev_score=0.0, max_drawdown_observed=0.10,
        n_trades_total=50, failed=False, failure_reason=None,
    )
    return Scored(individual=ind, fitness=fitness, detail=detail)


def test_insert_generation_persists_scalars(tmp_path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    run_id = db.create_run("ga", "BTCUSDT", "1h", "runs/0001/manifest.yaml")
    scored = [_scored("ema_cross", 1.5), _scored("ema_cross", 0.8)]
    db.insert_generation(run_id, generation=0, scored=scored)

    top = db.top_individuals(run_id, k=10)
    assert len(top) == 2
    assert top[0]["fitness"] == 1.5
    assert top[0]["strategy_id"] == "ema_cross"
    assert json.loads(top[0]["params_json"])["strategy_params"]["ema_fast"] == 10


def test_top_individuals_orders_by_fitness_desc(tmp_path):
    db = CatalogDB(tmp_path / "catalog.db")
    db.init_schema()
    run_id = db.create_run("ga", "BTCUSDT", "1h", "runs/0001/manifest.yaml")
    db.insert_generation(run_id, 0, [_scored("ema_cross", 0.5), _scored("ema_cross", 1.2)])
    db.insert_generation(run_id, 1, [_scored("ema_cross", 2.0), _scored("ema_cross", 1.7)])
    top = db.top_individuals(run_id, k=3)
    assert [r["fitness"] for r in top] == [2.0, 1.7, 1.2]
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_catalog_db.py -v -k "insert_generation or top_indiv"`
Expected: AttributeError.

- [ ] **Step 3: Implement insert_generation + top_individuals**

Append to `backtest_suite/persistence/catalog_db.py`:

```python
def _individual_id(generation: int, rank: int) -> str:
    return f"G{generation:03d}-{rank:03d}"


class _CatalogDBExtensions:
    """Solo per organizzazione — i metodi sotto sono aggiunti alla classe CatalogDB."""


def _insert_generation(self: CatalogDB, run_id: int, generation: int,
                       scored: list) -> None:
    rows = []
    for rank, s in enumerate(sorted(scored, key=lambda x: x.fitness, reverse=True), start=1):
        params_payload = json.dumps({
            "strategy_id":     s.individual.strategy_id,
            "strategy_params": s.individual.strategy_params,
            "risk_params":     s.individual.risk_params,
        }, sort_keys=True)
        rows.append((
            run_id, generation, rank,
            _individual_id(generation, rank),
            s.individual.strategy_id,
            params_payload,
            float(s.fitness),
            float(s.detail.mean_score),
            float(s.detail.stdev_score),
            float(s.detail.max_drawdown_observed),
            None,                              # sharpe non disponibile direttamente
            int(s.detail.n_trades_total),
        ))
    with self._connect() as conn:
        conn.executemany(
            """INSERT INTO individuals
               (run_id, generation, rank, individual_id, strategy_id, params_json,
                fitness, mean_oos_score, stdev_oos_score, max_drawdown, sharpe, n_trades)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def _top_individuals(self: CatalogDB, run_id: int, k: int) -> list[dict]:
    with self._connect() as conn:
        rows = conn.execute(
            """SELECT * FROM individuals WHERE run_id = ?
               ORDER BY fitness DESC LIMIT ?""",
            (run_id, k),
        ).fetchall()
        return [dict(r) for r in rows]


CatalogDB.insert_generation = _insert_generation     # type: ignore[attr-defined]
CatalogDB.top_individuals   = _top_individuals       # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_catalog_db.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/persistence/catalog_db.py tests/suite/test_catalog_db.py
git commit -m "feat(persistence): CatalogDB.insert_generation + top_individuals"
```

---

## Task 3: artifact_store — parquet equity + trades

**Files:**
- Create: `backtest_suite/persistence/artifact_store.py`
- Test: `tests/suite/test_artifact_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/suite/test_artifact_store.py`:

```python
"""Test artifact_store: save/load equity + trades + manifest."""
from pathlib import Path

import yaml

from backtest_suite.engine.types import Trade
from backtest_suite.persistence.artifact_store import ArtifactStore


def test_save_and_load_equity_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    curve = [{"ts": i, "equity": 1000.0 + i, "drawdown_pct": 0.0} for i in range(50)]
    store.save_equity(run_id=1, individual_id="G050-001", curve=curve)
    loaded = store.load_equity(run_id=1, individual_id="G050-001")
    assert len(loaded) == 50
    assert loaded[0]["equity"] == 1000.0
    assert loaded[-1]["ts"] == 49


def test_save_and_load_trades_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    trades = [
        Trade(side="long", entry_idx=1, exit_idx=5, entry=100.0, exit=110.0,
              pnl_pct=0.10, pnl_pct_gross=0.105, fee_paid=0.0052,
              reason="forced_close", partial_done=False),
        Trade(side="short", entry_idx=10, exit_idx=12, entry=110.0, exit=105.0,
              pnl_pct=0.045, pnl_pct_gross=0.05, fee_paid=0.0052,
              reason="stop_loss", partial_done=True),
    ]
    store.save_trades(run_id=2, individual_id="G050-002", trades=trades)
    loaded = store.load_trades(run_id=2, individual_id="G050-002")
    assert len(loaded) == 2
    assert loaded[0]["side"] == "long"
    assert loaded[1]["reason"] == "stop_loss"
    assert loaded[1]["partial_done"] is True


def test_save_and_load_manifest_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    manifest = {
        "suite_version": "0.1.0",
        "git_commit":    "abcd123",
        "seed":          42,
        "config":        {"kind": "ga", "symbol": "BTCUSDT"},
    }
    store.save_manifest(run_id=3, manifest=manifest)
    loaded = store.load_manifest(run_id=3)
    assert loaded["seed"] == 42
    assert loaded["config"]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_artifact_store.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement artifact_store.py**

Create `backtest_suite/persistence/artifact_store.py`:

```python
"""
artifact_store — parquet equity/trades + YAML manifest per run.

Layout: <runs_dir>/<NNNN>/{manifest.yaml, equity/<id>.parquet, trades/<id>.parquet}

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §10.1.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from backtest_suite.engine.types import Trade

_EQUITY_SCHEMA = pa.schema([
    pa.field("ts",           pa.int64()),
    pa.field("equity",       pa.float64()),
    pa.field("drawdown_pct", pa.float64()),
])

_TRADES_SCHEMA = pa.schema([
    pa.field("side",          pa.string()),
    pa.field("entry_idx",     pa.int64()),
    pa.field("exit_idx",      pa.int64()),
    pa.field("entry",         pa.float64()),
    pa.field("exit",          pa.float64()),
    pa.field("pnl_pct",       pa.float64()),
    pa.field("pnl_pct_gross", pa.float64()),
    pa.field("fee_paid",      pa.float64()),
    pa.field("reason",        pa.string()),
    pa.field("partial_done",  pa.bool_()),
])


def _run_dir(root: Path, run_id: int) -> Path:
    return Path(root) / f"{run_id:04d}"


class ArtifactStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def save_equity(self, run_id: int, individual_id: str,
                    curve: list[dict]) -> Path:
        d = _run_dir(self.runs_dir, run_id) / "equity"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{individual_id}.parquet"
        rows = [{"ts": int(r["ts"]),
                 "equity": float(r["equity"]),
                 "drawdown_pct": float(r["drawdown_pct"])} for r in curve]
        table = pa.Table.from_pylist(rows, schema=_EQUITY_SCHEMA)
        pq.write_table(table, path, compression="snappy")
        return path

    def load_equity(self, run_id: int, individual_id: str) -> list[dict]:
        path = _run_dir(self.runs_dir, run_id) / "equity" / f"{individual_id}.parquet"
        return pq.read_table(path, schema=_EQUITY_SCHEMA).to_pylist()

    def save_trades(self, run_id: int, individual_id: str,
                    trades: list[Trade]) -> Path:
        d = _run_dir(self.runs_dir, run_id) / "trades"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{individual_id}.parquet"
        rows = [{
            "side": t.side, "entry_idx": t.entry_idx, "exit_idx": t.exit_idx,
            "entry": t.entry, "exit": t.exit, "pnl_pct": t.pnl_pct,
            "pnl_pct_gross": t.pnl_pct_gross, "fee_paid": t.fee_paid,
            "reason": t.reason, "partial_done": t.partial_done,
        } for t in trades]
        table = pa.Table.from_pylist(rows, schema=_TRADES_SCHEMA)
        pq.write_table(table, path, compression="snappy")
        return path

    def load_trades(self, run_id: int, individual_id: str) -> list[dict]:
        path = _run_dir(self.runs_dir, run_id) / "trades" / f"{individual_id}.parquet"
        return pq.read_table(path, schema=_TRADES_SCHEMA).to_pylist()

    def save_manifest(self, run_id: int, manifest: dict) -> Path:
        d = _run_dir(self.runs_dir, run_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "manifest.yaml"
        path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
        return path

    def load_manifest(self, run_id: int) -> dict:
        path = _run_dir(self.runs_dir, run_id) / "manifest.yaml"
        return yaml.safe_load(path.read_text())
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_artifact_store.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/persistence/artifact_store.py tests/suite/test_artifact_store.py
git commit -m "feat(persistence): ArtifactStore equity/trades/manifest parquet+yaml"
```

---

## Task 4: config.py — pydantic models + YAML loader

**Files:**
- Create: `backtest_suite/config.py`
- Create: `tests/suite/fixtures/example_evolve.yaml`
- Create: `tests/suite/fixtures/example_grid.yaml`
- Test: `tests/suite/test_config.py`

- [ ] **Step 1: Write failing tests + fixtures**

Create `tests/suite/fixtures/example_evolve.yaml`:

```yaml
kind: ga
symbol: BTCUSDT
timeframe: 1h
range:
  since: "2024-01-01"
  until: "2024-06-30"
walk_forward:
  is_months: 3
  oos_months: 1
  step_months: 1
  min_trades_oos: 20
  max_drawdown_per_window: 0.30
ga:
  n_generations: 10
  pop_size: 20
  elite_size: 2
  mutation_rate: 0.15
  crossover_rate: 0.7
  tournament_k: 3
  species_quotas:
    ema_cross: 0.5
    rsi_mr: 0.25
    bb_breakout: 0.25
  mutate_strategy_id_prob: 0.05
  immigrants_rate: 0.05
  immigrants_every: 5
  seed: 42
fitness:
  variance_lambda: 0.5
execution:
  taker_fee: 0.0026
  slippage: 0.0005
  capital: 10000.0
persistence:
  save_top_k: 20
n_workers: 2
```

Create `tests/suite/fixtures/example_grid.yaml`:

```yaml
kind: grid
symbol: BTCUSDT
timeframe: 1h
range:
  since: "2024-01-01"
  until: "2024-06-30"
walk_forward:
  is_months: 3
  oos_months: 1
  step_months: 1
  min_trades_oos: 10
  max_drawdown_per_window: 0.30
grid:
  strategy_ids: ["ema_cross"]
  max_combos: 100
  strategy_params_grid:
    ema_cross:
      ema_fast: [5, 10]
      ema_slow: [20, 30]
      vwap_window: [100]
      vwap_filter: [0]
      direction: [2]
  risk_params_grid:
    stop_loss_pct: [0.03, 0.05]
    partial_exit_pct: [0.09]
    trailing_activate_pct: [0.06]
    trailing_stop_pct: [0.04]
    trailing_stop_tight_pct: [0.025]
execution:
  capital: 10000.0
persistence:
  save_top_k: 10
n_workers: 1
```

Create `tests/suite/test_config.py`:

```python
"""Test del config loader pydantic + YAML."""
from pathlib import Path

import pytest

from backtest_suite.config import load_run_config, RunConfig, EvolveSpec, GridSpec


def test_load_evolve_yaml_parses_correctly():
    cfg = load_run_config(Path("tests/suite/fixtures/example_evolve.yaml"))
    assert isinstance(cfg, RunConfig)
    assert cfg.kind == "ga"
    assert cfg.symbol == "BTCUSDT"
    assert cfg.timeframe == "1h"
    assert isinstance(cfg.ga, EvolveSpec)
    assert cfg.ga.pop_size == 20
    assert cfg.ga.species_quotas["ema_cross"] == 0.5


def test_load_grid_yaml_parses_correctly():
    cfg = load_run_config(Path("tests/suite/fixtures/example_grid.yaml"))
    assert cfg.kind == "grid"
    assert isinstance(cfg.grid, GridSpec)
    assert cfg.grid.strategy_ids == ["ema_cross"]
    assert cfg.grid.max_combos == 100


def test_load_rejects_unknown_kind(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("kind: woops\nsymbol: X\ntimeframe: 1h\nrange:\n  since: '2024-01-01'\n  until: '2024-06-30'\n")
    with pytest.raises(Exception):
        load_run_config(bad)


def test_load_rejects_ga_kind_missing_ga_section(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("""
kind: ga
symbol: BTCUSDT
timeframe: 1h
range:
  since: "2024-01-01"
  until: "2024-06-30"
walk_forward:
  is_months: 3
  oos_months: 1
  step_months: 1
  min_trades_oos: 10
  max_drawdown_per_window: 0.3
""")
    with pytest.raises(Exception):
        load_run_config(bad)
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_config.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement config.py**

Create `backtest_suite/config.py`:

```python
"""
config — pydantic models + YAML loader per i config dei run.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §11.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class RangeSpec(BaseModel):
    since: date | datetime
    until: date | datetime


class WalkForwardSpec(BaseModel):
    is_months:                int = Field(..., gt=0)
    oos_months:               int = Field(..., gt=0)
    step_months:              int = Field(..., gt=0)
    min_trades_oos:           int = Field(..., ge=0)
    max_drawdown_per_window:  float = Field(..., gt=0, le=1.0)


class EvolveSpec(BaseModel):
    n_generations:           int = Field(..., gt=0)
    pop_size:                int = Field(..., gt=0)
    elite_size:              int = Field(..., ge=0)
    mutation_rate:           float = Field(..., ge=0, le=1)
    crossover_rate:          float = Field(..., ge=0, le=1)
    tournament_k:            int = Field(..., gt=0)
    species_quotas:          dict[str, float]
    mutate_strategy_id_prob: float = Field(..., ge=0, le=1)
    immigrants_rate:         float = Field(..., ge=0, le=1)
    immigrants_every:        int = Field(..., ge=0)
    seed:                    int


class GridSpec(BaseModel):
    strategy_ids:         list[str]
    risk_params_grid:     dict[str, list[float]]
    strategy_params_grid: dict[str, dict[str, list[float]]] | None = None
    max_combos:           int = 5000


class FitnessSpec(BaseModel):
    variance_lambda: float = 0.5


class ExecutionSpec(BaseModel):
    taker_fee:    float = 0.0026
    slippage:     float = 0.0005
    latency_bars: int   = 1
    capital:      float = 10000.0
    allow_overlap: bool = False
    direction:    Literal["long", "short", "both"] = "both"


class PersistenceSpec(BaseModel):
    save_top_k: int = 20


class RunConfig(BaseModel):
    kind:        Literal["ga", "grid", "single"]
    symbol:      str
    timeframe:   Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    range:       RangeSpec
    walk_forward: WalkForwardSpec | None = None
    ga:          EvolveSpec | None = None
    grid:        GridSpec | None = None
    fitness:     FitnessSpec = FitnessSpec()
    execution:   ExecutionSpec = ExecutionSpec()
    persistence: PersistenceSpec = PersistenceSpec()
    n_workers:   int = 0

    @model_validator(mode="after")
    def _validate_kind_sections(self) -> "RunConfig":
        if self.kind == "ga" and self.ga is None:
            raise ValueError("kind=ga richiede la sezione 'ga' nel config")
        if self.kind == "grid" and self.grid is None:
            raise ValueError("kind=grid richiede la sezione 'grid' nel config")
        if self.kind in ("ga", "grid") and self.walk_forward is None:
            raise ValueError(f"kind={self.kind} richiede la sezione 'walk_forward'")
        return self


def load_run_config(path: Path) -> RunConfig:
    """Carica un config YAML e valida con pydantic."""
    raw = yaml.safe_load(Path(path).read_text())
    return RunConfig.model_validate(raw)
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_config.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/config.py tests/suite/test_config.py tests/suite/fixtures/example_evolve.yaml tests/suite/fixtures/example_grid.yaml
git commit -m "feat(config): pydantic RunConfig + YAML loader"
```

---

## Task 5: CLI — entry point + comando `fetch`

**Files:**
- Create: `backtest_suite/cli.py`
- Modify: `pyproject.toml` (entry point)
- Test: `tests/suite/test_cli.py`

- [ ] **Step 1: Write failing test for CLI fetch**

Create `tests/suite/test_cli.py`:

```python
"""Test della CLI hermes-bt (argparse)."""
import sys
from unittest.mock import patch

import pytest

from backtest_suite.cli import build_parser, main


def test_parser_recognizes_fetch_command():
    p = build_parser()
    args = p.parse_args(["fetch", "BTCUSDT", "1h",
                         "--since", "2024-01-01", "--until", "2024-01-02"])
    assert args.command == "fetch"
    assert args.symbol == "BTCUSDT"
    assert args.timeframe == "1h"


@patch("backtest_suite.data_lake.fetch")
def test_main_fetch_calls_data_lake(mock_fetch, tmp_path):
    mock_fetch.return_value = 10
    rc = main([
        "fetch", "BTCUSDT", "1h",
        "--since", "2024-01-01", "--until", "2024-01-02",
        "--root", str(tmp_path),
    ])
    assert rc == 0
    mock_fetch.assert_called_once()


def test_parser_errors_on_unknown_command():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["nonexistent"])
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_cli.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement CLI fetch**

Create `backtest_suite/cli.py`:

```python
"""
hermes-bt — CLI per la backtest_suite.

Comandi: fetch, run, grid, evolve, ui.
Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §11.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("hermes-bt")


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes-bt",
                                description="Backtest suite per hermes-trading.")
    sub = p.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("fetch", help="Scarica OHLCV nel data lake locale.")
    pf.add_argument("symbol", type=str)
    pf.add_argument("timeframe", choices=["1m", "5m", "15m", "1h", "4h", "1d"])
    pf.add_argument("--since", required=True, type=_parse_date)
    pf.add_argument("--until", required=True, type=_parse_date)
    pf.add_argument("--force-refresh", action="store_true")
    pf.add_argument("--root", type=Path, default=Path("data/ohlcv"))

    pr = sub.add_parser("run",    help="Esegui un singolo backtest da config.")
    pr.add_argument("config", type=Path)

    pg = sub.add_parser("grid",   help="Esegui una grid search da config.")
    pg.add_argument("config", type=Path)

    pe = sub.add_parser("evolve", help="Esegui un genetic algorithm da config.")
    pe.add_argument("config", type=Path)

    pu = sub.add_parser("ui",     help="(Plan D) Avvia FastAPI UI server.")
    pu.add_argument("--port", type=int, default=8765)
    pu.add_argument("--open", action="store_true")

    return p


def _cmd_fetch(args) -> int:
    from backtest_suite import data_lake
    log.info("Fetching %s %s [%s → %s]",
             args.symbol, args.timeframe, args.since, args.until)
    n = data_lake.fetch(args.symbol, args.timeframe, args.since, args.until,
                        force_refresh=args.force_refresh, root=args.root)
    print(f"Scaricate {n} candele.")
    return 0


def _cmd_not_yet(args) -> int:
    print(f"Comando '{args.command}' non ancora implementato (vedi Plan C/D).",
          file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "fetch":  _cmd_fetch,
        "run":    _cmd_not_yet,
        "grid":   _cmd_not_yet,
        "evolve": _cmd_not_yet,
        "ui":     _cmd_not_yet,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add entry point to pyproject.toml**

Modify `pyproject.toml`: add (or extend) the `[project.scripts]` section:

```toml
[project.scripts]
hermes-bt = "backtest_suite.cli:main"
```

- [ ] **Step 5: Re-install to register entry point**

Run: `uv sync --all-extras`
Expected: ok, the script `hermes-bt` is now available via `uv run hermes-bt`.

- [ ] **Step 6: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 7: Smoke test the CLI**

Run: `uv run hermes-bt --help`
Expected: show all 5 subcommands.

- [ ] **Step 8: Commit**

```bash
git add backtest_suite/cli.py pyproject.toml uv.lock tests/suite/test_cli.py
git commit -m "feat(cli): hermes-bt entry point + comando fetch"
```

---

## Task 6: CLI — comandi `run` / `grid` / `evolve` + RunOrchestrator

**Files:**
- Create: `backtest_suite/orchestrator.py` (nuovo modulo, glue tra config + optimizer + persistence)
- Modify: `backtest_suite/cli.py`
- Modify: `tests/suite/test_cli.py`

- [ ] **Step 1: Write failing test for orchestrator**

Append to `tests/suite/test_cli.py`:

```python
import math

from backtest_suite.config import load_run_config
from backtest_suite.orchestrator import RunOrchestrator


def test_orchestrator_evolve_writes_run_to_db(tmp_path):
    # Sintetic candles direttamente in-memory (no data_lake fetch)
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})

    cfg = load_run_config(__import__("pathlib").Path("tests/suite/fixtures/example_evolve.yaml"))
    # Override walk_forward + ga per tempi compatibili col test
    cfg.walk_forward.is_months = 2  # type: ignore[attr-defined]
    cfg.walk_forward.oos_months = 1  # type: ignore[attr-defined]
    cfg.walk_forward.step_months = 1  # type: ignore[attr-defined]
    cfg.walk_forward.min_trades_oos = 0  # type: ignore[attr-defined]
    cfg.walk_forward.max_drawdown_per_window = 1.0  # type: ignore[attr-defined]
    cfg.ga.n_generations = 2  # type: ignore[attr-defined]
    cfg.ga.pop_size = 4  # type: ignore[attr-defined]
    cfg.n_workers = 1  # type: ignore[attr-defined]

    orch = RunOrchestrator(
        config=cfg,
        candles=candles,
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
    )
    result = orch.evolve()
    assert result["run_id"] >= 1
    assert result["status"] == "finished"

    # Verifica DB
    from backtest_suite.persistence.catalog_db import CatalogDB
    db = CatalogDB(tmp_path / "catalog.db")
    runs = db.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "finished"
    assert runs[0]["n_generations"] == 2

    top = db.top_individuals(runs[0]["id"], k=5)
    assert len(top) == 4  # pop_size=4
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_cli.py -v -k orchestrator`
Expected: ImportError.

- [ ] **Step 3: Implement orchestrator.py**

Create `backtest_suite/orchestrator.py`:

```python
"""
orchestrator — glue tra config + optimizer + persistence.

Espone evolve() e grid() che caricano candele, lanciano il run,
persistono metadata e artefatti dei top-K, e ritornano un summary.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §10.3.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backtest_suite.config import RunConfig
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.engine import run_backtest
from backtest_suite.optimizer.ga import evolve
from backtest_suite.optimizer.grid import grid_search
from backtest_suite.optimizer.types import (
    GAConfig,
    GridConfig,
    WalkForwardConfig,
)
from backtest_suite.persistence.artifact_store import ArtifactStore
from backtest_suite.persistence.catalog_db import CatalogDB
from backtest_suite.strategies import STRATEGY_REGISTRY

log = logging.getLogger(__name__)


def _git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                      stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(["git", "status", "--porcelain"],
                                      stderr=subprocess.DEVNULL).decode()
        return bool(out.strip())
    except Exception:
        return False


def _build_execution(cfg: RunConfig) -> ExecutionConfig:
    e = cfg.execution
    return ExecutionConfig(taker_fee=e.taker_fee, slippage=e.slippage,
                           latency_bars=e.latency_bars, capital=e.capital,
                           allow_overlap=e.allow_overlap, direction=e.direction)


def _build_wf(cfg: RunConfig) -> WalkForwardConfig:
    w = cfg.walk_forward
    assert w is not None
    return WalkForwardConfig(
        is_months=w.is_months, oos_months=w.oos_months, step_months=w.step_months,
        min_trades_oos=w.min_trades_oos,
        max_drawdown_per_window=w.max_drawdown_per_window,
        variance_lambda=cfg.fitness.variance_lambda,
    )


def _build_ga_config(cfg: RunConfig) -> GAConfig:
    g = cfg.ga
    assert g is not None
    return GAConfig(
        n_generations=g.n_generations, pop_size=g.pop_size,
        elite_size=g.elite_size, mutation_rate=g.mutation_rate,
        crossover_rate=g.crossover_rate, tournament_k=g.tournament_k,
        species_quotas=dict(g.species_quotas),
        mutate_strategy_id_prob=g.mutate_strategy_id_prob,
        immigrants_rate=g.immigrants_rate, immigrants_every=g.immigrants_every,
        seed=g.seed,
    )


def _build_grid_config(cfg: RunConfig) -> GridConfig:
    g = cfg.grid
    assert g is not None
    return GridConfig(
        strategy_ids=list(g.strategy_ids),
        risk_params_grid=dict(g.risk_params_grid),
        strategy_params_grid=dict(g.strategy_params_grid) if g.strategy_params_grid else None,
        max_combos=g.max_combos,
    )


def _individual_id(generation: int, rank: int) -> str:
    return f"G{generation:03d}-{rank:03d}"


class RunOrchestrator:
    def __init__(self, config: RunConfig, candles: list[dict],
                 db_path: Path, runs_dir: Path) -> None:
        self.config   = config
        self.candles  = candles
        self.db       = CatalogDB(db_path)
        self.store    = ArtifactStore(runs_dir)
        self.db.init_schema()

    # ---------- helpers comuni ----------
    def _create_run_row(self, kind: str) -> int:
        run_id = self.db.create_run(
            kind=kind, symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            config_path="",   # aggiornato dopo
        )
        manifest = {
            "suite_version": "0.1.0",
            "git_commit":    _git_commit(),
            "git_dirty":     _git_dirty(),
            "python":        sys.version.split()[0],
            "started_at":    datetime.now(timezone.utc).isoformat(),
            "config":        self.config.model_dump(mode="json"),
        }
        self.store.save_manifest(run_id, manifest)
        self.db.update_run_status(
            run_id, status="running",
            config_path=f"runs/{run_id:04d}/manifest.yaml",
        )
        return run_id

    def _save_top_artifacts(self, run_id: int, scored_sorted: list,
                            save_top_k: int) -> None:
        execution = _build_execution(self.config)
        for rank, s in enumerate(scored_sorted[:save_top_k], start=1):
            ind = s.individual
            cls = STRATEGY_REGISTRY[ind.strategy_id]
            strat = cls(ind.strategy_params)
            from hermes_trading._engine_core import RiskConfig
            risk = RiskConfig(**ind.risk_params)
            result = run_backtest(self.candles, strat, risk, execution)
            ind_id = _individual_id(0, rank)
            self.store.save_equity(run_id, ind_id, result.equity_curve)
            self.store.save_trades(run_id, ind_id, result.trades)

    # ---------- evolve ----------
    def evolve(self) -> dict:
        run_id = self._create_run_row("ga")
        execution = _build_execution(self.config)
        wf  = _build_wf(self.config)
        gcfg = _build_ga_config(self.config)

        def _no_stop() -> bool:
            return False

        log.info("evolve run_id=%d started", run_id)
        result = evolve(gcfg, self.candles, wf, execution,
                        stop_flag=_no_stop, progress_callback=lambda _ev: None,
                        n_workers=self.config.n_workers)

        # Persist final generation scoring (riusiamo l'ultimo history.event come ranking?)
        # Per semplicità, ri-valutiamo la popolazione finale: troppo lento → no.
        # Persistiamo invece tutta la history: ogni generation ha best_individual ma non
        # l'intera popolazione. Per la persistenza completa serve hookare evolve().
        # Step 4 (più sotto) aggiunge il callback per persistenza per-generation.

        self.db.update_run_status(
            run_id, status=result.status,
            best_fitness=float(result.best_fitness),
            best_individual=__import__("json").dumps({
                "strategy_id":     result.best_individual.strategy_id,
                "strategy_params": result.best_individual.strategy_params,
                "risk_params":     result.best_individual.risk_params,
            }),
            n_generations=result.n_generations_completed,
        )
        return {"run_id": run_id, "status": result.status,
                "best_fitness": result.best_fitness}

    # ---------- grid ----------
    def grid(self) -> dict:
        run_id = self._create_run_row("grid")
        execution = _build_execution(self.config)
        wf  = _build_wf(self.config)
        gcfg = _build_grid_config(self.config)

        result = grid_search(gcfg, self.candles, wf, execution,
                             stop_flag=lambda: False,
                             progress_callback=lambda _ev: None,
                             n_workers=self.config.n_workers)

        self.db.insert_generation(run_id, generation=0, scored=result.all_scored)
        self._save_top_artifacts(run_id, result.all_scored, self.config.persistence.save_top_k)
        self.db.update_run_status(
            run_id, status=result.status,
            best_fitness=float(result.best_fitness),
            best_individual=__import__("json").dumps({
                "strategy_id":     result.best_individual.strategy_id,
                "strategy_params": result.best_individual.strategy_params,
                "risk_params":     result.best_individual.risk_params,
            }),
            n_individuals=len(result.all_scored),
        )
        return {"run_id": run_id, "status": result.status,
                "best_fitness": result.best_fitness,
                "n_individuals": len(result.all_scored)}
```

- [ ] **Step 4: Add per-generation persistence hook to evolve()**

Modify `backtest_suite/orchestrator.py` — replace the `evolve()` method's persistence pattern by passing a `progress_callback` that persists each generation. Replace the `evolve` method body with:

```python
    def evolve(self) -> dict:
        import json
        run_id = self._create_run_row("ga")
        execution = _build_execution(self.config)
        wf   = _build_wf(self.config)
        gcfg = _build_ga_config(self.config)

        # Persistenza per-generation: serve l'intera popolazione scored.
        # evolve() come è scritto in Plan B emette GenerationEvent ma non la
        # popolazione completa. Adattiamo: il callback aggiorna SOLO i metadati
        # del run; la popolazione completa la persistiamo solo a fine run via
        # re-evaluation degli individui memorizzati nell'history.

        last_event_gen = {"n": 0}
        def _cb(event) -> None:
            last_event_gen["n"] = event.generation + 1

        result = evolve(gcfg, self.candles, wf, execution,
                        stop_flag=lambda: False, progress_callback=_cb,
                        n_workers=self.config.n_workers)

        # Persistenza del best per generation (single row each)
        from backtest_suite.optimizer.fitness import score_individual
        from backtest_suite.optimizer.types import Scored
        for ev in result.history:
            detail = score_individual(ev.best_individual, self.candles, wf, execution)
            scored = Scored(individual=ev.best_individual, fitness=ev.best_fitness, detail=detail)
            self.db.insert_generation(run_id, generation=ev.generation, scored=[scored])

        # Artefatti del best overall
        self._save_top_artifacts(run_id, [Scored(
            individual=result.best_individual, fitness=result.best_fitness,
            detail=score_individual(result.best_individual, self.candles, wf, execution),
        )], self.config.persistence.save_top_k)

        self.db.update_run_status(
            run_id, status=result.status,
            best_fitness=float(result.best_fitness),
            best_individual=json.dumps({
                "strategy_id":     result.best_individual.strategy_id,
                "strategy_params": result.best_individual.strategy_params,
                "risk_params":     result.best_individual.risk_params,
            }),
            n_generations=result.n_generations_completed,
            n_individuals=result.n_generations_completed * gcfg.pop_size,
        )
        return {"run_id": run_id, "status": result.status,
                "best_fitness": result.best_fitness}
```

- [ ] **Step 5: Wire CLI commands to orchestrator**

Modify `backtest_suite/cli.py` — replace `_cmd_not_yet` references for `run/grid/evolve` and add:

```python
def _cmd_evolve(args) -> int:
    from backtest_suite import data_lake
    from backtest_suite.config import load_run_config
    from backtest_suite.orchestrator import RunOrchestrator
    cfg = load_run_config(args.config)
    candles = data_lake.load(cfg.symbol, cfg.timeframe,
                             since=cfg.range.since, until=cfg.range.until)
    orch = RunOrchestrator(
        config=cfg, candles=candles,
        db_path=Path("data/backtests/catalog.db"),
        runs_dir=Path("data/backtests/runs"),
    )
    out = orch.evolve()
    print(out)
    return 0 if out["status"] == "finished" else 1


def _cmd_grid(args) -> int:
    from backtest_suite import data_lake
    from backtest_suite.config import load_run_config
    from backtest_suite.orchestrator import RunOrchestrator
    cfg = load_run_config(args.config)
    candles = data_lake.load(cfg.symbol, cfg.timeframe,
                             since=cfg.range.since, until=cfg.range.until)
    orch = RunOrchestrator(
        config=cfg, candles=candles,
        db_path=Path("data/backtests/catalog.db"),
        runs_dir=Path("data/backtests/runs"),
    )
    out = orch.grid()
    print(out)
    return 0 if out["status"] == "finished" else 1


def _cmd_run(args) -> int:
    # Singolo backtest: usa primo individuo della grid (caso degenere) o richiede
    # specifica via config separato. Per il momento riusa il path grid con max_combos=1.
    print("'run' singolo: usa 'grid' con max_combos=1 per ora.", file=sys.stderr)
    return 2
```

Then in `main()`, update the handlers map:

```python
    handlers = {
        "fetch":  _cmd_fetch,
        "run":    _cmd_run,
        "grid":   _cmd_grid,
        "evolve": _cmd_evolve,
        "ui":     _cmd_not_yet,        # arriva in Plan D
    }
```

- [ ] **Step 6: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_cli.py -v`
Expected: orchestrator test passes.

- [ ] **Step 7: Full suite smoke**

Run: `uv run pytest tests/suite -v`
Expected: tutti passano.

- [ ] **Step 8: Commit**

```bash
git add backtest_suite/orchestrator.py backtest_suite/cli.py tests/suite/test_cli.py
git commit -m "feat(cli): comandi evolve/grid via RunOrchestrator + persistenza"
```

---

## Self-Review

**Spec coverage** (Plan C):
- §10.1 layout disco runs/{NNNN}/ con manifest + equity/trades parquet ✓
- §10.2 schema SQLite runs + individuals + indici ✓
- §10.3 write pattern batch per generazione (`insert_generation`) ✓
- §10.4 top-K configurabile (`persistence.save_top_k`) ✓
- §10.5 API CatalogDB e ArtifactStore ✓
- §11 CLI hermes-bt con fetch/evolve/grid + manifest riproducibilità ✓
- §13 manifest con git_commit, python, seed, config ✓

**Out of scope per Plan C** (Plan D):
- `hermes-bt ui` → Plan D (FastAPI server)
- WebSocket per live monitoring → Plan D
- Promote endpoint → Plan D
- E2E integration test via REST → Plan D

**Placeholder scan**: nessuno.

**Type consistency**:
- `RiskConfig` ricostruito da `risk_params` dict in `_save_top_artifacts` — chiavi coincidono con quelle del Plan A.
- `RunConfig.model_dump(mode="json")` produce un dict JSON-safe per il manifest YAML.
- `ExecutionConfig` immutabile (frozen=True) — `_build_execution` la istanzia fresca per run.

**Critical path**: Task 6 (orchestrator) — è il punto di integrazione tra config, optimizer e persistence. Se rotto, niente persistenza.

**Known limitations** (documentate nei prossimi plan):
- `evolve()` persiste solo `best_individual` per generation (non l'intera popolazione). Per la popolazione completa serve un hook più ricco in `evolve()` che invii lo `scored: list[Scored]` invece di `GenerationEvent`. Da considerare in Plan D quando lo stream WebSocket invierà popolazione completa.

---

**Plan C completo, salvato in** `docs/superpowers/plans/2026-05-27-backtest-suite-plan-C-persistence-cli.md`.

Plan D (server FastAPI + frontend + integration tests) viene scritto successivamente.

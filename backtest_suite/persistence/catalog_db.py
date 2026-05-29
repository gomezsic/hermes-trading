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

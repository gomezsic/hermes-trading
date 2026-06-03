# backtest_suite/arena/store.py
"""Persistenza dell'arena: SQLite WAL (tabelle proprie) + manifest via ArtifactStore.

Non modifica catalog_db.py: l'arena apre il file DB con lo stesso pattern WAL e
gestisce solo le proprie tabelle, rispettando l'invariante "arena → backtest_suite,
mai il contrario" (spec §3, §8).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backtest_suite.persistence.artifact_store import ArtifactStore
from backtest_suite.arena.types import ArenaConfig, LeaderboardRow


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS arena_runs (
        id            INTEGER PRIMARY KEY,
        status        TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        timeframe     TEXT NOT NULL,
        seed          INTEGER NOT NULL,
        n_generations INTEGER NOT NULL,
        started_at    TEXT NOT NULL,
        finished_at   TEXT,
        best_agent    TEXT,
        best_score    REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arena_individuals (
        run_id          INTEGER NOT NULL,
        generation      INTEGER NOT NULL,
        agent_id        TEXT NOT NULL,
        archetype_id    TEXT NOT NULL,
        proposer_id     TEXT NOT NULL,
        params_json     TEXT NOT NULL,
        composite_score REAL NOT NULL,
        fitness         REAL NOT NULL,
        quality         REAL NOT NULL,
        consistency     REAL NOT NULL,
        max_drawdown    REAL NOT NULL,
        n_trades        INTEGER NOT NULL,
        verdict         TEXT NOT NULL,
        PRIMARY KEY (run_id, generation, agent_id),
        FOREIGN KEY (run_id) REFERENCES arena_runs(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_arena_ind_score "
    "ON arena_individuals(run_id, composite_score DESC)",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArenaStore:
    def __init__(self, db_path: Path, runs_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.store = ArtifactStore(runs_dir)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    def create_run(self, cfg: ArenaConfig) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """INSERT INTO arena_runs
                   (status, symbol, timeframe, seed, n_generations, started_at)
                   VALUES ('running', ?, ?, ?, ?, ?)""",
                (cfg.symbol, cfg.timeframe, cfg.seed, cfg.n_generations, _now_iso()),
            )
            return int(cur.lastrowid)

    def insert_generation(self, run_id: int, generation: int,
                          rows: list[LeaderboardRow]) -> None:
        payload = [
            (
                run_id, generation, r.agent_id, r.archetype_id, r.proposer_id,
                json.dumps({
                    "strategy_id":     r.candidate.individual.strategy_id,
                    "strategy_params": r.candidate.individual.strategy_params,
                    "risk_params":     r.candidate.individual.risk_params,
                }, sort_keys=True),
                float(r.composite_score), float(r.candidate.fitness),
                float(r.candidate.quality), float(r.candidate.consistency),
                float(r.candidate.drawdown), int(r.candidate.n_trades), r.verdict,
            )
            for r in rows
        ]
        with closing(self._connect()) as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO arena_individuals
                   (run_id, generation, agent_id, archetype_id, proposer_id,
                    params_json, composite_score, fitness, quality, consistency,
                    max_drawdown, n_trades, verdict)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                payload,
            )

    def finish_run(self, run_id: int, rows: list[LeaderboardRow]) -> None:
        best = max(rows, key=lambda r: r.composite_score) if rows else None
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE arena_runs
                   SET status='finished', finished_at=?, best_agent=?, best_score=?
                   WHERE id=?""",
                (_now_iso(),
                 best.agent_id if best else None,
                 float(best.composite_score) if best else None,
                 run_id),
            )

    def top_individuals(self, run_id: int, k: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM arena_individuals WHERE run_id=?
                   ORDER BY composite_score DESC LIMIT ?""",
                (run_id, k),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM arena_runs WHERE id=?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

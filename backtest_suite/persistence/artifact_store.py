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

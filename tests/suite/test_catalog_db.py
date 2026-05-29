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

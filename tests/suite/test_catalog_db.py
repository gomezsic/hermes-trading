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

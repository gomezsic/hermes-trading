"""Test REST endpoints della backtest_suite."""
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from backtest_suite.server.app import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
        data_root=tmp_path / "ohlcv",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_healthcheck(client: httpx.AsyncClient):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_list_runs_empty(client: httpx.AsyncClient):
    r = await client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_run_detail_404(client: httpx.AsyncClient):
    r = await client.get("/api/runs/9999")
    assert r.status_code == 404


def _seed_run(client: httpx.AsyncClient) -> int:
    """Crea un run direttamente nel DB tramite app.state (helper sincrono)."""
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    run_id = db.create_run(kind="ga", symbol="BTCUSDT", timeframe="1h",
                           config_path="runs/0001/manifest.yaml")
    db.update_run_status(run_id, status="finished",
                         best_fitness=1.5, n_generations=10, n_individuals=100)
    return run_id


@pytest.mark.asyncio
async def test_run_detail_returns_run_plus_top(client: httpx.AsyncClient):
    run_id = _seed_run(client)
    r = await client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["id"] == run_id
    assert body["top"] == []     # niente individuals seedato


from unittest.mock import patch


@pytest.mark.asyncio
async def test_data_coverage_empty(client: httpx.AsyncClient):
    r = await client.get("/api/data/coverage", params={"symbol": "BTCUSDT", "timeframe": "1h"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_candles"] == 0


@pytest.mark.asyncio
@patch("backtest_suite.data_lake.fetch")
async def test_data_fetch_invokes_data_lake(mock_fetch, client: httpx.AsyncClient):
    mock_fetch.return_value = 42
    r = await client.post("/api/data/fetch", json={
        "symbol": "BTCUSDT", "timeframe": "1h",
        "since": "2024-01-01T00:00:00", "until": "2024-01-02T00:00:00",
    })
    assert r.status_code == 200
    assert r.json()["n_written"] == 42
import math


@pytest_asyncio.fixture
async def client_with_data(tmp_path) -> httpx.AsyncClient:
    """Client con candele sintetiche in-memory tramite override del loader."""
    app = create_app(
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
        data_root=tmp_path / "ohlcv",
    )
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    app.state.candles_override = candles
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_post_runs_starts_evolve_and_returns_id(client_with_data: httpx.AsyncClient):
    payload = {
        "kind": "ga", "symbol": "BTCUSDT", "timeframe": "1h",
        "range": {"since": "2024-01-01", "until": "2024-06-30"},
        "walk_forward": {
            "is_months": 2, "oos_months": 1, "step_months": 1,
            "min_trades_oos": 0, "max_drawdown_per_window": 1.0,
        },
        "ga": {
            "n_generations": 2, "pop_size": 4, "elite_size": 1,
            "mutation_rate": 0.2, "crossover_rate": 0.5, "tournament_k": 2,
            "species_quotas": {"ema_cross": 1.0},
            "mutate_strategy_id_prob": 0.0, "immigrants_rate": 0.0,
            "immigrants_every": 999, "seed": 42,
        },
        "n_workers": 1,
    }
    r = await client_with_data.post("/api/runs", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert "run_id" in body
    assert body["status"] in ("queued", "running")


@pytest.mark.asyncio
async def test_post_runs_stop_returns_ok(client_with_data: httpx.AsyncClient):
    r = await client_with_data.post("/api/runs/1/stop")
    # 200 anche se il run non esiste (non blocchiamo idempotency)
    assert r.status_code in (200, 404)


@pytest.mark.asyncio
async def test_promote_writes_to_strategy_yaml(client: httpx.AsyncClient, tmp_path):
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    run_id = db.create_run("ga", "BTCUSDT", "1h", "runs/0001/manifest.yaml")
    from backtest_suite.optimizer.types import IndividualConfig, FitnessResult, Scored
    ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={"ema_fast": 12, "ema_slow": 35, "vwap_window": 150,
                         "vwap_filter": 0, "direction": 2},
        risk_params={"stop_loss_pct": 0.035, "partial_exit_pct": 0.10,
                     "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                     "trailing_stop_tight_pct": 0.02},
    )
    detail = FitnessResult(fitness=1.5, per_window_scores=[1.5], mean_score=1.5,
                           stdev_score=0.0, max_drawdown_observed=0.1,
                           n_trades_total=30, failed=False, failure_reason=None)
    db.insert_generation(run_id, 0, [Scored(individual=ind, fitness=1.5, detail=detail)])

    target = tmp_path / "strategy.yaml"
    target.write_text("version: '06'\nstop_loss_pct: 3.0\n")

    r = await client.post(f"/api/runs/{run_id}/individuals/G000-001/promote",
                          json={"target_path": str(target)})
    assert r.status_code == 200
    body = r.json()
    assert "diff" in body

    import yaml as _yaml
    new_yaml = _yaml.safe_load(target.read_text())
    assert new_yaml["stop_loss_pct"] == 3.5    # 0.035 * 100
    assert new_yaml["ema_fast"] == 12

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

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

"""
End-to-end: POST /api/runs (ga) → polling fino a finished → GET /api/runs/{id}
→ verifica top_individuals popolato → POST promote → verifica strategy.yaml scritto.
Versione async (httpx.AsyncClient + asyncio.sleep) per httpx 0.28 e per far
progredire il task in background sullo stesso event loop del test.
"""
import asyncio
import math
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from backtest_suite.server.app import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    app = create_app(db_path=tmp_path / "catalog.db",
                     runs_dir=tmp_path / "runs",
                     data_root=tmp_path / "ohlcv")
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    app.state.candles_override = candles
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_e2e_evolve_and_promote(client: httpx.AsyncClient, tmp_path: Path):
    # 1. Start run
    payload = {
        "kind": "ga", "symbol": "BTCUSDT", "timeframe": "1h",
        "range": {"since": "2024-01-01", "until": "2024-06-30"},
        "walk_forward": {"is_months": 2, "oos_months": 1, "step_months": 1,
                         "min_trades_oos": 0, "max_drawdown_per_window": 1.0},
        "ga": {"n_generations": 2, "pop_size": 4, "elite_size": 1,
               "mutation_rate": 0.2, "crossover_rate": 0.5, "tournament_k": 2,
               "species_quotas": {"ema_cross": 1.0},
               "mutate_strategy_id_prob": 0.0, "immigrants_rate": 0.0,
               "immigrants_every": 999, "seed": 42},
        "n_workers": 1,
    }
    r = await client.post("/api/runs", json=payload)
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    # 2. Poll until finished (max ~30 sec)
    finished = False
    for _ in range(60):
        await asyncio.sleep(0.5)
        rr = await client.get(f"/api/runs/{run_id}")
        if rr.json()["run"]["status"] in ("finished", "failed", "stopped"):
            finished = True
            break
    assert finished, "run non terminato in tempo"

    # 3. Verify top individuals
    detail = (await client.get(f"/api/runs/{run_id}")).json()
    assert detail["run"]["status"] == "finished"
    assert len(detail["top"]) >= 1
    top = detail["top"][0]

    # 4. Promote
    target = tmp_path / "strategy.yaml"
    target.write_text("version: '06'\n")
    pr = await client.post(f"/api/runs/{run_id}/individuals/{top['individual_id']}/promote",
                           json={"target_path": str(target)})
    assert pr.status_code == 200

    import yaml as _yaml
    new = _yaml.safe_load(target.read_text())
    assert "stop_loss_pct" in new

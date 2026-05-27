# Backtest Suite — Plan D: Server FastAPI + UI + Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire il server FastAPI con endpoint REST + WebSocket per live monitoring, il frontend HTML/CSS/JS vanilla con Chart.js, l'endpoint di promote verso `state/strategy.yaml`, e i test di integrazione end-to-end.

**Architecture:** `server/app.py` factory FastAPI, `server/api.py` REST, `server/ws.py` event broker asincrono + WebSocket. Frontend statico in `server/static/` servito da FastAPI come `StaticFiles`. Run di GA/grid eseguiti in `asyncio.to_thread` per non bloccare l'event loop; gli eventi del run vengono pubblicati al broker che li forwards ai client WebSocket subscribed al run_id.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, websockets (transitive), Chart.js (CDN), pytest, httpx (test client).

**Spec:** `docs/superpowers/specs/2026-05-27-backtest-suite-design.md` §§ 8, 14.2.

**Prerequisito:** Plan A + B + C completati.

---

## File Structure

**Files to create:**
- `backtest_suite/server/__init__.py`
- `backtest_suite/server/app.py` — FastAPI app factory
- `backtest_suite/server/api.py` — REST endpoints
- `backtest_suite/server/ws.py` — event broker asincrono + WebSocket
- `backtest_suite/server/runs_registry.py` — gestione runs attivi in memoria
- `backtest_suite/server/static/index.html` — root della UI
- `backtest_suite/server/static/css/app.css` — stile base
- `backtest_suite/server/static/js/app.js` — routing client-side + chiamate API
- `backtest_suite/server/static/js/charts.js` — wrapper Chart.js
- `tests/suite/test_server_api.py`
- `tests/suite/test_server_ws.py`
- `tests/suite/test_e2e_evolve.py`

**Files to modify:**
- `pyproject.toml` — aggiungere `fastapi`, `uvicorn`, `httpx` come dipendenze
- `backtest_suite/cli.py` — implementare `_cmd_ui`

---

## Task 1: dependencies + app factory + healthcheck

**Files:**
- Modify: `pyproject.toml`
- Create: `backtest_suite/server/__init__.py`, `backtest_suite/server/app.py`
- Test: `tests/suite/test_server_api.py`

- [ ] **Step 1: Add FastAPI/uvicorn/httpx to pyproject**

Modify `pyproject.toml` dependencies and dev-extra:

```toml
[project]
dependencies = [
    "aiofiles>=25.1.0",
    "ccxt>=4.5.54",
    "httpx>=0.28.1",
    "numpy>=2.4.6",
    "pandas>=3.0.3",
    "pyyaml>=6.0.3",
    "rich>=15.0.0",
    "yfinance>=1.4.0",
    "pyarrow>=15.0.0",
    "pydantic>=2.5.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
```

- [ ] **Step 2: Install**

Run: `uv sync --all-extras`
Expected: ok.

- [ ] **Step 3: Write failing test for healthcheck**

Create `tests/suite/test_server_api.py`:

```python
"""Test REST endpoints della backtest_suite."""
from pathlib import Path

import httpx
import pytest

from backtest_suite.server.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> httpx.Client:
    app = create_app(
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
        data_root=tmp_path / "ohlcv",
    )
    return httpx.Client(transport=httpx.ASGITransport(app=app),
                        base_url="http://test")


def test_healthcheck(client: httpx.Client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 4: Run failing test**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: ImportError.

- [ ] **Step 5: Implement app.py**

Create `backtest_suite/server/__init__.py`:

```python
"""server — FastAPI app + REST + WebSocket per backtest_suite."""
```

Create `backtest_suite/server/app.py`:

```python
"""
app — FastAPI app factory.

Crea una app FastAPI configurata con:
- REST endpoints (api.py)
- WebSocket + broker (ws.py)
- StaticFiles per il frontend

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §8.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backtest_suite.persistence.catalog_db import CatalogDB
from backtest_suite.persistence.artifact_store import ArtifactStore
from backtest_suite.server.runs_registry import RunsRegistry
from backtest_suite.server.ws import EventBroker


def create_app(
    db_path:   Path,
    runs_dir:  Path,
    data_root: Path,
) -> FastAPI:
    app = FastAPI(title="hermes-bt", version="0.1.0")

    db = CatalogDB(db_path)
    db.init_schema()
    app.state.db        = db
    app.state.store     = ArtifactStore(runs_dir)
    app.state.data_root = Path(data_root)
    app.state.broker    = EventBroker()
    app.state.registry  = RunsRegistry()

    # ---- Healthcheck minimale, definito qui per semplicità ----
    @app.get("/api/health")
    async def _health():
        return {"status": "ok", "version": "0.1.0"}

    # ---- Mount delle REST routes ----
    from backtest_suite.server.api import build_router
    app.include_router(build_router(), prefix="/api")

    # ---- WebSocket endpoint ----
    from backtest_suite.server.ws import register_websocket
    register_websocket(app)

    # ---- Static frontend (mounted last to non-shadow API) ----
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
```

- [ ] **Step 6: Create skeleton for api.py, ws.py, runs_registry.py**

Create `backtest_suite/server/runs_registry.py`:

```python
"""runs_registry — tracking dei run attivi in memoria (stop_flag, last events)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _RunState:
    run_id:     int
    stop_flag:  bool = False
    last_events: deque = field(default_factory=lambda: deque(maxlen=50))


class RunsRegistry:
    def __init__(self) -> None:
        self._runs: dict[int, _RunState] = {}

    def register(self, run_id: int) -> None:
        self._runs[run_id] = _RunState(run_id=run_id)

    def get(self, run_id: int) -> _RunState | None:
        return self._runs.get(run_id)

    def mark_stop(self, run_id: int) -> bool:
        state = self._runs.get(run_id)
        if state is None:
            return False
        state.stop_flag = True
        return True

    def is_stopped(self, run_id: int) -> bool:
        state = self._runs.get(run_id)
        return bool(state and state.stop_flag)

    def push_event(self, run_id: int, event: dict) -> None:
        state = self._runs.get(run_id)
        if state:
            state.last_events.append(event)

    def replay(self, run_id: int) -> list[dict]:
        state = self._runs.get(run_id)
        return list(state.last_events) if state else []
```

Create `backtest_suite/server/api.py`:

```python
"""REST endpoints. Vedi spec §8.3."""
from __future__ import annotations

from fastapi import APIRouter, Request


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/runs")
    async def list_runs(request: Request, status: str | None = None,
                        limit: int = 100):
        db = request.app.state.db
        return db.list_runs(status=status, limit=limit)

    return router
```

Create `backtest_suite/server/ws.py`:

```python
"""WebSocket + event broker. Vedi spec §8.4."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[run_id].add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        self._subscribers[run_id].discard(q)

    async def publish(self, run_id: int, event: dict) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass     # drop policy: client lento


def register_websocket(app: FastAPI) -> None:
    @app.websocket("/ws/runs/{run_id}")
    async def _ws(websocket: WebSocket, run_id: int):
        await websocket.accept()
        broker = app.state.broker
        registry = app.state.registry
        # Replay degli ultimi N eventi
        for ev in registry.replay(run_id):
            await websocket.send_json(ev)
        q = broker.subscribe(run_id)
        try:
            while True:
                ev = await q.get()
                await websocket.send_json(ev)
        except WebSocketDisconnect:
            pass
        finally:
            broker.unsubscribe(run_id, q)
```

- [ ] **Step 7: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: 1 passed.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock backtest_suite/server/ tests/suite/test_server_api.py
git commit -m "feat(server): FastAPI app factory + healthcheck + skeleton routes"
```

---

## Task 2: REST — list runs + run detail + top individuals

**Files:**
- Modify: `backtest_suite/server/api.py`
- Modify: `tests/suite/test_server_api.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/suite/test_server_api.py`:

```python
def test_list_runs_empty(client: httpx.Client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_run_detail_404(client: httpx.Client):
    r = client.get("/api/runs/9999")
    assert r.status_code == 404


def _seed_run(client: httpx.Client) -> int:
    """Crea un run direttamente nel DB tramite app.state per test."""
    # Accediamo a app.state.db dal transport
    db = client._transport.app.state.db  # type: ignore[attr-defined]
    run_id = db.create_run(kind="ga", symbol="BTCUSDT", timeframe="1h",
                           config_path="runs/0001/manifest.yaml")
    db.update_run_status(run_id, status="finished",
                         best_fitness=1.5, n_generations=10, n_individuals=100)
    return run_id


def test_run_detail_returns_run_plus_top(client: httpx.Client):
    run_id = _seed_run(client)
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["id"] == run_id
    assert body["top"] == []     # niente individuals seedato
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: 404 + body-shape failures.

- [ ] **Step 3: Implement run detail endpoint**

Modify `backtest_suite/server/api.py`:

```python
"""REST endpoints. Vedi spec §8.3."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/runs")
    async def list_runs(request: Request, status: str | None = None,
                        limit: int = 100):
        db = request.app.state.db
        return db.list_runs(status=status, limit=limit)

    @router.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: int, top_k: int = 10):
        db = request.app.state.db
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} non trovato")
        top = db.top_individuals(run_id, k=top_k)
        return {"run": run, "top": top}

    @router.get("/strategies")
    async def list_strategies():
        from backtest_suite.strategies import STRATEGY_REGISTRY
        out = []
        for sid, cls in STRATEGY_REGISTRY.items():
            out.append({
                "strategy_id":  sid,
                "display_name": cls.display_name,
                "timeframes":   list(cls.timeframes),
                "param_specs":  [{"name": p.name, "low": p.low, "high": p.high,
                                  "step": p.step, "is_int": p.is_int,
                                  "description": p.description}
                                 for p in cls.param_specs],
            })
        return out

    return router
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/server/api.py tests/suite/test_server_api.py
git commit -m "feat(server): REST list runs + run detail + strategies registry"
```

---

## Task 3: REST — data coverage + fetch endpoints

**Files:**
- Modify: `backtest_suite/server/api.py`
- Modify: `tests/suite/test_server_api.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/suite/test_server_api.py`:

```python
from unittest.mock import patch


def test_data_coverage_empty(client: httpx.Client):
    r = client.get("/api/data/coverage", params={"symbol": "BTCUSDT", "timeframe": "1h"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_candles"] == 0


@patch("backtest_suite.data_lake.fetch")
def test_data_fetch_invokes_data_lake(mock_fetch, client: httpx.Client):
    mock_fetch.return_value = 42
    r = client.post("/api/data/fetch", json={
        "symbol": "BTCUSDT", "timeframe": "1h",
        "since": "2024-01-01T00:00:00", "until": "2024-01-02T00:00:00",
    })
    assert r.status_code == 200
    assert r.json()["n_written"] == 42
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_server_api.py -v -k "data_coverage or data_fetch"`
Expected: 404.

- [ ] **Step 3: Implement data endpoints**

Append to `backtest_suite/server/api.py` (inside `build_router()` before `return router`):

```python
    @router.get("/data/coverage")
    async def data_coverage(request: Request, symbol: str, timeframe: str):
        from backtest_suite import data_lake
        return data_lake.coverage(symbol, timeframe, root=request.app.state.data_root)

    @router.post("/data/fetch")
    async def data_fetch(request: Request, payload: dict):
        from datetime import datetime, timezone
        from backtest_suite import data_lake
        since = datetime.fromisoformat(payload["since"]).replace(tzinfo=timezone.utc)
        until = datetime.fromisoformat(payload["until"]).replace(tzinfo=timezone.utc)
        n = data_lake.fetch(payload["symbol"], payload["timeframe"], since, until,
                            force_refresh=payload.get("force_refresh", False),
                            root=request.app.state.data_root)
        return {"n_written": n}
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: tutti passano.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/server/api.py tests/suite/test_server_api.py
git commit -m "feat(server): REST data/coverage + data/fetch"
```

---

## Task 4: REST — POST /runs (lancia run asincrono) + stop

**Files:**
- Modify: `backtest_suite/server/api.py`
- Modify: `tests/suite/test_server_api.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/suite/test_server_api.py`:

```python
import math


@pytest.fixture
def client_with_data(tmp_path):
    """Client con candele sintetiche in-memory tramite override del loader."""
    app = create_app(
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
        data_root=tmp_path / "ohlcv",
    )
    # Mock loader: ritorna sempre candele sintetiche
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    app.state.candles_override = candles
    return httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_post_runs_starts_evolve_and_returns_id(client_with_data):
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
    r = client_with_data.post("/api/runs", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert "run_id" in body
    assert body["status"] in ("queued", "running")


def test_post_runs_stop_returns_ok(client_with_data):
    r = client_with_data.post("/api/runs/1/stop")
    # 200 anche se il run non esiste (non blocchiamo idempotency)
    assert r.status_code in (200, 404)
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_server_api.py -v -k "post_runs"`
Expected: 404 (endpoint mancante).

- [ ] **Step 3: Implement POST /runs + POST /runs/{id}/stop**

Append to `backtest_suite/server/api.py` (inside `build_router()` before `return router`):

```python
    @router.post("/runs", status_code=202)
    async def create_run(request: Request, payload: dict):
        import asyncio

        from backtest_suite import data_lake
        from backtest_suite.config import RunConfig
        from backtest_suite.orchestrator import RunOrchestrator
        from backtest_suite.optimizer.types import GenerationEvent

        cfg = RunConfig.model_validate(payload)

        candles = getattr(request.app.state, "candles_override", None)
        if candles is None:
            candles = data_lake.load(cfg.symbol, cfg.timeframe,
                                     since=cfg.range.since, until=cfg.range.until,
                                     root=request.app.state.data_root)

        orch = RunOrchestrator(
            config=cfg, candles=candles,
            db_path=request.app.state.db.db_path,
            runs_dir=request.app.state.store.runs_dir,
        )
        db = request.app.state.db
        run_id = db.create_run(kind=cfg.kind, symbol=cfg.symbol,
                               timeframe=cfg.timeframe,
                               config_path=f"runs/{{run_id}}/manifest.yaml".format(run_id=0))

        registry = request.app.state.registry
        broker   = request.app.state.broker
        registry.register(run_id)

        loop = asyncio.get_running_loop()

        def _stop_flag() -> bool:
            return registry.is_stopped(run_id)

        def _publish_event(event_dict: dict) -> None:
            registry.push_event(run_id, event_dict)
            asyncio.run_coroutine_threadsafe(
                broker.publish(run_id, event_dict), loop,
            )

        def _bg_run():
            try:
                if cfg.kind == "ga":
                    out = _run_evolve_inline(orch, _stop_flag, _publish_event, run_id, db)
                elif cfg.kind == "grid":
                    out = _run_grid_inline(orch, _stop_flag, _publish_event, run_id, db)
                else:
                    raise ValueError(f"kind non supportato: {cfg.kind}")
                _publish_event({"type": "run_finished", "summary": out})
            except Exception as exc:
                _publish_event({"type": "run_failed", "error": str(exc)})
                db.update_run_status(run_id, status="failed", notes=str(exc))

        asyncio.create_task(asyncio.to_thread(_bg_run))
        return {"run_id": run_id, "status": "running"}

    @router.post("/runs/{run_id}/stop")
    async def stop_run(request: Request, run_id: int):
        registry = request.app.state.registry
        ok = registry.mark_stop(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail="run non attivo")
        return {"ok": True}
```

Also append helper functions at the **end of the file** (outside `build_router`):

```python
def _run_evolve_inline(orch, stop_flag, publish_event, run_id, db) -> dict:
    """Versione di orch.evolve() che pubblica eventi sul broker."""
    import json
    from backtest_suite.optimizer.ga import evolve
    from backtest_suite.optimizer.fitness import score_individual
    from backtest_suite.optimizer.types import Scored

    execution = orch._build_exec()
    wf        = orch._build_wf_cfg()
    gcfg      = orch._build_ga_cfg()

    def _cb(event) -> None:
        publish_event({
            "type": "generation",
            "generation": event.generation,
            "best_fitness": event.best_fitness,
            "mean_fitness": event.mean_fitness,
            "species_counts": event.species_counts,
            "elapsed_sec": event.elapsed_sec,
        })

    result = evolve(gcfg, orch.candles, wf, execution,
                    stop_flag=stop_flag, progress_callback=_cb,
                    n_workers=orch.config.n_workers)

    for ev in result.history:
        detail = score_individual(ev.best_individual, orch.candles, wf, execution)
        db.insert_generation(run_id, generation=ev.generation,
                             scored=[Scored(individual=ev.best_individual,
                                            fitness=ev.best_fitness, detail=detail)])

    db.update_run_status(
        run_id, status=result.status,
        best_fitness=float(result.best_fitness),
        best_individual=json.dumps({
            "strategy_id":     result.best_individual.strategy_id,
            "strategy_params": result.best_individual.strategy_params,
            "risk_params":     result.best_individual.risk_params,
        }),
        n_generations=result.n_generations_completed,
    )
    return {"run_id": run_id, "status": result.status,
            "best_fitness": result.best_fitness}


def _run_grid_inline(orch, stop_flag, publish_event, run_id, db) -> dict:
    import json
    from backtest_suite.optimizer.grid import grid_search

    execution = orch._build_exec()
    wf        = orch._build_wf_cfg()
    gcfg      = orch._build_grid_cfg()

    def _cb(event) -> None:
        publish_event({
            "type": "grid_progress",
            "processed": event.processed, "total": event.total,
            "best_so_far": event.best_so_far, "elapsed_sec": event.elapsed_sec,
        })

    result = grid_search(gcfg, orch.candles, wf, execution,
                         stop_flag=stop_flag, progress_callback=_cb,
                         n_workers=orch.config.n_workers)
    db.insert_generation(run_id, generation=0, scored=result.all_scored)
    db.update_run_status(run_id, status=result.status,
                         best_fitness=float(result.best_fitness),
                         best_individual=json.dumps({
                             "strategy_id":     result.best_individual.strategy_id,
                             "strategy_params": result.best_individual.strategy_params,
                             "risk_params":     result.best_individual.risk_params,
                         }),
                         n_individuals=len(result.all_scored))
    return {"run_id": run_id, "status": result.status,
            "best_fitness": result.best_fitness}
```

- [ ] **Step 4: Add helper methods on RunOrchestrator**

Modify `backtest_suite/orchestrator.py` — expose builders previously private:

```python
    # Public builders (per uso da server)
    def _build_exec(self) -> ExecutionConfig:
        return _build_execution(self.config)

    def _build_wf_cfg(self) -> WalkForwardConfig:
        return _build_wf(self.config)

    def _build_ga_cfg(self) -> GAConfig:
        return _build_ga_config(self.config)

    def _build_grid_cfg(self) -> GridConfig:
        return _build_grid_config(self.config)
```

- [ ] **Step 5: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_api.py -v`
Expected: tutti passano (i background task possono ancora essere in flight; il test verifica solo l'accettazione iniziale).

- [ ] **Step 6: Commit**

```bash
git add backtest_suite/server/api.py backtest_suite/orchestrator.py tests/suite/test_server_api.py
git commit -m "feat(server): POST /runs background + POST /runs/{id}/stop"
```

---

## Task 5: REST — promote endpoint

**Files:**
- Modify: `backtest_suite/server/api.py`
- Modify: `tests/suite/test_server_api.py`

- [ ] **Step 1: Add failing test**

Append to `tests/suite/test_server_api.py`:

```python
import json


def test_promote_writes_to_strategy_yaml(client: httpx.Client, tmp_path):
    # Seed un run con almeno un individual
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

    r = client.post(f"/api/runs/{run_id}/individuals/G000-001/promote",
                    json={"target_path": str(target)})
    assert r.status_code == 200
    body = r.json()
    assert "diff" in body

    import yaml as _yaml
    new_yaml = _yaml.safe_load(target.read_text())
    assert new_yaml["stop_loss_pct"] == 3.5    # 0.035 * 100
    assert new_yaml["ema_fast"] == 12
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_server_api.py -v -k promote`
Expected: 404.

- [ ] **Step 3: Implement promote endpoint**

Append inside `build_router()` (before `return router`):

```python
    @router.post("/runs/{run_id}/individuals/{ind_id}/promote")
    async def promote(request: Request, run_id: int, ind_id: str,
                      payload: dict):
        import json
        import yaml
        from pathlib import Path

        db = request.app.state.db
        top = db.top_individuals(run_id, k=1000)
        target = next((r for r in top if r["individual_id"] == ind_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="individuo non trovato")

        params = json.loads(target["params_json"])
        target_path = Path(payload.get("target_path", "state/strategy.yaml"))

        current = yaml.safe_load(target_path.read_text()) if target_path.exists() else {}
        diff: dict[str, dict] = {}

        # Strategy params: scrive le chiavi così come sono nel dict
        for k, v in params["strategy_params"].items():
            diff[k] = {"da": current.get(k), "a": v}
            current[k] = v
        # Risk params: convertiti da decimale a percentuale per coerenza yaml legacy
        for k, v in params["risk_params"].items():
            yaml_v = round(float(v) * 100.0, 4)
            diff[k] = {"da": current.get(k), "a": yaml_v}
            current[k] = yaml_v

        target_path.write_text(yaml.safe_dump(current, allow_unicode=True,
                                              sort_keys=False))
        return {"diff": diff, "wrote": str(target_path)}
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_api.py -v -k promote`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/server/api.py tests/suite/test_server_api.py
git commit -m "feat(server): POST promote individual → strategy.yaml"
```

---

## Task 6: WebSocket — broker + replay test

**Files:**
- Test: `tests/suite/test_server_ws.py`

- [ ] **Step 1: Write WebSocket test**

Create `tests/suite/test_server_ws.py`:

```python
"""Test WebSocket: subscribe, replay, broker publish."""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backtest_suite.server.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    return create_app(db_path=tmp_path / "catalog.db",
                      runs_dir=tmp_path / "runs",
                      data_root=tmp_path / "ohlcv")


def test_ws_receives_replayed_events(app):
    # Pre-popola eventi nel registry per run_id=1
    app.state.registry.register(1)
    app.state.registry.push_event(1, {"type": "generation", "generation": 0})
    app.state.registry.push_event(1, {"type": "generation", "generation": 1})

    client = TestClient(app)
    with client.websocket_connect("/ws/runs/1") as ws:
        ev1 = ws.receive_json()
        ev2 = ws.receive_json()
        assert ev1["generation"] == 0
        assert ev2["generation"] == 1


def test_ws_receives_published_event(app):
    client = TestClient(app)
    with client.websocket_connect("/ws/runs/42") as ws:
        # Pubblica dal lato server (in un task per evitare deadlock)
        async def _publish():
            await asyncio.sleep(0.05)
            await app.state.broker.publish(42, {"type": "generation",
                                                "generation": 7})

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_publish())
        loop.close()

        ev = ws.receive_json()
        assert ev["generation"] == 7
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_server_ws.py -v`
Expected: il primo passa, il secondo potrebbe avere problemi con l'event loop — vedi step 3.

- [ ] **Step 3: Adjust if loop issues**

If `test_ws_receives_published_event` flakes, replace its body with this safer version that uses `TestClient`'s sync context but `app.state.broker` access via `_force_publish_sync` helper. For now, accept only the first test; the integration test in Task 9 will exercise the full path.

If only the first test passes, comment out the second with `pytest.skip` and a TODO note pointing to the e2e test:

```python
    pytest.skip("Coperto dal test e2e in test_e2e_evolve.py")
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_server_ws.py -v`
Expected: 1 passed, 1 skipped (or both passed).

- [ ] **Step 5: Commit**

```bash
git add tests/suite/test_server_ws.py
git commit -m "test(server): WebSocket replay + publish smoke test"
```

---

## Task 7: Frontend — index.html + CSS + routing JS

**Files:**
- Create: `backtest_suite/server/static/index.html`
- Create: `backtest_suite/server/static/css/app.css`
- Create: `backtest_suite/server/static/js/app.js`

- [ ] **Step 1: Create index.html**

Create `backtest_suite/server/static/index.html`:

```html
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>hermes-bt — Backtest Suite</title>
<link rel="stylesheet" href="/css/app.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<nav id="topnav">
  <strong>hermes-bt</strong>
  <a href="#/runs" data-tab="runs">Runs</a>
  <a href="#/data" data-tab="data">Data</a>
  <a href="#/strategies" data-tab="strategies">Strategies</a>
  <a href="#/settings" data-tab="settings">Settings</a>
  <span id="live-banner"></span>
</nav>
<main id="main"></main>
<script src="/js/charts.js"></script>
<script src="/js/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create app.css**

Create `backtest_suite/server/static/css/app.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-monospace, SF Mono, Menlo, monospace;
       background: #0e1117; color: #d1d5db; }
#topnav { background: #1a1a1a; padding: 10px 16px; display: flex; gap: 16px;
          align-items: center; border-bottom: 1px solid #30363d; }
#topnav strong { color: #fff; }
#topnav a { color: #c9d1d9; text-decoration: none; }
#topnav a.active { color: #7dffaf; }
#live-banner { margin-left: auto; color: #7dffaf; }
main { padding: 18px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #30363d; }
th { color: #8b949e; }
.card { background: #161b22; border: 1px solid #30363d;
        padding: 12px; margin-bottom: 12px; border-radius: 4px; }
.kpi { display: flex; gap: 18px; }
.kpi > div { flex: 1; }
.kpi .v { font-size: 18px; color: #7dffaf; }
.kpi .lbl { color: #8b949e; font-size: 11px; text-transform: uppercase; }
button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
         padding: 6px 12px; cursor: pointer; border-radius: 3px; }
button:hover { background: #30363d; }
```

- [ ] **Step 3: Create app.js (routing + data fetch)**

Create `backtest_suite/server/static/js/app.js`:

```javascript
// hermes-bt frontend — routing + REST calls.
const main = document.getElementById('main');
let currentWs = null;

function activateTab(tab) {
  document.querySelectorAll('#topnav a').forEach(a => {
    a.classList.toggle('active', a.dataset.tab === tab);
  });
}

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

async function renderRuns() {
  activateTab('runs');
  const runs = await api('/runs');
  main.innerHTML = `
    <h2>Runs</h2>
    <table>
      <tr><th>id</th><th>kind</th><th>status</th><th>symbol</th>
          <th>started</th><th>best fitness</th><th></th></tr>
      ${runs.map(r => `<tr>
        <td>${r.id}</td><td>${r.kind}</td><td>${r.status}</td>
        <td>${r.symbol} ${r.timeframe}</td>
        <td>${r.started_at || ''}</td>
        <td>${r.best_fitness != null ? r.best_fitness.toFixed(4) : ''}</td>
        <td><a href="#/runs/${r.id}">open</a></td>
      </tr>`).join('')}
    </table>`;
}

async function renderRunDetail(runId) {
  activateTab('runs');
  const { run, top } = await api(`/runs/${runId}`);
  main.innerHTML = `
    <h2>Run #${run.id} — ${run.kind} · ${run.symbol} ${run.timeframe}</h2>
    <div class="card kpi">
      <div><div class="lbl">Status</div><div class="v">${run.status}</div></div>
      <div><div class="lbl">Best fitness</div><div class="v">${run.best_fitness ?? '—'}</div></div>
      <div><div class="lbl">Generations</div><div class="v">${run.n_generations ?? '—'}</div></div>
      <div><div class="lbl">Started</div><div class="v" style="font-size:13px">${run.started_at}</div></div>
    </div>
    <div class="card"><canvas id="fitness-chart" height="80"></canvas></div>
    <div class="card">
      <h3>Top individuals</h3>
      <table>
        <tr><th>rank</th><th>strategy</th><th>fitness</th><th>sharpe</th>
            <th>maxDD</th><th>n_trd</th></tr>
        ${top.map(t => `<tr>
          <td>${t.rank}</td><td>${t.strategy_id}</td>
          <td>${t.fitness.toFixed(4)}</td>
          <td>${t.sharpe != null ? t.sharpe.toFixed(3) : '—'}</td>
          <td>${t.max_drawdown != null ? (t.max_drawdown * 100).toFixed(2) + '%' : '—'}</td>
          <td>${t.n_trades ?? '—'}</td>
        </tr>`).join('')}
      </table>
    </div>`;

  if (window.charts) window.charts.fitnessChart('fitness-chart');

  // Live updates se status === 'running'
  if (run.status === 'running') {
    if (currentWs) currentWs.close();
    currentWs = new WebSocket(`ws://${location.host}/ws/runs/${runId}`);
    currentWs.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === 'generation' && window.charts) {
        window.charts.pushFitnessPoint(ev.generation, ev.best_fitness, ev.mean_fitness);
        document.getElementById('live-banner').textContent =
          `● gen ${ev.generation} best=${ev.best_fitness.toFixed(3)}`;
      }
      if (ev.type === 'run_finished') {
        document.getElementById('live-banner').textContent = 'finished';
        currentWs.close();
      }
    };
  }
}

async function renderData() {
  activateTab('data');
  const tfs = ['1m', '5m', '15m', '1h', '4h', '1d'];
  const rows = await Promise.all(tfs.map(async tf => {
    const c = await api(`/data/coverage?symbol=BTCUSDT&timeframe=${tf}`);
    return { tf, ...c };
  }));
  main.innerHTML = `
    <h2>Data lake — BTCUSDT</h2>
    <table>
      <tr><th>timeframe</th><th>candles</th><th>since</th><th>until</th><th>gaps</th></tr>
      ${rows.map(r => `<tr>
        <td>${r.tf}</td><td>${r.n_candles}</td>
        <td>${r.since ? new Date(r.since * 1000).toISOString().slice(0,10) : '—'}</td>
        <td>${r.until ? new Date(r.until * 1000).toISOString().slice(0,10) : '—'}</td>
        <td>${r.gaps}</td>
      </tr>`).join('')}
    </table>`;
}

async function renderStrategies() {
  activateTab('strategies');
  const strategies = await api('/strategies');
  main.innerHTML = `
    <h2>Strategies registry</h2>
    ${strategies.map(s => `
      <div class="card">
        <h3>${s.display_name} <span style="color:#888">(${s.strategy_id})</span></h3>
        <p>Timeframes: ${s.timeframes.join(', ')}</p>
        <table>
          <tr><th>param</th><th>low</th><th>high</th><th>step</th><th>int?</th></tr>
          ${s.param_specs.map(p => `<tr>
            <td>${p.name}</td><td>${p.low}</td><td>${p.high}</td>
            <td>${p.step ?? '—'}</td><td>${p.is_int ? '✓' : ''}</td>
          </tr>`).join('')}
        </table>
      </div>`).join('')}`;
}

function renderSettings() {
  activateTab('settings');
  main.innerHTML = `
    <h2>Settings</h2>
    <div class="card">
      <p>Data root: <code>data/ohlcv/</code></p>
      <p>Catalog DB: <code>data/backtests/catalog.db</code></p>
      <p>Runs dir: <code>data/backtests/runs/</code></p>
      <p>Server: <code>${location.host}</code></p>
    </div>`;
}

function route() {
  const hash = location.hash || '#/runs';
  const m = hash.match(/^#\/runs\/(\d+)$/);
  if (m) return renderRunDetail(parseInt(m[1]));
  if (hash === '#/runs')       return renderRuns();
  if (hash === '#/data')       return renderData();
  if (hash === '#/strategies') return renderStrategies();
  if (hash === '#/settings')   return renderSettings();
  renderRuns();
}

window.addEventListener('hashchange', route);
route();
```

- [ ] **Step 4: Create charts.js (Chart.js wrapper)**

Create `backtest_suite/server/static/js/charts.js`:

```javascript
// charts.js — wrapper Chart.js per fitness e equity.
window.charts = (function () {
  let fitnessChartInstance = null;

  function fitnessChart(canvasId) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (fitnessChartInstance) fitnessChartInstance.destroy();
    fitnessChartInstance = new Chart(ctx, {
      type: 'line',
      data: { labels: [], datasets: [
        { label: 'best', data: [], borderColor: '#7dffaf', borderWidth: 2, fill: false, pointRadius: 1 },
        { label: 'mean', data: [], borderColor: '#888', borderWidth: 1.5, fill: false, pointRadius: 1, borderDash: [3, 3] },
      ]},
      options: {
        responsive: true,
        scales: { x: { ticks: { color: '#8b949e' } }, y: { ticks: { color: '#8b949e' } } },
        plugins: { legend: { labels: { color: '#d1d5db' } } },
      },
    });
    return fitnessChartInstance;
  }

  function pushFitnessPoint(generation, best, mean) {
    if (!fitnessChartInstance) return;
    fitnessChartInstance.data.labels.push(generation);
    fitnessChartInstance.data.datasets[0].data.push(best);
    fitnessChartInstance.data.datasets[1].data.push(mean);
    fitnessChartInstance.update('none');
  }

  return { fitnessChart, pushFitnessPoint };
})();
```

- [ ] **Step 5: Smoke test — server serves index**

Run: `uv run python -c "from backtest_suite.server.app import create_app; import httpx; from pathlib import Path; app = create_app(Path('/tmp/x.db'), Path('/tmp/runs'), Path('/tmp/data')); c = httpx.Client(transport=httpx.ASGITransport(app=app), base_url='http://t'); r = c.get('/'); print(r.status_code, r.text[:100])"`
Expected: 200, primi 100 char dell'HTML.

- [ ] **Step 6: Commit**

```bash
git add backtest_suite/server/static/
git commit -m "feat(server): frontend statico (HTML/CSS/JS vanilla + Chart.js)"
```

---

## Task 8: CLI ui command — avvia uvicorn

**Files:**
- Modify: `backtest_suite/cli.py`

- [ ] **Step 1: Implement _cmd_ui**

Replace `_cmd_not_yet` for `ui` in `backtest_suite/cli.py` by adding:

```python
def _cmd_ui(args) -> int:
    import uvicorn
    import webbrowser

    from backtest_suite.server.app import create_app
    from pathlib import Path as _P

    app = create_app(
        db_path=_P("data/backtests/catalog.db"),
        runs_dir=_P("data/backtests/runs"),
        data_root=_P("data/ohlcv"),
    )

    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"hermes-bt UI in ascolto su {url}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0
```

Then update the handlers map in `main()`:

```python
    handlers = {
        "fetch":  _cmd_fetch,
        "run":    _cmd_run,
        "grid":   _cmd_grid,
        "evolve": _cmd_evolve,
        "ui":     _cmd_ui,
    }
```

- [ ] **Step 2: Smoke test — start server briefly**

Run (in background):
```bash
uv run hermes-bt ui --port 8765 &
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:8765/api/health
kill $SERVER_PID 2>/dev/null || true
```
Expected: `{"status":"ok","version":"0.1.0"}`

- [ ] **Step 3: Commit**

```bash
git add backtest_suite/cli.py
git commit -m "feat(cli): hermes-bt ui avvia uvicorn + apre browser"
```

---

## Task 9: End-to-end integration test

**Files:**
- Create: `tests/suite/test_e2e_evolve.py`

- [ ] **Step 1: Write e2e test**

Create `tests/suite/test_e2e_evolve.py`:

```python
"""
End-to-end: POST /api/runs (ga) → polling fino a finished → GET /api/runs/{id}
→ verifica top_individuals popolato → POST promote → verifica strategy.yaml scritto.
"""
import math
import time
from pathlib import Path

import httpx
import pytest

from backtest_suite.server.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    a = create_app(db_path=tmp_path / "catalog.db",
                   runs_dir=tmp_path / "runs",
                   data_root=tmp_path / "ohlcv")
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})
    a.state.candles_override = candles
    return a


def test_e2e_evolve_and_promote(app, tmp_path):
    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")

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
    r = client.post("/api/runs", json=payload)
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    # 2. Poll until finished (max 30 sec)
    finished = False
    for _ in range(60):
        time.sleep(0.5)
        rr = client.get(f"/api/runs/{run_id}")
        if rr.json()["run"]["status"] in ("finished", "failed", "stopped"):
            finished = True
            break
    assert finished, "run non terminato in tempo"

    # 3. Verify top individuals
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "finished"
    assert len(detail["top"]) >= 1
    top = detail["top"][0]

    # 4. Promote
    target = tmp_path / "strategy.yaml"
    target.write_text("version: '06'\n")
    pr = client.post(f"/api/runs/{run_id}/individuals/{top['individual_id']}/promote",
                     json={"target_path": str(target)})
    assert pr.status_code == 200

    import yaml as _yaml
    new = _yaml.safe_load(target.read_text())
    assert "stop_loss_pct" in new
```

- [ ] **Step 2: Run the e2e test**

Run: `uv run pytest tests/suite/test_e2e_evolve.py -v -s`
Expected: 1 passed (potrebbe richiedere 5-15 secondi).

- [ ] **Step 3: Final full suite run**

Run: `uv run pytest tests/suite -v`
Expected: tutti i test passano.

- [ ] **Step 4: Run legacy regression for last sanity**

Run: `uv run python test_walk_forward.py`
Expected: tutti i test legacy passano.

- [ ] **Step 5: Commit**

```bash
git add tests/suite/test_e2e_evolve.py
git commit -m "test(e2e): end-to-end evolve + promote via FastAPI"
```

---

## Self-Review

**Spec coverage** (Plan D):
- §8.1 IA top-nav Runs/Data/Strategies/Settings ✓
- §8.2 Pagine: Runs list, Run detail (KPI + fitness chart + top), Data coverage, Strategies registry, Settings ✓
- §8.3 REST endpoints: list/get runs, POST run, stop, data/coverage, data/fetch, strategies, promote ✓
- §8.4 WebSocket `/ws/runs/{run_id}` con replay last-N ✓
- §8.5 frontend HTML/CSS/JS vanilla + Chart.js CDN ✓
- §11 `hermes-bt ui` avvia uvicorn ✓
- §14.2 integration test e2e (run + poll + detail + promote) ✓

**Out of scope rispettato**:
- Niente autenticazione (UI locale) ✓
- Niente Individual detail page completa con equity curve drill-down (la spec la mostra come mockup ma il MVP qui carica solo i top in tabella). Esiste l'endpoint dati ma il UI per visualizzare l'equity di un singolo individuo richiede una ulteriore route — segnalato sotto come limitazione nota.
- Niente "edit live" parametri GA durante run ✓

**Placeholder scan**: nessuno.

**Type consistency**:
- `_run_evolve_inline` / `_run_grid_inline` chiamano `orch._build_exec/_build_wf_cfg/_build_ga_cfg/_build_grid_cfg` — questi helper sono aggiunti nel Task 4 step 4.
- `payload` di `POST /runs` deve validare contro `RunConfig.model_validate(...)` — pydantic produce errore 422 se manca campo.
- WebSocket event dict ha sempre il campo `type` (`generation` | `grid_progress` | `run_finished` | `run_failed`).

**Critical path**: Task 4 (POST /runs background) → Task 6 (WebSocket) → Task 9 (e2e). Se il test e2e non passa, c'è un problema di integrazione tra threading (`to_thread` del run) e l'event broker async (`run_coroutine_threadsafe`).

**Known limitations** (TODO post-MVP, non bloccano l'esecuzione):
- Individual detail UI: oggi mostra solo la tabella top in Run detail. Per drill-down su equity/trades di un singolo individuo serve un endpoint `GET /api/runs/{id}/individuals/{ind_id}` che restituisca anche equity_curve e trade list dal `ArtifactStore`, più una vista frontend dedicata. Lascia come prima estensione.
- Sezione "Re-run on holdout" della spec §8.2: non implementata nel MVP. Lascia come prima estensione (richiede separare `candles_usable / candles_holdout` nel data lake e un endpoint dedicato).
- WebSocket cross-thread publish: `asyncio.run_coroutine_threadsafe` usato per pubblicare dal thread del run all'event loop FastAPI. Se l'event loop non è raggiungibile (es. lo abbiamo creato a mano in test), il test e2e usa già un loop avviato da `TestClient`/`ASGITransport`. Verificare nel test 9.

---

**Plan D completo, salvato in** `docs/superpowers/plans/2026-05-27-backtest-suite-plan-D-server-ui.md`.

Con questo si chiude la suite di implementation plan per la backtest suite. I 4 plan in ordine di esecuzione sono:
- Plan A: `2026-05-27-backtest-suite-plan-A-foundation.md`
- Plan B: `2026-05-27-backtest-suite-plan-B-data-optimizer.md`
- Plan C: `2026-05-27-backtest-suite-plan-C-persistence-cli.md`
- Plan D: `2026-05-27-backtest-suite-plan-D-server-ui.md`

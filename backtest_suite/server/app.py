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

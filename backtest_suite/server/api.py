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

    return router

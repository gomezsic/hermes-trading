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

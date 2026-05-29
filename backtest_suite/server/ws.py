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

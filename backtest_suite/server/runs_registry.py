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

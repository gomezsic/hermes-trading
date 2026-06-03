# backtest_suite/arena/agent.py
"""Agent: un archetipo abbinato a un proposer, con il proprio storico.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §7.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backtest_suite.arena.proposer import Proposer
from backtest_suite.arena.types import Attempt


@dataclass
class Agent:
    archetype_id: str                    # = strategy_id nel STRATEGY_REGISTRY
    proposer: Proposer
    history: list[Attempt] = field(default_factory=list)

    @property
    def agent_id(self) -> str:
        return f"{self.archetype_id}/{self.proposer.proposer_id}"

    @property
    def best(self) -> Attempt | None:
        if not self.history:
            return None
        return max(self.history, key=lambda a: a.fitness)

    def record(self, attempt: Attempt) -> None:
        self.history.append(attempt)

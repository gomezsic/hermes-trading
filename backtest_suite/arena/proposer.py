# backtest_suite/arena/proposer.py
"""Proposer: genera il prossimo genome (IndividualConfig) di un agente.

`RandomGAProposer` è la baseline di controllo (ricerca cieca seedata) che riusa
direttamente `_random_individual`/`mutate` dell'optimizer. L'`LLMProposer`
arriva in F3 con la stessa interfaccia.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §5.
"""
from __future__ import annotations

import random
from typing import Protocol, runtime_checkable

from backtest_suite.optimizer.types import IndividualConfig
from backtest_suite.optimizer.ga import _random_individual, mutate
from backtest_suite.arena.types import Attempt


@runtime_checkable
class Proposer(Protocol):
    """Contratto: dato l'archetipo + lo storico, ritorna un genome VALIDO."""

    proposer_id: str

    def propose(
        self,
        strategy_id: str,
        history: list[Attempt],
        rng: random.Random,
    ) -> IndividualConfig: ...


class RandomGAProposer:
    """Baseline: genome casuale (storia vuota) o mutazione del migliore finora."""

    proposer_id = "ga"

    def __init__(self, mutation_rate: float = 0.5) -> None:
        self.mutation_rate = mutation_rate

    def propose(
        self,
        strategy_id: str,
        history: list[Attempt],
        rng: random.Random,
    ) -> IndividualConfig:
        if not history:
            return _random_individual(strategy_id, rng)
        best = max(history, key=lambda a: a.fitness)
        # mutate_strategy_id_prob=0.0 → l'archetipo resta fisso
        return mutate(best.individual, self.mutation_rate, rng,
                      mutate_strategy_id_prob=0.0)

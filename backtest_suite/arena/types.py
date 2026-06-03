# backtest_suite/arena/types.py
"""Tipi dati del sottopacchetto arena.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §5-§8.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import IndividualConfig, WalkForwardConfig


@dataclass
class Attempt:
    """Un tentativo storico di un agente: genome proposto + fitness OOS grezza."""
    individual: IndividualConfig        # strategy_id + strategy_params + risk_params
    fitness: float                      # fitness OOS assoluta (score_individual)
    rationale: str = ""


@dataclass(frozen=True)
class CandidateMetrics:
    """Metriche di un candidato dopo la valutazione walk-forward OOS."""
    individual:      IndividualConfig
    fitness:         float              # FitnessResult.fitness (assoluta, cross-gen)
    quality:         float              # = mean_score (composito OOS)
    consistency:     float              # = -stdev_score
    drawdown:        float              # = max_drawdown_observed
    n_trades:        int
    failed:          bool
    failure_reason:  str | None = None

    @property
    def strategy_id(self) -> str:
        return self.individual.strategy_id


@dataclass(frozen=True)
class Weights:
    """Pesi del composite multi-obiettivo (spec §6)."""
    quality: float = 1.0
    consistency: float = 1.0
    drawdown: float = 1.0


@dataclass(frozen=True)
class ArenaConfig:
    """Configurazione di un torneo."""
    archetype_ids:  tuple[str, ...]
    proposer_ids:   tuple[str, ...]     # F2: solo ("ga",)
    n_generations:  int
    seed:           int
    wf:             WalkForwardConfig
    execution:      ExecutionConfig
    weights:        Weights = field(default_factory=Weights)
    symbol:         str = "BTCUSDT"
    timeframe:      str = "1h"


@dataclass(frozen=True)
class LeaderboardRow:
    """Riga di leaderboard: best-so-far o riga di una generazione."""
    agent_id: str
    archetype_id: str
    proposer_id: str
    generation: int
    composite_score: float
    verdict: str                        # "robust" | "weak" | "likely_overfit"
    candidate: CandidateMetrics


@dataclass
class Leaderboard:
    """Risultato finale di un torneo."""
    rows:   list[LeaderboardRow] = field(default_factory=list)  # ordinate per score desc
    run_id: int | None = None

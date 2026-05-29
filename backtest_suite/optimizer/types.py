"""
Tipi dell'optimizer.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndividualConfig:
    """Genoma di un individuo del GA / combinazione di Grid."""
    strategy_id:     str
    strategy_params: dict[str, float]
    risk_params:     dict[str, float]


@dataclass(frozen=True)
class WalkForwardConfig:
    is_months:                int
    oos_months:               int
    step_months:              int
    min_trades_oos:           int
    max_drawdown_per_window:  float
    variance_lambda:          float = 0.5


@dataclass
class FitnessResult:
    fitness:               float
    per_window_scores:     list[float]
    mean_score:            float
    stdev_score:           float
    max_drawdown_observed: float
    n_trades_total:        int
    failed:                bool
    failure_reason:        str | None = None


@dataclass
class Scored:
    individual: IndividualConfig
    fitness:    float
    detail:     FitnessResult


@dataclass(frozen=True)
class GAConfig:
    n_generations:           int
    pop_size:                int
    elite_size:              int
    mutation_rate:           float
    crossover_rate:          float
    tournament_k:            int
    species_quotas:          dict[str, float]
    mutate_strategy_id_prob: float
    immigrants_rate:         float
    immigrants_every:        int
    seed:                    int


@dataclass(frozen=True)
class GridConfig:
    strategy_ids:        list[str]
    risk_params_grid:    dict[str, list[float]]
    strategy_params_grid: dict[str, dict[str, list[float]]] | None
    max_combos:          int = 5000


@dataclass
class GenerationEvent:
    generation:      int
    pop_size:        int
    best_fitness:    float
    mean_fitness:    float
    best_individual: IndividualConfig
    species_counts:  dict[str, int]
    elapsed_sec:     float


@dataclass
class EvolutionResult:
    best_individual:         IndividualConfig
    best_fitness:            float
    n_generations_completed: int
    history:                 list[GenerationEvent] = field(default_factory=list)
    elapsed_sec:             float = 0.0
    status:                  str = "finished"     # 'finished' | 'stopped' | 'failed'


@dataclass
class GridProgressEvent:
    processed:   int
    total:       int
    best_so_far: float
    elapsed_sec: float


@dataclass
class GridResult:
    best_individual: IndividualConfig
    best_fitness:    float
    all_scored:      list[Scored]
    elapsed_sec:     float
    status:          str = "finished"

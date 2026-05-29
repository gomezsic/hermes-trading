"""
config — pydantic models + YAML loader per i config dei run.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §11.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class RangeSpec(BaseModel):
    since: date | datetime
    until: date | datetime


class WalkForwardSpec(BaseModel):
    is_months:                int = Field(..., gt=0)
    oos_months:               int = Field(..., gt=0)
    step_months:              int = Field(..., gt=0)
    min_trades_oos:           int = Field(..., ge=0)
    max_drawdown_per_window:  float = Field(..., gt=0, le=1.0)


class EvolveSpec(BaseModel):
    n_generations:           int = Field(..., gt=0)
    pop_size:                int = Field(..., gt=0)
    elite_size:              int = Field(..., ge=0)
    mutation_rate:           float = Field(..., ge=0, le=1)
    crossover_rate:          float = Field(..., ge=0, le=1)
    tournament_k:            int = Field(..., gt=0)
    species_quotas:          dict[str, float]
    mutate_strategy_id_prob: float = Field(..., ge=0, le=1)
    immigrants_rate:         float = Field(..., ge=0, le=1)
    immigrants_every:        int = Field(..., ge=0)
    seed:                    int


class GridSpec(BaseModel):
    strategy_ids:         list[str]
    risk_params_grid:     dict[str, list[float]]
    strategy_params_grid: dict[str, dict[str, list[float]]] | None = None
    max_combos:           int = 5000


class FitnessSpec(BaseModel):
    variance_lambda: float = 0.5


class ExecutionSpec(BaseModel):
    taker_fee:    float = 0.0026
    slippage:     float = 0.0005
    latency_bars: int   = 1
    capital:      float = 10000.0
    allow_overlap: bool = False
    direction:    Literal["long", "short", "both"] = "both"


class PersistenceSpec(BaseModel):
    save_top_k: int = 20


class RunConfig(BaseModel):
    kind:        Literal["ga", "grid", "single"]
    symbol:      str
    timeframe:   Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    range:       RangeSpec
    walk_forward: WalkForwardSpec | None = None
    ga:          EvolveSpec | None = None
    grid:        GridSpec | None = None
    fitness:     FitnessSpec = FitnessSpec()
    execution:   ExecutionSpec = ExecutionSpec()
    persistence: PersistenceSpec = PersistenceSpec()
    n_workers:   int = 0

    @model_validator(mode="after")
    def _validate_kind_sections(self) -> "RunConfig":
        if self.kind == "ga" and self.ga is None:
            raise ValueError("kind=ga richiede la sezione 'ga' nel config")
        if self.kind == "grid" and self.grid is None:
            raise ValueError("kind=grid richiede la sezione 'grid' nel config")
        if self.kind in ("ga", "grid") and self.walk_forward is None:
            raise ValueError(f"kind={self.kind} richiede la sezione 'walk_forward'")
        return self


def load_run_config(path: Path) -> RunConfig:
    """Carica un config YAML e valida con pydantic."""
    raw = yaml.safe_load(Path(path).read_text())
    return RunConfig.model_validate(raw)

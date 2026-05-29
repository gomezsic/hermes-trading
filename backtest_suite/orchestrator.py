"""
orchestrator — glue tra config + optimizer + persistence.

Espone evolve() e grid() che caricano candele, lanciano il run,
persistono metadata e artefatti dei top-K, e ritornano un summary.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §10.3.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backtest_suite.config import RunConfig
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.engine import run_backtest
from backtest_suite.optimizer.ga import evolve
from backtest_suite.optimizer.grid import grid_search
from backtest_suite.optimizer.types import (
    GAConfig,
    GridConfig,
    WalkForwardConfig,
)
from backtest_suite.persistence.artifact_store import ArtifactStore
from backtest_suite.persistence.catalog_db import CatalogDB
from backtest_suite.strategies import STRATEGY_REGISTRY

log = logging.getLogger(__name__)


def _git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                      stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(["git", "status", "--porcelain"],
                                      stderr=subprocess.DEVNULL).decode()
        return bool(out.strip())
    except Exception:
        return False


def _build_execution(cfg: RunConfig) -> ExecutionConfig:
    e = cfg.execution
    return ExecutionConfig(taker_fee=e.taker_fee, slippage=e.slippage,
                           latency_bars=e.latency_bars, capital=e.capital,
                           allow_overlap=e.allow_overlap, direction=e.direction)


def _build_wf(cfg: RunConfig) -> WalkForwardConfig:
    w = cfg.walk_forward
    assert w is not None
    return WalkForwardConfig(
        is_months=w.is_months, oos_months=w.oos_months, step_months=w.step_months,
        min_trades_oos=w.min_trades_oos,
        max_drawdown_per_window=w.max_drawdown_per_window,
        variance_lambda=cfg.fitness.variance_lambda,
    )


def _build_ga_config(cfg: RunConfig) -> GAConfig:
    g = cfg.ga
    assert g is not None
    return GAConfig(
        n_generations=g.n_generations, pop_size=g.pop_size,
        elite_size=g.elite_size, mutation_rate=g.mutation_rate,
        crossover_rate=g.crossover_rate, tournament_k=g.tournament_k,
        species_quotas=dict(g.species_quotas),
        mutate_strategy_id_prob=g.mutate_strategy_id_prob,
        immigrants_rate=g.immigrants_rate, immigrants_every=g.immigrants_every,
        seed=g.seed,
    )


def _build_grid_config(cfg: RunConfig) -> GridConfig:
    g = cfg.grid
    assert g is not None
    return GridConfig(
        strategy_ids=list(g.strategy_ids),
        risk_params_grid=dict(g.risk_params_grid),
        strategy_params_grid=dict(g.strategy_params_grid) if g.strategy_params_grid else None,
        max_combos=g.max_combos,
    )


def _individual_id(generation: int, rank: int) -> str:
    return f"G{generation:03d}-{rank:03d}"


class RunOrchestrator:
    def __init__(self, config: RunConfig, candles: list[dict],
                 db_path: Path, runs_dir: Path) -> None:
        self.config   = config
        self.candles  = candles
        self.db       = CatalogDB(db_path)
        self.store    = ArtifactStore(runs_dir)
        self.db.init_schema()

    # Public builders (per uso da server)
    def _build_exec(self) -> ExecutionConfig:
        return _build_execution(self.config)

    def _build_wf_cfg(self) -> WalkForwardConfig:
        return _build_wf(self.config)

    def _build_ga_cfg(self) -> GAConfig:
        return _build_ga_config(self.config)

    def _build_grid_cfg(self) -> GridConfig:
        return _build_grid_config(self.config)

    # ---------- helpers comuni ----------
    def _create_run_row(self, kind: str) -> int:
        run_id = self.db.create_run(
            kind=kind, symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            config_path="",   # aggiornato dopo
        )
        manifest = {
            "suite_version": "0.1.0",
            "git_commit":    _git_commit(),
            "git_dirty":     _git_dirty(),
            "python":        sys.version.split()[0],
            "started_at":    datetime.now(timezone.utc).isoformat(),
            "config":        self.config.model_dump(mode="json"),
        }
        self.store.save_manifest(run_id, manifest)
        self.db.update_run_status(
            run_id, status="running",
            config_path=f"runs/{run_id:04d}/manifest.yaml",
        )
        return run_id

    def _save_top_artifacts(self, run_id: int, scored_sorted: list,
                            save_top_k: int) -> None:
        execution = _build_execution(self.config)
        for rank, s in enumerate(scored_sorted[:save_top_k], start=1):
            ind = s.individual
            cls = STRATEGY_REGISTRY[ind.strategy_id]
            strat = cls(ind.strategy_params)
            from hermes_trading._engine_core import RiskConfig
            risk = RiskConfig(**ind.risk_params)
            result = run_backtest(self.candles, strat, risk, execution)
            ind_id = _individual_id(0, rank)
            self.store.save_equity(run_id, ind_id, result.equity_curve)
            self.store.save_trades(run_id, ind_id, result.trades)

    # ---------- evolve ----------
    def evolve(self) -> dict:
        run_id = self._create_run_row("ga")
        execution = _build_execution(self.config)
        wf   = _build_wf(self.config)
        gcfg = _build_ga_config(self.config)

        last_event_gen = {"n": 0}
        def _cb(event) -> None:
            last_event_gen["n"] = event.generation + 1

        result = evolve(gcfg, self.candles, wf, execution,
                        stop_flag=lambda: False, progress_callback=_cb,
                        n_workers=self.config.n_workers)

        # Persistenza del best per generation (single row each)
        from backtest_suite.optimizer.fitness import score_individual
        from backtest_suite.optimizer.types import Scored
        for ev in result.history:
            detail = score_individual(ev.best_individual, self.candles, wf, execution)
            scored = Scored(individual=ev.best_individual, fitness=ev.best_fitness, detail=detail)
            self.db.insert_generation(run_id, generation=ev.generation, scored=[scored])

        # Artefatti del best overall
        self._save_top_artifacts(run_id, [Scored(
            individual=result.best_individual, fitness=result.best_fitness,
            detail=score_individual(result.best_individual, self.candles, wf, execution),
        )], self.config.persistence.save_top_k)

        self.db.update_run_status(
            run_id, status=result.status,
            best_fitness=float(result.best_fitness),
            best_individual=json.dumps({
                "strategy_id":     result.best_individual.strategy_id,
                "strategy_params": result.best_individual.strategy_params,
                "risk_params":     result.best_individual.risk_params,
            }),
            n_generations=result.n_generations_completed,
            n_individuals=result.n_generations_completed * gcfg.pop_size,
        )
        return {"run_id": run_id, "status": result.status,
                "best_fitness": result.best_fitness}

    # ---------- grid ----------
    def grid(self) -> dict:
        run_id = self._create_run_row("grid")
        execution = _build_execution(self.config)
        wf  = _build_wf(self.config)
        gcfg = _build_grid_config(self.config)

        result = grid_search(gcfg, self.candles, wf, execution,
                             stop_flag=lambda: False,
                             progress_callback=lambda _ev: None,
                             n_workers=self.config.n_workers)

        self.db.insert_generation(run_id, generation=0, scored=result.all_scored)
        self._save_top_artifacts(run_id, result.all_scored, self.config.persistence.save_top_k)
        self.db.update_run_status(
            run_id, status=result.status,
            best_fitness=float(result.best_fitness),
            best_individual=json.dumps({
                "strategy_id":     result.best_individual.strategy_id,
                "strategy_params": result.best_individual.strategy_params,
                "risk_params":     result.best_individual.risk_params,
            }),
            n_individuals=len(result.all_scored),
        )
        return {"run_id": run_id, "status": result.status,
                "best_fitness": result.best_fitness,
                "n_individuals": len(result.all_scored)}

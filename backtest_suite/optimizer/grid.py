"""
grid — grid search sulla stessa fitness del GA.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.6.
"""
from __future__ import annotations

import itertools
import time
from typing import Callable, Iterator

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import score_individual
from backtest_suite.optimizer.ga import _DEFAULT_RISK_RANGES, _evaluate_population
from backtest_suite.optimizer.types import (
    GridConfig,
    GridProgressEvent,
    GridResult,
    IndividualConfig,
    Scored,
    WalkForwardConfig,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


def _values_from_spec(low: float, high: float, step: float | None, is_int: bool) -> list[float]:
    if is_int or (step is not None and step > 0):
        if step is None:
            step = 1.0 if is_int else (high - low) / 4.0
        out, v = [], low
        while v <= high + 1e-9:
            out.append(round(v, 6) if not is_int else float(int(round(v))))
            v += step
        return out
    return [round(low + i * (high - low) / 4.0, 6) for i in range(5)]


def _strategy_values(strategy_id: str, override: dict[str, list[float]] | None
                     ) -> dict[str, list[float]]:
    cls = STRATEGY_REGISTRY[strategy_id]
    out: dict[str, list[float]] = {}
    for ps in cls.param_specs:
        if override and ps.name in override:
            out[ps.name] = list(override[ps.name])
        else:
            out[ps.name] = _values_from_spec(ps.low, ps.high, ps.step, ps.is_int)
    return out


def _risk_values(override: dict[str, list[float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        if name in override:
            out[name] = list(override[name])
        else:
            out[name] = [round((lo + hi) / 2, 6)]
    return out


def _generate_combos(cfg: GridConfig) -> Iterator[IndividualConfig]:
    # Conta prima per validare max_combos
    total = 0
    per_strategy: list[tuple[str, dict[str, list[float]]]] = []
    risk_values = _risk_values(cfg.risk_params_grid)
    n_risk = 1
    for v in risk_values.values():
        n_risk *= max(1, len(v))

    for sid in cfg.strategy_ids:
        sp_override = (cfg.strategy_params_grid or {}).get(sid)
        sv = _strategy_values(sid, sp_override)
        per_strategy.append((sid, sv))
        n_strat = 1
        for v in sv.values():
            n_strat *= max(1, len(v))
        total += n_strat * n_risk

    if total > cfg.max_combos:
        raise ValueError(
            f"max_combos superato: {total} > {cfg.max_combos}. "
            f"Riduci i valori della grid o aumenta max_combos."
        )

    for sid, sv in per_strategy:
        sp_names = list(sv.keys())
        sp_values = [sv[k] for k in sp_names]
        rp_names = list(risk_values.keys())
        rp_values = [risk_values[k] for k in rp_names]
        for sp_combo in itertools.product(*sp_values):
            for rp_combo in itertools.product(*rp_values):
                yield IndividualConfig(
                    strategy_id=sid,
                    strategy_params=dict(zip(sp_names, sp_combo)),
                    risk_params=dict(zip(rp_names, rp_combo)),
                )


def grid_search(
    cfg:       GridConfig,
    candles:   list[dict],
    wf:        WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],
    progress_callback: Callable[[GridProgressEvent], None],
    n_workers: int = 0,
) -> GridResult:
    combos = list(_generate_combos(cfg))
    if not combos:
        raise ValueError("nessuna combinazione generata dalla GridConfig")

    t_start = time.time()
    scored_all: list[Scored] = []
    best_so_far = float("-inf")
    status = "finished"

    # Valuta in batch per supportare stop_flag e progress più granulare
    batch_size = max(1, min(50, len(combos)))
    for i in range(0, len(combos), batch_size):
        if stop_flag():
            status = "stopped"
            break
        batch = combos[i : i + batch_size]
        scored_batch = _evaluate_population(batch, candles, wf, execution, n_workers or 1)
        scored_all.extend(scored_batch)
        for s in scored_batch:
            if s.fitness > best_so_far:
                best_so_far = s.fitness
        progress_callback(GridProgressEvent(
            processed=len(scored_all),
            total=len(combos),
            best_so_far=best_so_far,
            elapsed_sec=round(time.time() - t_start, 3),
        ))

    if not scored_all:
        raise RuntimeError("grid search interrotta senza nessun individuo valutato")

    scored_all.sort(key=lambda s: s.fitness, reverse=True)
    best = scored_all[0]
    return GridResult(
        best_individual=best.individual,
        best_fitness=best.fitness,
        all_scored=scored_all,
        elapsed_sec=round(time.time() - t_start, 3),
        status=status,
    )

"""
ga — operatori GA + evolve loop.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.3, §7.4.
"""
from __future__ import annotations

import random
from typing import Callable

from backtest_suite.optimizer.types import (
    GAConfig,
    IndividualConfig,
    Scored,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


# Range default per i risk params (decimali).
_DEFAULT_RISK_RANGES: dict[str, tuple[float, float]] = {
    "stop_loss_pct":           (0.02,  0.08),
    "partial_exit_pct":        (0.05,  0.20),
    "trailing_activate_pct":   (0.03,  0.10),
    "trailing_stop_pct":       (0.02,  0.06),
    "trailing_stop_tight_pct": (0.01,  0.04),
}


def _sample_param_value(low: float, high: float, is_int: bool,
                        step: float | None, rng: random.Random) -> float:
    if is_int:
        return float(rng.randint(int(low), int(high)))
    if step is not None and step > 0:
        # Discretizzato
        n = int(round((high - low) / step))
        i = rng.randint(0, n)
        return round(low + i * step, 6)
    return rng.uniform(low, high)


def _random_individual(strategy_id: str, rng: random.Random) -> IndividualConfig:
    cls = STRATEGY_REGISTRY[strategy_id]
    strategy_params: dict[str, float] = {}
    for ps in cls.param_specs:
        strategy_params[ps.name] = _sample_param_value(ps.low, ps.high, ps.is_int,
                                                       ps.step, rng)

    risk_params: dict[str, float] = {}
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        risk_params[name] = round(rng.uniform(lo, hi), 6)

    return IndividualConfig(
        strategy_id=strategy_id,
        strategy_params=strategy_params,
        risk_params=risk_params,
    )


def init_population(config: GAConfig, rng: random.Random) -> list[IndividualConfig]:
    """Crea pop_size individui rispettando species_quotas."""
    pop: list[IndividualConfig] = []
    remaining = config.pop_size
    quotas = list(config.species_quotas.items())
    for i, (strategy_id, quota) in enumerate(quotas):
        if i == len(quotas) - 1:
            n = remaining
        else:
            n = max(1, int(round(config.pop_size * quota)))
            n = min(n, remaining)
        for _ in range(n):
            pop.append(_random_individual(strategy_id, rng))
        remaining -= n
        if remaining <= 0:
            break
    return pop


def _mutate_value(value: float, low: float, high: float, is_int: bool,
                  step: float | None, rng: random.Random) -> float:
    sigma = (high - low) * 0.1
    new_val = value + rng.gauss(0.0, sigma)
    new_val = max(low, min(high, new_val))
    if is_int:
        return float(int(round(new_val)))
    if step is not None and step > 0:
        # Snap al passo
        offset = round((new_val - low) / step)
        return round(low + offset * step, 6)
    return round(new_val, 6)


def mutate(ind: IndividualConfig, rate: float, rng: random.Random,
           mutate_strategy_id_prob: float = 0.0) -> IndividualConfig:
    """Gaussian mutation per parametro; opzionale flip della strategia."""
    # Eventuale flip della strategia
    if mutate_strategy_id_prob > 0 and rng.random() < mutate_strategy_id_prob:
        other_ids = [sid for sid in STRATEGY_REGISTRY if sid != ind.strategy_id]
        if other_ids:
            new_sid = rng.choice(other_ids)
            return _random_individual(new_sid, rng)

    cls = STRATEGY_REGISTRY[ind.strategy_id]
    new_strategy_params = dict(ind.strategy_params)
    for ps in cls.param_specs:
        if rng.random() < rate:
            new_strategy_params[ps.name] = _mutate_value(
                ind.strategy_params[ps.name], ps.low, ps.high,
                ps.is_int, ps.step, rng,
            )

    new_risk_params = dict(ind.risk_params)
    for name, (lo, hi) in _DEFAULT_RISK_RANGES.items():
        if rng.random() < rate:
            new_risk_params[name] = _mutate_value(
                ind.risk_params[name], lo, hi, is_int=False, step=None, rng=rng,
            )

    return IndividualConfig(
        strategy_id=ind.strategy_id,
        strategy_params=new_strategy_params,
        risk_params=new_risk_params,
    )


def crossover(a: IndividualConfig, b: IndividualConfig,
              rng: random.Random) -> tuple[IndividualConfig, IndividualConfig]:
    """Uniform crossover. Niente crossover tra specie diverse."""
    if a.strategy_id != b.strategy_id:
        return a, b

    sp_a, sp_b = dict(a.strategy_params), dict(b.strategy_params)
    rp_a, rp_b = dict(a.risk_params),     dict(b.risk_params)
    for k in sp_a:
        if rng.random() < 0.5:
            sp_a[k], sp_b[k] = sp_b[k], sp_a[k]
    for k in rp_a:
        if rng.random() < 0.5:
            rp_a[k], rp_b[k] = rp_b[k], rp_a[k]

    c1 = IndividualConfig(a.strategy_id, sp_a, rp_a)
    c2 = IndividualConfig(b.strategy_id, sp_b, rp_b)
    return c1, c2


def tournament_select(scored: list[Scored], k: int,
                      rng: random.Random) -> IndividualConfig:
    """Tournament selection size k — ritorna l'individuo migliore di k campionati."""
    k_eff = min(k, len(scored))
    contenders = rng.sample(scored, k_eff)
    best = max(contenders, key=lambda s: s.fitness)
    return best.individual


import multiprocessing
import os
import time
from typing import Callable

from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.fitness import score_individual
from backtest_suite.optimizer.types import (
    EvolutionResult,
    GenerationEvent,
    Scored,
    WalkForwardConfig,
)

# Stato globale per i worker (popolato da _init_worker via initializer del Pool)
_W_CANDLES: list[dict] | None = None
_W_WF: WalkForwardConfig | None = None
_W_EXEC: ExecutionConfig | None = None


def _init_worker(candles, wf, execution):
    global _W_CANDLES, _W_WF, _W_EXEC
    _W_CANDLES = candles
    _W_WF      = wf
    _W_EXEC    = execution


def _evaluate_one(individual: IndividualConfig) -> Scored:
    assert _W_CANDLES is not None and _W_WF is not None and _W_EXEC is not None
    detail = score_individual(individual, _W_CANDLES, _W_WF, _W_EXEC)
    return Scored(individual=individual, fitness=detail.fitness, detail=detail)


def _evaluate_serial(individual: IndividualConfig,
                     candles: list[dict],
                     wf: WalkForwardConfig,
                     execution: ExecutionConfig) -> Scored:
    detail = score_individual(individual, candles, wf, execution)
    return Scored(individual=individual, fitness=detail.fitness, detail=detail)


def _evaluate_population(
    pop: list[IndividualConfig],
    candles: list[dict],
    wf: WalkForwardConfig,
    execution: ExecutionConfig,
    n_workers: int,
) -> list[Scored]:
    if n_workers <= 1:
        return [_evaluate_serial(ind, candles, wf, execution) for ind in pop]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=n_workers,
                  initializer=_init_worker,
                  initargs=(candles, wf, execution)) as pool:
        scored = pool.map(_evaluate_one, pop)
    return scored


def _species_counts(pop: list[IndividualConfig]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ind in pop:
        counts[ind.strategy_id] = counts.get(ind.strategy_id, 0) + 1
    return counts


def evolve(
    config:    GAConfig,
    candles:   list[dict],
    wf:        WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],
    progress_callback: Callable[[GenerationEvent], None],
    n_workers: int = 0,
) -> EvolutionResult:
    """
    Evolve loop. n_workers=0 → auto (cpu_count-2); n_workers=1 → serial deterministico.
    """
    if n_workers == 0:
        n_workers = max(1, (os.cpu_count() or 2) - 2)

    rng = random.Random(config.seed)
    pop = init_population(config, rng)

    history: list[GenerationEvent] = []
    best_overall: Scored | None    = None
    t_start = time.time()
    status  = "finished"
    n_done  = 0

    for gen in range(config.n_generations):
        scored = _evaluate_population(pop, candles, wf, execution, n_workers)
        scored.sort(key=lambda s: s.fitness, reverse=True)

        if best_overall is None or scored[0].fitness > best_overall.fitness:
            best_overall = scored[0]

        valid_fitness = [s.fitness for s in scored if s.fitness != float("-inf")]
        mean_fit = sum(valid_fitness) / len(valid_fitness) if valid_fitness else float("-inf")

        event = GenerationEvent(
            generation=gen,
            pop_size=len(pop),
            best_fitness=scored[0].fitness,
            mean_fitness=mean_fit,
            best_individual=scored[0].individual,
            species_counts=_species_counts(pop),
            elapsed_sec=round(time.time() - t_start, 3),
        )
        progress_callback(event)
        history.append(event)
        n_done = gen + 1

        if stop_flag():
            status = "stopped"
            break

        # Costruisci la prossima generazione
        elites = [s.individual for s in scored[: config.elite_size]]
        next_pop: list[IndividualConfig] = list(elites)

        # Immigrants (random fresh) ogni N generazioni
        n_immigrants = 0
        if config.immigrants_every > 0 and (gen + 1) % config.immigrants_every == 0:
            n_immigrants = max(0, int(config.pop_size * config.immigrants_rate))
            for _ in range(n_immigrants):
                sid = rng.choices(
                    list(config.species_quotas.keys()),
                    weights=list(config.species_quotas.values()),
                )[0]
                next_pop.append(_random_individual(sid, rng))

        while len(next_pop) < config.pop_size:
            p1 = tournament_select(scored, config.tournament_k, rng)
            p2 = tournament_select(scored, config.tournament_k, rng)
            if rng.random() < config.crossover_rate:
                c1, c2 = crossover(p1, p2, rng)
            else:
                c1, c2 = p1, p2
            c1 = mutate(c1, config.mutation_rate, rng, config.mutate_strategy_id_prob)
            next_pop.append(c1)
            if len(next_pop) < config.pop_size:
                c2 = mutate(c2, config.mutation_rate, rng, config.mutate_strategy_id_prob)
                next_pop.append(c2)

        pop = next_pop[: config.pop_size]

    assert best_overall is not None
    return EvolutionResult(
        best_individual=best_overall.individual,
        best_fitness=best_overall.fitness,
        n_generations_completed=n_done,
        history=history,
        elapsed_sec=round(time.time() - t_start, 3),
        status=status,
    )

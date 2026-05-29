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

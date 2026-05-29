"""Test operatori GA: init_population, mutate, crossover, tournament."""
import random

from backtest_suite.optimizer.ga import (
    init_population,
    mutate,
    crossover,
    tournament_select,
    _DEFAULT_RISK_RANGES,
)
from backtest_suite.optimizer.types import GAConfig, IndividualConfig, Scored, FitnessResult


def _ga_config(pop=10, seed=1) -> GAConfig:
    return GAConfig(
        n_generations=3, pop_size=pop, elite_size=1,
        mutation_rate=0.2, crossover_rate=0.7, tournament_k=3,
        species_quotas={"ema_cross": 1.0},
        mutate_strategy_id_prob=0.0, immigrants_rate=0.0, immigrants_every=999,
        seed=seed,
    )


def test_init_population_size_and_quotas():
    rng = random.Random(42)
    cfg = _ga_config(pop=20)
    pop = init_population(cfg, rng)
    assert len(pop) == 20
    assert all(ind.strategy_id == "ema_cross" for ind in pop)
    assert all("ema_fast" in ind.strategy_params for ind in pop)
    assert all("stop_loss_pct" in ind.risk_params for ind in pop)


def test_init_population_respects_param_bounds():
    rng = random.Random(7)
    cfg = _ga_config(pop=50)
    pop = init_population(cfg, rng)
    for ind in pop:
        assert 5 <= ind.strategy_params["ema_fast"] <= 30
        assert 20 <= ind.strategy_params["ema_slow"] <= 100
        sl_lo, sl_hi = _DEFAULT_RISK_RANGES["stop_loss_pct"]
        assert sl_lo <= ind.risk_params["stop_loss_pct"] <= sl_hi


def test_mutate_changes_at_least_one_param_with_high_rate():
    rng = random.Random(0)
    cfg = _ga_config(pop=1)
    pop = init_population(cfg, rng)
    original = pop[0]
    mutated = mutate(original, rate=1.0, rng=rng,
                     mutate_strategy_id_prob=0.0)
    same_strategy = all(original.strategy_params[k] == mutated.strategy_params[k]
                        for k in original.strategy_params)
    same_risk = all(original.risk_params[k] == mutated.risk_params[k]
                    for k in original.risk_params)
    assert not (same_strategy and same_risk)


def test_crossover_same_species_produces_valid_children():
    rng = random.Random(0)
    cfg = _ga_config(pop=2)
    pop = init_population(cfg, rng)
    a, b = pop
    c1, c2 = crossover(a, b, rng)
    assert c1.strategy_id == a.strategy_id
    assert c2.strategy_id == b.strategy_id
    assert set(c1.strategy_params.keys()) == set(a.strategy_params.keys())


def test_crossover_different_species_returns_unchanged():
    a = IndividualConfig("ema_cross",
                         {"ema_fast": 10, "ema_slow": 30, "vwap_window": 100,
                          "vwap_filter": 0, "direction": 2},
                         {"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                          "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                          "trailing_stop_tight_pct": 0.025})
    b = IndividualConfig("rsi_mr",
                         {"rsi_period": 14, "oversold": 30, "overbought": 70, "exit_mid": 50},
                         a.risk_params)
    rng = random.Random(0)
    c1, c2 = crossover(a, b, rng)
    assert c1 is a and c2 is b


def test_tournament_select_returns_best_of_k():
    rng = random.Random(0)
    individuals = [
        IndividualConfig(f"ema_cross", {"ema_fast": 10 + i, "ema_slow": 30,
                                        "vwap_window": 100, "vwap_filter": 0, "direction": 2},
                         {"stop_loss_pct": 0.05, "partial_exit_pct": 0.10,
                          "trailing_activate_pct": 0.06, "trailing_stop_pct": 0.04,
                          "trailing_stop_tight_pct": 0.025})
        for i in range(10)
    ]
    scored = [Scored(individual=ind, fitness=float(i),
                     detail=FitnessResult(fitness=float(i), per_window_scores=[],
                                          mean_score=0.0, stdev_score=0.0,
                                          max_drawdown_observed=0.0, n_trades_total=0,
                                          failed=False, failure_reason=None))
              for i, ind in enumerate(individuals)]
    chosen = tournament_select(scored, k=10, rng=rng)
    assert chosen is scored[-1].individual

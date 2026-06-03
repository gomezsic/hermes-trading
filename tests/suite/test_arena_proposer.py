# tests/suite/test_arena_proposer.py
"""Test del RandomGAProposer (baseline di controllo)."""
import random

from backtest_suite.strategies import STRATEGY_REGISTRY
from backtest_suite.optimizer.ga import _DEFAULT_RISK_RANGES
from backtest_suite.optimizer.types import IndividualConfig
from backtest_suite.arena.proposer import Proposer, RandomGAProposer
from backtest_suite.arena.types import Attempt


def _check_bounds(ind: IndividualConfig):
    specs = {ps.name: ps for ps in STRATEGY_REGISTRY[ind.strategy_id].param_specs}
    for name, val in ind.strategy_params.items():
        ps = specs[name]
        assert ps.low <= val <= ps.high, f"strat {name}={val} fuori [{ps.low},{ps.high}]"
    for name, val in ind.risk_params.items():
        lo, hi = _DEFAULT_RISK_RANGES[name]
        assert lo <= val <= hi, f"risk {name}={val} fuori [{lo},{hi}]"


def test_is_a_proposer():
    p = RandomGAProposer()
    assert isinstance(p, Proposer)
    assert p.proposer_id == "ga"


def test_empty_history_samples_full_genome_within_bounds():
    p = RandomGAProposer()
    ind = p.propose("ema_cross", [], random.Random(42))
    assert isinstance(ind, IndividualConfig)
    assert ind.strategy_id == "ema_cross"
    assert set(ind.strategy_params) == {ps.name for ps in STRATEGY_REGISTRY["ema_cross"].param_specs}
    assert set(ind.risk_params) == set(_DEFAULT_RISK_RANGES)
    _check_bounds(ind)


def test_deterministic_for_same_seed():
    p = RandomGAProposer()
    a = p.propose("ema_cross", [], random.Random(7))
    b = p.propose("ema_cross", [], random.Random(7))
    assert a == b


def test_keeps_archetype_fixed_and_stays_in_bounds():
    p = RandomGAProposer(mutation_rate=1.0)
    seed_ind = IndividualConfig(
        strategy_id="ema_cross",
        strategy_params={ps.name: ps.low for ps in STRATEGY_REGISTRY["ema_cross"].param_specs},
        risk_params={name: lo for name, (lo, hi) in _DEFAULT_RISK_RANGES.items()},
    )
    history = [
        Attempt(individual=seed_ind, fitness=5.0),
        Attempt(individual=IndividualConfig(
            strategy_id="ema_cross",
            strategy_params={ps.name: ps.high for ps in STRATEGY_REGISTRY["ema_cross"].param_specs},
            risk_params={name: hi for name, (lo, hi) in _DEFAULT_RISK_RANGES.items()},
        ), fitness=1.0),   # peggiore
    ]
    out = p.propose("ema_cross", history, random.Random(3))
    assert out.strategy_id == "ema_cross"     # archetipo invariato
    _check_bounds(out)

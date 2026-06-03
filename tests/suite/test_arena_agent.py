# tests/suite/test_arena_agent.py
"""Test dello stato di un Agent."""
from backtest_suite.optimizer.types import IndividualConfig
from backtest_suite.arena.agent import Agent
from backtest_suite.arena.proposer import RandomGAProposer
from backtest_suite.arena.types import Attempt


def _ind(sl):
    return IndividualConfig(strategy_id="rsi_mr", strategy_params={},
                            risk_params={"stop_loss_pct": sl})


def test_agent_id_combines_archetype_and_proposer():
    a = Agent(archetype_id="ema_cross", proposer=RandomGAProposer())
    assert a.agent_id == "ema_cross/ga"


def test_best_is_none_without_history():
    a = Agent(archetype_id="rsi_mr", proposer=RandomGAProposer())
    assert a.best is None


def test_record_appends_and_best_picks_max_fitness():
    a = Agent(archetype_id="rsi_mr", proposer=RandomGAProposer())
    a.record(Attempt(individual=_ind(0.02), fitness=0.2))
    a.record(Attempt(individual=_ind(0.03), fitness=0.9))
    a.record(Attempt(individual=_ind(0.04), fitness=0.5))
    assert len(a.history) == 3
    assert a.best.fitness == 0.9
    assert a.best.individual.risk_params["stop_loss_pct"] == 0.03

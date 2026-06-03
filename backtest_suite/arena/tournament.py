# backtest_suite/arena/tournament.py
"""Loop di evoluzione + leaderboard dell'arena (spec §7).

F2: gira col solo proposer baseline GA. La persistenza è opzionale (Task 7-8).
Determinismo: RNG per agente/generazione seedato da stringa
`"{seed}:{agent_id}:{gen}"` (riproducibile cross-run).
"""
from __future__ import annotations

import random

from backtest_suite.arena.agent import Agent
from backtest_suite.arena.proposer import RandomGAProposer
from backtest_suite.arena.fitness import (
    evaluate, composite_scores, validation_verdict,
)
from backtest_suite.arena.types import (
    Attempt, ArenaConfig, CandidateMetrics, LeaderboardRow, Leaderboard,
)


def _make_proposer(proposer_id: str):
    """Factory dei proposer disponibili in F2 (solo GA)."""
    if proposer_id == "ga":
        return RandomGAProposer()
    raise ValueError(f"proposer sconosciuto in F2: {proposer_id!r}")


def run_tournament(
    cfg: ArenaConfig,
    candles: list[dict],
    store=None,
) -> Leaderboard:
    """Esegue il torneo e ritorna la leaderboard best-so-far ordinata.

    `store`, se fornito, deve esporre create_run/insert_generation/finish_run
    (vedi arena/store.py, Task 7). Se None, nessuna persistenza.
    """
    agents = [
        Agent(archetype_id=arch, proposer=_make_proposer(pid))
        for arch in cfg.archetype_ids
        for pid in cfg.proposer_ids
    ]

    run_id = store.create_run(cfg) if store is not None else None
    best_by_agent: dict[str, LeaderboardRow] = {}

    for gen in range(1, cfg.n_generations + 1):
        # 1) ogni agente propone un genome e viene valutato
        proposals: list[tuple[Agent, CandidateMetrics]] = []
        for agent in agents:
            rng = random.Random(f"{cfg.seed}:{agent.agent_id}:{gen}")
            individual = agent.proposer.propose(agent.archetype_id, agent.history, rng)
            cm = evaluate(individual, candles, cfg.wf, cfg.execution)
            proposals.append((agent, cm))

        # 2) composite z-scorato sulla popolazione della generazione
        scores = composite_scores([cm for _, cm in proposals], cfg.weights)

        # 3) registra storico, verdetto, leaderboard best-so-far
        gen_rows: list[LeaderboardRow] = []
        for (agent, cm), score in zip(proposals, scores):
            agent.record(Attempt(individual=cm.individual, fitness=cm.fitness,
                                 rationale=agent.proposer.proposer_id))
            row = LeaderboardRow(
                agent_id=agent.agent_id,
                archetype_id=agent.archetype_id,
                proposer_id=agent.proposer.proposer_id,
                generation=gen,
                composite_score=score,
                verdict=validation_verdict(cm),
                candidate=cm,
            )
            gen_rows.append(row)
            prev = best_by_agent.get(agent.agent_id)
            if prev is None or score > prev.composite_score:
                best_by_agent[agent.agent_id] = row

        if store is not None:
            store.insert_generation(run_id, gen, gen_rows)

    rows = sorted(best_by_agent.values(),
                  key=lambda r: r.composite_score, reverse=True)
    if store is not None:
        store.finish_run(run_id, rows)
    return Leaderboard(rows=rows, run_id=run_id)

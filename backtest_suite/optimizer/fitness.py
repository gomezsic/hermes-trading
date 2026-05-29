"""
fitness — calcola fitness anti-overfit di un IndividualConfig su finestre OOS.

Approccio: fitness = mean(score_OOS) - lambda * stdev(score_OOS).
Filtri hard: min_trades_oos cumulato, max_drawdown_per_window.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §7.1.
"""
from __future__ import annotations

from statistics import mean, pstdev

from hermes_trading._engine_core import RiskConfig
from hermes_trading import score as score_mod
from hermes_trading.walk_forward import _DAYS_PER_MONTH, _generate_windows

from backtest_suite.engine import run_backtest
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.optimizer.types import (
    FitnessResult,
    IndividualConfig,
    WalkForwardConfig,
)
from backtest_suite.strategies import STRATEGY_REGISTRY


def generate_walk_forward_windows(
    candles: list[dict],
    wf:      WalkForwardConfig,
) -> list[tuple[list[dict], list[dict]]]:
    """Riusa walk_forward._generate_windows. mesi → giorni × _DAYS_PER_MONTH."""
    is_days   = wf.is_months   * _DAYS_PER_MONTH
    oos_days  = wf.oos_months  * _DAYS_PER_MONTH
    step_days = wf.step_months * _DAYS_PER_MONTH
    return _generate_windows(candles, is_days, oos_days, step_days)


def _build_risk_config(risk_params: dict[str, float]) -> RiskConfig:
    return RiskConfig(
        stop_loss_pct           = float(risk_params["stop_loss_pct"]),
        partial_exit_pct        = float(risk_params["partial_exit_pct"]),
        trailing_activate_pct   = float(risk_params["trailing_activate_pct"]),
        trailing_stop_pct       = float(risk_params["trailing_stop_pct"]),
        trailing_stop_tight_pct = float(risk_params["trailing_stop_tight_pct"]),
    )


def _composite_score(report: dict) -> float:
    return float(report.get("composite_score", 0.0))


def score_individual(
    individual: IndividualConfig,
    candles:    list[dict],
    wf:         WalkForwardConfig,
    execution:  ExecutionConfig,
) -> FitnessResult:
    """
    Valuta un individuo sulle finestre OOS aggregate.

    Per ogni finestra (IS, OOS):
      - costruisce Strategy + RiskConfig dall'individuo
      - run_backtest sulla OOS
      - calcola composite_score con score.full_report
    Aggrega: fitness = mean(scores) - variance_lambda * stdev(scores).
    Filtri hard: somma trade >= min_trades_oos, max DD per finestra <= soglia.
    """
    strategy_cls = STRATEGY_REGISTRY.get(individual.strategy_id)
    if strategy_cls is None:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=[], mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=0.0, n_trades_total=0,
            failed=True, failure_reason=f"strategy_id sconosciuto: {individual.strategy_id}",
        )

    risk = _build_risk_config(individual.risk_params)
    windows = generate_walk_forward_windows(candles, wf)
    if not windows:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=[], mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=0.0, n_trades_total=0,
            failed=True, failure_reason="nessuna finestra IS/OOS generabile",
        )

    scores: list[float]     = []
    n_trades_total: int     = 0
    max_dd_observed: float  = 0.0

    for _is_w, oos_w in windows:
        try:
            strat  = strategy_cls(individual.strategy_params)
            result = run_backtest(oos_w, strat, risk, execution)
        except Exception:
            scores.append(0.0)
            continue
        n_trades_total += len(result.trades)
        dd = float(result.metrics.get("max_drawdown", 0.0))
        if dd > max_dd_observed:
            max_dd_observed = dd
        if dd > wf.max_drawdown_per_window:
            # La finestra che sfora il DD non ha uno score: riportiamo solo
            # quelli effettivamente calcolati (niente score-fantasma 0.0).
            return FitnessResult(
                fitness=float("-inf"),
                per_window_scores=scores,
                mean_score=0.0, stdev_score=0.0,
                max_drawdown_observed=dd,
                n_trades_total=n_trades_total,
                failed=True,
                failure_reason=f"max_dd {dd:.4f} > {wf.max_drawdown_per_window}",
            )
        trade_dicts = [{"pnl_pct": t.pnl_pct} for t in result.trades]
        report = score_mod.full_report(trade_dicts, {})
        scores.append(_composite_score(report))

    if n_trades_total < wf.min_trades_oos:
        return FitnessResult(
            fitness=float("-inf"),
            per_window_scores=scores, mean_score=0.0, stdev_score=0.0,
            max_drawdown_observed=max_dd_observed,
            n_trades_total=n_trades_total,
            failed=True,
            failure_reason=f"n_trades_total {n_trades_total} < {wf.min_trades_oos}",
        )

    mu = mean(scores) if scores else 0.0
    sd = pstdev(scores) if len(scores) >= 2 else 0.0
    fitness = mu - wf.variance_lambda * sd
    return FitnessResult(
        fitness=fitness,
        per_window_scores=[round(s, 6) for s in scores],
        mean_score=round(mu, 6),
        stdev_score=round(sd, 6),
        max_drawdown_observed=round(max_dd_observed, 6),
        n_trades_total=n_trades_total,
        failed=False,
        failure_reason=None,
    )

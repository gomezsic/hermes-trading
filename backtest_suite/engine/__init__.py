"""
engine — esecutore deterministico di un singolo backtest.

API pubblica: run_backtest(candles, strategy, risk, execution) -> BacktestResult.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6.
"""
from __future__ import annotations

import hashlib
import json

from hermes_trading._engine_core import (
    RiskConfig,
    build_equity_curve,
    simulate_trade,
)
from hermes_trading.score import (
    compute_calmar,
    compute_cvar,
    compute_expectancy,
    compute_max_drawdown,
    compute_sharpe,
    compute_tail_ratio,
    compute_ulcer_index,
    compute_win_stats,
)

from backtest_suite.engine.types import (
    BacktestResult,
    ExecutionConfig,
    Trade,
)


def _config_hash(strategy_id: str, params: dict, risk: RiskConfig,
                 execution: ExecutionConfig) -> str:
    payload = {
        "strategy_id": strategy_id,
        "params":      {k: v for k, v in sorted(params.items())},
        "risk":        risk.__dict__,
        "execution":   {k: v for k, v in execution.__dict__.items()},
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _empty_metrics() -> dict:
    return {
        "max_drawdown": 0.0, "cvar_5pct":  0.0, "calmar_ratio": 0.0,
        "ulcer_index":  0.0, "tail_ratio": 0.0, "sharpe":       0.0,
        "win_rate":     0.0, "n_trades":   0,   "expectancy":   0.0,
    }


def _compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return _empty_metrics()
    pnls = [t["pnl_pct"] for t in trades]
    ws   = compute_win_stats(pnls)
    return {
        "max_drawdown": round(compute_max_drawdown(pnls), 6),
        "cvar_5pct":    round(compute_cvar(pnls, 0.05),  6),
        "calmar_ratio": round(compute_calmar(pnls),       4),
        "ulcer_index":  round(compute_ulcer_index(pnls),  4),
        "tail_ratio":   round(compute_tail_ratio(pnls),   4),
        "sharpe":       round(compute_sharpe(pnls),       4),
        "win_rate":     ws["win_rate"],
        "n_trades":     len(trades),
        "expectancy":   round(compute_expectancy(pnls),   6),
    }


def run_backtest(
    candles:   list[dict],
    strategy,                              # Strategy istanziata
    risk:      RiskConfig,
    execution: ExecutionConfig,
) -> BacktestResult:
    """
    Esegue un backtest deterministico.

    Pipeline:
      1. Warmup: skip primi strategy.warmup_bars() indici.
      2. Per ogni candela: signal = strategy.on_bar(i, candles).
         Se posizione aperta e fuori range cooperativo, skip.
         Se signal valido e non in posizione, entry a i + execution.latency_bars.
      3. Filtro direction: long/short/both (execution.direction override).
      4. simulate_trade(...) dal _engine_core.
      5. Costruzione equity_curve + metrics.
    """
    n = len(candles)
    cfg_hash = _config_hash(
        strategy.strategy_id,
        {k: getattr(strategy, k, None)
         for k in (ps.name for ps in strategy.param_specs)},
        risk, execution,
    )

    warmup = strategy.warmup_bars()
    if n < warmup + execution.latency_bars + 1:
        return BacktestResult(
            trades=[],
            equity_curve=build_equity_curve(candles, [], execution.capital),
            metrics=_empty_metrics(),
            config_hash=cfg_hash,
            n_candles=n,
        )

    trades_raw: list[dict]  = []
    next_free_idx: int      = warmup

    i = warmup
    while i < n - 1:
        if i < next_free_idx and not execution.allow_overlap:
            i += 1
            continue

        sig = strategy.on_bar(i, candles)
        if sig.side is None:
            i += 1
            continue

        if execution.direction == "long"  and sig.side != "long":
            i += 1
            continue
        if execution.direction == "short" and sig.side != "short":
            i += 1
            continue

        entry_idx = i + execution.latency_bars
        if entry_idx >= n:
            break

        trade = simulate_trade(candles, entry_idx, sig.side, risk)
        trades_raw.append(trade)

        if not execution.allow_overlap:
            next_free_idx = trade["exit_idx"] + 1
            i = next_free_idx
        else:
            i += 1

    trades = [Trade(
        side=t["side"], entry_idx=t["entry_idx"], exit_idx=t["exit_idx"],
        entry=t["entry"], exit=t["exit"], pnl_pct=t["pnl_pct"],
        pnl_pct_gross=t["pnl_pct_gross"], fee_paid=t["fee_paid"],
        reason=t["reason"], partial_done=t["partial_done"],
    ) for t in trades_raw]

    equity_curve = build_equity_curve(candles, trades_raw, execution.capital)
    metrics      = _compute_metrics(trades_raw)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        config_hash=cfg_hash,
        n_candles=n,
    )

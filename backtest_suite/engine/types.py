"""Tipi dell'engine generico — vedi spec §6."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionConfig:
    taker_fee:    float = 0.0026
    slippage:     float = 0.0005
    latency_bars: int   = 1
    capital:      float = 10_000.0
    allow_overlap: bool = False
    direction:    str   = "both"          # "long" | "short" | "both"


@dataclass
class Trade:
    side:          str
    entry_idx:     int
    exit_idx:      int
    entry:         float
    exit:          float
    pnl_pct:       float
    pnl_pct_gross: float
    fee_paid:      float
    reason:        str                    # stop_loss | trailing_stop | forced_close
    partial_done:  bool


@dataclass
class BacktestResult:
    trades:       list[Trade]             = field(default_factory=list)
    equity_curve: list[dict]              = field(default_factory=list)
    metrics:      dict                    = field(default_factory=dict)
    config_hash:  str                     = ""
    n_candles:    int                     = 0

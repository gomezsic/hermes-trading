"""
_engine_core.py — Helper puri condivisi tra backtester legacy e backtest_suite engine.

Estratto da backtester.py durante il refactor non-distruttivo.
Stesse costanti e semantica; nessun cambiamento di comportamento.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §15.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Costanti di costo (Kraken taker fee + slippage market order) — invariate.
TAKER_FEE: float = 0.0026
SLIPPAGE:  float = 0.0005


@dataclass(frozen=True)
class RiskConfig:
    """Parametri di risk management usati dall'engine (decimali, non percentuali)."""
    stop_loss_pct: float
    partial_exit_pct: float
    trailing_activate_pct: float
    trailing_stop_pct: float
    trailing_stop_tight_pct: float


def apply_slippage_entry(price: float, side: str) -> float:
    """Slippage entry: long peggiora verso l'alto, short verso il basso."""
    if side == "long":
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def apply_slippage_exit(price: float, side: str) -> float:
    """Slippage exit: long abbassa prezzo, short alza prezzo."""
    if side == "long":
        return price * (1.0 - SLIPPAGE)
    return price * (1.0 + SLIPPAGE)


def gross_pnl_pct(entry: float, exit_p: float, side: str) -> float:
    """PnL lordo decimale (es. 0.05 = +5%)."""
    if side == "long":
        return (exit_p - entry) / entry
    return (entry - exit_p) / entry


def build_equity_curve(
    candles: list[dict],
    trades: list[dict],
    capital: float,
) -> list[dict]:
    """
    Costruisce equity curve candela per candela.

    Aggiorna il capitale all'exit_idx di ogni trade usando trade["pnl_pct"].
    Tra trade il capitale resta invariato.

    Returns:
        lista di dict {ts, equity, drawdown_pct} per ogni candela.
    """
    exit_map: dict[int, float] = {}
    for t in trades:
        idx = t["exit_idx"]
        exit_map[idx] = exit_map.get(idx, 0.0) + t["pnl_pct"]

    equity = float(capital)
    peak = equity
    curve: list[dict] = []

    for i, c in enumerate(candles):
        if i in exit_map:
            equity = equity * (1.0 + exit_map[i])
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0 if peak > 0.0 else 0.0
        curve.append({
            "ts":           c.get("t", i),
            "equity":       round(equity, 4),
            "drawdown_pct": round(dd, 4),
        })

    return curve


def simulate_trade(
    candles:   list[dict],
    entry_idx: int,
    side:      str,
    risk:      RiskConfig,
) -> dict:
    """
    Simula un singolo trade candela per candela.

    Logica intra-candela (conservativa, avverso prima del favorevole):
      1. Controlla SL e trailing stop sull'estremo avverso
      2. Aggiorna best_price, partial exit, trailing stop sull'estremo favorevole

    Fee: 2 * TAKER_FEE (entry + exit leg).
    La partial al 50% suddivide i volumi ma le leg restano 2.

    Returns:
        dict con: entry, exit, side, pnl_pct (netto), pnl_pct_gross, fee_paid,
        reason, entry_idx, exit_idx, partial_done.
    """
    sl_pct         = risk.stop_loss_pct
    partial_pct    = risk.partial_exit_pct
    trail_act_pct  = risk.trailing_activate_pct
    trail_dist_pct = risk.trailing_stop_pct
    tight_dist_pct = risk.trailing_stop_tight_pct

    entry = apply_slippage_entry(float(candles[entry_idx]["o"]), side)

    if side == "long":
        sl_price      = entry * (1.0 - sl_pct)
        partial_price = entry * (1.0 + partial_pct)
    else:
        sl_price      = entry * (1.0 + sl_pct)
        partial_price = entry * (1.0 - partial_pct)

    trail_active:   bool         = False
    trail_level:    float | None = None
    partial_done:   bool         = False
    partial_exit_p: float | None = None
    best_price: float = entry
    exit_p:   float | None = None
    exit_idx: int | None   = None
    reason:   str          = "forced_close"

    n = len(candles)

    for i in range(entry_idx, n):
        c  = candles[i]
        lo = float(c["l"])
        hi = float(c["h"])

        # Step 1 - estremo avverso
        if side == "long":
            if lo <= sl_price:
                exit_p   = apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break
            if trail_active and trail_level is not None and lo <= trail_level:
                exit_p   = apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break
        else:
            if hi >= sl_price:
                exit_p   = apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break
            if trail_active and trail_level is not None and hi >= trail_level:
                exit_p   = apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break

        # Step 2 - estremo favorevole + trailing + partial
        if side == "long":
            if hi > best_price:
                best_price = hi
                gain = (best_price - entry) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 - dist)
                    trail_level = max(trail_level or 0.0, new_trail)
            if not partial_done and hi >= partial_price:
                partial_done   = True
                partial_exit_p = apply_slippage_exit(partial_price, side)
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 - tight_dist_pct)
                    trail_level = max(trail_level, new_trail)
        else:
            if lo < best_price:
                best_price = lo
                gain = (entry - best_price) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 + dist)
                    trail_level = min(
                        trail_level if trail_level is not None else math.inf,
                        new_trail,
                    )
            if not partial_done and lo <= partial_price:
                partial_done   = True
                partial_exit_p = apply_slippage_exit(partial_price, side)
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 + tight_dist_pct)
                    trail_level = min(trail_level, new_trail)

    if exit_p is None:
        last     = candles[-1]
        exit_p   = apply_slippage_exit(float(last["c"]), side)
        exit_idx = n - 1
        reason   = "forced_close"

    assert exit_idx is not None

    if partial_done and partial_exit_p is not None:
        gross_partial   = gross_pnl_pct(entry, partial_exit_p, side)
        gross_remaining = gross_pnl_pct(entry, exit_p, side)
        pnl_gross = 0.5 * gross_partial + 0.5 * gross_remaining
    else:
        pnl_gross = gross_pnl_pct(entry, exit_p, side)

    fee_paid = 2.0 * TAKER_FEE
    pnl_net  = pnl_gross - fee_paid

    return {
        "entry":         round(entry, 6),
        "exit":          round(exit_p, 6),
        "side":          side,
        "pnl_pct":       round(pnl_net, 8),
        "pnl_pct_gross": round(pnl_gross, 8),
        "fee_paid":      round(fee_paid, 6),
        "reason":        reason,
        "entry_idx":     entry_idx,
        "exit_idx":      exit_idx,
        "partial_done":  partial_done,
    }

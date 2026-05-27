"""
score.py — Metriche di valutazione della strategia.

Filosofia: "Si fa prestissimo a perdere tutto, c'e' tempo a guadagnare."
Le metriche sono ordinate per priorita':

  1. SOPRAVVIVENZA (non negoziabile)
     - Max Drawdown assoluto e durata del recovery
     - CVaR 5% (perdita media nel 5% dei casi peggiori)
     - Max consecutive losses (quante perdite di fila si puo' sopportare)

  2. ROBUSTEZZA
     - Calmar Ratio = return annualizzato / max drawdown
       > 1.0 e' buono, > 2.0 e' eccellente
     - Ulcer Index = penalizza le drawdown prolungate piu' di quelle brevi
     - Tail Ratio = gain medio dei trade top 10% / perdita media trade bottom 10%

  3. EFFICIENZA (secondario)
     - Sharpe Ratio
     - Win Rate, avg win / avg loss
     - Expectancy per trade

Il composite score che guida la reflection di Hermes pesa:
  50% sopravvivenza + 30% robustezza + 20% efficienza
"""
from __future__ import annotations

import math
from statistics import mean, pstdev


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# 1. METRICHE DI SOPRAVVIVENZA
# ---------------------------------------------------------------------------

def compute_max_drawdown(returns: list[float]) -> float:
    """Max drawdown da serie di return per-trade. Ritorna valore positivo (es. 0.12 = 12%)."""
    if not returns:
        return 0.0
    equity = 1.0
    peak   = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak
        max_dd  = max(max_dd, dd)
    return max_dd


def compute_drawdown_duration(returns: list[float]) -> int:
    """
    Numero massimo di trade consecutivi in cui il capitale era sotto il peak.
    Proxy della durata del recovery — piu' alto, piu' a lungo si e' sotto acqua.
    """
    if not returns:
        return 0
    equity  = 1.0
    peak    = 1.0
    current_dd_len = 0
    max_dd_len     = 0
    for r in returns:
        equity *= 1.0 + r
        if equity >= peak:
            peak = equity
            current_dd_len = 0
        else:
            current_dd_len += 1
            max_dd_len = max(max_dd_len, current_dd_len)
    return max_dd_len


def compute_cvar(returns: list[float], percentile: float = 0.05) -> float:
    """
    CVaR (Conditional Value at Risk) al percentile dato.
    Perdita media nel peggior X% dei trade. Valore positivo = perdita.
    Cattura il rischio di coda — quanto perdiamo nelle giornate nere.
    """
    if len(returns) < 2:
        return 0.0
    sorted_r = sorted(returns)
    cutoff   = max(1, int(len(sorted_r) * percentile))
    tail     = sorted_r[:cutoff]
    return -mean(tail)   # positivo = perdita


def compute_max_consecutive_losses(returns: list[float]) -> int:
    """Numero massimo di perdite consecutive."""
    max_streak = 0
    streak     = 0
    for r in returns:
        if r < 0:
            streak    += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


# ---------------------------------------------------------------------------
# 2. METRICHE DI ROBUSTEZZA
# ---------------------------------------------------------------------------

def compute_calmar(returns: list[float], n_trades_per_year: float = 252.0) -> float:
    """
    Calmar Ratio = return annualizzato / max drawdown.
    > 1.0 buono, > 2.0 eccellente.
    Penalizza fortemente le strategie con drawdown grandi anche se redditizie.
    """
    if not returns:
        return 0.0
    max_dd = compute_max_drawdown(returns)
    if max_dd <= 0:
        return 10.0   # cap: nessun drawdown
    annual_return = mean(returns) * n_trades_per_year
    return annual_return / max_dd


def compute_ulcer_index(returns: list[float]) -> float:
    """
    Ulcer Index: radice quadrata della media dei drawdown al quadrato.
    Penalizza le drawdown prolungate piu' di quelle brevi e acute.
    Un valore basso indica che il capitale e' rimasto vicino al peak.
    """
    if not returns:
        return 0.0
    equity     = 1.0
    peak       = 1.0
    dd_sq_sum  = 0.0
    for r in returns:
        equity   *= 1.0 + r
        peak      = max(peak, equity)
        dd        = (peak - equity) / peak * 100   # in %
        dd_sq_sum += dd * dd
    return math.sqrt(dd_sq_sum / len(returns))


def compute_tail_ratio(returns: list[float], pct: float = 0.10) -> float:
    """
    Tail Ratio = gain medio trade top X% / perdita media trade bottom X%.
    > 1.0: le vittorie estreme sono piu' grandi delle sconfitte estreme.
    E' la versione "event-based" del CVaR — misura l'asimmetria delle code.
    """
    if len(returns) < 10:
        return 0.0
    sorted_r = sorted(returns)
    n        = max(1, int(len(sorted_r) * pct))
    worst    = mean(sorted_r[:n])           # negativo
    best     = mean(sorted_r[-n:])          # positivo
    if worst >= 0:
        return 10.0   # cap: nessuna perdita in coda
    return abs(best / worst)


# ---------------------------------------------------------------------------
# 3. METRICHE DI EFFICIENZA
# ---------------------------------------------------------------------------

def compute_sharpe(returns: list[float], rf: float = 0.0) -> float:
    """Sharpe annualizzato da serie di return per-trade."""
    if len(returns) < 2:
        return 0.0
    excess = [r - rf for r in returns]
    sd     = pstdev(excess)
    if sd == 0:
        return 0.0
    return (mean(excess) / sd) * math.sqrt(252)


def compute_expectancy(returns: list[float]) -> float:
    """Expectancy = media dei return. Positivo = strategia profittevole nel lungo termine."""
    if not returns:
        return 0.0
    return mean(returns)


def compute_win_stats(returns: list[float]) -> dict:
    """Win rate, avg win, avg loss, profit factor."""
    if not returns:
        return {"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0}
    wins   = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    avg_w  = mean(wins)   if wins   else 0.0
    avg_l  = mean(losses) if losses else 0.0
    pf     = abs(avg_w * len(wins) / (avg_l * len(losses))) if losses and avg_l != 0 else 10.0
    return {
        "win_rate":      round(len(wins) / len(returns), 4),
        "avg_win":       round(avg_w, 6),
        "avg_loss":      round(avg_l, 6),
        "profit_factor": round(pf, 3),
    }


# ---------------------------------------------------------------------------
# Score composito — usato dalla reflection di Hermes
# ---------------------------------------------------------------------------

def score(trades: list[dict], goal: dict) -> float:
    """
    Score in [-1, +1]. Composito pesato:
      50% sopravvivenza (drawdown, CVaR, consecutive losses)
      30% robustezza    (Calmar, Ulcer Index, Tail Ratio)
      20% efficienza    (Sharpe, expectancy)

    Un sistema che guadagna poco ma non crasha mai
    batte un sistema ad alto rendimento ma fragile.
    """
    if not trades:
        return 0.0

    pnls = [t["pnl_pct"] for t in trades]

    # --- Sopravvivenza (50%) ---
    max_dd      = compute_max_drawdown(pnls)
    cvar        = compute_cvar(pnls, 0.05)
    max_consec  = compute_max_consecutive_losses(pnls)
    dd_duration = compute_drawdown_duration(pnls)

    max_dd_tgt  = goal.get("max_drawdown", 0.15)
    cvar_tgt    = goal.get("max_cvar_5pct", 0.03)
    consec_tgt  = goal.get("max_consecutive_losses", 5)

    dd_score     = clamp(1.0 - max_dd / max_dd_tgt, -1.0, 1.0)
    cvar_score   = clamp(1.0 - cvar / cvar_tgt, -1.0, 1.0)
    consec_score = clamp(1.0 - max_consec / consec_tgt, -1.0, 1.0)
    survival     = (dd_score * 0.50 + cvar_score * 0.30 + consec_score * 0.20)

    # --- Robustezza (30%) ---
    calmar     = compute_calmar(pnls)
    ulcer      = compute_ulcer_index(pnls)
    tail_r     = compute_tail_ratio(pnls)

    calmar_score = clamp(calmar / 2.0, -1.0, 1.0)         # target Calmar > 2
    ulcer_score  = clamp(1.0 - ulcer / 5.0, -1.0, 1.0)   # target Ulcer < 5
    tail_score   = clamp((tail_r - 1.0) / 2.0, -1.0, 1.0) # target Tail > 1
    robustness   = (calmar_score * 0.40 + ulcer_score * 0.35 + tail_score * 0.25)

    # --- Efficienza (20%) ---
    sharpe     = compute_sharpe(pnls)
    expectancy = compute_expectancy(pnls)
    ret_target = goal.get("target_return_30d", 0.05)

    sharpe_score  = clamp(sharpe / goal.get("min_sharpe", 1.2), -1.0, 1.0)
    return_score  = clamp(expectancy * len(pnls) / ret_target, -1.0, 1.0)
    efficiency    = (sharpe_score * 0.50 + return_score * 0.50)

    composite = (survival * 0.50 + robustness * 0.30 + efficiency * 0.20)

    # Circuit breaker: se CVaR o drawdown sono catastrofici, score minimo
    if max_dd > max_dd_tgt * 2 or cvar > cvar_tgt * 3:
        composite = min(composite, -0.5)

    floor = goal.get("failure_below", -1.0)
    return max(floor, round(composite, 4))


def full_report(trades: list[dict], goal: dict) -> dict:
    """
    Report completo per la reflection di Hermes.
    Tutte le metriche in un dict strutturato per priorita'.
    """
    if not trades:
        return {"n_trades": 0}

    pnls = [t["pnl_pct"] for t in trades]
    ws   = compute_win_stats(pnls)

    return {
        "n_trades": len(trades),

        # Sopravvivenza — guardare prima
        "survival": {
            "max_drawdown_pct":       round(compute_max_drawdown(pnls) * 100, 2),
            "drawdown_duration_trades": compute_drawdown_duration(pnls),
            "cvar_5pct":              round(compute_cvar(pnls, 0.05) * 100, 3),
            "max_consecutive_losses": compute_max_consecutive_losses(pnls),
        },

        # Robustezza
        "robustness": {
            "calmar_ratio":  round(compute_calmar(pnls), 3),
            "ulcer_index":   round(compute_ulcer_index(pnls), 3),
            "tail_ratio":    round(compute_tail_ratio(pnls), 3),
        },

        # Efficienza — guardare per ultimi
        "efficiency": {
            "sharpe":         round(compute_sharpe(pnls), 3),
            "expectancy_pct": round(compute_expectancy(pnls) * 100, 4),
            "win_rate":       round(ws["win_rate"] * 100, 1),
            "avg_win_pct":    round(ws["avg_win"] * 100, 3),
            "avg_loss_pct":   round(ws["avg_loss"] * 100, 3),
            "profit_factor":  ws["profit_factor"],
            "total_return_pct": round(sum(pnls) * 100, 3),
        },

        "composite_score": score(trades, goal),
    }

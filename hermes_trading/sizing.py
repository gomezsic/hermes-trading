"""
sizing.py — Position sizing Kelly-frazionario + Volatility Targeting.

FILOSOFIA:
  Il sizing tradizionale confidence-only e' proporzionale al capitale ma
  NON al rischio. Quando la volatilita' di BTC cambia regime, il rischio
  reale per trade varia di 2-3x a parita' di size.

  Questo modulo combina due approcci ortogonali:

  1. KELLY FRAZIONARIO: "quanto rischiare dato l'edge osservato?"
     Usa la distribuzione storica dei trade per stimare win rate e
     average win/loss. Applica Wilson lower bound (conservative) per
     evitare overfitting su campioni piccoli. Usa al massimo quarter
     Kelly (0.25) — Kelly pieno rompe i conti in 95% dei casi reali.

  2. VOLATILITY TARGETING: "quanto rischiare dato il regime di mercato?"
     Normalizza la size affinche' il rischio annualizzato sia sempre
     vicino a sigma_target (default 18%). Se BTC e' tranquillo investe
     di piu', se e' in un regime di alta vol investe di meno.
     Vol shock (sigma > 2x media 30gg) dimezza la size in automatico.

  La size finale e' min(kelly, vol) moltiplicata per confidence e
  dd_penalty. Mai sopra il 50% del capitale per nessun motivo.

  CIRCUITI DI PROTEZIONE:
  - SKIP_TRADE: size < 5% del capitale → trade economicamente inutile
  - PAUSE_SYSTEM: win rate ultimi 30 trade < break-even - 5pp →
    il sistema non ha piu' edge statistico, si ferma 7 giorni

  LOGGING: ogni decisione viene scritta in state/sizing_log.jsonl —
  e' il deliverable piu' importante per il walk-forward futuro.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SizingDecision:
    action:          str            # "OPEN_TRADE" | "SKIP_TRADE" | "PAUSE_SYSTEM"
    size_pct:        float = 0.0    # frazione del capitale (0-1)
    size_notional:   float = 0.0    # in USD
    risk_at_sl:      float = 0.0    # USD a rischio se SL colpito
    reason:          str  = ""
    pause_until:     str  = ""      # ISO timestamp se PAUSE_SYSTEM
    debug:           dict = field(default_factory=dict)


@dataclass
class KellyResult:
    n_obs:         int
    p_obs:         float
    p_lower:       float
    p_used:        float
    b_used:        float
    f_star:        float    # Kelly puro (0-1)
    f_used:        float    # Kelly frazionario applicato
    kelly_fraction:float
    size_kelly:    float    # f_used / stop_loss_pct
    flag:          str      # "prior" | "weak_edge" | "significant_edge"


@dataclass
class VolResult:
    atr_14_d:       float
    prezzo:         float
    sigma_daily:    float
    sigma_annual:   float
    sigma_target:   float
    size_vol:       float
    shock_active:   bool
    sigma_30d_mean: float


# ---------------------------------------------------------------------------
# Caricamento trades
# ---------------------------------------------------------------------------

def _load_trades(trades_path: Path, window: int) -> list[dict]:
    """Legge gli ultimi `window` trade da trades.jsonl."""
    if not trades_path.exists():
        return []
    lines = [l for l in trades_path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines[-window:]]


def _load_pause(state_dir: Path) -> dict:
    p = state_dir / "pause.json"
    if not p.exists():
        return {"active": False}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"active": False}


def _save_pause(state_dir: Path, until_iso: str, reason: str) -> None:
    (state_dir / "pause.json").write_text(json.dumps({
        "active": True,
        "until":  until_iso,
        "reason": reason,
    }, indent=2))


def _clear_pause(state_dir: Path) -> None:
    p = state_dir / "pause.json"
    if p.exists():
        p.write_text(json.dumps({"active": False}))


# ---------------------------------------------------------------------------
# Wilson lower bound
# ---------------------------------------------------------------------------

def _wilson_lower(p_obs: float, n: int, z: float = 1.96) -> float:
    """
    Lower bound del confidence interval di Wilson al livello z.
    Usato per stima conservativa del win rate — evita overfitting
    su campioni piccoli.
    """
    if n == 0:
        return 0.0
    z2 = z * z
    denom  = 1 + z2 / n
    center = (p_obs + z2 / (2 * n)) / denom
    margin = (z * math.sqrt((p_obs * (1 - p_obs) + z2 / (4 * n)) / n)) / denom
    return max(0.0, center - margin)


# ---------------------------------------------------------------------------
# 1. KELLY FRAZIONARIO
# ---------------------------------------------------------------------------

def _compute_kelly(trades: list[dict], cfg: dict) -> KellyResult:
    min_trades = int(cfg.get("min_trades_for_estimate", 50))
    frac_sig   = float(cfg.get("fraction_significant",  0.25))
    frac_weak  = float(cfg.get("fraction_weak_or_prior",0.10))
    z          = float(cfg.get("wilson_z",              1.96))
    prior_p    = float(cfg.get("prior_p",               0.30))
    prior_b    = float(cfg.get("prior_b",               2.00))

    n_obs = len(trades)

    if n_obs < min_trades:
        # Prior conservativo — troppo pochi dati
        f_star = max(0.0, (prior_p * prior_b - (1 - prior_p)) / prior_b)
        f_used = frac_weak * f_star
        return KellyResult(
            n_obs=n_obs, p_obs=prior_p, p_lower=prior_p,
            p_used=prior_p, b_used=prior_b,
            f_star=f_star, f_used=f_used,
            kelly_fraction=frac_weak, size_kelly=0.0,  # calcolato fuori
            flag="prior",
        )

    wins   = [t["pnl_pct"] for t in trades if float(t.get("pnl_pct", 0)) > 0]
    losses = [abs(float(t["pnl_pct"])) for t in trades if float(t.get("pnl_pct", 0)) <= 0]

    p_obs   = len(wins) / n_obs
    avg_win = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss= sum(losses) / len(losses) if losses else 1e-6
    b       = avg_win / max(avg_loss, 1e-6)

    p_lower = _wilson_lower(p_obs, n_obs, z)

    # Edge significativo al 95%?
    edge_at_lower = p_lower * b - (1 - p_lower)
    if edge_at_lower <= 0:
        # Edge non dimostrabile — usa tenth Kelly
        p_used  = p_obs
        b_used  = b
        kelly_f = frac_weak
        flag    = "weak_edge"
    else:
        # Edge robusto — usa quarter Kelly sul lower bound
        p_used  = p_lower   # conservativo: non usare p_obs
        b_used  = b
        kelly_f = frac_sig
        flag    = "significant_edge"

    f_star = max(0.0, (p_used * b_used - (1 - p_used)) / b_used)
    f_used = kelly_f * f_star

    return KellyResult(
        n_obs=n_obs, p_obs=p_obs, p_lower=p_lower,
        p_used=p_used, b_used=b_used,
        f_star=f_star, f_used=f_used,
        kelly_fraction=kelly_f, size_kelly=0.0,
        flag=flag,
    )


# ---------------------------------------------------------------------------
# 2. VOLATILITY TARGETING
# ---------------------------------------------------------------------------

def _compute_vol(
    atr_14_d:          float | None,
    prezzo:            float,
    sigma_history:     list[float],   # sigma_daily giornaliero (30gg)
    cfg:               dict,
) -> VolResult:
    sigma_target   = float(cfg.get("sigma_target_annual",  0.18))
    annualize      = float(cfg.get("annualization_factor", 19.105))  # sqrt(365)
    shock_mult     = float(cfg.get("shock_multiplier",     2.0))
    shock_reduce   = float(cfg.get("shock_size_reduction", 0.5))

    # Stima sigma daily corrente
    if atr_14_d and prezzo > 0:
        sigma_daily = atr_14_d / prezzo
    elif sigma_history:
        sigma_daily = sigma_history[-1]
    else:
        sigma_daily = 0.025   # fallback: 2.5% al giorno (BTC tipico)

    sigma_annual = sigma_daily * annualize

    # Size da vol targeting: target_vol / realized_vol
    size_vol = sigma_target / max(sigma_annual, 1e-6)
    size_vol = min(size_vol, 1.0)   # cap a 100% prima dei caps finali

    # Vol shock detector
    shock_active  = False
    sigma_30d_mean = sigma_daily  # default se non abbiamo storia
    if len(sigma_history) >= 5:
        sigma_30d_mean = sum(sigma_history) / len(sigma_history)
        if sigma_daily > shock_mult * sigma_30d_mean:
            shock_active = True
            size_vol    *= shock_reduce

    return VolResult(
        atr_14_d      = atr_14_d or 0.0,
        prezzo        = prezzo,
        sigma_daily   = round(sigma_daily, 6),
        sigma_annual  = round(sigma_annual, 4),
        sigma_target  = sigma_target,
        size_vol      = round(size_vol, 6),
        shock_active  = shock_active,
        sigma_30d_mean= round(sigma_30d_mean, 6),
    )


# ---------------------------------------------------------------------------
# 3. CIRCUIT BREAKERS
# ---------------------------------------------------------------------------

def _check_edge_degradation(trades: list[dict], b_used: float, cfg: dict) -> bool:
    """
    True se l'edge sta degenerando negli ultimi 30 trade.
    Win rate < break-even - tolleranza.
    """
    window    = int(cfg.get("edge_check_window",    30))
    tolerance = float(cfg.get("edge_check_tolerance", 0.05))
    if len(trades) < window:
        return False
    recent = trades[-window:]
    wr_30  = sum(1 for t in recent if float(t.get("pnl_pct", 0)) > 0) / window
    be_wr  = 1.0 / (1.0 + max(b_used, 1e-6))
    return wr_30 < (be_wr - tolerance)


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

def _log_decision(
    state_dir:      Path,
    decision:       SizingDecision,
    capitale:       float,
    confidence:     float,
    dd_penalty:     float,
    stop_loss_pct:  float,
    atr_14_d:       float | None,
    prezzo:         float,
    k:              KellyResult,
    v:              VolResult,
    size_base:      float,
    size_after_conf:float,
    size_after_dd:  float,
) -> None:
    record = {
        "ts":     datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "action": decision.action,
        "size_pct":          round(decision.size_pct, 6),
        "size_notional_usd": round(decision.size_notional, 2),
        "risk_at_sl_usd":    round(decision.risk_at_sl, 2),
        "inputs": {
            "capitale":      capitale,
            "confidence":    confidence,
            "dd_penalty":    dd_penalty,
            "stop_loss_pct": stop_loss_pct,
            "atr_14_d":      atr_14_d,
            "prezzo":        prezzo,
        },
        "kelly": {
            "n_obs":         k.n_obs,
            "p_obs":         round(k.p_obs, 6),
            "p_lower":       round(k.p_lower, 6),
            "p_used":        round(k.p_used, 6),
            "b":             round(k.b_used, 4),
            "f_star":        round(k.f_star, 6),
            "f_used":        round(k.f_used, 6),
            "size_kelly":    round(k.size_kelly, 6),
            "flag":          k.flag,
            "kelly_fraction":k.kelly_fraction,
        },
        "vol": {
            "sigma_annual":  v.sigma_annual,
            "sigma_daily":   v.sigma_daily,
            "sigma_30d_mean":v.sigma_30d_mean,
            "sigma_target":  v.sigma_target,
            "size_vol":      round(v.size_vol, 6),
            "shock_active":  v.shock_active,
        },
        "final": {
            "size_base":         round(size_base, 6),
            "after_confidence":  round(size_after_conf, 6),
            "after_dd":          round(size_after_dd, 6),
            "after_caps":        round(decision.size_pct, 6),
        },
        "reason": decision.reason,
    }
    log_path = state_dir / "sizing_log.jsonl"
    with log_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# API PUBBLICA
# ---------------------------------------------------------------------------

def compute_position_size(
    capitale:           float,
    confidence:         float,
    dd_penalty:         float,         # [0.5, 1.0] dalla logica esistente
    atr_14_d:           float | None,  # ATR(14) daily in $
    prezzo:             float,
    sigma_30d_history:  list[float],   # sigma_daily ultimi 30gg
    trades_history:     list[dict],    # tutti i trade chiusi
    state_dir:          Path,
    config:             dict,          # sezione sizing: da strategy.yaml
    stop_loss_pct:      float = 0.05,
) -> SizingDecision:
    """
    Calcola la size ottimale combinando Kelly frazionario e vol targeting.
    Scrive il risultato in state/sizing_log.jsonl.
    """
    kelly_cfg  = config.get("kelly",         {})
    vol_cfg    = config.get("vol_target",    {})
    cap_cfg    = config.get("caps",          {})
    cb_cfg     = config.get("circuit_breakers", {})

    max_size   = float(cap_cfg.get("max_size_pct", 0.50))
    min_size   = float(cap_cfg.get("min_size_pct", 0.05))
    pause_days = int(cb_cfg.get("pause_days_on_edge_loss", 7))

    # --- Controlla pausa attiva ---
    pause_state = _load_pause(state_dir)
    if pause_state.get("active"):
        until_str = pause_state.get("until", "")
        if until_str:
            try:
                until_dt = datetime.fromisoformat(until_str)
                if datetime.now(timezone.utc) < until_dt:
                    d = SizingDecision(
                        action   = "PAUSE_SYSTEM",
                        reason   = f"pausa attiva fino a {until_str}",
                        pause_until = until_str,
                        debug    = {"pause_state": pause_state},
                    )
                    _log_decision(state_dir, d, capitale, confidence, dd_penalty,
                                  stop_loss_pct, atr_14_d, prezzo,
                                  KellyResult(0,0,0,0,0,0,0,0,0,"prior"),
                                  VolResult(0,prezzo,0,0,0,0,False,0),
                                  0, 0, 0)
                    return d
                else:
                    _clear_pause(state_dir)
            except ValueError:
                _clear_pause(state_dir)

    # --- Kelly ---
    window = int(kelly_cfg.get("rolling_window", 150))
    trades = trades_history[-window:] if len(trades_history) > window else trades_history
    k      = _compute_kelly(trades, kelly_cfg)
    k.size_kelly = k.f_used / max(stop_loss_pct, 1e-6)

    # --- Vol targeting ---
    v = _compute_vol(atr_14_d, prezzo, sigma_30d_history, vol_cfg)

    # --- Combinazione ---
    size_base      = min(k.size_kelly, v.size_vol)
    size_after_conf= size_base * max(0.0, min(1.0, confidence))
    size_after_dd  = size_after_conf * max(0.0, min(1.0, dd_penalty))
    size_capped    = min(size_after_dd, max_size)

    # --- Circuit breaker: edge degradation ---
    if _check_edge_degradation(trades, k.b_used, cb_cfg):
        until_dt  = datetime.now(timezone.utc) + timedelta(days=pause_days)
        until_str = until_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        _save_pause(state_dir, until_str, "edge_degradation")
        d = SizingDecision(
            action      = "PAUSE_SYSTEM",
            reason      = "edge_degradation: WR ultimi 30 trade sotto break-even",
            pause_until = until_str,
            debug       = {"b_used": k.b_used, "n_trades": len(trades)},
        )
        _log_decision(state_dir, d, capitale, confidence, dd_penalty,
                      stop_loss_pct, atr_14_d, prezzo, k, v,
                      size_base, size_after_conf, size_after_dd)
        return d

    # --- Skip se size troppo piccola ---
    if size_capped < min_size:
        d = SizingDecision(
            action = "SKIP_TRADE",
            reason = f"size {size_capped:.3f} < min {min_size}",
            debug  = {
                "size_kelly": k.size_kelly, "size_vol": v.size_vol,
                "size_base": size_base, "after_conf": size_after_conf,
                "after_dd": size_after_dd,
            },
        )
        _log_decision(state_dir, d, capitale, confidence, dd_penalty,
                      stop_loss_pct, atr_14_d, prezzo, k, v,
                      size_base, size_after_conf, size_after_dd)
        return d

    # --- OPEN_TRADE ---
    notional   = round(size_capped * capitale, 2)
    risk_at_sl = round(notional * stop_loss_pct, 2)

    d = SizingDecision(
        action         = "OPEN_TRADE",
        size_pct       = round(size_capped, 6),
        size_notional  = notional,
        risk_at_sl     = risk_at_sl,
        reason         = f"kelly={k.flag} vol_shock={v.shock_active}",
        debug          = {
            "size_kelly":  round(k.size_kelly, 4),
            "size_vol":    round(v.size_vol, 4),
            "size_base":   round(size_base, 4),
            "f_star":      round(k.f_star, 4),
            "f_used":      round(k.f_used, 4),
            "p_used":      round(k.p_used, 4),
            "b_used":      round(k.b_used, 4),
            "sigma_annual":v.sigma_annual,
            "vol_shock":   v.shock_active,
            "n_obs":       k.n_obs,
            "flag":        k.flag,
        },
    )
    _log_decision(state_dir, d, capitale, confidence, dd_penalty,
                  stop_loss_pct, atr_14_d, prezzo, k, v,
                  size_base, size_after_conf, size_after_dd)
    return d

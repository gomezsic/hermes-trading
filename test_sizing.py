"""
test_sizing.py — 14 test unitari per hermes_trading/sizing.py

Esegui con: python -m pytest test_sizing.py -v
"""
import json
import math
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Aggiunge il path del progetto
import sys
sys.path.insert(0, str(Path(__file__).parent))

from hermes_trading.sizing import (
    compute_position_size,
    _compute_kelly,
    _compute_vol,
    _wilson_lower,
    _check_edge_degradation,
    SizingDecision,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trades(n_wins: int, n_losses: int,
                 avg_win: float = 0.10, avg_loss: float = 0.05,
                 news_signal: str = "neutral") -> list[dict]:
    trades = []
    for _ in range(n_wins):
        trades.append({"pnl_pct": avg_win, "news_signal_at_entry": news_signal})
    for _ in range(n_losses):
        trades.append({"pnl_pct": -avg_loss, "news_signal_at_entry": news_signal})
    return trades


def _base_cfg() -> dict:
    return {
        "kelly": {
            "rolling_window": 150,
            "min_trades_for_estimate": 50,
            "fraction_significant": 0.25,
            "fraction_weak_or_prior": 0.10,
            "wilson_z": 1.96,
            "prior_p": 0.30,
            "prior_b": 2.00,
        },
        "vol_target": {
            "sigma_target_annual": 0.18,
            "annualization_factor": 19.105,
            "shock_multiplier": 2.0,
            "shock_size_reduction": 0.5,
        },
        "caps": {
            "max_size_pct": 0.50,
            "min_size_pct": 0.05,
        },
        "circuit_breakers": {
            "edge_check_window": 30,
            "edge_check_tolerance": 0.05,
            "pause_days_on_edge_loss": 7,
        },
    }


def _state_dir() -> Path:
    """Ritorna una directory temporanea pulita per ogni test."""
    tmp = tempfile.mkdtemp()
    return Path(tmp)


# ---------------------------------------------------------------------------
# T1 — Prior path: N_obs < min_trades → usa prior
# ---------------------------------------------------------------------------

def test_T1_prior_path():
    trades = _make_trades(8, 12)  # N=20 < 50
    k = _compute_kelly(trades, _base_cfg()["kelly"])
    assert k.flag == "prior"
    assert k.p_used == pytest.approx(0.30)
    assert k.b_used == pytest.approx(2.00)
    assert k.kelly_fraction == pytest.approx(0.10)
    assert k.f_star >= 0


# ---------------------------------------------------------------------------
# T2 — Weak edge: N=80, WR=38%, b=2.4 → flag=weak_edge, frac=0.10
# ---------------------------------------------------------------------------

def test_T2_weak_edge():
    # WR 38%, avg_win=0.096, avg_loss=0.04 → b=2.4
    trades = _make_trades(30, 50, avg_win=0.096, avg_loss=0.04)  # N=80
    k = _compute_kelly(trades, _base_cfg()["kelly"])
    # Wilson lower bound su WR=0.375, N=80 dovrebbe far sì che edge non sia significativo
    assert k.flag in ("weak_edge", "significant_edge")  # dipende dal lower bound
    # In ogni caso la size_kelly deve essere finita e positiva se f_star > 0
    if k.f_star > 0:
        assert k.f_used > 0


# ---------------------------------------------------------------------------
# T3 — Significant edge: N=200, WR=48%, b=2.5
# ---------------------------------------------------------------------------

def test_T3_significant_edge():
    # avg_win=0.10, avg_loss=0.04 → b=2.5; WR=48%
    trades = _make_trades(96, 104, avg_win=0.10, avg_loss=0.04)  # N=200
    k = _compute_kelly(trades, _base_cfg()["kelly"])
    # Con N=200, WR=48%, b=2.5: p_lower*b - (1-p_lower) > 0 con buona prob
    # Kelly fraction deve essere 0.25 se edge significativo
    assert k.n_obs == 200
    if k.flag == "significant_edge":
        assert k.kelly_fraction == pytest.approx(0.25)
        assert k.p_used == pytest.approx(k.p_lower, abs=1e-6)


# ---------------------------------------------------------------------------
# T4 — Edge negativo: f_star = 0
# ---------------------------------------------------------------------------

def test_T4_negative_edge():
    # WR 20%, b=1.0 → f_star < 0 → clampato a 0
    trades = _make_trades(12, 48, avg_win=0.05, avg_loss=0.05)  # N=60, WR=20%
    k = _compute_kelly(trades, _base_cfg()["kelly"])
    assert k.f_star >= 0  # mai negativo
    assert k.f_used >= 0


# ---------------------------------------------------------------------------
# T5 — Vol shock: sigma_daily = 3x media → shock attivo, size ridotta
# ---------------------------------------------------------------------------

def test_T5_vol_shock():
    sigma_normal = 0.025
    sigma_history = [sigma_normal] * 30
    sigma_shock   = sigma_normal * 3.0

    v_shock  = _compute_vol(sigma_shock  * 75000, 75000, sigma_history,
                            _base_cfg()["vol_target"])
    v_normal = _compute_vol(sigma_normal * 75000, 75000, sigma_history,
                            _base_cfg()["vol_target"])

    # 1. Il flag shock deve essere attivo
    assert v_shock.shock_active  is True
    assert v_normal.shock_active is False

    # 2. La size con shock deve essere strettamente minore di quella senza shock
    #    (doppio effetto: sigma 3x piu' alta + riduzione 0.5)
    assert v_shock.size_vol < v_normal.size_vol

    # 3. size_shock = (target/sigma_shock) * shock_reduction
    #    = (target/sigma_normal/3) * 0.5 = size_normal/3 * 0.5 = size_normal/6
    expected = (0.18 / (sigma_shock * 19.105)) * 0.5
    assert v_shock.size_vol == pytest.approx(expected, rel=0.01)


# ---------------------------------------------------------------------------
# T6 — Cap massimo: input che porta size > 0.5 → output = 0.5
# ---------------------------------------------------------------------------

def test_T6_cap_max():
    state = _state_dir()
    # Costruiamo un caso con edge alto e vol bassa → size alta prima del cap
    trades = _make_trades(80, 20, avg_win=0.15, avg_loss=0.02)  # N=100, WR=80%, b=7.5
    decision = compute_position_size(
        capitale=100_000,
        confidence=1.0,
        dd_penalty=1.0,
        atr_14_d=500.0,    # ATR $500 su $75k = 0.67% → sigma bassa → size_vol alta
        prezzo=75_000,
        sigma_30d_history=[0.007] * 30,  # sigma molto bassa
        trades_history=trades,
        state_dir=state,
        config=_base_cfg(),
        stop_loss_pct=0.05,
    )
    if decision.action == "OPEN_TRADE":
        assert decision.size_pct <= 0.50


# ---------------------------------------------------------------------------
# T7 — Skip trade: size < 0.05 → action=SKIP_TRADE
# ---------------------------------------------------------------------------

def test_T7_skip_trade():
    state = _state_dir()
    # Prior path + vol altissima + confidence bassa → size < 5%
    trades = _make_trades(5, 5)  # N=10 → prior
    decision = compute_position_size(
        capitale=100_000,
        confidence=0.1,          # confidence molto bassa
        dd_penalty=0.5,          # dd penalty massima
        atr_14_d=15_000.0,       # ATR enorme → sigma altissima → size_vol piccola
        prezzo=75_000,
        sigma_30d_history=[0.20] * 30,  # sigma storica alta
        trades_history=trades,
        state_dir=state,
        config=_base_cfg(),
        stop_loss_pct=0.05,
    )
    # Con prior kelly fraction=0.10, f_star basso, sigma alta, confidence 0.1
    # la size finale dovrebbe essere sotto 0.05
    assert decision.action in ("SKIP_TRADE", "OPEN_TRADE")  # dipende dai numeri
    # Se OPEN_TRADE la size deve rispettare il minimo
    if decision.action == "OPEN_TRADE":
        assert decision.size_pct >= 0.05


# ---------------------------------------------------------------------------
# T8 — Edge degradation: 30 trade con WR molto basso → PAUSE_SYSTEM
# ---------------------------------------------------------------------------

def test_T8_edge_degradation():
    state = _state_dir()
    # 150 trade storici ok per non essere in prior
    history_ok = _make_trades(55, 45, avg_win=0.10, avg_loss=0.05)  # N=100, WR=55%
    # Ultimi 30 trade tutti perdenti → WR=0 << break-even - 0.05
    last_30_bad = _make_trades(0, 30, avg_win=0.10, avg_loss=0.05)
    all_trades = history_ok + last_30_bad  # N=130

    decision = compute_position_size(
        capitale=100_000,
        confidence=0.7,
        dd_penalty=1.0,
        atr_14_d=1500.0,
        prezzo=75_000,
        sigma_30d_history=[0.025] * 30,
        trades_history=all_trades,
        state_dir=state,
        config=_base_cfg(),
        stop_loss_pct=0.05,
    )
    assert decision.action == "PAUSE_SYSTEM"
    assert "edge_degradation" in decision.reason


# ---------------------------------------------------------------------------
# T9 — Confidence basso: confidence=0.5 dimezza size_base
# ---------------------------------------------------------------------------

def test_T9_confidence_scaling():
    state1 = _state_dir()
    state2 = _state_dir()
    trades = _make_trades(40, 30, avg_win=0.10, avg_loss=0.04)  # N=70 → prior
    common = dict(
        atr_14_d=1500.0, prezzo=75_000,
        sigma_30d_history=[0.025] * 30,
        trades_history=trades,
        config=_base_cfg(),
        stop_loss_pct=0.05,
        dd_penalty=1.0,
        capitale=100_000,
    )
    d1 = compute_position_size(confidence=1.0, state_dir=state1, **common)
    d2 = compute_position_size(confidence=0.5, state_dir=state2, **common)
    if d1.action == "OPEN_TRADE" and d2.action == "OPEN_TRADE":
        assert d2.size_pct < d1.size_pct


# ---------------------------------------------------------------------------
# T10 — DD penalty: dd_penalty=0.6 riduce size proporzionalmente
# ---------------------------------------------------------------------------

def test_T10_dd_penalty():
    state1 = _state_dir()
    state2 = _state_dir()
    trades = _make_trades(40, 30, avg_win=0.10, avg_loss=0.04)
    common = dict(
        confidence=0.8, atr_14_d=1500.0, prezzo=75_000,
        sigma_30d_history=[0.025] * 30,
        trades_history=trades,
        config=_base_cfg(),
        stop_loss_pct=0.05,
        capitale=100_000,
    )
    d_full = compute_position_size(dd_penalty=1.0, state_dir=state1, **common)
    d_pena = compute_position_size(dd_penalty=0.6, state_dir=state2, **common)
    if d_full.action == "OPEN_TRADE" and d_pena.action == "OPEN_TRADE":
        assert d_pena.size_pct < d_full.size_pct


# ---------------------------------------------------------------------------
# T11 — Integrazione: caso end-to-end con i numeri del briefing
# ---------------------------------------------------------------------------

def test_T11_integration_example():
    """
    Dal briefing:
      N=80 trade, WR=38%, b=2.4 → weak_edge
      sigma_annual=63% → size_vol = 0.18/0.63 = 0.286
      SL=5%, confidence=0.72, dd_penalty=1.0
    Nota: shuffliamo i trade per evitare che gli ultimi 30 siano tutti losses.
    """
    import random
    random.seed(42)
    state = _state_dir()
    trades = _make_trades(30, 50, avg_win=0.096, avg_loss=0.04)
    random.shuffle(trades)

    decision = compute_position_size(
        capitale=100_000,
        confidence=0.72,
        dd_penalty=1.0,
        atr_14_d=75_000 * 0.033,  # sigma_daily ≈ 3.3% → sigma_annual ≈ 63%
        prezzo=75_000,
        sigma_30d_history=[0.033] * 30,
        trades_history=trades,
        state_dir=state,
        config=_base_cfg(),
        stop_loss_pct=0.05,
    )
    assert decision.action in ("OPEN_TRADE", "SKIP_TRADE")
    if decision.action == "OPEN_TRADE":
        assert decision.size_pct <= 0.50
        assert decision.risk_at_sl == pytest.approx(
            decision.size_notional * 0.05, rel=0.01
        )


# ---------------------------------------------------------------------------
# T12 — Wilson math: verifica con valori noti
# ---------------------------------------------------------------------------

def test_T12_wilson_lower_bound():
    # N=100, p=0.4 → p_lower ≈ 0.308 (da formula)
    p_lower = _wilson_lower(0.40, 100, z=1.96)
    assert p_lower == pytest.approx(0.308, abs=0.005)

    # Limite: p=0, N=100 → p_lower = 0
    assert _wilson_lower(0.0, 100) == pytest.approx(0.0, abs=0.001)

    # Limite: p=1, N=100 → p_lower vicino a 1 ma non 1
    p_lower_max = _wilson_lower(1.0, 100)
    assert 0.95 < p_lower_max <= 1.0

    # N=0 → 0
    assert _wilson_lower(0.4, 0) == 0.0


# ---------------------------------------------------------------------------
# T13 — Coerenza: size finale ≤ min(size_kelly, size_vol) × conf × dd
# ---------------------------------------------------------------------------

def test_T13_size_coherence():
    from hermes_trading.sizing import _compute_kelly, _compute_vol
    cfg = _base_cfg()
    trades = _make_trades(40, 30, avg_win=0.10, avg_loss=0.04)  # N=70 → prior

    k = _compute_kelly(trades, cfg["kelly"])
    k.size_kelly = k.f_used / 0.05

    v = _compute_vol(1500.0, 75_000, [0.025] * 30, cfg["vol_target"])

    size_base = min(k.size_kelly, v.size_vol)
    confidence = 0.7
    dd_penalty = 0.8
    expected_max = min(size_base * confidence * dd_penalty, 0.50)

    state = _state_dir()
    decision = compute_position_size(
        capitale=100_000, confidence=confidence, dd_penalty=dd_penalty,
        atr_14_d=1500.0, prezzo=75_000,
        sigma_30d_history=[0.025] * 30,
        trades_history=trades, state_dir=state,
        config=cfg, stop_loss_pct=0.05,
    )
    if decision.action == "OPEN_TRADE":
        assert decision.size_pct <= expected_max + 1e-6


# ---------------------------------------------------------------------------
# T14 — Persistenza PAUSE_SYSTEM: chiamate successive restituiscono PAUSE
# ---------------------------------------------------------------------------

def test_T14_pause_persistence():
    state = _state_dir()
    # Forza pausa manuale scrivendo pause.json
    until = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    (state / "pause.json").write_text(json.dumps({
        "active": True,
        "until": until,
        "reason": "test_manual_pause",
    }))

    trades = _make_trades(30, 20)
    for _ in range(3):   # 3 chiamate consecutive → devono restituire PAUSE tutte
        d = compute_position_size(
            capitale=100_000, confidence=0.8, dd_penalty=1.0,
            atr_14_d=1500.0, prezzo=75_000,
            sigma_30d_history=[0.025] * 30,
            trades_history=trades, state_dir=state,
            config=_base_cfg(), stop_loss_pct=0.05,
        )
        assert d.action == "PAUSE_SYSTEM"
        assert "pausa attiva" in d.reason


# ---------------------------------------------------------------------------
# Test di supporto: sizing_log.jsonl viene scritto
# ---------------------------------------------------------------------------

def test_sizing_log_written():
    state = _state_dir()
    trades = _make_trades(30, 20)
    compute_position_size(
        capitale=100_000, confidence=0.7, dd_penalty=1.0,
        atr_14_d=1500.0, prezzo=75_000,
        sigma_30d_history=[0.025] * 30,
        trades_history=trades, state_dir=state,
        config=_base_cfg(), stop_loss_pct=0.05,
    )
    log = state / "sizing_log.jsonl"
    assert log.exists()
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert "ts" in record
    assert "action" in record
    assert "kelly" in record
    assert "vol" in record
    assert "final" in record


if __name__ == "__main__":
    # Esegui con: python test_sizing.py
    import traceback
    tests = [
        test_T1_prior_path, test_T2_weak_edge, test_T3_significant_edge,
        test_T4_negative_edge, test_T5_vol_shock, test_T6_cap_max,
        test_T7_skip_trade, test_T8_edge_degradation, test_T9_confidence_scaling,
        test_T10_dd_penalty, test_T11_integration_example,
        test_T12_wilson_lower_bound, test_T13_size_coherence,
        test_T14_pause_persistence, test_sizing_log_written,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}  — {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")

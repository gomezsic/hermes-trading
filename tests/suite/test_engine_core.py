"""Test per hermes_trading._engine_core — helper puri condivisi."""
from hermes_trading._engine_core import (
    RiskConfig,
    apply_slippage_entry,
    apply_slippage_exit,
    gross_pnl_pct,
)


def test_risk_config_dataclass():
    rc = RiskConfig(
        stop_loss_pct=0.03,
        partial_exit_pct=0.09,
        trailing_activate_pct=0.036,
        trailing_stop_pct=0.024,
        trailing_stop_tight_pct=0.015,
    )
    assert rc.stop_loss_pct == 0.03


def test_apply_slippage_entry_long_raises_price():
    # SLIPPAGE = 0.0005 (5 bp)
    out = apply_slippage_entry(100.0, "long")
    assert out == 100.0 * 1.0005


def test_apply_slippage_entry_short_lowers_price():
    out = apply_slippage_entry(100.0, "short")
    assert out == 100.0 * 0.9995


def test_apply_slippage_exit_long_lowers_price():
    out = apply_slippage_exit(100.0, "long")
    assert out == 100.0 * 0.9995


def test_apply_slippage_exit_short_raises_price():
    out = apply_slippage_exit(100.0, "short")
    assert out == 100.0 * 1.0005


def test_gross_pnl_pct_long():
    assert gross_pnl_pct(100.0, 110.0, "long") == 0.1


def test_gross_pnl_pct_short():
    assert gross_pnl_pct(100.0, 90.0, "short") == 0.1


from hermes_trading._engine_core import build_equity_curve


def test_build_equity_curve_no_trades_keeps_capital_flat():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(3)]
    curve = build_equity_curve(candles, trades=[], capital=10000.0)
    assert len(curve) == 3
    for row in curve:
        assert row["equity"] == 10000.0
        assert row["drawdown_pct"] == 0.0


def test_build_equity_curve_applies_pnl_at_exit_idx():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(5)]
    trades = [{"exit_idx": 2, "pnl_pct": 0.10}]   # +10% al trade
    curve = build_equity_curve(candles, trades, capital=1000.0)
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0
    assert curve[2]["equity"] == 1100.0          # 1000 * (1 + 0.10)
    assert curve[4]["equity"] == 1100.0
    assert curve[2]["drawdown_pct"] == 0.0


def test_build_equity_curve_drawdown_after_loss():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(4)]
    trades = [
        {"exit_idx": 1, "pnl_pct":  0.20},   # capitale 1200
        {"exit_idx": 3, "pnl_pct": -0.10},   # capitale 1080
    ]
    curve = build_equity_curve(candles, trades, capital=1000.0)
    assert curve[1]["equity"] == 1200.0
    assert curve[3]["equity"] == 1080.0
    # peak = 1200, equity = 1080 -> dd = (1200-1080)/1200 * 100 = 10%
    assert curve[3]["drawdown_pct"] == 10.0


from hermes_trading._engine_core import simulate_trade


def _flat_candles(n: int, price: float = 100.0) -> list[dict]:
    return [{"t": i, "o": price, "h": price, "l": price, "c": price, "v": 1.0}
            for i in range(n)]


def test_simulate_trade_forced_close_on_flat_market():
    candles = _flat_candles(5, 100.0)
    risk = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    trade = simulate_trade(candles, entry_idx=1, side="long", risk=risk)
    assert trade["reason"] == "forced_close"
    assert trade["exit_idx"] == 4
    assert trade["pnl_pct_gross"] < 0
    assert trade["partial_done"] is False


def test_simulate_trade_long_hits_stop_loss():
    candles = [
        {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1.0},
        {"t": 1, "o": 100, "h": 100, "l": 90,  "c": 95,  "v": 1.0},  # low 90 trigger SL=95
    ]
    risk = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    trade = simulate_trade(candles, entry_idx=0, side="long", risk=risk)
    assert trade["reason"] == "stop_loss"
    assert trade["exit_idx"] == 1


def test_simulate_trade_long_partial_then_trailing():
    candles = []
    for i, c in enumerate([100, 105, 112, 115, 110, 108, 105]):
        candles.append({"t": i, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1.0})
    risk = RiskConfig(
        stop_loss_pct=0.05,
        partial_exit_pct=0.10,
        trailing_activate_pct=0.06,
        trailing_stop_pct=0.04,
        trailing_stop_tight_pct=0.025,
    )
    trade = simulate_trade(candles, entry_idx=0, side="long", risk=risk)
    assert trade["partial_done"] is True

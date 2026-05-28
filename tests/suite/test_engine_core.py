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

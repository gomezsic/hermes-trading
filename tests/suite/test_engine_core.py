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

"""Test dell'engine generico backtest_suite.engine."""
from backtest_suite.engine.types import (
    ExecutionConfig,
    Trade,
    BacktestResult,
)


def test_execution_config_defaults_match_legacy():
    ec = ExecutionConfig()
    assert ec.taker_fee == 0.0026
    assert ec.slippage == 0.0005
    assert ec.latency_bars == 1
    assert ec.capital == 10_000.0
    assert ec.allow_overlap is False
    assert ec.direction == "both"


def test_trade_dataclass_fields():
    t = Trade(
        side="long", entry_idx=1, exit_idx=5, entry=100.0, exit=110.0,
        pnl_pct=0.10, pnl_pct_gross=0.10, fee_paid=0.0052,
        reason="forced_close", partial_done=False,
    )
    assert t.pnl_pct == 0.10
    assert t.partial_done is False


def test_backtest_result_holds_trades_and_curve():
    r = BacktestResult(
        trades=[],
        equity_curve=[],
        metrics={},
        config_hash="abc12345",
        n_candles=0,
    )
    assert r.config_hash == "abc12345"

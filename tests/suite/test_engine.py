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


from hermes_trading._engine_core import RiskConfig
from backtest_suite.engine import run_backtest
from backtest_suite.strategies.ema_cross import EmaCrossStrategy


def _gen_market_candles(n: int = 300) -> list[dict]:
    """Mercato sintetico con due trend chiari (per generare almeno 1 trade)."""
    import math
    candles = []
    for i in range(n):
        price = 100.0 + 20.0 * math.sin(i / 25.0) + i * 0.05
        candles.append({"t": i * 3600, "o": price, "h": price + 1.0,
                        "l": price - 1.0, "c": price, "v": 100.0})
    return candles


def test_run_backtest_returns_result_with_trades_and_curve():
    candles = _gen_market_candles(300)
    strat   = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 20,
                                "vwap_filter": 0, "direction": 2})
    risk    = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    exec_   = ExecutionConfig()

    result = run_backtest(candles, strat, risk, exec_)

    assert isinstance(result, BacktestResult)
    assert result.n_candles == 300
    assert len(result.equity_curve) == 300
    assert result.metrics["n_trades"] >= 1


def test_run_backtest_no_signals_returns_empty_trades():
    candles = [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1.0}
               for i in range(100)]
    strat   = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 20,
                                "vwap_filter": 0, "direction": 2})
    risk    = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    result  = run_backtest(candles, strat, risk, ExecutionConfig())
    assert result.trades == []

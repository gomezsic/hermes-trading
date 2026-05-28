"""
Regression gate: il nuovo engine + EmaCrossStrategy deve produrre output
bit-perfect identico al backtester legacy. Se questo test fallisce, blocca
il merge: significa che il refactor ha alterato il comportamento osservabile.
"""
import math
import random

from hermes_trading.backtester import run_backtest as legacy_run
from hermes_trading._engine_core import RiskConfig
from backtest_suite.engine import run_backtest as new_run
from backtest_suite.engine.types import ExecutionConfig
from backtest_suite.strategies.ema_cross import EmaCrossStrategy


def _make_candles(n: int, seed: int = 42, trend: float = 0.0001) -> list[dict]:
    """Stesso generatore di test_walk_forward._make_candles per consistenza."""
    rng = random.Random(seed)
    candles = []
    price = 30000.0
    t = 1700000000
    for i in range(n):
        price *= (1.0 + trend + rng.gauss(0.0, 0.01))
        o = price * (1.0 + rng.gauss(0.0, 0.001))
        c = price * (1.0 + rng.gauss(0.0, 0.001))
        h = max(o, c) * (1.0 + abs(rng.gauss(0.0, 0.002)))
        l = min(o, c) * (1.0 - abs(rng.gauss(0.0, 0.002)))
        candles.append({"t": t + i * 3600, "o": o, "h": h, "l": l,
                        "c": c, "v": 100.0 + rng.uniform(0, 50)})
    return candles


# Strategy YAML come in state/strategy.yaml (scala percentuale)
STRATEGY_YAML = {
    "ema_fast": 20,
    "ema_slow": 50,
    "vwap_window": 200,
    "vwap_filter": False,
    "direction": "both",
    "stop_loss_pct":            3.0,
    "partial_exit_pct":         9.0,
    "trailing_activate_pct":    3.6,
    "trailing_stop_pct":        2.4,
    "trailing_stop_tight_pct":  1.5,
}


def _params_for_new_engine():
    """Adatta lo strategy yaml ai parametri di EmaCrossStrategy + RiskConfig."""
    direction_map = {"long": 0, "short": 1, "both": 2}
    params = {
        "ema_fast":    STRATEGY_YAML["ema_fast"],
        "ema_slow":    STRATEGY_YAML["ema_slow"],
        "vwap_window": STRATEGY_YAML["vwap_window"],
        "vwap_filter": 1 if STRATEGY_YAML["vwap_filter"] else 0,
        "direction":   direction_map[STRATEGY_YAML["direction"]],
    }
    risk = RiskConfig(
        stop_loss_pct           = STRATEGY_YAML["stop_loss_pct"]           / 100.0,
        partial_exit_pct        = STRATEGY_YAML["partial_exit_pct"]        / 100.0,
        trailing_activate_pct   = STRATEGY_YAML["trailing_activate_pct"]   / 100.0,
        trailing_stop_pct       = STRATEGY_YAML["trailing_stop_pct"]       / 100.0,
        trailing_stop_tight_pct = STRATEGY_YAML["trailing_stop_tight_pct"] / 100.0,
    )
    return params, risk


def test_bit_perfect_equivalence_long_run():
    candles = _make_candles(2000, seed=42)
    capital = 10_000.0

    legacy = legacy_run(candles, STRATEGY_YAML, capital, seed=42)

    params, risk = _params_for_new_engine()
    new = new_run(candles, EmaCrossStrategy(params), risk, ExecutionConfig(capital=capital))

    assert len(new.trades) == len(legacy["trades"]), \
        f"trade count mismatch: new={len(new.trades)} legacy={len(legacy['trades'])}"

    for i, (nt, lt) in enumerate(zip(new.trades, legacy["trades"])):
        assert nt.entry_idx == lt["entry_idx"], f"trade {i} entry_idx"
        assert nt.exit_idx  == lt["exit_idx"],  f"trade {i} exit_idx"
        assert nt.side      == lt["side"],      f"trade {i} side"
        assert nt.entry     == lt["entry"],     f"trade {i} entry price"
        assert nt.exit      == lt["exit"],      f"trade {i} exit price"
        assert nt.pnl_pct   == lt["pnl_pct"],   f"trade {i} pnl_pct"
        assert nt.reason    == lt["reason"],    f"trade {i} reason"
        assert nt.partial_done == lt["partial_done"], f"trade {i} partial_done"

    assert len(new.equity_curve) == len(legacy["equity_curve"])
    for i, (n_row, l_row) in enumerate(zip(new.equity_curve, legacy["equity_curve"])):
        assert n_row["equity"]       == l_row["equity"],       f"equity idx {i}"
        assert n_row["drawdown_pct"] == l_row["drawdown_pct"], f"dd idx {i}"

    for key in ("max_drawdown", "cvar_5pct", "calmar_ratio", "ulcer_index",
                "tail_ratio", "sharpe", "win_rate", "n_trades", "expectancy"):
        assert new.metrics[key] == legacy["metrics"][key], f"metric {key}"


def test_bit_perfect_equivalence_short_series():
    candles = _make_candles(200, seed=7)
    capital = 5_000.0

    legacy = legacy_run(candles, STRATEGY_YAML, capital, seed=7)
    params, risk = _params_for_new_engine()
    new = new_run(candles, EmaCrossStrategy(params), risk, ExecutionConfig(capital=capital))

    assert len(new.trades) == len(legacy["trades"])
    for nt, lt in zip(new.trades, legacy["trades"]):
        assert nt.entry_idx == lt["entry_idx"]
        assert nt.pnl_pct   == lt["pnl_pct"]

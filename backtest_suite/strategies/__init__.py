"""strategies — registry e implementazioni delle Strategy."""
from backtest_suite.strategies.base import ParamSpec, Signal, Strategy
from backtest_suite.strategies.ema_cross import EmaCrossStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    EmaCrossStrategy.strategy_id: EmaCrossStrategy,
}

__all__ = ["ParamSpec", "Signal", "Strategy", "STRATEGY_REGISTRY", "EmaCrossStrategy"]

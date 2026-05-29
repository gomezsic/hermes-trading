"""strategies — registry e implementazioni delle Strategy."""
from backtest_suite.strategies.base       import ParamSpec, Signal, Strategy
from backtest_suite.strategies.ema_cross  import EmaCrossStrategy
from backtest_suite.strategies.rsi_mr     import RsiMeanReversionStrategy
from backtest_suite.strategies.bb_breakout import BollingerBreakoutStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    EmaCrossStrategy.strategy_id:           EmaCrossStrategy,
    RsiMeanReversionStrategy.strategy_id:   RsiMeanReversionStrategy,
    BollingerBreakoutStrategy.strategy_id:  BollingerBreakoutStrategy,
}

__all__ = [
    "ParamSpec", "Signal", "Strategy", "STRATEGY_REGISTRY",
    "EmaCrossStrategy", "RsiMeanReversionStrategy", "BollingerBreakoutStrategy",
]

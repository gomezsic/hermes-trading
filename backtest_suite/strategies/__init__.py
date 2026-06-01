"""strategies — registry e implementazioni delle Strategy."""
from backtest_suite.strategies.base              import ParamSpec, Signal, Strategy
from backtest_suite.strategies.ema_cross         import EmaCrossStrategy
from backtest_suite.strategies.rsi_mr            import RsiMeanReversionStrategy
from backtest_suite.strategies.bb_breakout       import BollingerBreakoutStrategy
from backtest_suite.strategies.price_vs_ma_cross import PriceVsMaCrossStrategy
from backtest_suite.strategies.donchian_breakout import DonchianBreakoutStrategy
from backtest_suite.strategies.macd_signal       import MacdSignalStrategy
from backtest_suite.strategies.supertrend        import SupertrendStrategy
from backtest_suite.strategies.momentum_roc      import MomentumRocStrategy
from backtest_suite.strategies.keltner_breakout  import KeltnerBreakoutStrategy
from backtest_suite.strategies.vwap_reversion    import VwapReversionStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    EmaCrossStrategy.strategy_id:           EmaCrossStrategy,
    RsiMeanReversionStrategy.strategy_id:   RsiMeanReversionStrategy,
    BollingerBreakoutStrategy.strategy_id:  BollingerBreakoutStrategy,
    PriceVsMaCrossStrategy.strategy_id:     PriceVsMaCrossStrategy,
    DonchianBreakoutStrategy.strategy_id:   DonchianBreakoutStrategy,
    MacdSignalStrategy.strategy_id:         MacdSignalStrategy,
    SupertrendStrategy.strategy_id:         SupertrendStrategy,
    MomentumRocStrategy.strategy_id:        MomentumRocStrategy,
    KeltnerBreakoutStrategy.strategy_id:    KeltnerBreakoutStrategy,
    VwapReversionStrategy.strategy_id:      VwapReversionStrategy,
}

__all__ = [
    "ParamSpec", "Signal", "Strategy", "STRATEGY_REGISTRY",
    "EmaCrossStrategy", "RsiMeanReversionStrategy", "BollingerBreakoutStrategy",
    "PriceVsMaCrossStrategy", "DonchianBreakoutStrategy", "MacdSignalStrategy",
    "SupertrendStrategy", "MomentumRocStrategy", "KeltnerBreakoutStrategy",
    "VwapReversionStrategy",
]

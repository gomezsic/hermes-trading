"""Il registry deve contenere tutte e 10 le strategie con id univoci."""
from backtest_suite.strategies import STRATEGY_REGISTRY


EXPECTED_IDS = {
    "ema_cross", "rsi_mr", "bb_breakout",
    "price_vs_ma_cross", "donchian_breakout", "macd_signal",
    "supertrend", "momentum_roc", "keltner_breakout", "vwap_reversion",
}


def test_registry_contains_ten_strategies():
    assert set(STRATEGY_REGISTRY.keys()) == EXPECTED_IDS
    assert len(STRATEGY_REGISTRY) == 10


def test_each_class_id_matches_registry_key():
    for key, cls in STRATEGY_REGISTRY.items():
        assert cls.strategy_id == key
        assert isinstance(cls.param_specs, tuple) and len(cls.param_specs) >= 1

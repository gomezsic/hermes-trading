"""Test del contratto base Strategy + ParamSpec + Signal."""
from backtest_suite.strategies.base import ParamSpec, Signal, Strategy


def test_paramspec_frozen_and_defaults():
    ps = ParamSpec(name="x", low=0.0, high=1.0)
    assert ps.step is None
    assert ps.is_int is False
    assert ps.description == ""
    # frozen
    try:
        ps.name = "y"     # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ParamSpec should be frozen")


def test_signal_defaults():
    s = Signal(side=None)
    assert s.side is None
    assert s.confidence == 1.0


def test_strategy_protocol_runtime_check_minimal():
    class Dummy:
        strategy_id = "dummy"
        display_name = "Dummy"
        timeframes = ("1h",)
        param_specs = ()

        def __init__(self, params: dict[str, float]) -> None:
            self.params = params

        def warmup_bars(self) -> int:
            return 0

        def on_bar(self, idx: int, candles: list[dict]) -> Signal:
            return Signal(side=None)

    d = Dummy({})
    assert d.warmup_bars() == 0
    assert d.on_bar(0, []).side is None
    assert Dummy.strategy_id == "dummy"


def test_strategy_registry_contains_ema_cross():
    from backtest_suite.strategies import STRATEGY_REGISTRY
    from backtest_suite.strategies.ema_cross import EmaCrossStrategy

    assert "ema_cross" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["ema_cross"] is EmaCrossStrategy

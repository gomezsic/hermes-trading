"""Fixtures pytest condivise per la backtest_suite."""
import pytest


@pytest.fixture
def trend_candles() -> list[dict]:
    """20 candele 1h con trend lineare crescente (per smoke test deterministici)."""
    base = 30000.0
    candles = []
    t0 = 1700000000
    for i in range(20):
        c = base + i * 100.0
        candles.append({
            "t": t0 + i * 3600,
            "o": c - 50.0,
            "h": c + 60.0,
            "l": c - 60.0,
            "c": c + 40.0,
            "v": 100.0,
        })
    return candles

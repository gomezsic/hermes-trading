# Strategy Arena — Fase 1: le 7 nuove strategie — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere 7 nuove famiglie di strategia al `backtest_suite`, portando il registry da 3 a 10, ognuna conforme al `Strategy` Protocol esistente e coperta da test.

**Architecture:** Ogni strategia è una classe self-contained in `backtest_suite/strategies/`, con i propri `param_specs` (bound) e indicatori calcolati internamente, seguendo il pattern di `rsi_mr.py`/`ema_cross.py`. Gli indicatori condivisi (SMA, EMA, ATR) vivono in un nuovo modulo `_indicators.py` per restare DRY. Nessuna dipendenza inversa: `backtest_suite` non viene importato da `hermes_trading`.

**Tech Stack:** Python 3.11, pytest, dataclass `ParamSpec`/`Signal`. Nessuna nuova dipendenza.

**Riferimenti:**
- Spec: `docs/superpowers/specs/2026-05-31-strategy-arena-design.md` §4
- Interfaccia: `backtest_suite/strategies/base.py`
- Esempi di stile: `backtest_suite/strategies/rsi_mr.py`, `backtest_suite/strategies/ema_cross.py`
- Stile test: `tests/suite/test_rsi_mr.py`

**Convenzioni del repo (da rispettare):**
- Candele = `dict` con chiavi `t, o, h, l, c, v` (tutte float tranne `t`).
- Cache indicatori protetta con identità `is` sulla lista candele (non `id()`), come in `rsi_mr.py`.
- `on_bar(idx, candles) -> Signal`; `Signal(side="long"|"short"|None, confidence=float)`.
- `direction` codificato come int param: `0=long, 1=short, 2=both`.
- Comando test: `uv run --project . pytest <path> -v` dalla dir `worker/`.

**File structure (questa fase):**
- Create: `backtest_suite/strategies/_indicators.py` — helper SMA/EMA/ATR condivisi
- Create: `backtest_suite/strategies/price_vs_ma_cross.py`
- Create: `backtest_suite/strategies/donchian_breakout.py`
- Create: `backtest_suite/strategies/macd_signal.py`
- Create: `backtest_suite/strategies/supertrend.py`
- Create: `backtest_suite/strategies/momentum_roc.py`
- Create: `backtest_suite/strategies/keltner_breakout.py`
- Create: `backtest_suite/strategies/vwap_reversion.py`
- Modify: `backtest_suite/strategies/__init__.py` — registrare le 7 classi
- Test: un file `tests/suite/test_<strategy>.py` per ciascuna + `tests/suite/test_indicators_shared.py` + `tests/suite/test_registry_has_ten.py`

---

### Task 1: Helper indicatori condivisi (`_indicators.py`)

**Files:**
- Create: `backtest_suite/strategies/_indicators.py`
- Test: `tests/suite/test_indicators_shared.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_indicators_shared.py
"""Test helper indicatori condivisi."""
from backtest_suite.strategies._indicators import (
    compute_sma, compute_ema, compute_atr,
)


def _candles(values):
    return [{"t": i, "o": v, "h": v + 1.0, "l": v - 1.0, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def test_sma_simple():
    out = compute_sma([1.0, 2.0, 3.0, 4.0], 2)
    assert out[0] is None
    assert out[1] == 1.5
    assert out[2] == 2.5
    assert out[3] == 3.5


def test_ema_first_value_is_sma_seed():
    out = compute_ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    # primi (period-1) None, seed = SMA dei primi 3 = 2.0
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0
    assert out[3] is not None and out[3] > 2.0


def test_atr_length_and_positive():
    candles = _candles([10.0, 11.0, 12.0, 11.0, 13.0, 12.0, 14.0])
    out = compute_atr(candles, 3)
    assert len(out) == len(candles)
    assert out[2] is None          # non ancora pronto
    assert out[3] is not None and out[3] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_indicators_shared.py -v`
Expected: FAIL — `ModuleNotFoundError: backtest_suite.strategies._indicators`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/_indicators.py
"""Indicatori condivisi tra le strategie del backtest_suite.

Funzioni pure su liste di float / candele dict {t,o,h,l,c,v}. Nessuno stato.
Ogni funzione ritorna una lista della stessa lunghezza dell'input, con None
nelle posizioni in cui l'indicatore non è ancora calcolabile.
"""
from __future__ import annotations


def compute_sma(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def compute_ema(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def compute_atr(candles: list[dict], period: int) -> list[float | None]:
    """ATR di Wilder. True Range = max(h-l, |h-prev_c|, |l-prev_c|)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n <= period:
        return out
    trs: list[float] = [0.0] * n
    trs[0] = float(candles[0]["h"]) - float(candles[0]["l"])
    for i in range(1, n):
        h = float(candles[i]["h"])
        l = float(candles[i]["l"])
        pc = float(candles[i - 1]["c"])
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    # seed = media dei primi `period` TR (indici 1..period)
    atr = sum(trs[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_indicators_shared.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/_indicators.py tests/suite/test_indicators_shared.py
git commit -m "feat(arena): add shared SMA/EMA/ATR indicators for new strategies"
```

---

### Task 2: `price_vs_ma_cross`

**Logica:** il close incrocia una media mobile. `ma_type` 0=SMA, 1=EMA. Long quando close passa da sotto a sopra la MA; short quando passa da sopra a sotto. `direction` filtra.

**Files:**
- Create: `backtest_suite/strategies/price_vs_ma_cross.py`
- Test: `tests/suite/test_price_vs_ma_cross.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_price_vs_ma_cross.py
"""Test PriceVsMaCrossStrategy."""
from backtest_suite.strategies.price_vs_ma_cross import PriceVsMaCrossStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"ma_period": 5, "ma_type": 0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_ma_period():
    s = PriceVsMaCrossStrategy(_params(ma_period=10))
    assert s.warmup_bars() == 10


def test_no_signal_before_warmup():
    s = PriceVsMaCrossStrategy(_params(ma_period=5))
    candles = _candles([100.0] * 20)
    assert s.on_bar(2, candles).side is None


def test_long_on_cross_up():
    # prezzo piatto sotto, poi sale sopra la MA -> golden cross prezzo/MA
    values = [100.0] * 10 + [101.0, 103.0, 106.0, 110.0]
    candles = _candles(values)
    s = PriceVsMaCrossStrategy(_params(ma_period=5, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long


def test_direction_long_only_blocks_short():
    values = [110.0, 108.0, 105.0, 101.0] + [100.0] * 6 + [90.0, 80.0]
    candles = _candles(values)
    s = PriceVsMaCrossStrategy(_params(ma_period=5, direction=0))
    sides = {s.on_bar(i, candles).side for i in range(s.warmup_bars(), len(candles))}
    assert "short" not in sides
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_price_vs_ma_cross.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/price_vs_ma_cross.py
"""PriceVsMaCrossStrategy — il close reale incrocia una media mobile (SMA o EMA).

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_sma, compute_ema


class PriceVsMaCrossStrategy:
    strategy_id:  ClassVar[str]                 = "price_vs_ma_cross"
    display_name: ClassVar[str]                 = "Price vs MA Cross"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ma_period", 5, 200, 1, is_int=True),
        ParamSpec("ma_type",   0,   1, 1, is_int=True, description="0=SMA, 1=EMA"),
        ParamSpec("direction", 0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ma_period = int(params["ma_period"])
        self.ma_type   = int(params.get("ma_type", 0))
        self.direction = int(params.get("direction", 2))
        self._ma_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.ma_period

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._ma_cache = (compute_ema(closes, self.ma_period)
                          if self.ma_type == 1
                          else compute_sma(closes, self.ma_period))
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._ma_cache is not None
        if idx < self.ma_period:
            return Signal(side=None)
        ma_now  = self._ma_cache[idx]
        ma_prev = self._ma_cache[idx - 1]
        if ma_now is None or ma_prev is None:
            return Signal(side=None)
        c_now  = float(candles[idx]["c"])
        c_prev = float(candles[idx - 1]["c"])

        side: str | None = None
        if c_prev <= ma_prev and c_now > ma_now:
            side = "long"
        elif c_prev >= ma_prev and c_now < ma_now:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_price_vs_ma_cross.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/price_vs_ma_cross.py tests/suite/test_price_vs_ma_cross.py
git commit -m "feat(arena): add price_vs_ma_cross strategy"
```

---

### Task 3: `donchian_breakout`

**Logica:** canale di Donchian su `channel_period`. Long se il close supera il massimo dei `channel_period` high PRECEDENTI (esclusa la barra corrente); short se scende sotto il minimo dei low precedenti.

**Files:**
- Create: `backtest_suite/strategies/donchian_breakout.py`
- Test: `tests/suite/test_donchian_breakout.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_donchian_breakout.py
"""Test DonchianBreakoutStrategy."""
from backtest_suite.strategies.donchian_breakout import DonchianBreakoutStrategy


def _candles(rows):
    # rows = list di (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"channel_period": 5, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_channel_period():
    s = DonchianBreakoutStrategy(_params(channel_period=20))
    assert s.warmup_bars() == 20


def test_no_signal_before_warmup():
    s = DonchianBreakoutStrategy(_params(channel_period=5))
    candles = _candles([(101.0, 99.0, 100.0)] * 20)
    assert s.on_bar(3, candles).side is None


def test_long_on_breakout_up():
    rows = [(101.0, 99.0, 100.0)] * 10 + [(120.0, 110.0, 119.0)]
    candles = _candles(rows)
    s = DonchianBreakoutStrategy(_params(channel_period=5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_on_breakout_down():
    rows = [(101.0, 99.0, 100.0)] * 10 + [(90.0, 80.0, 81.0)]
    candles = _candles(rows)
    s = DonchianBreakoutStrategy(_params(channel_period=5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_donchian_breakout.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/donchian_breakout.py
"""DonchianBreakoutStrategy — rottura del canale di Donchian a N periodi.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


class DonchianBreakoutStrategy:
    strategy_id:  ClassVar[str]                 = "donchian_breakout"
    display_name: ClassVar[str]                 = "Donchian Breakout"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("channel_period", 5, 100, 1, is_int=True),
        ParamSpec("direction",      0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.channel_period = int(params["channel_period"])
        self.direction      = int(params.get("direction", 2))

    def warmup_bars(self) -> int:
        return self.channel_period

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        if idx < self.channel_period:
            return Signal(side=None)
        window = candles[idx - self.channel_period: idx]   # barre PRECEDENTI
        hi = max(float(c["h"]) for c in window)
        lo = min(float(c["l"]) for c in window)
        c_now = float(candles[idx]["c"])

        side: str | None = None
        if c_now > hi:
            side = "long"
        elif c_now < lo:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_donchian_breakout.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/donchian_breakout.py tests/suite/test_donchian_breakout.py
git commit -m "feat(arena): add donchian_breakout strategy"
```

---

### Task 4: `macd_signal`

**Logica:** MACD = EMA(fast) − EMA(slow); signal line = EMA(MACD, signal_period). Long quando MACD incrocia sopra la signal; short quando sotto.

**Files:**
- Create: `backtest_suite/strategies/macd_signal.py`
- Test: `tests/suite/test_macd_signal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_macd_signal.py
"""Test MacdSignalStrategy."""
from backtest_suite.strategies.macd_signal import MacdSignalStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"ema_fast": 12, "ema_slow": 26, "signal_period": 9, "direction": 2}
    base.update(kw)
    return base


def test_warmup_covers_slow_plus_signal():
    s = MacdSignalStrategy(_params(ema_slow=26, signal_period=9))
    assert s.warmup_bars() == 26 + 9


def test_no_signal_before_warmup():
    s = MacdSignalStrategy(_params())
    candles = _candles([100.0] * 10)
    assert s.on_bar(5, candles).side is None


def test_long_appears_on_uptrend_reversal():
    # discesa poi salita decisa: la MACD incrocia sopra la signal almeno una volta
    values = [100.0 - i for i in range(30)] + [70.0 + i * 2 for i in range(30)]
    candles = _candles(values)
    s = MacdSignalStrategy(_params(ema_fast=5, ema_slow=13, signal_period=5, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_macd_signal.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/macd_signal.py
"""MacdSignalStrategy — cross MACD / signal line.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_ema


def _ema_of_series(series: list[float | None], period: int) -> list[float | None]:
    """EMA su una serie che può contenere None all'inizio (es. la MACD line)."""
    n = len(series)
    out: list[float | None] = [None] * n
    # indice del primo valore non-None
    start = next((i for i, v in enumerate(series) if v is not None), None)
    if start is None or n - start < period:
        return out
    vals = [float(v) for v in series[start:]]   # type: ignore[arg-type]
    sub = compute_ema(vals, period)
    for j, v in enumerate(sub):
        out[start + j] = v
    return out


class MacdSignalStrategy:
    strategy_id:  ClassVar[str]                 = "macd_signal"
    display_name: ClassVar[str]                 = "MACD Signal Cross"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_fast",      5,  20, 1, is_int=True),
        ParamSpec("ema_slow",     20,  50, 1, is_int=True),
        ParamSpec("signal_period", 5,  15, 1, is_int=True),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_fast      = int(params["ema_fast"])
        self.ema_slow      = int(params["ema_slow"])
        self.signal_period = int(params["signal_period"])
        self.direction     = int(params.get("direction", 2))
        self._macd_cache:   list[float | None] | None = None
        self._signal_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.ema_slow + self.signal_period

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        ema_f = compute_ema(closes, self.ema_fast)
        ema_s = compute_ema(closes, self.ema_slow)
        macd: list[float | None] = [
            (f - s) if (f is not None and s is not None) else None
            for f, s in zip(ema_f, ema_s)
        ]
        self._macd_cache   = macd
        self._signal_cache = _ema_of_series(macd, self.signal_period)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._macd_cache is not None and self._signal_cache is not None
        if idx < 1:
            return Signal(side=None)
        m_now,  m_prev = self._macd_cache[idx],   self._macd_cache[idx - 1]
        s_now,  s_prev = self._signal_cache[idx], self._signal_cache[idx - 1]
        if None in (m_now, m_prev, s_now, s_prev):
            return Signal(side=None)

        side: str | None = None
        if m_prev <= s_prev and m_now > s_now:
            side = "long"
        elif m_prev >= s_prev and m_now < s_now:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_macd_signal.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/macd_signal.py tests/suite/test_macd_signal.py
git commit -m "feat(arena): add macd_signal strategy"
```

---

### Task 5: `momentum_roc`

**Logica:** ROC = (close / close[idx − roc_period] − 1) · 100. Long se ROC > soglia; short se ROC < −soglia.

**Files:**
- Create: `backtest_suite/strategies/momentum_roc.py`
- Test: `tests/suite/test_momentum_roc.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_momentum_roc.py
"""Test MomentumRocStrategy."""
from backtest_suite.strategies.momentum_roc import MomentumRocStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"roc_period": 10, "threshold_pct": 5.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_roc_period():
    s = MomentumRocStrategy(_params(roc_period=14))
    assert s.warmup_bars() == 14


def test_no_signal_before_warmup():
    s = MomentumRocStrategy(_params(roc_period=10))
    candles = _candles([100.0] * 20)
    assert s.on_bar(5, candles).side is None


def test_long_when_roc_above_threshold():
    values = [100.0] * 11 + [120.0]   # +20% rispetto a 10 barre prima
    candles = _candles(values)
    s = MomentumRocStrategy(_params(roc_period=10, threshold_pct=5.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_when_roc_below_negative_threshold():
    values = [100.0] * 11 + [80.0]    # −20%
    candles = _candles(values)
    s = MomentumRocStrategy(_params(roc_period=10, threshold_pct=5.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_momentum_roc.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/momentum_roc.py
"""MomentumRocStrategy — Rate-of-Change oltre soglia.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


class MomentumRocStrategy:
    strategy_id:  ClassVar[str]                 = "momentum_roc"
    display_name: ClassVar[str]                 = "Momentum ROC"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("roc_period",    5, 50, 1, is_int=True),
        ParamSpec("threshold_pct", 1.0, 20.0, None, description="soglia % ROC"),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.roc_period    = int(params["roc_period"])
        self.threshold_pct = float(params["threshold_pct"])
        self.direction     = int(params.get("direction", 2))

    def warmup_bars(self) -> int:
        return self.roc_period

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        if idx < self.roc_period:
            return Signal(side=None)
        c_now  = float(candles[idx]["c"])
        c_past = float(candles[idx - self.roc_period]["c"])
        if c_past == 0:
            return Signal(side=None)
        roc = (c_now / c_past - 1.0) * 100.0

        side: str | None = None
        if roc > self.threshold_pct:
            side = "long"
        elif roc < -self.threshold_pct:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_momentum_roc.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/momentum_roc.py tests/suite/test_momentum_roc.py
git commit -m "feat(arena): add momentum_roc strategy"
```

---

### Task 6: `keltner_breakout`

**Logica:** middle = EMA(`ema_period`); upper/lower = middle ± `multiplier`·ATR(`atr_period`). Long se close > upper; short se close < lower.

**Files:**
- Create: `backtest_suite/strategies/keltner_breakout.py`
- Test: `tests/suite/test_keltner_breakout.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_keltner_breakout.py
"""Test KeltnerBreakoutStrategy."""
from backtest_suite.strategies.keltner_breakout import KeltnerBreakoutStrategy


def _candles(rows):
    # rows = (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"ema_period": 10, "atr_period": 5, "multiplier": 1.5, "direction": 2}
    base.update(kw)
    return base


def test_warmup_is_max_of_periods():
    s = KeltnerBreakoutStrategy(_params(ema_period=20, atr_period=14))
    assert s.warmup_bars() == 20


def test_no_signal_before_warmup():
    s = KeltnerBreakoutStrategy(_params())
    candles = _candles([(101.0, 99.0, 100.0)] * 30)
    assert s.on_bar(3, candles).side is None


def test_long_on_break_above_upper():
    rows = [(101.0, 99.0, 100.0)] * 25 + [(140.0, 130.0, 138.0)]
    candles = _candles(rows)
    s = KeltnerBreakoutStrategy(_params(ema_period=10, atr_period=5, multiplier=1.5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_on_break_below_lower():
    rows = [(101.0, 99.0, 100.0)] * 25 + [(70.0, 60.0, 62.0)]
    candles = _candles(rows)
    s = KeltnerBreakoutStrategy(_params(ema_period=10, atr_period=5, multiplier=1.5, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_keltner_breakout.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/keltner_breakout.py
"""KeltnerBreakoutStrategy — rottura del canale di Keltner (EMA ± k·ATR).

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_ema, compute_atr


class KeltnerBreakoutStrategy:
    strategy_id:  ClassVar[str]                 = "keltner_breakout"
    display_name: ClassVar[str]                 = "Keltner Breakout"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_period", 10, 50, 1, is_int=True),
        ParamSpec("atr_period",  5, 30, 1, is_int=True),
        ParamSpec("multiplier",  1.0, 3.0, None),
        ParamSpec("direction",   0,  2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_period = int(params["ema_period"])
        self.atr_period = int(params["atr_period"])
        self.multiplier = float(params["multiplier"])
        self.direction  = int(params.get("direction", 2))
        self._ema_cache: list[float | None] | None = None
        self._atr_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return max(self.ema_period, self.atr_period + 1)

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        closes = [float(c["c"]) for c in candles]
        self._ema_cache = compute_ema(closes, self.ema_period)
        self._atr_cache = compute_atr(candles, self.atr_period)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._ema_cache is not None and self._atr_cache is not None
        mid = self._ema_cache[idx]
        atr = self._atr_cache[idx]
        if mid is None or atr is None:
            return Signal(side=None)
        upper = mid + self.multiplier * atr
        lower = mid - self.multiplier * atr
        c_now = float(candles[idx]["c"])

        side: str | None = None
        if c_now > upper:
            side = "long"
        elif c_now < lower:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_keltner_breakout.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/keltner_breakout.py tests/suite/test_keltner_breakout.py
git commit -m "feat(arena): add keltner_breakout strategy"
```

---

### Task 7: `supertrend`

**Logica:** ATR trailing trend. `hl2 = (h+l)/2`. Banda base upper = hl2 + mult·ATR, lower = hl2 − mult·ATR. Supertrend con regola standard: l'upper finale scende solo (a meno che il close precedente non lo superi), il lower finale sale solo; il trend gira quando il close attraversa la banda finale. Long quando il trend passa a rialzista, short quando passa a ribassista.

**Files:**
- Create: `backtest_suite/strategies/supertrend.py`
- Test: `tests/suite/test_supertrend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_supertrend.py
"""Test SupertrendStrategy."""
from backtest_suite.strategies.supertrend import SupertrendStrategy


def _candles(rows):
    # rows = (high, low, close)
    return [{"t": i, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i, (h, l, c) in enumerate(rows)]


def _params(**kw):
    base = {"atr_period": 5, "multiplier": 2.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_positive():
    s = SupertrendStrategy(_params(atr_period=10))
    assert s.warmup_bars() == 11


def test_no_signal_before_warmup():
    s = SupertrendStrategy(_params(atr_period=5))
    candles = _candles([(101.0, 99.0, 100.0)] * 30)
    assert s.on_bar(2, candles).side is None


def test_long_appears_when_trend_flips_up():
    # discesa stabile poi forte salita: il flip a rialzo deve emettere un long
    down = [(100.0 - i + 1, 100.0 - i - 1, 100.0 - i) for i in range(20)]
    up   = [(85.0 + i * 3 + 1, 85.0 + i * 3 - 1, 85.0 + i * 3) for i in range(20)]
    candles = _candles(down + up)
    s = SupertrendStrategy(_params(atr_period=5, multiplier=2.0, direction=2))
    seen_long = any(s.on_bar(i, candles).side == "long"
                    for i in range(s.warmup_bars(), len(candles)))
    assert seen_long
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_supertrend.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/supertrend.py
"""SupertrendStrategy — trend follower ATR-based (Supertrend classico).

Calcola la linea Supertrend in un'unica passata cachata sulle candele e
emette un segnale solo sulla barra in cui il trend cambia direzione.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal
from backtest_suite.strategies._indicators import compute_atr


class SupertrendStrategy:
    strategy_id:  ClassVar[str]                 = "supertrend"
    display_name: ClassVar[str]                 = "Supertrend"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("atr_period", 5, 30, 1, is_int=True),
        ParamSpec("multiplier", 1.0, 5.0, None),
        ParamSpec("direction",  0,  2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.atr_period = int(params["atr_period"])
        self.multiplier = float(params["multiplier"])
        self.direction  = int(params.get("direction", 2))
        # trend[idx] = +1 (rialzo) / -1 (ribasso) / 0 (non pronto)
        self._trend_cache: list[int] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.atr_period + 1

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        n = len(candles)
        atr = compute_atr(candles, self.atr_period)
        trend: list[int] = [0] * n
        final_upper = [0.0] * n
        final_lower = [0.0] * n
        prev_trend = 1
        for i in range(n):
            if atr[i] is None:
                trend[i] = 0
                continue
            hl2 = (float(candles[i]["h"]) + float(candles[i]["l"])) / 2.0
            basic_upper = hl2 + self.multiplier * atr[i]
            basic_lower = hl2 - self.multiplier * atr[i]
            c_prev = float(candles[i - 1]["c"]) if i > 0 else float(candles[i]["c"])

            if i == 0 or final_upper[i - 1] == 0.0:
                final_upper[i] = basic_upper
                final_lower[i] = basic_lower
            else:
                final_upper[i] = (basic_upper
                                  if (basic_upper < final_upper[i - 1] or c_prev > final_upper[i - 1])
                                  else final_upper[i - 1])
                final_lower[i] = (basic_lower
                                  if (basic_lower > final_lower[i - 1] or c_prev < final_lower[i - 1])
                                  else final_lower[i - 1])

            c_now = float(candles[i]["c"])
            if c_now > final_upper[i]:
                cur = 1
            elif c_now < final_lower[i]:
                cur = -1
            else:
                cur = prev_trend
            trend[i] = cur
            prev_trend = cur
        self._trend_cache = trend
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._trend_cache is not None
        if idx < self.warmup_bars():
            return Signal(side=None)
        cur  = self._trend_cache[idx]
        prev = self._trend_cache[idx - 1]
        if cur == 0 or prev == 0:
            return Signal(side=None)

        side: str | None = None
        if prev <= 0 and cur > 0:
            side = "long"
        elif prev >= 0 and cur < 0:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_supertrend.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/supertrend.py tests/suite/test_supertrend.py
git commit -m "feat(arena): add supertrend strategy"
```

---

### Task 8: `vwap_reversion`

**Logica:** VWAP rolling su `vwap_window` barre. `dist% = (close − vwap)/vwap·100`. Long quando il prezzo è sotto il VWAP oltre soglia (`dist% < −threshold` → si attende rientro al rialzo); short quando è sopra oltre soglia.

**Files:**
- Create: `backtest_suite/strategies/vwap_reversion.py`
- Test: `tests/suite/test_vwap_reversion.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_vwap_reversion.py
"""Test VwapReversionStrategy."""
from backtest_suite.strategies.vwap_reversion import VwapReversionStrategy


def _candles(values):
    return [{"t": i, "o": v, "h": v + 0.5, "l": v - 0.5, "c": v, "v": 100.0}
            for i, v in enumerate(values)]


def _params(**kw):
    base = {"vwap_window": 20, "threshold_pct": 2.0, "direction": 2}
    base.update(kw)
    return base


def test_warmup_equals_window():
    s = VwapReversionStrategy(_params(vwap_window=50))
    assert s.warmup_bars() == 50


def test_no_signal_before_warmup():
    s = VwapReversionStrategy(_params(vwap_window=20))
    candles = _candles([100.0] * 30)
    assert s.on_bar(5, candles).side is None


def test_long_when_price_far_below_vwap():
    values = [100.0] * 20 + [90.0]   # close ben sotto il VWAP ~100
    candles = _candles(values)
    s = VwapReversionStrategy(_params(vwap_window=20, threshold_pct=2.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "long"


def test_short_when_price_far_above_vwap():
    values = [100.0] * 20 + [110.0]
    candles = _candles(values)
    s = VwapReversionStrategy(_params(vwap_window=20, threshold_pct=2.0, direction=2))
    assert s.on_bar(len(candles) - 1, candles).side == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_vwap_reversion.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# backtest_suite/strategies/vwap_reversion.py
"""VwapReversionStrategy — rientro verso il VWAP rolling.

Vedi: docs/superpowers/specs/2026-05-31-strategy-arena-design.md §4.
"""
from __future__ import annotations

from typing import ClassVar

from backtest_suite.strategies.base import ParamSpec, Signal


def _rolling_vwap(candles: list[dict], window: int) -> list[float | None]:
    """VWAP su finestra mobile di `window` barre. typical price = (h+l+c)/3."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if window <= 0 or n < window:
        return out
    tp = [(float(c["h"]) + float(c["l"]) + float(c["c"])) / 3.0 for c in candles]
    vol = [float(c["v"]) for c in candles]
    for i in range(window - 1, n):
        num = 0.0
        den = 0.0
        for j in range(i - window + 1, i + 1):
            num += tp[j] * vol[j]
            den += vol[j]
        out[i] = (num / den) if den > 0 else None
    return out


class VwapReversionStrategy:
    strategy_id:  ClassVar[str]                 = "vwap_reversion"
    display_name: ClassVar[str]                 = "VWAP Reversion"
    timeframes:   ClassVar[tuple[str, ...]]     = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("vwap_window",   10, 200, 1, is_int=True),
        ParamSpec("threshold_pct", 0.5, 10.0, None, description="distanza % dal VWAP"),
        ParamSpec("direction",     0,   2, 1, is_int=True, description="0=long,1=short,2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.vwap_window   = int(params["vwap_window"])
        self.threshold_pct = float(params["threshold_pct"])
        self.direction     = int(params.get("direction", 2))
        self._vwap_cache: list[float | None] | None = None
        self._cached_candles: list[dict] | None = None

    def warmup_bars(self) -> int:
        return self.vwap_window

    def _ensure_cache(self, candles: list[dict]) -> None:
        if self._cached_candles is candles:
            return
        self._vwap_cache = _rolling_vwap(candles, self.vwap_window)
        self._cached_candles = candles

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_cache(candles)
        assert self._vwap_cache is not None
        vwap = self._vwap_cache[idx]
        if vwap is None or vwap == 0:
            return Signal(side=None)
        c_now = float(candles[idx]["c"])
        dist = (c_now - vwap) / vwap * 100.0

        side: str | None = None
        if dist < -self.threshold_pct:
            side = "long"
        elif dist > self.threshold_pct:
            side = "short"
        if side is None:
            return Signal(side=None)
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)
        return Signal(side=side)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_vwap_reversion.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/vwap_reversion.py tests/suite/test_vwap_reversion.py
git commit -m "feat(arena): add vwap_reversion strategy"
```

---

### Task 9: Registrare le 7 strategie nel registry

**Files:**
- Modify: `backtest_suite/strategies/__init__.py`
- Test: `tests/suite/test_registry_has_ten.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/suite/test_registry_has_ten.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project . pytest tests/suite/test_registry_has_ten.py -v`
Expected: FAIL — il registry ha solo 3 chiavi

- [ ] **Step 3: Write minimal implementation**

Sostituire interamente il contenuto di `backtest_suite/strategies/__init__.py` con:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project . pytest tests/suite/test_registry_has_ten.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the FULL suite to confirm no regressions**

Run: `uv run --project . pytest tests/suite -q`
Expected: tutti verdi (i 99 esistenti + i nuovi test di questa fase)

- [ ] **Step 6: Commit**

```bash
git add backtest_suite/strategies/__init__.py tests/suite/test_registry_has_ten.py
git commit -m "feat(arena): register 7 new strategies, registry now has 10"
```

---

## Self-Review (eseguita)

**1. Spec coverage (§4):** tutte e 7 le nuove strategie della tabella §4 hanno un task (Task 2–8); le 3 esistenti restano; il registry a 10 è verificato (Task 9). ✅
**2. Placeholder scan:** nessun TODO/TBD; ogni step ha codice completo e comando con output atteso. ✅
**3. Type consistency:** ogni strategia espone `strategy_id/display_name/timeframes/param_specs` + `__init__(params)`, `warmup_bars()`, `on_bar(idx, candles) -> Signal` come da `base.py`; gli helper `compute_sma/compute_ema/compute_atr` sono definiti in Task 1 e usati con la stessa firma in Task 4/6/7. ✅

## Nota su git

Il repo **non è ancora un repository git**. Gli step di commit presuppongono `git init` + primo commit della baseline. Eseguire prima dell'avvio:
```bash
cd worker && git init && git add -A && git commit -m "chore: baseline before strategy-arena F1"
```
(oppure rimuovere gli step di commit se si preferisce lavorare senza git in questa fase).

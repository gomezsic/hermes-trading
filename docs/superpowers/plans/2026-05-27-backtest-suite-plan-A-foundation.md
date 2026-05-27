# Backtest Suite — Plan A: Foundation (Engine + Strategy + EMA)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `hermes_trading/backtester.py` in modo non-distruttivo estraendo helper puri in `_engine_core.py`, introdurre l'interfaccia `Strategy` e l'engine generico in `backtest_suite/engine/`, e gatekeeper il merge con un test di equivalenza bit-perfect tra il backtester legacy e il nuovo engine con `EmaCrossStrategy`.

**Architecture:** Helper puri (slippage, pnl, equity, simulate_trade) vivono in `hermes_trading/_engine_core.py` per rispettare la regola "`hermes_trading` non importa da `backtest_suite`". Il nuovo engine in `backtest_suite/engine/` orchestra un loop bar-by-bar che chiama `Strategy.on_bar()` e usa `_engine_core` per la simulazione del trade.

**Tech Stack:** Python 3.11, pytest, pyarrow, pydantic v2, dataclasses stdlib.

**Spec:** `docs/superpowers/specs/2026-05-27-backtest-suite-design.md` §§ 4, 5, 6, 14, 15.

---

## File Structure

**Files to create:**
- `hermes_trading/_engine_core.py` — RiskConfig + helper puri (slippage, pnl, equity_curve, simulate_trade)
- `backtest_suite/__init__.py` — package marker
- `backtest_suite/engine/__init__.py` — `run_backtest()` generico
- `backtest_suite/engine/types.py` — ExecutionConfig, Trade, BacktestResult dataclasses
- `backtest_suite/engine/execution.py` — re-export di helper da _engine_core + glue
- `backtest_suite/engine/risk.py` — re-export di simulate_trade da _engine_core
- `backtest_suite/strategies/__init__.py` — STRATEGY_REGISTRY
- `backtest_suite/strategies/base.py` — Strategy Protocol, ParamSpec, Signal
- `backtest_suite/strategies/ema_cross.py` — EmaCrossStrategy
- `tests/suite/__init__.py`
- `tests/suite/conftest.py` — fixtures pytest
- `tests/suite/test_engine_core.py`
- `tests/suite/test_strategy_base.py`
- `tests/suite/test_ema_cross.py`
- `tests/suite/test_engine.py`
- `tests/suite/test_backtester_compat.py` — **regression gate bit-perfect**

**Files to modify:**
- `pyproject.toml` — add deps (pyarrow, pydantic, pytest)
- `hermes_trading/backtester.py` — import helper da `_engine_core.py`, mantenere stessa API esterna

---

## Task 1: Setup pyproject + directory skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `backtest_suite/__init__.py`, `backtest_suite/engine/__init__.py`, `backtest_suite/strategies/__init__.py`, `tests/suite/__init__.py`, `tests/suite/conftest.py`

- [ ] **Step 1: Read current pyproject.toml**

Run: `cat pyproject.toml`

- [ ] **Step 2: Add new dependencies and dev group**

Modify `pyproject.toml` — replace the `[project]` block end with:

```toml
[project]
name = "hermes-trading"
version = "0.1.0"
description = "Add your description here"
requires-python = ">=3.11"
dependencies = [
    "aiofiles>=25.1.0",
    "ccxt>=4.5.54",
    "httpx>=0.28.1",
    "numpy>=2.4.6",
    "pandas>=3.0.3",
    "pyyaml>=6.0.3",
    "rich>=15.0.0",
    "yfinance>=1.4.0",
    "pyarrow>=15.0.0",
    "pydantic>=2.5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Install new deps**

Run: `uv sync --all-extras`
Expected: install completes, lock updated.

- [ ] **Step 4: Create skeleton files**

Create each as an empty file with a single docstring:

`backtest_suite/__init__.py`:
```python
"""backtest_suite — strategie pluggable, GA, UI per hermes-trading."""
```

`backtest_suite/engine/__init__.py`:
```python
"""engine — esecutore deterministico di un singolo backtest."""
```

`backtest_suite/strategies/__init__.py`:
```python
"""strategies — registry e implementazioni delle Strategy."""
```

`tests/suite/__init__.py`:
```python
"""Test della backtest_suite."""
```

`tests/suite/conftest.py`:
```python
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
```

- [ ] **Step 5: Verify pytest collects the suite**

Run: `uv run pytest tests/suite -v --collect-only`
Expected: "collected 0 items" but no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock backtest_suite/ tests/suite/
git commit -m "chore: skeleton backtest_suite + deps pyarrow/pydantic/pytest"
```

---

## Task 2: RiskConfig + apply_slippage helpers in _engine_core

**Files:**
- Create: `hermes_trading/_engine_core.py`
- Test: `tests/suite/test_engine_core.py`

- [ ] **Step 1: Write failing test for RiskConfig + slippage helpers**

Create `tests/suite/test_engine_core.py`:

```python
"""Test per hermes_trading._engine_core — helper puri condivisi."""
from hermes_trading._engine_core import (
    RiskConfig,
    apply_slippage_entry,
    apply_slippage_exit,
    gross_pnl_pct,
)


def test_risk_config_dataclass():
    rc = RiskConfig(
        stop_loss_pct=0.03,
        partial_exit_pct=0.09,
        trailing_activate_pct=0.036,
        trailing_stop_pct=0.024,
        trailing_stop_tight_pct=0.015,
    )
    assert rc.stop_loss_pct == 0.03


def test_apply_slippage_entry_long_raises_price():
    # SLIPPAGE = 0.0005 (5 bp)
    out = apply_slippage_entry(100.0, "long")
    assert out == 100.0 * 1.0005


def test_apply_slippage_entry_short_lowers_price():
    out = apply_slippage_entry(100.0, "short")
    assert out == 100.0 * 0.9995


def test_apply_slippage_exit_long_lowers_price():
    out = apply_slippage_exit(100.0, "long")
    assert out == 100.0 * 0.9995


def test_apply_slippage_exit_short_raises_price():
    out = apply_slippage_exit(100.0, "short")
    assert out == 100.0 * 1.0005


def test_gross_pnl_pct_long():
    assert gross_pnl_pct(100.0, 110.0, "long") == 0.1


def test_gross_pnl_pct_short():
    assert gross_pnl_pct(100.0, 90.0, "short") == 0.1
```

- [ ] **Step 2: Run tests — verify they fail with ImportError**

Run: `uv run pytest tests/suite/test_engine_core.py -v`
Expected: collection errors (module not found).

- [ ] **Step 3: Create `_engine_core.py` with RiskConfig and slippage/pnl helpers**

Create `hermes_trading/_engine_core.py`:

```python
"""
_engine_core.py — Helper puri condivisi tra backtester legacy e backtest_suite engine.

Estratto da backtester.py durante il refactor non-distruttivo.
Stesse costanti e semantica; nessun cambiamento di comportamento.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §15.
"""
from __future__ import annotations

from dataclasses import dataclass

# Costanti di costo (Kraken taker fee + slippage market order) — invariate.
TAKER_FEE: float = 0.0026
SLIPPAGE:  float = 0.0005


@dataclass(frozen=True)
class RiskConfig:
    """Parametri di risk management usati dall'engine (decimali, non percentuali)."""
    stop_loss_pct: float
    partial_exit_pct: float
    trailing_activate_pct: float
    trailing_stop_pct: float
    trailing_stop_tight_pct: float


def apply_slippage_entry(price: float, side: str) -> float:
    """Slippage entry: long peggiora verso l'alto, short verso il basso."""
    if side == "long":
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def apply_slippage_exit(price: float, side: str) -> float:
    """Slippage exit: long abbassa prezzo, short alza prezzo."""
    if side == "long":
        return price * (1.0 - SLIPPAGE)
    return price * (1.0 + SLIPPAGE)


def gross_pnl_pct(entry: float, exit_p: float, side: str) -> float:
    """PnL lordo decimale (es. 0.05 = +5%)."""
    if side == "long":
        return (exit_p - entry) / entry
    return (entry - exit_p) / entry
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/suite/test_engine_core.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add hermes_trading/_engine_core.py tests/suite/test_engine_core.py
git commit -m "feat(engine_core): RiskConfig + slippage/pnl helpers"
```

---

## Task 3: build_equity_curve in _engine_core

**Files:**
- Modify: `hermes_trading/_engine_core.py`
- Modify: `tests/suite/test_engine_core.py`

- [ ] **Step 1: Add failing test for build_equity_curve**

Append to `tests/suite/test_engine_core.py`:

```python
from hermes_trading._engine_core import build_equity_curve


def test_build_equity_curve_no_trades_keeps_capital_flat():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(3)]
    curve = build_equity_curve(candles, trades=[], capital=10000.0)
    assert len(curve) == 3
    for row in curve:
        assert row["equity"] == 10000.0
        assert row["drawdown_pct"] == 0.0


def test_build_equity_curve_applies_pnl_at_exit_idx():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(5)]
    trades = [{"exit_idx": 2, "pnl_pct": 0.10}]   # +10% al trade
    curve = build_equity_curve(candles, trades, capital=1000.0)
    assert curve[0]["equity"] == 1000.0
    assert curve[1]["equity"] == 1000.0
    assert curve[2]["equity"] == 1100.0          # 1000 * (1 + 0.10)
    assert curve[4]["equity"] == 1100.0
    assert curve[2]["drawdown_pct"] == 0.0


def test_build_equity_curve_drawdown_after_loss():
    candles = [{"t": i, "o": 0, "h": 0, "l": 0, "c": 0, "v": 0} for i in range(4)]
    trades = [
        {"exit_idx": 1, "pnl_pct":  0.20},   # capitale 1200
        {"exit_idx": 3, "pnl_pct": -0.10},   # capitale 1080
    ]
    curve = build_equity_curve(candles, trades, capital=1000.0)
    assert curve[1]["equity"] == 1200.0
    assert curve[3]["equity"] == 1080.0
    # peak = 1200, equity = 1080 → dd = (1200-1080)/1200 * 100 = 10%
    assert curve[3]["drawdown_pct"] == 10.0
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_engine_core.py -v -k equity`
Expected: ImportError or collection error.

- [ ] **Step 3: Implement build_equity_curve in _engine_core**

Append to `hermes_trading/_engine_core.py`:

```python
def build_equity_curve(
    candles: list[dict],
    trades: list[dict],
    capital: float,
) -> list[dict]:
    """
    Costruisce equity curve candela per candela.

    Aggiorna il capitale all'exit_idx di ogni trade usando trade["pnl_pct"].
    Tra trade il capitale resta invariato.

    Returns:
        lista di dict {ts, equity, drawdown_pct} per ogni candela.
    """
    exit_map: dict[int, float] = {}
    for t in trades:
        idx = t["exit_idx"]
        exit_map[idx] = exit_map.get(idx, 0.0) + t["pnl_pct"]

    equity = float(capital)
    peak = equity
    curve: list[dict] = []

    for i, c in enumerate(candles):
        if i in exit_map:
            equity = equity * (1.0 + exit_map[i])
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0 if peak > 0.0 else 0.0
        curve.append({
            "ts":           c.get("t", i),
            "equity":       round(equity, 4),
            "drawdown_pct": round(dd, 4),
        })

    return curve
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_engine_core.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add hermes_trading/_engine_core.py tests/suite/test_engine_core.py
git commit -m "feat(engine_core): build_equity_curve helper"
```

---

## Task 4: simulate_trade in _engine_core (refactored)

**Files:**
- Modify: `hermes_trading/_engine_core.py`
- Modify: `tests/suite/test_engine_core.py`

- [ ] **Step 1: Add failing tests for simulate_trade**

Append to `tests/suite/test_engine_core.py`:

```python
from hermes_trading._engine_core import simulate_trade


def _flat_candles(n: int, price: float = 100.0) -> list[dict]:
    return [{"t": i, "o": price, "h": price, "l": price, "c": price, "v": 1.0}
            for i in range(n)]


def test_simulate_trade_forced_close_on_flat_market():
    candles = _flat_candles(5, 100.0)
    risk = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    trade = simulate_trade(candles, entry_idx=1, side="long", risk=risk)
    assert trade["reason"] == "forced_close"
    assert trade["exit_idx"] == 4
    # entry = 100 * 1.0005 = 100.05; exit = 100 * 0.9995 = 99.95
    # gross = (99.95 - 100.05) / 100.05 ≈ -0.000999...
    assert trade["pnl_pct_gross"] < 0
    assert trade["partial_done"] is False


def test_simulate_trade_long_hits_stop_loss():
    # Candele: index 0 entry, index 1 prezzo crolla sotto SL
    candles = [
        {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1.0},
        {"t": 1, "o": 100, "h": 100, "l": 90,  "c": 95,  "v": 1.0},  # low 90 trigger SL=95
    ]
    risk = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    trade = simulate_trade(candles, entry_idx=0, side="long", risk=risk)
    assert trade["reason"] == "stop_loss"
    assert trade["exit_idx"] == 1


def test_simulate_trade_long_partial_then_trailing():
    # Costruisce candele che superano partial (+10%) poi attivano trailing
    candles = []
    for i, c in enumerate([100, 105, 112, 115, 110, 108, 105]):
        candles.append({"t": i, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1.0})
    risk = RiskConfig(
        stop_loss_pct=0.05,
        partial_exit_pct=0.10,
        trailing_activate_pct=0.06,
        trailing_stop_pct=0.04,
        trailing_stop_tight_pct=0.025,
    )
    trade = simulate_trade(candles, entry_idx=0, side="long", risk=risk)
    # Partial deve essere stato preso
    assert trade["partial_done"] is True
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_engine_core.py -v -k simulate`
Expected: ImportError.

- [ ] **Step 3: Implement simulate_trade in _engine_core**

Append to `hermes_trading/_engine_core.py`:

```python
import math


def simulate_trade(
    candles:   list[dict],
    entry_idx: int,
    side:      str,
    risk:      RiskConfig,
) -> dict:
    """
    Simula un singolo trade candela per candela.

    Logica intra-candela (conservativa, avverso prima del favorevole):
      1. Controlla SL e trailing stop sull'estremo avverso
      2. Aggiorna best_price, partial exit, trailing stop sull'estremo favorevole

    Fee: 2 * TAKER_FEE (entry + exit leg).
    La partial al 50% suddivide i volumi ma le leg restano 2.

    Returns:
        dict con: entry, exit, side, pnl_pct (netto), pnl_pct_gross, fee_paid,
        reason, entry_idx, exit_idx, partial_done.
    """
    sl_pct         = risk.stop_loss_pct
    partial_pct    = risk.partial_exit_pct
    trail_act_pct  = risk.trailing_activate_pct
    trail_dist_pct = risk.trailing_stop_pct
    tight_dist_pct = risk.trailing_stop_tight_pct

    entry = apply_slippage_entry(float(candles[entry_idx]["o"]), side)

    if side == "long":
        sl_price      = entry * (1.0 - sl_pct)
        partial_price = entry * (1.0 + partial_pct)
    else:
        sl_price      = entry * (1.0 + sl_pct)
        partial_price = entry * (1.0 - partial_pct)

    trail_active:   bool         = False
    trail_level:    float | None = None
    partial_done:   bool         = False
    partial_exit_p: float | None = None
    best_price: float = entry
    exit_p:   float | None = None
    exit_idx: int | None   = None
    reason:   str          = "forced_close"

    n = len(candles)

    for i in range(entry_idx, n):
        c  = candles[i]
        lo = float(c["l"])
        hi = float(c["h"])

        # Step 1 — estremo avverso
        if side == "long":
            if lo <= sl_price:
                exit_p   = apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break
            if trail_active and trail_level is not None and lo <= trail_level:
                exit_p   = apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break
        else:
            if hi >= sl_price:
                exit_p   = apply_slippage_exit(sl_price, side)
                exit_idx = i
                reason   = "stop_loss"
                break
            if trail_active and trail_level is not None and hi >= trail_level:
                exit_p   = apply_slippage_exit(trail_level, side)
                exit_idx = i
                reason   = "trailing_stop"
                break

        # Step 2 — estremo favorevole + trailing + partial
        if side == "long":
            if hi > best_price:
                best_price = hi
                gain = (best_price - entry) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 - dist)
                    trail_level = max(trail_level or 0.0, new_trail)
            if not partial_done and hi >= partial_price:
                partial_done   = True
                partial_exit_p = apply_slippage_exit(partial_price, side)
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 - tight_dist_pct)
                    trail_level = max(trail_level, new_trail)
        else:
            if lo < best_price:
                best_price = lo
                gain = (entry - best_price) / entry
                if gain >= trail_act_pct:
                    trail_active = True
                    dist      = tight_dist_pct if partial_done else trail_dist_pct
                    new_trail = best_price * (1.0 + dist)
                    trail_level = min(
                        trail_level if trail_level is not None else math.inf,
                        new_trail,
                    )
            if not partial_done and lo <= partial_price:
                partial_done   = True
                partial_exit_p = apply_slippage_exit(partial_price, side)
                if trail_active and trail_level is not None:
                    new_trail   = best_price * (1.0 + tight_dist_pct)
                    trail_level = min(trail_level, new_trail)

    if exit_p is None:
        last     = candles[-1]
        exit_p   = apply_slippage_exit(float(last["c"]), side)
        exit_idx = n - 1
        reason   = "forced_close"

    assert exit_idx is not None

    if partial_done and partial_exit_p is not None:
        gross_partial   = gross_pnl_pct(entry, partial_exit_p, side)
        gross_remaining = gross_pnl_pct(entry, exit_p, side)
        pnl_gross = 0.5 * gross_partial + 0.5 * gross_remaining
    else:
        pnl_gross = gross_pnl_pct(entry, exit_p, side)

    fee_paid = 2.0 * TAKER_FEE
    pnl_net  = pnl_gross - fee_paid

    return {
        "entry":         round(entry, 6),
        "exit":          round(exit_p, 6),
        "side":          side,
        "pnl_pct":       round(pnl_net, 8),
        "pnl_pct_gross": round(pnl_gross, 8),
        "fee_paid":      round(fee_paid, 6),
        "reason":        reason,
        "entry_idx":     entry_idx,
        "exit_idx":      exit_idx,
        "partial_done":  partial_done,
    }
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_engine_core.py -v`
Expected: all passed (12+).

- [ ] **Step 5: Commit**

```bash
git add hermes_trading/_engine_core.py tests/suite/test_engine_core.py
git commit -m "feat(engine_core): simulate_trade refactored on RiskConfig"
```

---

## Task 5: Refactor backtester.py to use _engine_core (non-distruttivo)

**Files:**
- Modify: `hermes_trading/backtester.py`

**Goal:** sostituire le definizioni locali con import da `_engine_core`. **Test legacy `test_walk_forward.py` deve continuare a passare bit-perfect.**

- [ ] **Step 1: Baseline — run existing legacy test**

Run: `uv run python test_walk_forward.py`
Expected: tutti i test passano (capturare output per confronto).

- [ ] **Step 2: Modify backtester.py imports — replace local helpers**

Open `hermes_trading/backtester.py`. Replace the constants block:

```python
TAKER_FEE: float = 0.0026   # 0.26% per singola leg (entry o exit)
SLIPPAGE:  float = 0.0005   # 5 bp per market order, applicato al prezzo di fill
```

with:

```python
from hermes_trading._engine_core import (
    TAKER_FEE,
    SLIPPAGE,
    RiskConfig,
    apply_slippage_entry as _apply_slippage_entry,
    apply_slippage_exit  as _apply_slippage_exit,
    gross_pnl_pct        as _gross_pnl_pct,
    build_equity_curve   as _build_equity_curve_core,
    simulate_trade       as _simulate_trade_core,
)
```

- [ ] **Step 3: Remove local definitions of _apply_slippage_entry/_exit, _gross_pnl_pct**

Delete the function definitions `_apply_slippage_entry`, `_apply_slippage_exit`, `_gross_pnl_pct` in `backtester.py` (the helpers are now imported with the same name).

- [ ] **Step 4: Replace _simulate_trade with adapter to core**

In `backtester.py`, find `def _simulate_trade(...)`. Replace its body so it builds a `RiskConfig` from the strategy dict and delegates to the core:

```python
def _simulate_trade(
    candles:   list[dict],
    entry_idx: int,
    side:      str,
    strategy:  dict,
) -> dict:
    """Adapter: costruisce RiskConfig dalla strategy dict e delega a _engine_core."""
    risk = RiskConfig(
        stop_loss_pct           = strategy.get("stop_loss_pct", 5.0)            / 100.0,
        partial_exit_pct        = strategy.get("partial_exit_pct", 12.0)         / 100.0,
        trailing_activate_pct   = strategy.get("trailing_activate_pct", 6.0)     / 100.0,
        trailing_stop_pct       = strategy.get("trailing_stop_pct", 4.0)         / 100.0,
        trailing_stop_tight_pct = strategy.get("trailing_stop_tight_pct", 2.5)   / 100.0,
    )
    return _simulate_trade_core(candles, entry_idx, side, risk)
```

- [ ] **Step 5: Replace _build_equity_curve with adapter**

Find `def _build_equity_curve(...)`. Replace with:

```python
def _build_equity_curve(
    candles: list[dict],
    trades:  list[dict],
    capital: float,
) -> list[dict]:
    """Adapter verso _engine_core.build_equity_curve."""
    return _build_equity_curve_core(candles, trades, capital)
```

- [ ] **Step 6: Re-run legacy regression test**

Run: `uv run python test_walk_forward.py`
Expected: **bit-perfect identical output to baseline** in Step 1. If diff: investigate before continuing.

- [ ] **Step 7: Run new unit tests still pass**

Run: `uv run pytest tests/suite/test_engine_core.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add hermes_trading/backtester.py
git commit -m "refactor(backtester): use _engine_core helpers (non-distruttivo)"
```

---

## Task 6: Strategy Protocol + ParamSpec + Signal

**Files:**
- Create: `backtest_suite/strategies/base.py`
- Test: `tests/suite/test_strategy_base.py`

- [ ] **Step 1: Write failing test for Strategy contract**

Create `tests/suite/test_strategy_base.py`:

```python
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
    # A class with the right ClassVars + methods conforms to Protocol structurally.
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
    # Strategy is a Protocol; runtime isinstance check requires @runtime_checkable
    # We only verify the duck-typed interface here:
    assert d.warmup_bars() == 0
    assert d.on_bar(0, []).side is None
    assert Dummy.strategy_id == "dummy"
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_strategy_base.py -v`
Expected: collection error.

- [ ] **Step 3: Implement base.py**

Create `backtest_suite/strategies/base.py`:

```python
"""
Contratto base per le Strategy della backtest_suite.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True)
class ParamSpec:
    """Definisce un parametro tunabile di una strategy."""
    name: str
    low: float
    high: float
    step: float | None = None       # None = continuo; valore = discretizzato (per grid)
    is_int: bool = False
    description: str = ""


@dataclass
class Signal:
    """Output del segnale a una candela."""
    side: str | None                # "long" | "short" | None
    confidence: float = 1.0


@runtime_checkable
class Strategy(Protocol):
    """Contratto che ogni strategia deve rispettare."""

    strategy_id:  ClassVar[str]
    display_name: ClassVar[str]
    timeframes:   ClassVar[tuple[str, ...]]
    param_specs:  ClassVar[tuple[ParamSpec, ...]]

    def __init__(self, params: dict[str, float]) -> None: ...

    def warmup_bars(self) -> int: ...

    def on_bar(self, idx: int, candles: list[dict]) -> Signal: ...
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_strategy_base.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/base.py tests/suite/test_strategy_base.py
git commit -m "feat(strategies): Strategy Protocol + ParamSpec + Signal"
```

---

## Task 7: EmaCrossStrategy (wrapping della logica esistente)

**Files:**
- Create: `backtest_suite/strategies/ema_cross.py`
- Test: `tests/suite/test_ema_cross.py`

- [ ] **Step 1: Write failing test**

Create `tests/suite/test_ema_cross.py`:

```python
"""Test EmaCrossStrategy — replica la logica esistente di backtester.py."""
from backtest_suite.strategies.base import Signal
from backtest_suite.strategies.ema_cross import EmaCrossStrategy


def _candles_with_golden_cross() -> list[dict]:
    # 60 candele: prima discendente, poi forte salita → golden cross atteso intorno a metà.
    candles = []
    price = 100.0
    for i in range(30):
        price -= 0.2
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    for i in range(30, 80):
        price += 0.5
        candles.append({"t": i, "o": price, "h": price + 0.5, "l": price - 0.5,
                        "c": price, "v": 100.0})
    return candles


def test_ema_cross_warmup_equals_ema_slow():
    s = EmaCrossStrategy({"ema_fast": 20, "ema_slow": 50,
                          "vwap_filter": 0, "direction": 2})
    assert s.warmup_bars() == 50


def test_ema_cross_emits_long_after_golden_cross():
    candles = _candles_with_golden_cross()
    s = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 15,
                          "vwap_filter": 0, "direction": 2})

    seen_long = False
    for i in range(s.warmup_bars(), len(candles)):
        sig = s.on_bar(i, candles)
        if sig.side == "long":
            seen_long = True
            break
    assert seen_long


def test_ema_cross_direction_long_only_filters_short():
    # Death cross deve essere filtrato se direction=0 (long only)
    candles = _candles_with_golden_cross()[::-1]   # invertiamo per avere death cross
    s = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 15,
                          "vwap_filter": 0, "direction": 0})

    for i in range(s.warmup_bars(), len(candles)):
        sig = s.on_bar(i, candles)
        assert sig.side != "short"
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_ema_cross.py -v`
Expected: collection error.

- [ ] **Step 3: Implement EmaCrossStrategy**

Create `backtest_suite/strategies/ema_cross.py`:

```python
"""
EmaCrossStrategy — wrap della logica EMA cross 20/50 + filtro VWAP esistente.

Riusa _compute_ema e _compute_vwap_rolling da hermes_trading.backtester per
garantire equivalenza bit-perfect col backtester legacy.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §5, §15.
"""
from __future__ import annotations

from typing import ClassVar

from hermes_trading.backtester import _compute_ema, _compute_vwap_rolling

from backtest_suite.strategies.base import ParamSpec, Signal


class EmaCrossStrategy:
    """EMA cross fast/slow con filtro VWAP opzionale. direction codificato come int."""

    strategy_id:  ClassVar[str]                = "ema_cross"
    display_name: ClassVar[str]                = "EMA Cross"
    timeframes:   ClassVar[tuple[str, ...]]    = ("1h", "4h", "1d")
    param_specs:  ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("ema_fast",    5,  30,  1, is_int=True),
        ParamSpec("ema_slow",   20, 100,  1, is_int=True),
        ParamSpec("vwap_window", 50, 400, 10, is_int=True),
        ParamSpec("vwap_filter",  0,   1,  1, is_int=True, description="0|1"),
        ParamSpec("direction",    0,   2,  1, is_int=True,
                  description="0=long, 1=short, 2=both"),
    )

    def __init__(self, params: dict[str, float]) -> None:
        self.ema_fast    = int(params["ema_fast"])
        self.ema_slow    = int(params["ema_slow"])
        self.vwap_window = int(params.get("vwap_window", 200))
        self.vwap_filter = bool(int(params.get("vwap_filter", 0)))
        self.direction   = int(params.get("direction", 2))

        # Cache lazy degli indicatori per evitare ricalcolo a ogni on_bar
        self._ema_f_cache: list[float | None] | None = None
        self._ema_s_cache: list[float | None] | None = None
        self._vwap_cache: list[float | None] | None  = None
        self._candles_id: int | None = None

    def warmup_bars(self) -> int:
        return self.ema_slow

    def _ensure_caches(self, candles: list[dict]) -> None:
        # Identifichiamo la stessa serie via id() — l'engine non muta la lista.
        if self._candles_id == id(candles):
            return
        closes = [float(c["c"]) for c in candles]
        self._ema_f_cache = _compute_ema(closes, self.ema_fast)
        self._ema_s_cache = _compute_ema(closes, self.ema_slow)
        self._vwap_cache  = _compute_vwap_rolling(candles, self.vwap_window) \
            if self.vwap_filter else None
        self._candles_id  = id(candles)

    def on_bar(self, idx: int, candles: list[dict]) -> Signal:
        self._ensure_caches(candles)
        assert self._ema_f_cache is not None and self._ema_s_cache is not None

        if idx < self.ema_slow:
            return Signal(side=None)

        ef_now  = self._ema_f_cache[idx]
        es_now  = self._ema_s_cache[idx]
        ef_prev = self._ema_f_cache[idx - 1]
        es_prev = self._ema_s_cache[idx - 1]

        if ef_now is None or es_now is None or ef_prev is None or es_prev is None:
            return Signal(side=None)

        side: str | None = None
        if ef_prev <= es_prev and ef_now > es_now:
            side = "long"
        elif ef_prev >= es_prev and ef_now < es_now:
            side = "short"

        if side is None:
            return Signal(side=None)

        # Filtro direzione
        if self.direction == 0 and side != "long":
            return Signal(side=None)
        if self.direction == 1 and side != "short":
            return Signal(side=None)

        # Filtro VWAP
        if self.vwap_filter and self._vwap_cache is not None:
            vwap_val = self._vwap_cache[idx]
            if vwap_val is not None:
                close_i = float(candles[idx]["c"])
                if side == "long"  and close_i < vwap_val:
                    return Signal(side=None)
                if side == "short" and close_i > vwap_val:
                    return Signal(side=None)

        return Signal(side=side)
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_ema_cross.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/ema_cross.py tests/suite/test_ema_cross.py
git commit -m "feat(strategies): EmaCrossStrategy wrapping logic esistente"
```

---

## Task 8: STRATEGY_REGISTRY

**Files:**
- Modify: `backtest_suite/strategies/__init__.py`
- Test: extend `tests/suite/test_strategy_base.py`

- [ ] **Step 1: Add failing test for registry**

Append to `tests/suite/test_strategy_base.py`:

```python
def test_strategy_registry_contains_ema_cross():
    from backtest_suite.strategies import STRATEGY_REGISTRY
    from backtest_suite.strategies.ema_cross import EmaCrossStrategy

    assert "ema_cross" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["ema_cross"] is EmaCrossStrategy
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_strategy_base.py::test_strategy_registry_contains_ema_cross -v`
Expected: ImportError.

- [ ] **Step 3: Populate registry**

Replace `backtest_suite/strategies/__init__.py` content:

```python
"""strategies — registry e implementazioni delle Strategy."""
from backtest_suite.strategies.base import ParamSpec, Signal, Strategy
from backtest_suite.strategies.ema_cross import EmaCrossStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    EmaCrossStrategy.strategy_id: EmaCrossStrategy,
}

__all__ = ["ParamSpec", "Signal", "Strategy", "STRATEGY_REGISTRY", "EmaCrossStrategy"]
```

- [ ] **Step 4: Run test — verify passing**

Run: `uv run pytest tests/suite/test_strategy_base.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/strategies/__init__.py tests/suite/test_strategy_base.py
git commit -m "feat(strategies): STRATEGY_REGISTRY con EmaCrossStrategy"
```

---

## Task 9: engine/types.py — ExecutionConfig, Trade, BacktestResult

**Files:**
- Create: `backtest_suite/engine/types.py`
- Test: `tests/suite/test_engine.py`

- [ ] **Step 1: Write failing test for types**

Create `tests/suite/test_engine.py`:

```python
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
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_engine.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement types.py**

Create `backtest_suite/engine/types.py`:

```python
"""Tipi dell'engine generico — vedi spec §6."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionConfig:
    taker_fee:    float = 0.0026
    slippage:     float = 0.0005
    latency_bars: int   = 1
    capital:      float = 10_000.0
    allow_overlap: bool = False
    direction:    str   = "both"          # "long" | "short" | "both"


@dataclass
class Trade:
    side:          str
    entry_idx:     int
    exit_idx:      int
    entry:         float
    exit:          float
    pnl_pct:       float
    pnl_pct_gross: float
    fee_paid:      float
    reason:        str                    # stop_loss | trailing_stop | forced_close
    partial_done:  bool


@dataclass
class BacktestResult:
    trades:       list[Trade]             = field(default_factory=list)
    equity_curve: list[dict]              = field(default_factory=list)
    metrics:      dict                    = field(default_factory=dict)
    config_hash:  str                     = ""
    n_candles:    int                     = 0
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_engine.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/engine/types.py tests/suite/test_engine.py
git commit -m "feat(engine): types ExecutionConfig + Trade + BacktestResult"
```

---

## Task 10: Generic run_backtest in backtest_suite/engine

**Files:**
- Create: `backtest_suite/engine/__init__.py` (replace skeleton)
- Modify: `tests/suite/test_engine.py`

- [ ] **Step 1: Add failing test for run_backtest with EmaCrossStrategy**

Append to `tests/suite/test_engine.py`:

```python
from hermes_trading._engine_core import RiskConfig
from backtest_suite.engine import run_backtest
from backtest_suite.strategies.ema_cross import EmaCrossStrategy


def _gen_market_candles(n: int = 300) -> list[dict]:
    """Mercato sintetico con due trend chiari (per generare almeno 1 trade)."""
    import math
    candles = []
    for i in range(n):
        # sinusoide + trend per innescare cross EMA
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
    assert "sharpe" in result.metrics or result.metrics == {} or "max_drawdown" in result.metrics


def test_run_backtest_no_signals_returns_empty_trades():
    # Candele flat → nessun cross EMA → nessun trade
    candles = [{"t": i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1.0}
               for i in range(100)]
    strat   = EmaCrossStrategy({"ema_fast": 5, "ema_slow": 20,
                                "vwap_filter": 0, "direction": 2})
    risk    = RiskConfig(0.05, 0.10, 0.06, 0.04, 0.025)
    result  = run_backtest(candles, strat, risk, ExecutionConfig())
    assert result.trades == []
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/suite/test_engine.py -v -k run_backtest`
Expected: ImportError.

- [ ] **Step 3: Implement run_backtest**

Replace `backtest_suite/engine/__init__.py`:

```python
"""
engine — esecutore deterministico di un singolo backtest.

API pubblica: run_backtest(candles, strategy, risk, execution) -> BacktestResult.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6.
"""
from __future__ import annotations

import hashlib
import json

from hermes_trading._engine_core import (
    RiskConfig,
    build_equity_curve,
    simulate_trade,
)
from hermes_trading.score import (
    compute_calmar,
    compute_cvar,
    compute_expectancy,
    compute_max_drawdown,
    compute_sharpe,
    compute_tail_ratio,
    compute_ulcer_index,
    compute_win_stats,
)

from backtest_suite.engine.types import (
    BacktestResult,
    ExecutionConfig,
    Trade,
)


def _config_hash(strategy_id: str, params: dict, risk: RiskConfig,
                 execution: ExecutionConfig) -> str:
    payload = {
        "strategy_id": strategy_id,
        "params":      {k: v for k, v in sorted(params.items())},
        "risk":        risk.__dict__,
        "execution":   {k: v for k, v in execution.__dict__.items()},
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _empty_metrics() -> dict:
    return {
        "max_drawdown": 0.0, "cvar_5pct":  0.0, "calmar_ratio": 0.0,
        "ulcer_index":  0.0, "tail_ratio": 0.0, "sharpe":       0.0,
        "win_rate":     0.0, "n_trades":   0,   "expectancy":   0.0,
    }


def _compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return _empty_metrics()
    pnls = [t["pnl_pct"] for t in trades]
    ws   = compute_win_stats(pnls)
    return {
        "max_drawdown": round(compute_max_drawdown(pnls), 6),
        "cvar_5pct":    round(compute_cvar(pnls, 0.05),  6),
        "calmar_ratio": round(compute_calmar(pnls),       4),
        "ulcer_index":  round(compute_ulcer_index(pnls),  4),
        "tail_ratio":   round(compute_tail_ratio(pnls),   4),
        "sharpe":       round(compute_sharpe(pnls),       4),
        "win_rate":     ws["win_rate"],
        "n_trades":     len(trades),
        "expectancy":   round(compute_expectancy(pnls),   6),
    }


def run_backtest(
    candles:   list[dict],
    strategy,                              # Strategy istanziata
    risk:      RiskConfig,
    execution: ExecutionConfig,
) -> BacktestResult:
    """
    Esegue un backtest deterministico.

    Pipeline:
      1. Warmup: skip primi strategy.warmup_bars() indici.
      2. Per ogni candela: signal = strategy.on_bar(i, candles).
         Se posizione aperta e fuori range cooperativo, skip.
         Se signal valido e non in posizione, entry a i + execution.latency_bars.
      3. Filtro direction: long/short/both (execution.direction override).
      4. simulate_trade(...) dal _engine_core.
      5. Costruzione equity_curve + metrics.
    """
    n = len(candles)
    cfg_hash = _config_hash(
        strategy.strategy_id,
        {k: getattr(strategy, k, None)
         for k in (ps.name for ps in strategy.param_specs)},
        risk, execution,
    )

    warmup = strategy.warmup_bars()
    if n < warmup + execution.latency_bars + 1:
        return BacktestResult(
            trades=[],
            equity_curve=build_equity_curve(candles, [], execution.capital),
            metrics=_empty_metrics(),
            config_hash=cfg_hash,
            n_candles=n,
        )

    trades_raw: list[dict]  = []
    next_free_idx: int      = warmup

    i = warmup
    while i < n - 1:
        if i < next_free_idx and not execution.allow_overlap:
            i += 1
            continue

        sig = strategy.on_bar(i, candles)
        if sig.side is None:
            i += 1
            continue

        if execution.direction == "long"  and sig.side != "long":
            i += 1
            continue
        if execution.direction == "short" and sig.side != "short":
            i += 1
            continue

        entry_idx = i + execution.latency_bars
        if entry_idx >= n:
            break

        trade = simulate_trade(candles, entry_idx, sig.side, risk)
        trades_raw.append(trade)

        if not execution.allow_overlap:
            next_free_idx = trade["exit_idx"] + 1
            i = next_free_idx
        else:
            i += 1

    trades = [Trade(
        side=t["side"], entry_idx=t["entry_idx"], exit_idx=t["exit_idx"],
        entry=t["entry"], exit=t["exit"], pnl_pct=t["pnl_pct"],
        pnl_pct_gross=t["pnl_pct_gross"], fee_paid=t["fee_paid"],
        reason=t["reason"], partial_done=t["partial_done"],
    ) for t in trades_raw]

    equity_curve = build_equity_curve(candles, trades_raw, execution.capital)
    metrics      = _compute_metrics(trades_raw)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        config_hash=cfg_hash,
        n_candles=n,
    )
```

- [ ] **Step 4: Run tests — verify passing**

Run: `uv run pytest tests/suite/test_engine.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/engine/__init__.py tests/suite/test_engine.py
git commit -m "feat(engine): run_backtest generico bar-by-bar via Strategy.on_bar"
```

---

## Task 11: Regression gate — bit-perfect equivalence

**Files:**
- Create: `tests/suite/test_backtester_compat.py`

**Goal:** dimostrare che `backtest_suite.engine.run_backtest` con `EmaCrossStrategy` produce output **bit-perfect identici** a `hermes_trading.backtester.run_backtest` sugli stessi input. Questo test è il **gatekeeper** del refactor.

- [ ] **Step 1: Write the regression test**

Create `tests/suite/test_backtester_compat.py`:

```python
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

    # Trades: stesso numero, stessi entry/exit indices/prices, stesso pnl
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

    # Equity curve: stessa lunghezza, stessi valori
    assert len(new.equity_curve) == len(legacy["equity_curve"])
    for i, (n_row, l_row) in enumerate(zip(new.equity_curve, legacy["equity_curve"])):
        assert n_row["equity"]       == l_row["equity"],       f"equity idx {i}"
        assert n_row["drawdown_pct"] == l_row["drawdown_pct"], f"dd idx {i}"

    # Metrics: identiche
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
```

- [ ] **Step 2: Run regression test**

Run: `uv run pytest tests/suite/test_backtester_compat.py -v`
Expected: 2 passed. **Se fallisce, NON committare**: investigare la differenza tra legacy e nuovo engine prima di proseguire.

- [ ] **Step 3: Run full suite for sanity**

Run: `uv run pytest tests/suite -v`
Expected: tutti i test passano.

- [ ] **Step 4: Re-run legacy test as final smoke**

Run: `uv run python test_walk_forward.py`
Expected: tutto passa.

- [ ] **Step 5: Commit**

```bash
git add tests/suite/test_backtester_compat.py
git commit -m "test(compat): bit-perfect regression gate legacy ↔ new engine"
```

---

## Task 12: engine/execution.py + engine/risk.py re-exports

**Files:**
- Create: `backtest_suite/engine/execution.py`
- Create: `backtest_suite/engine/risk.py`

**Goal:** moduli wrapper minimali per coerenza con la struttura prevista dalla spec (§4.1) e per facilitare l'import da `backtest_suite.engine.execution` nelle fasi successive.

- [ ] **Step 1: Create execution.py**

Create `backtest_suite/engine/execution.py`:

```python
"""
execution — re-export di helper di esecuzione dal modulo condiviso _engine_core.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6, §15.
"""
from hermes_trading._engine_core import (
    SLIPPAGE,
    TAKER_FEE,
    apply_slippage_entry,
    apply_slippage_exit,
    build_equity_curve,
    gross_pnl_pct,
)

__all__ = [
    "SLIPPAGE", "TAKER_FEE",
    "apply_slippage_entry", "apply_slippage_exit",
    "gross_pnl_pct", "build_equity_curve",
]
```

- [ ] **Step 2: Create risk.py**

Create `backtest_suite/engine/risk.py`:

```python
"""
risk — re-export di simulate_trade e RiskConfig dal modulo condiviso _engine_core.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §6, §15.
"""
from hermes_trading._engine_core import RiskConfig, simulate_trade

__all__ = ["RiskConfig", "simulate_trade"]
```

- [ ] **Step 3: Quick smoke test — verify importable**

Run: `uv run python -c "from backtest_suite.engine import execution, risk; print(execution.SLIPPAGE, risk.RiskConfig)"`
Expected: `0.0005 <class 'hermes_trading._engine_core.RiskConfig'>`

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/suite -v && uv run python test_walk_forward.py`
Expected: tutti passano.

- [ ] **Step 5: Commit**

```bash
git add backtest_suite/engine/execution.py backtest_suite/engine/risk.py
git commit -m "feat(engine): execution & risk re-exports for namespace consistency"
```

---

## Self-Review

**Spec coverage** (questo Plan A copre):
- §4.1 layout `hermes_trading/_engine_core.py`, `backtest_suite/engine/*`, `backtest_suite/strategies/*` ✓
- §4.3 confini live↔research (mai import inverso) ✓
- §5 Strategy interface + ParamSpec + Signal + registry ✓
- §6 engine generico run_backtest(strategy, risk, execution) ✓
- §14.3 regression test bit-perfect (`test_backtester_compat.py`) ✓
- §15 refactor non-distruttivo via `_engine_core` ✓

**Out of scope per questo Plan A** (coperti in Plan B–D):
- RsiMeanReversionStrategy, BollingerBreakoutStrategy → Plan B
- Data lake parquet + Kraken downloader → Plan B
- Optimizer (GA, fitness, grid) → Plan B
- Persistence SQLite + parquet → Plan C
- CLI hermes-bt → Plan C
- FastAPI server + WebSocket + frontend → Plan D
- Integration e2e tests → Plan D

**Placeholder scan**: nessun TODO/TBD nelle 12 task.

**Type consistency**:
- `RiskConfig` in `_engine_core` ed esportato da `engine/risk.py` — stessa classe.
- `Trade` dataclass in `engine/types.py` ricostruito a partire dal dict di `simulate_trade` — campi allineati uno a uno.
- `EmaCrossStrategy.param_specs` ricalca le chiavi usate in `STRATEGY_YAML` del test compat (eccetto `direction` codificato come int 0/1/2 vs stringa).

**Critical path**: Task 5 (refactor backtester) → Task 11 (regression gate). Se 11 fallisce, NON proseguire ai Plan B–D.

---

**Plan A completo, salvato in** `docs/superpowers/plans/2026-05-27-backtest-suite-plan-A-foundation.md`.

I Plan B, C, D verranno scritti su richiesta dopo che Plan A è eseguito e il regression gate passa.

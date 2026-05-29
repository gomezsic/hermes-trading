# Backtest Suite — Stato & Handoff

**Ultimo aggiornamento:** 2026-05-29
**Branch:** `dev`
**Come ripartire domani:** leggi questo file, poi apri il prossimo plan da eseguire (vedi sotto) ed esegui task-by-task con `superpowers:subagent-driven-development`.

---

## Obiettivo complessivo

Costruire una backtest suite generica + ottimizzatore genetico accanto al sistema di trading live, senza toccarlo. 4 plan sequenziali (spec + plan in `docs/superpowers/`):

| Plan | Contenuto | Stato |
|---|---|---|
| **A — Foundation** | Engine generico + interfaccia `Strategy` + `EmaCrossStrategy` + regression gate bit-perfect | ✅ **COMPLETO** |
| **B — Data + Optimizer** | Data lake parquet (Kraken/ccxt) + RSI/Bollinger + **fitness OOS + GA + grid search** | ✅ **COMPLETO (10/10)** |
| **C — Persistence + CLI** | SQLite (metadati) + parquet (artefatti) + CLI `hermes-bt` | ⏳ **PROSSIMO (0/6 task)** |
| **D — Server + UI** | FastAPI + WebSocket + frontend + E2E | 📄 scritto, non iniziato |

Spec di design: `docs/superpowers/specs/2026-05-27-backtest-suite-design.md`

---

## Plan A — COMPLETO ✅ (12 task)

Tutto committato su `dev`. **Regression gate bit-perfect VERDE** (`tests/suite/test_backtester_compat.py`): il nuovo engine produce output identico al backtester legacy su 2000 e 200 candele.

**Cosa è stato costruito:**
- `hermes_trading/_engine_core.py` — helper puri condivisi: `RiskConfig`, `apply_slippage_entry/exit`, `gross_pnl_pct`, `build_equity_curve`, `simulate_trade`.
- `hermes_trading/backtester.py` — refactor NON-distruttivo: ora importa gli helper da `_engine_core` (regola: `hermes_trading` non importa mai da `backtest_suite`).
- `backtest_suite/engine/` — `run_backtest(candles, strategy, risk, execution) -> BacktestResult`, `types.py` (`ExecutionConfig`, `Trade`, `BacktestResult`), re-export `execution.py`/`risk.py`.
- `backtest_suite/strategies/` — `base.py` (`Strategy` Protocol, `ParamSpec`, `Signal`), `ema_cross.py` (`EmaCrossStrategy`), `STRATEGY_REGISTRY`.

**Code review finale:** APPROVED. Trovato e RISOLTO un bug latente importante: la cache indicatori delle strategy ora usa identity (`is`), non `id(candles)` — necessario perché il GA di Plan B riusa le istanze di strategy su finestre diverse. Aggiunto test anti-regressione.

**Test:** 28 suite + 17 legacy walk-forward = tutti verdi.

---

## Plan B — COMPLETO ✅ (10/10 task, 2026-05-29)

File: `docs/superpowers/plans/2026-05-27-backtest-suite-plan-B-data-optimizer.md`. Eseguito task-by-task con `subagent-driven-development` (implementer → spec review → code-quality review per ogni task + final cross-cutting review). Commit su `dev` da `b27ba9d` a `cbfff2d`.

**Cosa è stato costruito:**
- `backtest_suite/data_lake/` — `parquet_store.py` (schema OHLCV, write/read/dedup/gap/coverage), `kraken_source.py` (downloader ccxt paginato, mockato nei test), `__init__.py` (API pubblica `fetch`/`load`/`coverage` idempotente). Layout: `data/ohlcv/kraken/{symbol}/{tf}/{YYYY}.parquet`.
- `backtest_suite/strategies/` — `rsi_mr.py` (RSI di Wilder), `bb_breakout.py` (Bollinger), `STRATEGY_REGISTRY` ora con 3 strategie. **Cache fix applicato**: rsi_mr e bb_breakout usano identity (`is`), non `id(candles)`, come deciso in Plan A.
- `backtest_suite/optimizer/` — `types.py` (10 dataclass), `fitness.py` (score OOS aggregato + filtri hard, riusa `walk_forward._generate_windows` e `score.full_report`), `ga.py` (operatori + evolve loop + multiprocessing spawn pool), `grid.py` (grid search con cap `max_combos` + batching).

**Verifica finale:** 65 test suite + 17 legacy walk-forward = tutti verdi. Confine architetturale intatto (nessun import `backtest_suite` dentro `hermes_trading`), nessun import circolare, 5 chiavi risk con singola fonte di verità (`ga._DEFAULT_RISK_RANGES`).

**Polish pass dei follow-up (2026-05-29, commit `c592a46`→`373e03f`) — RISOLTI ✅:**
- `optimizer/ga.py`: `evolve()` ora rifiuta `n_generations<1` con `ValueError` (+ test). ✅
- `optimizer/ga.py`: import duplicati a metà file consolidati in cima. ✅
- `optimizer/grid.py`: rimosso import `score_individual` inutilizzato. ✅
- `optimizer/fitness.py`: rimosso `import math`; sul fail-per-DD ora `per_window_scores=scores` (niente score-fantasma). ✅
- `data_lake/parquet_store.py`: `read_range` ignora file `.parquet` con nome non-intero (try/except); `write_year_file` ora atomica (temp + `os.replace`) (+ test). ✅
- `data_lake/kraken_source.py`: aggiunto test del path di retry su eccezione (time.sleep mockato). ✅

Suite dopo polish: **68 test + 17 legacy = verdi**.

**Follow-up NON applicati (decisioni di design, non polish):**
- `data_lake/__init__.py`: idempotenza di `fetch` può ri-scaricare se l'exchange non ha ancora l'ultima candela del range (costo, non correttezza). Rilassare il boundary è una scelta di semantica meglio decisa con la CLI in **Plan C** (`hermes-bt fetch`). Lasciato com'è.
- `pyproject.toml`: coesistono `[dependency-groups] dev` e `[project.optional-dependencies] dev` (entrambi pytest/pytest-asyncio). Additivo e benigno; eventuale dedup a discrezione.

---

## Plan C — PROSSIMO ⏳ (0/6 task)

File: `docs/superpowers/plans/2026-05-27-backtest-suite-plan-C-persistence-cli.md`. Aggiunge persistenza SQLite (metadati) + parquet (artefatti) + CLI `hermes-bt`.

**Comando per ripartire:**
```
cd ~/hermes-trading/worker
uv run pytest tests/suite -q   # baseline: deve dare 65 passed
# eseguire Plan C task-by-task via subagent-driven-development
```

## Plan D

Già scritto (`...-plan-D-server-ui.md`), da eseguire dopo Plan C. Aggiunge server FastAPI + WebSocket + UI + test E2E.

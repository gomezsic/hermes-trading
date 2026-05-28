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
| **B — Data + Optimizer** | Data lake parquet (Kraken/ccxt) + RSI/Bollinger + **fitness OOS + GA + grid search** | ⏳ **PROSSIMO (0/10 task)** |
| **C — Persistence + CLI** | SQLite (metadati) + parquet (artefatti) + CLI `hermes-bt` | 📄 scritto, non iniziato |
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

## Plan B — PROSSIMO (da iniziare) ⏳

File: `docs/superpowers/plans/2026-05-27-backtest-suite-plan-B-data-optimizer.md`

**10 task, in ordine:**
1. `data_lake/parquet_store.py` — schema/write/read/dedup/gap/coverage (7 test)
2. `data_lake/kraken_source.py` — downloader OHLCV ccxt con paginazione (ccxt MOCKATO nei test, no rete)
3. `data_lake/__init__.py` — API pubblica `fetch`/`load`/`coverage` idempotente
4. `strategies/rsi_mr.py` — `RsiMeanReversionStrategy` (RSI di Wilder)
5. `strategies/bb_breakout.py` — `BollingerBreakoutStrategy` + completa `STRATEGY_REGISTRY` (3 strategie)
6. `optimizer/types.py` — tutti i dataclass (IndividualConfig, GAConfig, GridConfig, FitnessResult, ...)
7. `optimizer/fitness.py` — fitness OOS aggregata + filtri hard (**critical path**)
8. `optimizer/ga.py` — operatori GA (init/mutate/crossover/tournament)
9. `optimizer/ga.py` — evolve loop + multiprocessing pool (test usano `n_workers=1` serial)
10. `optimizer/grid.py` — grid search con cap `max_combos`

**Prerequisiti già VERIFICATI (2026-05-29) — non ri-verificare domani:**
- `hermes_trading/walk_forward.py` espone `_DAYS_PER_MONTH = 30` (riga 50) e `_generate_windows(candles, is_days, oos_days, step_days)` (riga 213) — firma combacia con `fitness.py`.
- `hermes_trading/score.py` espone `full_report(trades, goal) -> dict` (riga 261) che include la chiave `composite_score` (riga 301) — usata da `fitness.py`.
- Plan A completo + regression gate verde (prerequisito dichiarato in Plan B).

**Note di attenzione per Plan B:**
- Le strategy nuove (rsi_mr, bb_breakout) nel plan usano ancora `id(candles)` per la cache: applicare lo STESSO fix fatto su `ema_cross` (identity `is` + reference) per coerenza e GA-safety.
- Il downloader Kraken è pubblico (no auth), ma i test devono mockare `ccxt` — nessuna chiamata di rete nella suite.
- `multiprocessing` usa `spawn`; i test girano serial (`n_workers=1`), quindi niente flakiness.

**Comando per ripartire:**
```
cd ~/hermes-trading/worker
# eseguire Plan B task-by-task via subagent-driven-development
uv run pytest tests/suite -q   # baseline: deve dare 28 passed
```

---

## Plan C e D

Già scritti (`...-plan-C-persistence-cli.md`, `...-plan-D-server-ui.md`), da eseguire dopo Plan B. C aggiunge persistenza + CLI `hermes-bt`; D aggiunge server FastAPI + UI + test E2E.

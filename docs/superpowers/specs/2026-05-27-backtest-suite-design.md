# Backtest Suite — Design Spec

**Data**: 2026-05-27
**Stato**: bozza in attesa di review
**Branch**: `dev`

## 1. Contesto e motivazione

Il progetto `hermes-trading` ha oggi:

- `hermes_trading/backtester.py` — backtester deterministico cablato sulla strategia EMA cross 20/50 + filtro VWAP + stops/trailing/partial.
- `hermes_trading/walk_forward.py` — grid search IS/OOS su 2 parametri continui, guardrail (Deflated Sharpe, varianza subwindow, distance penalty), promote/reject automatico.
- `hermes_trading/score.py` — metriche (Calmar, CVaR, Ulcer, Sharpe, win-rate, expectancy).
- Dashboard statiche HTML (`dashboard.html`, `markov_report.html`) generate da script Python on-demand.

Mancano:

- Un **motore di backtest generico**, agnostico rispetto alla strategia.
- Un **ottimizzatore genetico** per esplorare in modo robusto spazi parametrici grandi e selezionare anche la strategia da usare.
- Una **UI interattiva** per lanciare run, monitorarli live e analizzare i risultati a posteriori.
- Una **cache locale di candele OHLCV** per evitare di tirare dati live dall'exchange ogni volta (un GA serio fa 10k+ backtest per run).

Questa spec definisce la backtest suite che colma queste lacune mantenendo il sistema di trading live esistente isolato e indipendente.

## 2. Scope

**In scope**:
- Nuovo package `backtest_suite/` accanto a `hermes_trading/`.
- Interfaccia `Strategy` pluggable + 3 strategie iniziali (EMA cross, RSI mean-reversion, Bollinger breakout).
- Engine deterministico generico.
- Ottimizzatore genetico con genoma a lunghezza variabile (`strategy_id` + parametri).
- Grid search (sulla stessa fitness function del GA).
- Fitness anti-overfit basata su walk-forward IS/OOS aggregato.
- Multiprocessing pool per parallelizzare la valutazione fitness.
- Data lake locale parquet con downloader Kraken via `ccxt`.
- Persistenza SQLite (metadati) + parquet (artefatti).
- UI web locale (FastAPI + frontend HTML/JS vanilla + Chart.js) con monitoring live via WebSocket.
- CLI `hermes-bt` (fetch, run, grid, evolve, ui).
- Promote automatico di un individuo vincente verso `state/strategy.yaml`.

**Out of scope** (esplicitamente esclusi):
- Multi-asset / portfolio backtest (universe = solo BTC/USDT).
- Cloud sync, autenticazione, multi-utente (la UI è locale).
- Vettorializzazione numpy/numba del backtester (la velocità si raggiunge col solo multiprocessing).
- Multi-objective NSGA-II (Pareto front).
- Plugin discovery dinamica via entry-points.
- Random search / Sobol sampling.
- Simulazione di Markov regime, ADX guard, news/calendar/weekend guard (coerente con l'engine attuale).
- Pannello "edit live" dei parametri GA durante un run.

**Non-goal**: la suite NON deve poter eseguire trading live. Quello rimane responsabilità di `hermes_trading/` invariato.

## 3. Decisioni architetturali

| # | Decisione | Razionale |
|---|---|---|
| 1 | Package separato `backtest_suite/` accanto a `hermes_trading/` | Separazione pulita live ↔ research. `backtest_suite` può importare da `hermes_trading`, mai il contrario. Test indipendenti, refactor sicuri. |
| 2 | Interfaccia `Strategy` pluggable | Aggiungere nuove strategie senza toccare engine/optimizer. |
| 3 | Risk management condiviso (NON dentro la `Strategy`) | SL/partial/trailing comuni a tutte; il GA li ottimizza indipendentemente dalla logica d'ingresso. |
| 4 | Engine deterministico (zero stocasticità) | Riproducibilità bit-perfect. Coerente con `backtester.py` attuale. |
| 5 | GA con genoma variabile + speciation per `strategy_id` | Crossover compatibile solo tra individui della stessa specie; mutazione speciale `mutate_strategy_id` per migrazione cross-specie. |
| 6 | Fitness = aggregata OOS multi-finestra | Anti-overfit built-in. Riusa logica di `walk_forward.py`. |
| 7 | Multiprocessing pool per parallelismo | Speedup ~6-7x su CPU 8-core. Zero rewrite dell'engine. |
| 8 | Data lake parquet locale + downloader Kraken | Idempotente, fast, riproducibile. Kraken via `ccxt` come l'adapter esistente. |
| 9 | SQLite (metadati) + parquet (artefatti pesanti) | Query veloci dalla UI, file-based, backup banale, zero servizi esterni. |
| 10 | WebSocket custom in FastAPI per live monitoring | Eventi push generation-by-generation, replay degli ultimi N su connect. |
| 11 | Frontend HTML/CSS/JS vanilla + Chart.js | Coerente con `dashboard.html` esistente. Zero build toolchain. |
| 12 | CLI dedicato `hermes-bt` | Entry point unico per fetch/run/grid/evolve/ui. |

## 4. Architettura ad alto livello

### 4.1 Layout repo

```
hermes-trading/
├── hermes_trading/                  # esistente — toccato solo per refactor minimo
│   ├── backtester.py                # estratte funzioni core riusabili dall'engine nuovo
│   ├── score.py                     # invariato (riusato dalla fitness)
│   ├── indicators.py                # invariato (EMA, ATR, ecc.)
│   └── walk_forward.py              # invariato (logica window-gen riusata)
│
├── backtest_suite/                  # NUOVO package
│   ├── __init__.py
│   ├── cli.py                       # entry point: hermes-bt
│   ├── config.py                    # pydantic models + YAML loader
│   │
│   ├── data_lake/
│   │   ├── __init__.py              # API pubblica: fetch, load, coverage
│   │   ├── kraken_source.py         # downloader via ccxt
│   │   └── parquet_store.py         # read/write parquet, validation, gap detection
│   │
│   ├── engine/
│   │   ├── __init__.py              # API: run_backtest()
│   │   ├── types.py                 # RiskConfig, ExecutionConfig, Trade, BacktestResult
│   │   ├── execution.py             # slippage, fee, pnl helpers (estratti da backtester.py)
│   │   └── risk.py                  # SL/trailing/partial logic (estratti da backtester.py)
│   │
│   ├── strategies/
│   │   ├── __init__.py              # STRATEGY_REGISTRY
│   │   ├── base.py                  # Strategy Protocol, ParamSpec, Signal
│   │   ├── ema_cross.py             # wrapping della logica esistente
│   │   ├── rsi_mr.py
│   │   └── bb_breakout.py
│   │
│   ├── optimizer/
│   │   ├── __init__.py
│   │   ├── types.py                 # IndividualConfig, Scored, GAConfig, GridConfig
│   │   ├── fitness.py               # fitness OOS aggregata
│   │   ├── ga.py                    # operatori + evolve loop
│   │   └── grid.py                  # grid search
│   │
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── catalog_db.py            # SQLite wrapper
│   │   └── artifact_store.py        # parquet I/O per equity/trades/generations
│   │
│   └── server/
│       ├── __init__.py
│       ├── app.py                   # FastAPI app factory
│       ├── api.py                   # REST endpoints
│       ├── ws.py                    # WebSocket + event broker
│       └── static/                  # frontend
│           ├── index.html
│           ├── runs.html
│           ├── data.html
│           ├── strategies.html
│           ├── settings.html
│           ├── css/app.css
│           ├── js/app.js
│           └── js/charts.js         # wrapper Chart.js
│
├── data/                            # NUOVA root per dati locali (gitignored)
│   ├── ohlcv/kraken/BTCUSDT/{1m,5m,15m,1h,4h,1d}/{YYYY}.parquet
│   └── backtests/
│       ├── catalog.db
│       └── runs/{NNNN}/...
│
├── tests/
│   ├── live/                        # test esistenti hermes_trading
│   └── suite/                       # test nuovi backtest_suite
│       └── fixtures/
│           └── btc_1h_2024_h1.parquet
│
└── pyproject.toml                   # entry point: hermes-bt = backtest_suite.cli:main
```

### 4.2 Dipendenze nuove

- `pyarrow` — read/write parquet
- `pydantic` ≥ 2 — validazione config
- `fastapi` + `uvicorn` — server
- `ccxt` (già presente) — downloader Kraken
- `websockets` (transitive di FastAPI)
- `chart.js` (CDN, non Python) — frontend

`pandas` opzionale; il pipeline interno può rimanere su `list[dict]` per coerenza con l'engine esistente. `pyarrow` espone direttamente lettura/scrittura.

### 4.3 Confini live ↔ research

- `backtest_suite` importa da `hermes_trading` (`score`, `indicators`, `_engine_core`).
- `hermes_trading` NON importa da `backtest_suite` mai. Il bot live deve poter girare anche senza la suite installata.
- La `Strategy` interface vive in `backtest_suite/strategies/base.py` (la suite la usa; il bot live non ne ha bisogno).
- Pure helpers condivisi (slippage, pnl, equity curve, risk step) vivono in `hermes_trading/_engine_core.py` e sono importati sia da `hermes_trading/backtester.py` sia da `backtest_suite/engine/*.py`. Vedi §15 per i dettagli del refactor.
- Test separati per fascia (`tests/live/`, `tests/suite/`).
- `pyproject.toml` espone `hermes-bt` ma il bot live continua a girare come prima (`uv run python -m hermes_trading.run`).

## 5. Strategy interface

```python
# backtest_suite/strategies/base.py
from typing import Protocol, ClassVar
from dataclasses import dataclass

@dataclass(frozen=True)
class ParamSpec:
    name: str
    low: float
    high: float
    step: float | None = None     # None = continuo; valore = discretizzato (per grid)
    is_int: bool = False
    description: str = ""

@dataclass
class Signal:
    side: str | None              # "long" | "short" | None
    confidence: float = 1.0

class Strategy(Protocol):
    strategy_id: ClassVar[str]    # es. "ema_cross"
    display_name: ClassVar[str]
    timeframes: ClassVar[tuple[str, ...]]
    param_specs: ClassVar[tuple[ParamSpec, ...]]

    def __init__(self, params: dict[str, float]) -> None: ...
    def warmup_bars(self) -> int: ...
    def on_bar(self, idx: int, candles: list[dict]) -> Signal: ...
```

**Registry** (`backtest_suite/strategies/__init__.py`):
```python
from backtest_suite.strategies.ema_cross import EmaCrossStrategy
from backtest_suite.strategies.rsi_mr   import RsiMeanReversionStrategy
from backtest_suite.strategies.bb_breakout import BollingerBreakoutStrategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    EmaCrossStrategy.strategy_id:         EmaCrossStrategy,
    RsiMeanReversionStrategy.strategy_id: RsiMeanReversionStrategy,
    BollingerBreakoutStrategy.strategy_id: BollingerBreakoutStrategy,
}
```

**Param specs delle 3 strategie iniziali**:

| strategy_id | param | low | high | step | notes |
|---|---|---|---|---|---|
| `ema_cross` | `ema_fast` | 5 | 30 | 1 | int |
| `ema_cross` | `ema_slow` | 20 | 100 | 1 | int, validation: slow > fast |
| `ema_cross` | `vwap_window` | 50 | 400 | 10 | int |
| `ema_cross` | `vwap_filter` | 0 | 1 | 1 | bool encoded |
| `ema_cross` | `direction` | 0 | 2 | 1 | 0=long, 1=short, 2=both |
| `rsi_mr` | `rsi_period` | 7 | 21 | 1 | int |
| `rsi_mr` | `oversold` | 15 | 35 | 1 | int |
| `rsi_mr` | `overbought` | 65 | 85 | 1 | int |
| `rsi_mr` | `exit_mid` | 40 | 60 | 1 | int, exit when RSI crosses |
| `bb_breakout` | `bb_period` | 10 | 40 | 1 | int |
| `bb_breakout` | `bb_std` | 1.5 | 3.0 | 0.1 | float |
| `bb_breakout` | `confirmation_bars` | 1 | 5 | 1 | int |

## 6. Engine

```python
# backtest_suite/engine/__init__.py
def run_backtest(
    candles: list[dict],
    strategy: Strategy,
    risk: RiskConfig,
    execution: ExecutionConfig,
) -> BacktestResult: ...
```

**Pipeline**:
1. Warmup: salta i primi `strategy.warmup_bars()` indici.
2. Loop `i = warmup .. n-1`:
   - `signal = strategy.on_bar(i, candles)`
   - Se posizione aperta → applica risk step (SL, trailing, partial) via `engine/risk.py`
   - Se `signal.side` valido e nessuna posizione (`allow_overlap=False`) → apri trade alla candela `i + latency_bars`
3. Chiusura forzata sull'ultima candela.
4. `_build_equity_curve` (riusato) + `score.full_report` → `BacktestResult`.

**Determinismo**: zero RNG. Output bit-perfect sugli stessi input.

**Non simula** (coerente con `backtester.py`): Markov regime, ADX guard, news/calendar/weekend guard.

**Riuso dal codice esistente** (estratto in modulo condiviso `hermes_trading/_engine_core.py` — vedi §15):
- `apply_slippage_entry`, `apply_slippage_exit`, `gross_pnl_pct`, `build_equity_curve` → `hermes_trading/_engine_core.py`
- `simulate_trade` (logica SL/trailing/partial) → `hermes_trading/_engine_core.py`, refactor parametrico su `RiskConfig`
- `backtest_suite/engine/execution.py` e `engine/risk.py` re-esportano queste funzioni e aggiungono la glue logic specifica del nuovo engine (loop bar-by-bar che interroga `Strategy.on_bar`).
- `compute_metrics` → invariato in `hermes_trading.score`.

## 7. Optimizer

### 7.1 Fitness function

```python
@dataclass
class WalkForwardConfig:
    is_months: int                       # es. 6
    oos_months: int                      # es. 2
    step_months: int                     # es. 2
    min_trades_oos: int                  # es. 20 — filtro hard
    max_drawdown_per_window: float       # es. 0.30 — filtro hard
    variance_lambda: float = 0.5         # penalty su stdev fra finestre

@dataclass
class FitnessResult:
    fitness: float                       # scalar finale (mean - lambda*stdev)
    per_window_scores: list[float]       # per UI / debug
    mean_score: float
    stdev_score: float
    max_drawdown_observed: float
    n_trades_total: int
    failed: bool                         # True se filtro hard violato
    failure_reason: str | None

def fitness(
    config: IndividualConfig,
    candles: list[dict],
    wf_config: WalkForwardConfig,
    execution: ExecutionConfig,
) -> FitnessResult:
    """
    1. Genera N finestre IS/OOS rolling (riusa logica di walk_forward._generate_windows)
    2. Per ogni finestra OOS: run_backtest(...) → composite_score
    3. Aggrega: mean(scores_oos) - variance_lambda * stdev(scores_oos)
    4. Filtri hard (fitness = -inf se violati):
       - min_trades_oos: somma trade su tutte le finestre < soglia
       - max_drawdown_per_window: max DD su qualsiasi finestra > soglia
    """
```

### 7.2 Genoma

```python
@dataclass
class IndividualConfig:
    strategy_id: str
    strategy_params: dict[str, float]
    risk_params: dict[str, float]    # SL, partial, trailing_activate, trailing, trailing_tight
```

**Speciation**: crossover ammesso solo tra individui con stesso `strategy_id`. Migrazione cross-specie via `mutate_strategy_id` (probabilità default 5%).

### 7.3 Operatori GA

- **`mutate(ind, rate, rng)`**: Gaussian mutation per ogni parametro con prob=rate, σ=(high-low)*0.1, clamp + round per int. Con prob `mutate_strategy_id_prob` cambia `strategy_id` e re-inizializza i parametri uniformemente.
- **`crossover(a, b, rng)`**: uniform crossover per parametro (solo se stessa specie). Se specie diverse: ritorna `(a, b)` invariati.
- **`select_tournament(pop, k, rng)`**: tournament size k=3.

### 7.4 Evolve loop

```python
@dataclass
class GAConfig:
    n_generations: int
    pop_size: int
    elite_size: int
    mutation_rate: float                 # prob. di mutazione per parametro
    crossover_rate: float                # prob. di crossover (resto: cloning)
    tournament_k: int                    # tournament selection size, default 3
    species_quotas: dict[str, float]     # init pop quote per strategy_id
    mutate_strategy_id_prob: float       # default 0.05
    immigrants_rate: float               # default 0.05
    immigrants_every: int                # ogni N generazioni
    seed: int

@dataclass
class EvolutionResult:
    best_individual: IndividualConfig
    best_fitness: float
    n_generations_completed: int
    history: list[GenerationEvent]
    elapsed_sec: float
    status: str                          # 'finished' | 'stopped' | 'failed'

def evolve(
    config: GAConfig,
    candles: list[dict],
    wf_config: WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],       # cooperative stop check
    progress_callback: Callable[[GenerationEvent], None],
) -> EvolutionResult:
    """
    1. Init: popolazione random con quote per specie (config.species_quotas)
    2. Per generation in range(config.n_generations):
       a. Valuta fitness in parallelo (multiprocessing.Pool)
       b. Sort per fitness, salva top-K, emit GenerationEvent
       c. Elite = top-E individuals preservati
       d. Resto: tournament select → crossover → mutate
       e. Inietta random immigrants 5% ogni immigrants_every generation (diversità)
       f. Controllo stop_flag() — se True: status='stopped', flush ed esci
    3. Ritorna EvolutionResult con best individual + storia generazioni
    """
```

### 7.5 Multiprocessing pool

```python
with multiprocessing.Pool(n_workers, initializer=_init_worker, initargs=(candles,)) as pool:
    scored = pool.map(_evaluate_one, population)
```

`_evaluate_one` è funzione top-level (no closure). `_init_worker` carica `candles` una volta per worker (evita pickle ripetuto del payload OHLCV).

### 7.6 Grid search

```python
@dataclass
class GridConfig:
    strategy_ids: list[str]              # default: tutti i registered
    risk_params_grid: dict[str, list[float]]  # override esplicito per SL/trailing/...
    strategy_params_grid: dict[str, dict[str, list[float]]] | None
        # None = usa ParamSpec.step di ogni strategy per generare i valori
    max_combos: int                      # cap di sicurezza, default 5000

def grid_search(
    config: GridConfig,
    candles: list[dict],
    wf_config: WalkForwardConfig,
    execution: ExecutionConfig,
    stop_flag: Callable[[], bool],
    progress_callback: Callable[[GridProgressEvent], None],
) -> GridResult: ...
```

Stessa `fitness()` del GA. Per ogni `strategy_id` in `config.strategy_ids`: `itertools.product` dei valori discreti di ogni param (da `strategy_params_grid` se presente, altrimenti generati da `ParamSpec.low/high/step`). Unione di tutte le combinazioni. Se `len(combos) > max_combos`: errore pre-run con suggerimento. Parallelizzato col Pool. `GridProgressEvent` analogo a `GenerationEvent` per UI live.

### 7.7 Eventi live

```python
@dataclass
class GenerationEvent:
    generation: int
    pop_size: int
    best_fitness: float
    mean_fitness: float
    best_individual: IndividualConfig
    species_counts: dict[str, int]
    elapsed_sec: float
```

L'optimizer chiama `progress_callback(event)`. Il server FastAPI inietta un callback che pubblica su un broker async interno → WebSocket. Optimizer testabile in isolamento.

## 8. UI

### 8.1 Information Architecture

Top nav: **Runs · Data · Strategies · Settings**. Banner live in alto a destra se c'è un run in corso (click → drill-down al run).

### 8.2 Pagine

- **Runs**: lista esperimenti (passati + corrente). Filtri per status, kind, strategy.
- **Run detail (live monitor)**: status bar (gen X/Y, best/mean fitness, elapsed, ETA, stop), fitness chart (best + mean), top-5 individuals, species distribution.
- **Individual detail** (drill-down): strategy params, risk params, aggregate metrics (5 OOS), equity curve vs buy&hold, per-window OOS scores, azioni (Export YAML, Promote to strategy.yaml, Re-run on holdout).
- **Data**: coverage map (timeframe × anno), candles count, gap count, pulsanti Refresh.
- **Strategies**: registry + param specs (sola lettura). Form per test singolo manuale (sola strategia + risk + range candele → 1 backtest).
- **Settings**: percorsi data lake, n_workers default, fee/slippage di default.

### 8.3 Endpoint REST

| metodo | path | scope |
|---|---|---|
| GET | `/api/runs` | lista, filtri |
| GET | `/api/runs/{id}` | dettaglio + top-K individuals |
| POST | `/api/runs` | lancia run (kind: ga/grid/single) |
| POST | `/api/runs/{id}/stop` | stop cooperativo |
| GET | `/api/runs/{id}/individuals/{ind_id}` | individual detail (params + metrics + equity + trades) |
| POST | `/api/runs/{id}/individuals/{ind_id}/promote` | scrive in `state/strategy.yaml` |
| POST | `/api/runs/{id}/individuals/{ind_id}/holdout` | re-run su holdout |
| GET | `/api/data/coverage` | coverage map |
| POST | `/api/data/fetch` | scarica range |
| GET | `/api/strategies` | registry |

### 8.4 WebSocket

`WS /ws/runs/{id}` — eventi:
- `generation` (GenerationEvent)
- `individual_failed` (per debugging fitness fail)
- `run_finished` (best + summary)
- `run_failed` (motivazione)

Replay degli ultimi 50 eventi su connect (per riaprire la pagina senza perdere lo stato).

### 8.5 Stack frontend

- HTML statico + CSS + JS vanilla
- Chart.js via CDN per fitness chart, equity curve, species distribution
- Tabelle ordinabili con JS leggero (no librerie esterne)
- Servito da FastAPI come `StaticFiles` su `/`

## 9. Data Lake

### 9.1 Layout disco

```
data/ohlcv/kraken/BTCUSDT/{1m,5m,15m,1h,4h,1d}/{YYYY}.parquet
```

Un file per anno-timeframe. Append-only sort per `t`, no duplicati.

### 9.2 Schema parquet

| colonna | tipo | note |
|---|---|---|
| `t` | int64 | unix timestamp seconds, UTC, allineato al timeframe |
| `o`, `h`, `l`, `c` | float64 | OHLC |
| `v` | float64 | volume base |
| `n_trades` | int32 | per data quality |

### 9.3 API

```python
def fetch(symbol, timeframe, since, until, force_refresh=False) -> Path: ...
def load(symbol, timeframe, since=None, until=None) -> list[dict]: ...
def coverage(symbol, timeframe) -> dict: ...
```

### 9.4 Decisioni

- **Idempotenza**: `fetch()` confronta `coverage()` col range richiesto e scarica solo i buchi.
- **Rate-limit Kraken**: 1 req/sec, backoff esponenziale su errori.
- **Validation**: `t` allineato al timeframe; candele `v==0` and `n_trades==0` droppate con warning.
- **Gap detection**: `coverage()` segnala gap; l'engine rifiuta range con gap > 1% (configurabile).
- **Multi-source futuro**: layout directory già pronto per altri exchange, fuori scope qui.

## 10. Persistenza

### 10.1 Layout disco

```
data/backtests/
├── catalog.db                         # SQLite
└── runs/{NNNN}/
    ├── manifest.yaml                  # config completo + git commit + data fingerprint
    ├── run.log                        # log JSON
    ├── generations.parquet            # tutte le row (gen, individual) — fitness + key metrics
    ├── equity/{individual_id}.parquet # solo top-K (default 20)
    └── trades/{individual_id}.parquet # solo top-K
```

### 10.2 Schema SQLite

```sql
CREATE TABLE runs (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,        -- 'ga' | 'grid' | 'single'
  status          TEXT NOT NULL,        -- 'running' | 'finished' | 'failed' | 'stopped'
  symbol          TEXT NOT NULL,
  timeframe       TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  config_path     TEXT NOT NULL,
  best_fitness    REAL,
  best_individual TEXT,                 -- JSON IndividualConfig
  n_generations   INTEGER,
  n_individuals   INTEGER,
  notes           TEXT
);

CREATE TABLE individuals (
  run_id          INTEGER NOT NULL,
  generation      INTEGER NOT NULL,
  rank            INTEGER NOT NULL,
  individual_id   TEXT NOT NULL,
  strategy_id     TEXT NOT NULL,
  params_json     TEXT NOT NULL,
  fitness         REAL NOT NULL,
  mean_oos_score  REAL,
  stdev_oos_score REAL,
  max_drawdown    REAL,
  sharpe          REAL,
  n_trades        INTEGER,
  PRIMARY KEY (run_id, generation, rank),
  FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX idx_individuals_fitness ON individuals(run_id, fitness DESC);
CREATE INDEX idx_runs_status        ON runs(status, started_at DESC);
```

Migration: `CREATE TABLE IF NOT EXISTS` al boot. `PRAGMA user_version` per versioning manuale.

### 10.3 Write pattern durante GA

- Generation evaluator → `GenerationEvent`
- `PersistenceWriter` batcheria `INSERT` per generazione (≤ pop_size rows)
- WebSocket broker pubblica in parallelo, non bloccato dall'I/O DB

### 10.4 Top-K configurable

Equity/trades parquet salvati solo per top-K individuals (default `save_top_k: 20`). Gli altri solo in SQLite scalars + `generations.parquet`.

### 10.5 API

```python
class CatalogDB:
    def create_run(self, kind, symbol, timeframe, config_path) -> int: ...
    def update_run_status(self, run_id, status, **fields) -> None: ...
    def insert_generation(self, run_id, generation, scored: list[Scored]) -> None: ...
    def list_runs(self, status=None, limit=100) -> list[dict]: ...
    def get_run(self, run_id) -> dict: ...
    def top_individuals(self, run_id, k) -> list[dict]: ...

class ArtifactStore:
    def save_equity(self, run_id, individual_id, curve: list[dict]) -> None: ...
    def save_trades(self, run_id, individual_id, trades: list[Trade]) -> None: ...
    def load_equity(self, run_id, individual_id) -> list[dict]: ...
    def load_manifest(self, run_id) -> dict: ...
```

## 11. CLI

```
hermes-bt fetch <symbol> <timeframe> --since 2022-01-01 [--until ...]
hermes-bt run <config.yaml>           # singolo backtest
hermes-bt grid <config.yaml>          # grid search
hermes-bt evolve <config.yaml>        # genetic algorithm
hermes-bt ui [--port 8765] [--open]   # avvia FastAPI
hermes-bt run --replay runs/0042/manifest.yaml  # ri-esegue identico
```

Config YAML (esempio `evolve`):
```yaml
kind: ga
symbol: BTCUSDT
timeframe: 1h
range:
  since: "2023-01-01"
  until: "2026-05-01"
walk_forward:
  is_months: 6
  oos_months: 2
  step_months: 2
  min_trades_oos: 20
  max_drawdown_per_window: 0.30
ga:
  n_generations: 50
  pop_size: 100
  elite_size: 5
  mutation_rate: 0.15
  crossover_rate: 0.7
  tournament_k: 3
  species_quotas:
    ema_cross: 0.4
    rsi_mr: 0.3
    bb_breakout: 0.3
  mutate_strategy_id_prob: 0.05
  immigrants_rate: 0.05
  immigrants_every: 10
  seed: 42
fitness:
  variance_lambda: 0.5
execution:
  taker_fee: 0.0026
  slippage: 0.0005
  capital: 10000.0
persistence:
  save_top_k: 20
n_workers: 6   # default = os.cpu_count() - 2
```

## 12. Error handling

| Livello | Comportamento |
|---|---|
| Single backtest fail | individuo scartato (`fitness = -inf`), evento `individual_failed`, GA continua |
| Generation evaluator fail | retry x1 con worker fresco; se rifallisce, run `failed` + motivazione |
| Persistence write fail | log warning, evento UI; run continua |
| Disk full / DB locked | run abortito, status `failed`, manifest aggiornato |
| `KeyboardInterrupt` (CLI) | run marcato `stopped`, ultimo batch flush, exit pulito |
| `POST /runs/{id}/stop` (UI) | flag cooperativo letto tra le generazioni |
| Config invalida (pydantic) | rifiuto pre-run, errore con nome param + range atteso |
| Range OHLCV con gap > 1% | `engine` rifiuta, errore chiaro che indica il comando `fetch` da lanciare |

Logging: stdlib `logging`. JSON log per run a `data/backtests/runs/{id}/run.log` + log condiviso `data/backtests/suite.log`.

## 13. Determinismo e riproducibilità

Tre livelli, tutti bit-perfect sugli stessi input:

1. **Engine** — zero RNG (eredità di `backtester.py`).
2. **Fitness** — deterministica (run_backtest su finestre fisse).
3. **GA** — pseudo-deterministico via `random.Random(seed)`. Seed salvato nel manifest. Tutte le operazioni stocastiche consumano l'unica istanza Random in ordine deterministico (post-scoring batch).

**Multiprocessing**: l'ordine di valutazione può variare, ma lo scoring batch è ordinato per indice nella popolazione PRIMA della selezione → RNG sempre consumato in ordine deterministico.

**Manifest per riproducibilità**:
```yaml
suite_version: "0.1.0"
git_commit: "a1b2c3d"
git_dirty: false
python: "3.11.7"
seed: 42
config: {...}
data_fingerprint:
  "BTCUSDT/1h/2024.parquet": "f3a2..."
  "BTCUSDT/1h/2025.parquet": "9e1b..."
```

`hermes-bt run --replay runs/0042/manifest.yaml` → identico bit-per-bit se git+data fingerprint matchano (warning altrimenti).

## 14. Testing

### 14.1 Unit (veloci)

- `engine/`: deterministic outputs su candele sintetiche (trend, range, gap), partial+trailing, latency 0 vs 1.
- `strategies/{ema_cross,rsi_mr,bb_breakout}`: signal generation su candele costruite ad hoc.
- `optimizer/ga.py`: `mutate/crossover/tournament` su RNG seeded → output atteso.
- `optimizer/fitness.py`: window generation, score aggregation, penalty stdev.
- `persistence/`: CRUD SQLite tmp dir, schema invariants, parquet roundtrip.
- `data_lake/`: timestamp alignment, gap detection, parquet append idempotent.

### 14.2 Integration

- end-to-end `ga.evolve(pop=10, gen=3)` su fixture 6 mesi 1h → fitness > 0, file su disco, SQLite popolato.
- live monitor: avvia run dal server, subscribe WebSocket, verifica eventi `generation` in ordine.
- promote: `POST /api/runs/{id}/promote/{ind}` aggiorna `state/strategy.yaml` correttamente.

### 14.3 Regression (critici per la migrazione)

- `test_backtester_compat.py`: invoca il NUOVO engine con `EmaCrossStrategy` su candele identiche al test esistente di `walk_forward`. Output `trades`, `equity_curve`, `metrics` **bit-perfect identici** al backtester originale. Questo test gatekeepa il refactor.

### 14.4 Coverage target

- ≥ 80% per `engine/` e `optimizer/`
- ≥ 70% per il resto

### 14.5 Fixture

`tests/suite/fixtures/btc_1h_2024_h1.parquet` — 6 mesi candele 1h (~4320 righe, ~250KB), committato.

## 15. Migrazione e refactor

Il refactor di `hermes_trading/backtester.py` è **non-distruttivo**: il modulo continua a esporre `run_backtest(candles, strategy, capital, seed=42)` con la stessa signature e gli stessi output. Cambia la struttura interna per estrarre funzioni pure in `hermes_trading/_engine_core.py` (modulo nuovo dentro lo stesso package), da cui sia il backtester legacy sia il nuovo `backtest_suite/engine/` importano. Questo preserva la regola "`hermes_trading` non importa da `backtest_suite`" (§4.3).

**Step di migrazione** (verranno dettagliati nel plan):

1. Creare `hermes_trading/_engine_core.py` con:
   - `apply_slippage_entry(price, side)`, `apply_slippage_exit(price, side)`, `gross_pnl_pct(entry, exit, side)`
   - `build_equity_curve(candles, trades, capital)`
   - `simulate_trade(candles, entry_idx, side, risk: RiskConfig)` — refactor di `_simulate_trade` parametrizzato su `RiskConfig` invece di leggere campi da `strategy: dict`
   - `RiskConfig` dataclass (anche `hermes_trading.backtester` lo usa internamente)
2. `hermes_trading/backtester.py` rimane in piedi:
   - importa helpers da `_engine_core`
   - costruisce `RiskConfig` dai campi dello `strategy: dict` come fa oggi
   - mantiene la stessa interfaccia esterna `run_backtest(candles, strategy, capital, seed=42)`
3. `backtest_suite/engine/execution.py` e `engine/risk.py` re-esportano `_engine_core` + aggiungono il nuovo loop generico che interroga `Strategy.on_bar`.
4. `backtest_suite/strategies/ema_cross.py` implementa `EmaCrossStrategy` con la stessa logica di cross-detection (golden/death) di `backtester.py`.
5. Test regression `test_backtester_compat.py`: chiama `hermes_trading.backtester.run_backtest(...)` e `backtest_suite.engine.run_backtest(candles, EmaCrossStrategy(params), RiskConfig(...), ExecutionConfig(...))` sugli stessi input → `trades`, `equity_curve`, `metrics` bit-perfect identici. Questo test gatekeepa il merge del refactor.

`walk_forward.py` resta invariato. Lo si potrà aggiornare in futuro per usare l'optimizer/fitness della suite, ma quel cambio è fuori scope qui.

## 16. Cose deliberatamente NON fatte (riepilogo)

- Multi-asset/portfolio
- Cloud sync / Supabase / auth
- Vettorializzazione numba
- Pareto multi-objective
- Plugin discovery dinamica
- Random/Sobol sampling
- Simulazione regime/news/calendar guards nell'engine
- Edit live dei parametri GA durante un run

## 17. Domande aperte / rischi noti

- **Pickle overhead nel pool**: con `pop_size=100` e `fitness OOS multi-finestra`, l'I/O di pickle potrebbe diventare dominante. Misurare presto; se è un problema, valutare `multiprocessing.shared_memory` per le candele.
- **Determinismo cross-platform**: `multiprocessing` su macOS (default `spawn`) vs Linux (`fork`) — assicurarsi che l'output sia identico. Test in CI su entrambi se possibile.
- **SQLite write contention** sotto live monitoring + GA che scrive: tenere d'occhio. Se diventa problema, switchare a WAL mode (`PRAGMA journal_mode=WAL`) — già supportato da SQLite, zero codice extra.
- **Fitness OOS molto lenta**: 100 ind × 5 finestre = 500 backtest per generazione × 50 gen = 25000 backtest. Stima 50ms/backtest × 25000 / 6 worker ≈ 3.5 min/run. Misurare; se troppo lento, prima azione = ridurre `pop_size` o `n_generations`.

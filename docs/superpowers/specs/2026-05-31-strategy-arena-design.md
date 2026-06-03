# Strategy Arena — 10 agenti LLM-evolutivi in competizione

**Data:** 2026-05-31
**Stato:** F1 + F2 COMPLETATE (2026-06-03); F3 (LLM) prossima. Vedi `docs/superpowers/BACKTEST_SUITE_STATUS.md` → sezione Strategy Arena.
**Branch:** dev

## 1. Obiettivo

Costruire un'**arena** in cui 10 agenti, ognuno proprietario di una **famiglia di strategia diversa**, competono su backtest storico. Ogni agente ha un *proposer* che genera i prossimi parametri della sua strategia generazione dopo generazione; il proposer principale è un **LLM** (evoluzione guidata da modello), affiancato da un **baseline deterministico** (ricerca cieca) per misurare se l'LLM aggiunge edge reale.

Il vincitore non è chi massimizza il return in-sample, ma chi ottiene il miglior compromesso **net profit + drawdown minimo + profit factor** *out-of-sample*, con difesa anti-overfitting.

Non-goal di questa fase: trading live degli agenti, eliminazione/crossover tra lignaggi, mutazione strutturale (cambio di indicatori/regole). Restano 10 archetipi fissi che evolvono solo i propri parametri.

## 2. Decisioni di design (dal brainstorming)

| # | Decisione | Scelta |
|---|-----------|--------|
| D1 | Forma della competizione | Agenti LLM-evolutivi |
| D2 | Substrato di valutazione | Backtest storico offline (generazioni in minuti) |
| D3 | Fitness | Multi-obiettivo: net profit + drawdown minimo + profit factor |
| D4 | Diversità & selezione | 10 archetipi fissi distinti, evolvono i parametri, **nessuna eliminazione** |
| D5 | Approccio costruttivo | **C** — interfaccia `Proposer` pluggable: `LLMProposer` (testa) + `RandomGAProposer` (controllo) |
| D6 | Anti-overfit | Validazione OOS walk-forward ora; aggancio a CPCV/DSR quando il validation layer sarà costruito |

## 3. Collocazione e confini

Nuovo sottopacchetto **`backtest_suite/arena/`**.

Regola di import (invariante architetturale del repo): `arena` può importare da `backtest_suite` e da `hermes_trading`, **mai** il contrario.

Riuso dell'esistente:
- `backtest_suite/engine/` — backtest deterministici (`run_backtest`)
- `backtest_suite/strategies/` — interfaccia `Strategy` Protocol pluggable + 3 strategie esistenti
- `backtest_suite/optimizer/fitness.py` — valutazione walk-forward OOS
- `backtest_suite/persistence/` — SQLite WAL (`catalog_db.py`) + `artifact_store.py`
- `backtest_suite/server/` — FastAPI + frontend vanilla/Chart.js, esteso con vista "Arena"

Moduli nuovi in `arena/`:
- `archetypes.py` — registro delle 10 famiglie di strategia
- `proposer.py` — `Proposer` Protocol + `LLMProposer` + `RandomGAProposer`
- `agent.py` — stato di un agente (archetipo, parametri correnti, lineage)
- `fitness.py` — composite score multi-obiettivo + verdetto di validazione
- `tournament.py` — loop di evoluzione, leaderboard, orchestrazione
- `llm_client.py` — client Anthropic isolato con cache delle proposte

## 4. I 10 archetipi

Ogni archetipo implementa il `Strategy` Protocol esistente (`on_bar`, `warmup_bars`, `param_specs`). I `param_specs` definiscono i bound che vincolano sia l'LLM che il GA.

| # | id | Idea | Stato |
|---|----|------|-------|
| 1 | `price_vs_ma_cross` | Close reale incrocia una MA (più reattivo dell'EMA-EMA) | NUOVO |
| 2 | `ema_cross` | Golden/death cross EMA fast/slow | esistente |
| 3 | `rsi_mean_reversion` | RSI oltre soglia → rientro | esistente |
| 4 | `bollinger_breakout` | Rottura banda di Bollinger | esistente |
| 5 | `donchian_breakout` | Rottura canale N-periodi | NUOVO |
| 6 | `macd_signal` | Cross MACD / signal line | NUOVO |
| 7 | `supertrend` | Trend follower ATR-based | NUOVO |
| 8 | `momentum_roc` | Rate-of-Change oltre soglia | NUOVO |
| 9 | `keltner_breakout` | Rottura canale Keltner (EMA ± k·ATR) | NUOVO |
| 10 | `vwap_reversion` | Rientro verso il VWAP | NUOVO |

## 5. Interfaccia `Proposer`

```python
class Proposer(Protocol):
    def propose(self, archetype, param_specs, history, goals, rng) -> dict:
        """Ritorna un dict di parametri VALIDI (entro param_specs)."""
```

**`LLMProposer`** (proposer principale):
- Costruisce un prompt con: `param_specs` (nomi + bound), gli ultimi K tentativi dell'agente con breakdown di fitness (net profit, maxDD, profit factor, verdetto validazione) e il razionale precedente.
- Chiede i prossimi parametri come **JSON vincolato**.
- Validazione rigida: fuori-bound → clamp; JSON invalido → 1 retry → fallback a mutazione GA (loggato).
- **Cache** delle risposte per `hash(prompt + model_id)` → riproducibilità + zero costo sui re-run.

**`RandomGAProposer`** (controllo/baseline):
- Mutazione/campionamento deterministico seedato entro i `param_specs`. Nessuna chiamata LLM.

L'arena fa girare **ogni archetipo con entrambi i proposer**: la leaderboard mostra side-by-side `<archetipo>/LLM` vs `<archetipo>/GA`, rispondendo a "l'LLM batte la ricerca cieca?".

## 6. Fitness multi-obiettivo + gate di validazione

Per ogni set di parametri: backtest su split **train**, poi valutazione **out-of-sample** walk-forward (riuso `fitness.py`).

Metriche grezze sempre riportate: `net_profit`, `max_drawdown`, `profit_factor` (+ Sharpe per contesto).

Ranking via **composite score configurabile** (pesi in YAML, default trasparenti):

```
score = w1·norm(net_profit) + w2·norm(profit_factor) − w3·norm(max_drawdown)
```

dove `norm` è uno z-score robusto calcolato sulla popolazione della generazione. **Il ranking usa esclusivamente le metriche OOS**, mai quelle in-sample.

**Gate di validazione**: ogni candidato riceve un verdetto `robust / weak / likely_overfit`.
- Ora: derivato dal walk-forward OOS (varianza tra finestre + degradazione IS→OOS).
- Futuro: quando il validation layer CPCV/DSR (spec `2026-05-29-validation-layer-design.md`) sarà costruito, si aggancia qui.
- I candidati `likely_overfit` vengono **retrocessi** in classifica, mai eliminati silenziosamente; il caso è loggato.

## 7. Loop di evoluzione + leaderboard

```
per generazione g in 1..G:
    per ogni agente (10 archetipi) × ogni proposer (LLM, GA):
        params  = proposer.propose(archetype, specs, agent.history, goals, rng)
        m_train = run_backtest(strategy(params), train_split)
        m_oos   = walk_forward_validate(strategy(params), oos_splits)
        fit     = composite_score(m_oos) + verdetto_validazione
        agent.history.append((params, fit, rationale))
        persist(generation=g, agent, proposer, params, metriche, fit)
    leaderboard.update()       # best-so-far per (archetipo, proposer)
stop quando:  g == G   OR   budget LLM esaurito   OR   nessun miglioramento per P generazioni
```

- **Fan-out**: 10 archetipi × 2 proposer = 20 backtest/generazione → parallelizzati col multiprocessing già presente nell'optimizer. Le chiamate LLM sono il collo di bottiglia → limite di concorrenza configurabile.
- **Leaderboard**: per ogni (archetipo, proposer) mantiene il best-so-far OOS + i tre KPI + verdetto; vista globale ordinata per composite score.
- **Nessuna eliminazione**: tutti i 10 archetipi restano vivi per l'intero torneo.

## 8. Persistenza

Riuso di `persistence/catalog_db.py` (SQLite WAL):
- `arena_runs` — un torneo: config, data range, seed, model_id, data-hash, timestamp.
- `arena_individuals` — una riga per prova: `run_id, generation, agent_id, archetype, proposer, params_json, net_profit, max_drawdown, profit_factor, sharpe, score, verdict`.
- Equity/trades dei campioni in parquet via `artifact_store`.

## 9. UI (vista Arena)

Estensione del server FastAPI esistente:
- Tabella **leaderboard** ordinabile per KPI.
- **Curva di evoluzione** del best-score per agente lungo le generazioni.
- **Dettaglio campione**: parametri + equity curve.
- Riuso del frontend vanilla + Chart.js in `server/static/`.

## 10. Determinismo e riproducibilità

- Seed unico per `RandomGAProposer` e per gli split.
- Cache delle proposte LLM per `hash(prompt + model_id)`.
- **Manifest** per run: seed, data-hash, versioni delle strategie, model_id.
- Garanzia: stesso input (seed + cache + dati) ⇒ leaderboard identica.

## 11. Error handling (l'arena non si blocca mai)

- Proposta LLM invalida → clamp → retry×1 → fallback `RandomGAProposer` per quel passo.
- Backtest in errore → individuo saltato e loggato (non interrompe la generazione).
- API LLM non disponibile → backoff/retry → fallback GA per quel passo.
- Tutto tracciato; nessun cap silenzioso (se si limita la coverage, lo si logga).

## 12. Testing

- **Unit** per ognuna delle 7 nuove strategie: `param_specs` validi + segnali `on_bar` attesi su serie sintetiche.
- **Unit** Proposer: validazione/clamp dei parametri fuori-bound, fallback su JSON invalido, determinismo del GA proposer.
- **Unit** fitness: composite score, normalizzazione, gate di validazione (overfit → retrocessione).
- **Unit** leaderboard: ranking corretto, retrocessione `likely_overfit`.
- **Riproducibilità**: stesso seed + cache ⇒ leaderboard identica.
- **E2E mini-arena**: 2 archetipi, 1 generazione, LLM mockato, sotto pytest (~secondi).

## 13. Fasi di implementazione

Ognuna testabile da sola; ordine consigliato:

- **F1 — Strategie**: le 7 nuove famiglie + relativi `param_specs` e test. ✅ **COMPLETA (2026-06-03)**
- **F2 — Core arena**: `agent.py`, `proposer.py` (interfaccia + `RandomGAProposer`), `fitness.py`, `tournament.py`, persistenza. Funziona end-to-end col solo baseline GA. ✅ **COMPLETA (2026-06-03)** — `backtest_suite/arena/`, plan `docs/superpowers/plans/2026-06-03-strategy-arena-F2-core-arena.md`. NB: l'arena evolve il genome completo (strategy **e** risk params).
- **F3 — LLM**: `LLMProposer` + `llm_client.py` + cache; integrazione nel loop. ⏳ **PROSSIMA**
- **F4 — UI**: vista Arena nel server FastAPI.

## 14. Dipendenze e rischi noti

- Il **validation layer CPCV/DSR non è ancora implementato**: F1–F4 usano il walk-forward OOS esistente; il gate DSR/PBO si aggancia dopo (sezione 6).
- **Costo LLM**: una generazione = fino a 10 chiamate LLM (una per archetipo lato LLMProposer). La cache azzera il costo sui re-run; budget e concorrenza configurabili.
- **Limite dati Kraken** (OHLC pubblico ignora `since`, ~720 candele): per orizzonti walk-forward lunghi serve la sorgente bulk (`binance_bulk_source.py` già presente) o accumulo incrementale — vincolo ereditato dal `backtest_suite`, non specifico dell'arena.

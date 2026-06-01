# Status & Diagnosi — sessione 2026-05-31 / 2026-06-01

Documento di riepilogo della sessione: stato reale del sistema, diagnosi del bot che non tradava, e nuovo programma "Strategy Arena". Sostituisce come fonte autoritativa le parti datate di `PROJECT.md` e `README_FIRST.md`.

## 1. Stato reale verificato (da Railway, live)

- **Worker ONLINE**: ultimo tick 2026-05-31 ~20:03 UTC, nessun errore, `consecutive_failures: 0`.
- **Strategia live = v06** (non v01/v02/v03 come dicono i doc master datati). Parametri chiave: `ema_fast 20 / ema_slow 50`, `direction: both`, `stop_loss 3.0%`, `trailing_activate 3.6% / trailing 2.4%`, `partial_exit 9.0%`, `sma50d_gate: true`, `vwap_filter: false`, sizing Kelly+vol, `max_drawdown 20%`. Safe_guard: weekend/nyse_open/calendar/adx (adx_min 20) tutti attivi.
- **Backtest Suite: COMPLETA** (4/4 plan, ~99 test + 17 legacy verdi) — confermato da `BACKTEST_SUITE_STATUS.md` (2026-05-29).
- **Trades: 0** dall'avvio (24/05). Balance fermo a 100.000, drawdown 0, `pnl_total 0`.
- **Regime Markov**: genuinamente **Bear** (`signal -0.795`, persistenza Bear 80%, `rows 729`, calcolo reale e fresco — NON un fallback).

## 2. Diagnosi: perché il bot non ha mai aperto un trade

Non è un crash né un singolo bug. È la **congiunzione** di filtri restrittivi su un mercato avverso, in un runtime brevissimo. Evidenze:

1. **Regime Bear costante** → il filtro Markov (`loop.py:783`) blocca il **100% dei segnali LONG**. In Bear possono passare solo SHORT.
2. **`_ema_cross_signal` scatta solo sulla barra esatta dell'incrocio** → quasi tutti i tick hanno `signal=None` (verificato live: spread +41 ma signal None).
3. **Weekend guard** spegne ~2,5 giorni/settimana (ogni tick: `[WEEKEND_(Dom)]`). *Discutibile per cripto 24/7 — candidato a rimozione (`weekend_guard: false`), ma è una modifica alla strategia live: da confermare con l'operatore.*
4. **ADX guard (min 20)**: il mercato osservato è piattissimo (range ~0,4%) → ADX basso → blocca gli short residui.
5. **Runtime**: solo ~4,5 giorni feriali effettivi dall'avvio.

Per uno SHORT servono CONTEMPORANEAMENTE: death-cross-sulla-barra + ADX≥20 + prezzo < SMA50d + fuori da weekend/calendar + regime non-Bull. Congiunzione mai soddisfatta nella finestra osservata. **Zero trade è il comportamento atteso del sistema così configurato.**

### Nota di design separata (non è la causa dei zero-trade)
Il segnale EMA cross gira su `recent_closes` = candele da **1 minuto** (`price.py:62` → `loop.py:612-616`), quindi è un cross **20min/50min**, diverso dall'edge "1h" che il commento del gate SMA50d (`loop.py:635`) dichiara validato su 18 mesi di dati. Vale la pena allineare timeframe del segnale e timeframe di validazione.

### Osservazione operativa (da confermare live)
Nel fetch locale `candles_1h` e `candles_1d` sono tornate **vuote** (solo 1m popolato). Se accade anche sul worker: il **gate SMA50d si auto-disattiva** (`sma50d=None`) e l'**ADX guard ripiega sulle candele 1m** (`loop.py:654` → `candles_1h or candles`). Da verificare con strumentazione sul worker.

## 3. Nuovo programma: Strategy Arena (deciso in questa sessione)

10 agenti, ognuno con una **famiglia di strategia diversa**, evolvono i propri parametri tramite un **LLM** e competono su backtest storico offline. Fitness multi-obiettivo (net profit + drawdown minimo + profit factor) con difesa anti-overfit.

- **Spec**: `docs/superpowers/specs/2026-05-31-strategy-arena-design.md` (approvata).
- **Decisioni**: 10 archetipi fissi (no eliminazione); substrato backtest offline; approccio "C" = interfaccia `Proposer` pluggable (`LLMProposer` + `RandomGAProposer` come baseline di controllo).
- **Fasi**: F1 strategie → F2 core arena → F3 LLM → F4 UI.
- **Piano F1 pronto**: `docs/superpowers/plans/2026-05-31-strategy-arena-F1-strategies.md` (7 nuove strategie + modulo indicatori condiviso, TDD, registry a 10).
- **Dipendenza**: il validation layer CPCV/DSR (`specs/2026-05-29-validation-layer-design.md`) non è ancora implementato; l'arena parte col walk-forward OOS esistente e aggancia DSR/PBO dopo.

## 4. Prossimi passi aperti

- [ ] `git init` del repo `worker/` (oggi NON è un repository git — gli step di commit del piano lo presuppongono).
- [ ] Eseguire la Fase 1 dell'arena (subagent-driven o inline).
- [ ] Scrivere i piani F2/F3/F4 (dopo F1).
- [ ] Decidere sul weekend_guard e sugli altri guard (idealmente: lasciar decidere all'arena quali guard danno edge).
- [ ] Verificare live se `candles_1h`/`candles_1d` arrivano vuote sul worker.
- [ ] Allineare timeframe del segnale EMA con quello di validazione.

## 5. Tooling installato in questa sessione

- **gstack** installato in `~/.claude/skills/gstack` (skill `/browse` per il browsing, ecc.). Aggiunta sezione `gstack` a `~/.claude/CLAUDE.md` (globale) e a `hermes-trading/CLAUDE.md` (progetto). `bun` installato via Homebrew come prerequisito.

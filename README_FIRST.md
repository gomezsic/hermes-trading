# HERMES TRADING — README FIRST
# Leggi questo prima di qualsiasi azione sul progetto.
# Aggiornato: 2026-05-26

---

## STATO ATTUALE: ONLINE E OPERATIVO

Il bot è deployato su Railway in paper mode e sta girando.
Non toccare HERMES_TRADING_MODE — solo l'operatore decide quando andare live.

---

## INFRASTRUTTURA

### Railway
- Project:     hermes-trading
- Project ID:  f605bdbc-e84b-43d0-834e-2ded4b22229a
- Environment: production (ID: 04b537da-5111-4452-8c90-2d8d107e2972)
- Service:     hermes-trading (ID: e7cf77ab-a8fd-4eec-ab66-0f43b3a579e4)
- Regione:     US West (California) — SOLO UNA. Il piano free non supporta multi-region.
  ATTENZIONE: se Railway aggiunge di nuovo EU West, i deploy falliscono.
  Fix: railway service scale eu-west=0 us-west=1
  Oppure via GraphQL: serviceInstanceUpdate con region="us-west1", numReplicas=1

### Comandi rapidi
  railway logs --tail 30           # log live
  railway ssh "cat /app/state/strategy.yaml"   # strategia nel container
  railway ssh "cat /app/state/trades.jsonl"    # trade chiusi
  railway deployment list          # lista deploy
  cd ~/hermes-trading/worker && railway up --detach   # deploy nuovo codice

---

## CODEBASE LOCALE: ~/hermes-trading/worker/

### Struttura
  hermes_trading/
    run.py          — entrypoint, chiama bootstrap + loop
    loop.py         — logica principale tick-by-tick (EMA cross + position management)
    reflect.py      — reflection cycle (--fallback o --hermes)
    score.py        — scoring trade vs goal.yaml
    bootstrap.py    — seed state/ da state-template/ al primo boot
    volume_profile.py — calcolo VP: POC, VAH, VAL, HVN, LVN  [MODULO NUOVO - da integrare nel loop]
    adapters/
      price.py      — fetch Kraken via ccxt, 200 candele 1m, ritorna closes + candles {h,l,v}
      onchain.py    — blockchain.info / mempool.space
      news.py       — Fear & Greed Index (alternative.me)
      macro.py      — FX rates (frankfurter.app)

  state/
    goal.yaml       — obiettivi locked (NON MODIFICARE)
    strategy.yaml   — strategia corrente v03
    trades.jsonl    — storico trade paper
    heartbeat.json  — stato worker
    history/        — versioni precedenti strategia

  state-template/   — seed per Railway volume al primo boot
  generate_report.py — genera markov_report.html (report interattivo)

---

## STRATEGIA CORRENTE: v03 — TREND FOLLOWER

### Entry
  Indicatore: EMA20 cross EMA50 (golden cross)
  Direzione:  long only

### Risk Management
  stop_loss_pct:           2.5%   — hard stop fisso dall'entry
  trailing_stop_pct:       2.5%   — trailing attivo dopo +2.5% di gain
  trailing_activate_pct:   2.5%   — soglia di attivazione trailing
  partial_exit_pct:        4.0%   — a +4% chiude il 50% della posizione
  trailing_stop_tight_pct: 1.5%   — trailing stretto sul 50% rimanente

### Logica completa
  1. Hard stop -2.5%: chiude 100% immediatamente (priorità assoluta)
  2. Gain >= +2.5%: attiva trailing stop a max*(1-2.5%), sale ma non scende
  3. Gain >= +4%: chiude 50%, stringe trailing a 1.5% sul restante
  4. Trailing stop hit: chiude tutto il restante
  Il 50% residuo corre finché il mercato non chiude il trailing.

### Price adapter
  Exchange: Kraken (ccxt) — no geo-block
  Candles:  200 candele 1m per tick — ritorna recent_closes + candles {h,l,v}
  Warmup:   52 candele necessarie per EMA20 + EMA50

---

## GOAL (LOCKED)
  asset:             BTC/USDT
  target_return_30d: 0.05 (+5%)
  max_drawdown:      0.08 (8%)
  min_sharpe:        1.2
  reflection_every:  5 trade chiusi
  one_variable_only: true

---

## MARKOV SKILL — INSTALLATA

### Posizione
  ~/.claude/skills/markov-hedge-fund-method/

### Cosa fa
  - Fetcha daily OHLCV via yfinance (free)
  - Labella ogni giorno: Bear/Sideways/Bull (rolling log-return 20d, threshold 8% per crypto)
  - Costruisce matrice di transizione 3x3 via MLE
  - Forecast n-step via Chapman-Kolmogorov
  - Walk-forward backtest con Sharpe, MaxDD, benchmark Buy&Hold
  - Position sizing magnitude-proportional: clip(P(Bull)-P(Bear), -1, +1)
  - HMM layer via hmmlearn (installato, v0.3.3)

### Risultati su BTC-USD 2y (threshold 0.08)
  Bear: 16.6% | Sideways: 62.1% | Bull: 21.4%
  Persistenza: Bear 80%, Sideways 89%, Bull 83%  — i trend durano
  Sharpe strategia: -0.001 | B&H: -0.202  — Markov batte nettamente il B&H
  Max DD strategia: -15.9% | B&H: -49.7%

### Come runnare
  cd ~/.claude/skills/markov-hedge-fund-method
  uv run python -m markov_hedge_fund_method.run --ticker BTC-USD --years 2 --threshold 0.08

---

## REPORT DINAMICO

### File
  ~/hermes-trading/worker/generate_report.py

### Come generare
  cd ~/hermes-trading/worker && uv run python generate_report.py --ticker BTC-USD --years 2 --threshold 0.08
  open ~/hermes-trading/worker/markov_report.html

### Contenuto del report
  - Grafico prezzo daily colorato per regime (rosso/giallo/verde)
  - Volume daily colorato per regime
  - Equity curve walk-forward vs Buy&Hold
  - Matrice di transizione con heatmap
  - Probabilità regime prossimo giorno
  - Mix stazionario (lungo termine)
  - Donut distribuzione storica regimi
  - KPI: segnale corrente, Sharpe, MaxDD

---

## TODO / PROSSIMI PASSI

### Priorità alta
  3. Cron Hermes per reflection automatica ogni 5 trade chiusi

### Priorità media
  4. Alert trade aperto/chiuso (Telegram o email)

### Completati
  ✓ volume_profile.py integrato nel loop (entry filter POC, partial exit HVN)
  ✓ Markov daily regime integrato nel loop (blocca entry in Bear, refresh 60min)
  ✓ Cron reflection: job_id=2cf1e835b079, ogni 30min, trigger ogni 5 trade chiusi
  ✓ Dashboard operativo: generate_dashboard.py → dashboard.html (auto-refresh 60s)
     Per aggiornare: cd ~/hermes-trading/worker && uv run python generate_dashboard.py && open dashboard.html

---

## PITFALLS NOTI

- Multi-region Railway: deploy fallisce se il servizio è su 2 regioni.
  Fix: railway service scale eu-west=0 us-west=1 + GraphQL serviceInstanceUpdate
- Il counter warmup era bloccato a 31/52 perché il price adapter portava 30
  candele storiche e il loop le sovrascriveva. Fix: portare 200 candele e
  usare "espandi solo se len(recent) > len(closes)".
- railway ssh per leggere/scrivere nel container. railway run esegue in locale.
- PID 1 nel container = non killabile via SSH. Per riavviare: railway up --detach.
- uv sync invece di uv pip install per coerenza con pyproject.toml.
- BTC threshold Markov: usare 0.08, non 0.05 (troppo tight per crypto).

---

## REGOLE OPERATIVE

- HERMES_TRADING_MODE=paper sempre. Solo l'operatore flippa a live.
- Un solo variabile per ciclo di riflessione (one_variable_only: true).
- Ipotesi extra → ~/hermes-trading/worker/state/pending.jsonl, non applicare subito.
- Ogni modifica a strategy.yaml: bump version (zero-padded), salva history/.
- Writes solo dentro ~/hermes-trading/worker/ (locale) e /app/ (Railway).

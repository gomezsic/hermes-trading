# Hermes Trading — stato del progetto

Sistema self-improving paper-trading completo, deployato e operativo dal 2026-05-24. 8 fasi eseguite seguendo un prompt strutturato (`~/Downloads/hermes-trading-prompt-v2.md`) + Fase 8 (UI) aggiunta su richiesta.

## Componenti & topologia

```
┌─────────────────────┐         ┌─────────────────────────┐
│  Railway worker     │         │  Hermes (locale)        │
│  US West region     │◄────────│  Terminal.app           │
│  Python + uv        │ railway │  Claude Sonnet 4.6      │
│  ccxt → Kraken      │ ssh/up  │  via Anthropic API      │
│  /app/state vol     │         │  Loop reflection 30min  │
└─────────┬───────────┘         └─────────────────────────┘
          │
          │ railway ssh (20s polling)
          ▼
┌─────────────────────┐
│  Next.js dashboard  │
│  localhost:3000     │
│  SSE real-time      │
└─────────────────────┘
```

## File chiave

| Path | Cosa |
|---|---|
| `~/hermes-trading/worker/` | Worker Python (uv project) — **questa cartella** |
| `~/hermes-trading/worker/state/` | State files locali (mirror parziale, vero state su Railway in `/app/state`) |
| `~/hermes-trading/worker/.railway-token` | Railway API token, gitignored, perms 600 |
| `~/hermes-trading/config/` | Dump pull-da-Railway: `strategy.yaml`, `hypotheses.jsonl`, `trades.jsonl`, `hermes-briefing.txt` |
| `~/hermes-trading/ui/` | Dashboard Next.js 16.2.6 (TS, Tailwind 4, App Router) |
| `~/.hermes/` | Hermes CLI install (v0.14.0) |
| `~/.hermes/.env` | Contiene `ANTHROPIC_API_KEY` e `RAILWAY_API_TOKEN` |
| `~/Desktop/Hermes Dashboard.command` | Launcher doppio-clickabile per aprire la UI |

## Stack & versioni

- Python 3.11 (worker), uv 0.11.16
- Node.js 24.15.0, Next.js 16.2.6 (Turbopack), Tailwind 4
- Railway CLI 4.61.1 (auth via `RAILWAY_API_TOKEN` file-based)
- Hermes Agent 0.14.0, modello `anthropic/claude-sonnet-4-6` (95-98% cache hit)
- ccxt → **Kraken** (NON Binance — Binance geo-blocca Railway us-west)

## Decisioni architetturali importanti

- **Kraken invece di Binance.** Binance.com risponde HTTP 451 da IP Railway us-west. Switch a Kraken evita di dover migrare region (volume Railway region-pinned, migrazione via CLI rotta — `sfo` non scalabile come region, `eu-west=1 us-west=0` non sposta finché c'è volume in us-west).
- **Frankfurter:** URL aggiornato a `https://api.frankfurter.dev/v1/latest` (il `.app` fa 301 e httpx non segue redirect default).
- **Provider LLM:** Anthropic diretto, non Nous Portal. Nous Free tier dà accesso solo a `openrouter/owl-alpha` e `stepfun/step-3.5-flash` (HTTP 503 frequenti per capacity). Sonnet 4.6 costa ~$0.02 per reflection (~$5-30/mese realistico).
- **Dashboard real-time via SSE, non WebSocket.** Next.js App Router non supporta WS nativi sul dev server senza custom server. SSE è one-way server→client (esattamente quello che serve) ed è una `ReadableStream` in route.ts.
- **Polling via `railway ssh` combinato.** Ogni `railway ssh` ha overhead ~3-5s. La route `/api/stream` fa UNA ssh call che concatena `cat heartbeat.json && echo SEP && cat goal.yaml && echo SEP && ...` con un separatore noto, poi splitta lato server. Riduce 5 ssh calls a 1 per ciclo polling.

## Worker — come funziona

`hermes_trading/loop.py` esegue ogni 60s:
1. Pull adapters in parallelo: `price.py` (ccxt Kraken — ticker + ultime 30 candele 1m), `onchain.py` (mempool.space, BTC-only), `news.py` (alternative.me fear&greed), `macro.py` (frankfurter FX).
2. Calcola RSI(14) su `recent_closes` + tick corrente.
3. Se nessuna posizione aperta e `RSI < entry.threshold` (direction=long) → apre posizione paper, salva in `state/position.json`.
4. Se posizione aperta → check stop_loss (`entry * (1 - stop_loss_pct/100)`) e take_profit (1:1 R/R hardcoded). Se hit, chiude e appende a `state/trades.jsonl`.
5. Heartbeat in `state/heartbeat.json` ad ogni tick.
6. Retry 3x exp backoff (1s, 2s, 4s) per adapter, circuit-break dopo 5 fallimenti consecutivi.

**Strategy iniziale (v01):** RSI<30 long, stop_loss 2%, take_profit 2% (1:1), position_size_r 0.5.

**Reflection deterministica fallback:** se return < target → loosen `entry.threshold +2`, se drawdown > max → tighten `stop_loss_pct -0.2`. Sempre UNA variabile per ciclo, bumpa `version`, archivia in `state/history/v{NN}.yaml`.

## Hermes loop

Briefing in `~/hermes-trading/config/hermes-briefing.txt`. Hermes deve:
1. `railway logs --tail 200` ogni 30 min
2. Quando 5 nuovi trade chiusi: `railway ssh "cat /app/state/trades.jsonl"` + strategy
3. Genera 1-3 hypothesis JSON (schema fisso: variable/current_value/proposed_value/rationale/predicted_score_delta/confidence)
4. Picka per `confidence × |predicted_score_delta|` max, applica via `railway ssh` overwrite di strategy.yaml + `railway up --detach`

Hard constraint nel briefing: MAI cambiare più di una variabile, MAI flip `HERMES_TRADING_MODE` a live.

## Dashboard Next.js

`~/hermes-trading/ui/src/`:
- `lib/railway.ts` — helper `fetchStateSnapshot()` (single multi-cat ssh) + `spawnRailwayLogs()` (long-lived child process)
- `lib/types.ts` — TypeScript types per heartbeat/goal/strategy/trade/hypothesis/log
- `lib/useEventStream.ts` — React hook per `EventSource` con handlers per-event
- `app/api/state/route.ts` — snapshot one-shot
- `app/api/stream/route.ts` — SSE state, polling 20s
- `app/api/logs/route.ts` — SSE worker logs (`railway logs` spawn)
- `app/api/hermes/route.ts` — SSE tail `~/.hermes/logs/agent.log`
- `components/Dashboard.tsx`, `StatusBar.tsx`, `StrategyCard.tsx`, `TradesTable.tsx`, `HypothesesList.tsx`, `LogPanel.tsx`

## Come avviare la dashboard

- **Doppio-click su `~/Desktop/Hermes Dashboard.command`** (più semplice)
- oppure: `cd ~/hermes-trading/ui && npm run dev` poi apri http://localhost:3000

## Come avviare/riavviare Hermes

```bash
hermes
# poi incolla il briefing:
pbcopy < ~/hermes-trading/config/hermes-briefing.txt
# Cmd+V dentro Hermes, Invio
```

## Cosa MANCA (TODO prioritari)

1. **Grafico BTC/USD con segnale d'ingresso.** Candele 1m/5m + linea RSI + marker visivi dove il bot ha aperto/chiuso. Probabile: `lightweight-charts` di TradingView (open-source, leggero, dark theme nativo). Source dati: il worker già pulla `recent_closes`; potrebbe servire una serie più lunga (es. ultime 200 candele 1m da Kraken via API route Next.js, NO `railway ssh` perché Kraken è pubblica).
2. **Conteggio economico operazione.** Per ogni trade aperto/chiuso mostrare:
   - Capitale teorico allocato (R-multiple × equity base, oggi 0.5R hardcoded ma equity = ?)
   - Guadagno potenziale (entry → take_profit price, in USD assoluti)
   - Perdita massima (entry → stop_loss price, in USD assoluti)
   - PnL effettivo realizzato (per trade chiuso) e ATM (per trade aperto in live)
   - Cumulativo equity curve mese su mese
   Manca: NON c'è il concetto di "equity base" nel worker. Va aggiunto in `goal.yaml` (es. `starting_equity_usd: 10000`) e propagato a `loop.py` per calcolare valori assoluti.
3. **Equity curve grafica** nella dashboard (LineChart su `trades.jsonl` cumulativo).
4. **Win rate / avg win / avg loss / max drawdown** già calcolabili da `trades.jsonl` ma non mostrati in UI.
5. **Pulsante "force reflection"** nella UI che chiama un endpoint che esegue `railway ssh uv run python -m hermes_trading.reflect --fallback` (o `--hermes`). Comodo per debug senza CLI.
6. **History viewer** delle versioni strategy (`state/history/v*.yaml`) con diff visivo tra versioni.
7. **Dark/light toggle** (ora hardcoded dark via globals.css).
8. **Hermes auto-restart watcher.** Se chiudi il Terminale dove gira Hermes, il loop si ferma. Considerare wrapping Hermes in launchd plist o pm2 per auto-restart.

## Caveat noti

- **Hermes nel Terminale dell'utente, non in background daemon.** Se chiudi il terminale, il loop di reflection muore. Worker continua a tradare senza nessuno che ottimizza la strategia.
- **Free tier Anthropic:** la key è personale, non c'è failover.
- **Paper mode hard-coded:** `HERMES_TRADING_MODE=paper` in `.env`. Per andare live serve flip + `HERMES_TRADING_I_ACCEPT_RISK=true` + import live adapter (NON ancora scritto).
- **No persistenza di equity/capitale:** ogni trade riporta solo `pnl_pct`. Non c'è tracking di capitale assoluto.
- **`railway service scale` non riconosce `sfo`** come region (è display-only). Le regions valide CLI sono: eu-west, us-west, us-east, southeast-asia.

## Comandi utili

```bash
# Log worker live dal terminale
cd ~/hermes-trading/worker && export RAILWAY_API_TOKEN=$(cat .railway-token) && railway logs

# Pull state files freschi da Railway
cd ~/hermes-trading/worker && export RAILWAY_API_TOKEN=$(cat .railway-token) && \
  railway ssh cat /app/state/strategy.yaml > ~/hermes-trading/config/strategy.yaml 2>/dev/null && \
  railway ssh cat /app/state/trades.jsonl > ~/hermes-trading/config/trades.jsonl 2>/dev/null && \
  railway ssh cat /app/state/hypotheses.jsonl > ~/hermes-trading/config/hypotheses.jsonl 2>/dev/null

# Forzare reflection fallback (deterministica)
cd ~/hermes-trading/worker && export RAILWAY_API_TOKEN=$(cat .railway-token) && \
  railway ssh uv run python -m hermes_trading.reflect --fallback

# Killare il dev server della UI
lsof -nP -iTCP:3000 -sTCP:LISTEN | awk 'NR>1{print $2}' | xargs kill
```

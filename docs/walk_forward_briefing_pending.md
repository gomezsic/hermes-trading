# WALK-FORWARD VALIDATION — BRIEFING (in attesa)

**Stato**: IN ATTESA — prerequisiti non soddisfatti  
**Data ricezione briefing**: 2026-05-27  
**Data prevista implementazione**: 2026-07 (dopo 30-60 giorni di paper trading)

## Prerequisiti da soddisfare prima di iniziare

| Prerequisito | Stato | Note |
|---|---|---|
| Kelly+vol sizing operativo | 🔄 In corso | Deploy entro oggi |
| sizing_log.jsonl popolato | ❌ Non esiste | Serve dopo deploy |
| trades.jsonl con param_hash | ❌ Non esiste | Da aggiungere al _build_trade_record |
| Minimo 100 trade IS | ❌ 3 trade oggi | Serve ~30-60 giorni |
| Backtester deterministico | ❌ Non esiste | Da costruire da zero |
| Storico >= 18 mesi | ❌ 0 giorni live | Dipende da trade accumulation |
| Credenziali Git in Railway | ❌ Non configurato | Serve per auto-commit su PROMOTE |
| Canale Telegram audit | ❌ Non configurato | Per alert PROMOTE/REJECT |

## Checklist pre-implementazione

Prima di iniziare il walk-forward verificare:

- [ ] Kelly+vol ha girato per almeno 30 giorni senza modifiche al codice
- [ ] sizing_log.jsonl ha almeno 50 entry (trade aperti con sizing)
- [ ] trades.jsonl ha param_hash su tutti i trade recenti
- [ ] Storico trades >= 150 record (finestra rolling Kelly)
- [ ] Nessuna modifica a strategy.yaml nelle ultime 4 settimane (stabilita')
- [ ] Backtester costruito e testato su dati storici Kraken

## Regola operativa

> "Il walk-forward ha bisogno dei sizing_log.jsonl puliti per funzionare 
> correttamente. Se li fai in parallelo, il walk-forward sta tunando 
> contro un sizing che sta a sua volta cambiando — caos."

## Risposta alle 7 domande del briefing (stato al 2026-05-27)

1. **Storico Kraken**: 0 giorni di paper trading pulito. Minimo richiesto: 18 mesi.
2. **param_hash in trades.jsonl**: No. Va aggiunto a _build_trade_record in loop.py.
3. **Backtester deterministico**: Non esiste. Da costruire interamente.
4. **Impatto news guard su trade volume**: Sconosciuto. Zero cicli completati.
5. **Compute per backtester**: Railway gratuito. 81 combo x 8 cicli = ~5h. Da valutare upgrade.
6. **Alert routing**: Un solo chat_id configurato. Canale audit da creare.
7. **Auto-commit Git da Railway**: Non configurato. Serve deploy key + setup Docker.

## Note architetturali da non dimenticare

- Kelly fraction frozen a 0.25 (significant) e 0.10 (weak/prior) — decisione teorica, non ottimizzabile
- Max 2 parametri per ciclo — regola anti-overfitting non negoziabile  
- Holdout permanente (10% storico) — mai contaminare con IS o OOS
- LLM = solo report ex-post, mai decisioni ex-ante
- Deflated Sharpe obbligatorio quando si testano N combinazioni
- Cool-down 30 giorni dopo ogni PROMOTE

## Prossimo passo concreto da fare quando sara' il momento

1. Aggiungere `param_hash` e `params_snapshot` a `_build_trade_record` in loop.py
2. Costruire `backtester.py` deterministico con fee 0.26% + slippage 5bp
3. Implementare `walk_forward.py` seguendo il briefing completo
4. Rimuovere il cron reflection-ogni-5-trade (job_id: 2cf1e835b079)
5. Archiviare le reflection passate in `state/legacy_reflections.jsonl`

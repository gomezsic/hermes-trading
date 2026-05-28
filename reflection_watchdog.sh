#!/usr/bin/env bash
# reflection_watchdog.sh
# Controlla se ci sono abbastanza trade chiusi per una reflection.
# Output (stdout):
#   REFLECT <n_new_trades>   → trigger reflection
#   WAIT <n_new_trades>/<threshold>  → non ancora
#   ERROR <msg>              → qualcosa è andato storto
# Exit 0 sempre — errori non devono silenziare il cron.

set -euo pipefail

STATE_DIR="$HOME/hermes-trading/worker/state"
TRIGGER_FILE="$STATE_DIR/.last_reflection_trades"
GOAL_FILE="$STATE_DIR/goal.yaml"

# Leggi la soglia da goal.yaml
THRESHOLD=$(grep "reflection_every" "$GOAL_FILE" 2>/dev/null | awk '{print $2}' || echo "5")
THRESHOLD="${THRESHOLD:-5}"

# Leggi il totale trade correnti dal Railway via SSH
TRADES_TOTAL=$(cd "$HOME/hermes-trading/worker" && railway ssh "wc -l < /app/state/trades.jsonl 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]' || echo "0")
TRADES_TOTAL="${TRADES_TOTAL:-0}"

# Leggi l'ultimo conteggio al momento della reflection
if [[ -f "$TRIGGER_FILE" ]]; then
    LAST_REFLECTION=$(cat "$TRIGGER_FILE")
else
    LAST_REFLECTION=0
fi

NEW_TRADES=$(( TRADES_TOTAL - LAST_REFLECTION ))

if (( NEW_TRADES < 0 )); then
    # Conta resettato (nuovo container), aggiorna baseline
    echo "$TRADES_TOTAL" > "$TRIGGER_FILE"
    NEW_TRADES=0
fi

if (( NEW_TRADES >= THRESHOLD )); then
    echo "REFLECT $NEW_TRADES"
else
    echo "WAIT $NEW_TRADES/$THRESHOLD"
fi

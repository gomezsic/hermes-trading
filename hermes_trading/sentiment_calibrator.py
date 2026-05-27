"""
sentiment_calibrator.py — Auto-calibrazione del moltiplicatore sentiment.

Il bot impara dai propri trade quale impatto hanno le notizie sul risultato.
NON usa ML esterno. E' un sistema statistico online con EMA decay.

FILOSOFIA:
  I moltiplicatori iniziali sono ipotesi di bootstrap.
  Dopo ogni trade chiuso, aggiorna la stima EMA per quel bucket di sentiment.
  Dopo MIN_SAMPLES osservazioni per bucket, il moltiplicatore diventa
  data-driven invece che hardcoded.

STRUTTURA DI APPRENDIMENTO:

  Per ogni bucket (bearish_shock, bearish, neutral, bullish, bullish_surge):
    - Mantieni EMA del pnl_pct dei trade aperti con quel sentiment
    - Confronta con il pnl medio globale (reference)
    - Moltiplicatore = 1.0 + (ema_pnl - reference_pnl) / |reference_pnl|
    - Clamp in [MULT_MIN, MULT_MAX]
    - Non aggiornare se n < MIN_SAMPLES (usa default bootstrap)

  EMA update ad ogni trade:
    ema_new = alpha * pnl_new + (1 - alpha) * ema_old
    alpha = 0.15  (memoria ~6 trade recenti hanno peso dominante)

  Decay verso reference se bucket inattivo > DECAY_TRADES trade:
    ema = ema * (1 - decay_rate) + reference_pnl * decay_rate
    → il sistema "dimentica" configurazioni di mercato che non vede piu'

File di stato: state/sentiment_calibration.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

BUCKETS = ["bearish_shock", "bearish", "neutral", "bullish", "bullish_surge"]

# Moltiplicatori di bootstrap (usati finche' non ci sono abbastanza dati)
BOOTSTRAP_MULTIPLIERS: dict[str, float] = {
    "bearish_shock": 0.50,
    "bearish":       0.75,
    "neutral":       1.00,
    "bullish":       1.10,
    "bullish_surge": 1.20,
}

# Parametri di apprendimento
ALPHA          = 0.15    # EMA decay: 0.15 = ~6 trade di "memoria effettiva"
MIN_SAMPLES    = 10      # sotto questa soglia usa bootstrap
MULT_MIN       = 0.30    # mai moltiplicare sotto il 30%
MULT_MAX       = 1.50    # mai moltiplicare sopra il 150%
DECAY_RATE     = 0.02    # convergenza verso reference se bucket inattivo
DECAY_AFTER    = 20      # trade globali di inattivita' prima di applicare decay
CALIBRATE_EVERY= 5       # ricalibra moltiplicatori ogni N trade chiusi


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    return {
        "buckets": {
            b: {
                "n":           0,
                "ema_pnl":     0.0,
                "multiplier":  BOOTSTRAP_MULTIPLIERS[b],
                "last_trade_n": 0,    # indice trade globale dell'ultimo aggiornamento
                "source":      "bootstrap",
            }
            for b in BUCKETS
        },
        "reference_pnl":      0.0,    # EMA pnl globale (tutti i bucket)
        "reference_n":        0,
        "total_trades_seen":  0,
        "last_calibrated":    None,
        "version":            1,
    }


def load(state_dir: Path) -> dict:
    path = state_dir / "sentiment_calibration.json"
    if not path.exists():
        return _default_state()
    try:
        return json.loads(path.read_text())
    except Exception:
        return _default_state()


def save(state_dir: Path, cal: dict) -> None:
    (state_dir / "sentiment_calibration.json").write_text(
        json.dumps(cal, indent=2)
    )


# ---------------------------------------------------------------------------
# Core: aggiorna la calibrazione con un nuovo trade chiuso
# ---------------------------------------------------------------------------

def update(state_dir: Path, trade: dict) -> dict:
    """
    Chiamato ogni volta che un trade viene chiuso.
    Aggiorna la EMA del bucket corrispondente al sentiment all'apertura.
    Ricalibra i moltiplicatori ogni CALIBRATE_EVERY trade.
    Ritorna la calibrazione aggiornata.
    """
    cal    = load(state_dir)
    bucket = trade.get("news_signal_at_entry", "neutral")
    pnl    = float(trade.get("pnl_pct", 0.0))

    if bucket not in BUCKETS:
        bucket = "neutral"

    cal["total_trades_seen"] += 1
    n_total = cal["total_trades_seen"]

    # --- Aggiorna EMA reference (tutti i trade) ---
    ref_alpha = ALPHA
    if cal["reference_n"] == 0:
        cal["reference_pnl"] = pnl
    else:
        cal["reference_pnl"] = (
            ref_alpha * pnl + (1 - ref_alpha) * cal["reference_pnl"]
        )
    cal["reference_n"] += 1

    # --- Aggiorna EMA del bucket ---
    b = cal["buckets"][bucket]
    if b["n"] == 0:
        b["ema_pnl"] = pnl
    else:
        b["ema_pnl"] = ALPHA * pnl + (1 - ALPHA) * b["ema_pnl"]
    b["n"] += 1
    b["last_trade_n"] = n_total

    # --- Decay verso reference per bucket inattivi ---
    for bname, bdata in cal["buckets"].items():
        if bname == bucket:
            continue
        trades_since = n_total - bdata.get("last_trade_n", 0)
        if trades_since >= DECAY_AFTER and bdata["n"] > 0:
            bdata["ema_pnl"] = (
                (1 - DECAY_RATE) * bdata["ema_pnl"] +
                DECAY_RATE * cal["reference_pnl"]
            )

    # --- Ricalibra moltiplicatori ogni CALIBRATE_EVERY trade ---
    if n_total % CALIBRATE_EVERY == 0:
        _recalibrate(cal)
        cal["last_calibrated"] = datetime.now(timezone.utc).isoformat()

    save(state_dir, cal)
    return cal


def _recalibrate(cal: dict) -> None:
    """
    Ricalcola i moltiplicatori da ogni bucket EMA.
    Flag di confidenza:
      bootstrap         — n < MIN_SAMPLES, usa valori di partenza arbitrari
      learned_low_conf  — n in [MIN_SAMPLES, 30), statisticamente debole
      learned_credible  — n >= 30, statisticamente significativo
    """
    ref   = cal["reference_pnl"]
    eps   = 1e-5

    for bname, bdata in cal["buckets"].items():
        n = bdata["n"]
        if n < MIN_SAMPLES:
            bdata["multiplier"] = BOOTSTRAP_MULTIPLIERS[bname]
            bdata["source"]     = "bootstrap"
            bdata["conf_flag"]  = "bootstrap"
            continue

        denom = max(abs(ref), eps)
        raw   = 1.0 + (bdata["ema_pnl"] - ref) / denom
        mult  = max(MULT_MIN, min(MULT_MAX, round(raw, 3)))

        bdata["multiplier"] = mult
        bdata["source"]     = "learned"
        # Punto 4 del feedback: distingui credibile da bassa confidenza
        if n >= 30:
            bdata["conf_flag"] = "learned_credible"    # statistically meaningful
        else:
            bdata["conf_flag"] = "learned_low_conf"    # direction ok, magnitude uncertain


# ---------------------------------------------------------------------------
# API pubblica: ottieni il moltiplicatore per il sentiment corrente
# ---------------------------------------------------------------------------

def get_multiplier(state_dir: Path, signal: str) -> tuple[float, str]:
    """
    Ritorna (multiplier, source) per il signal dato.
    source = "learned_credible" | "learned_low_conf" | "bootstrap"
    Il chiamante puo' usare source per decidere se fidarsi del moltiplicatore.
    """
    cal    = load(state_dir)
    bucket = signal if signal in BUCKETS else "neutral"
    bdata  = cal["buckets"].get(bucket, {})
    mult   = bdata.get("multiplier", BOOTSTRAP_MULTIPLIERS.get(bucket, 1.0))
    source = bdata.get("conf_flag", bdata.get("source", "bootstrap"))
    return mult, source


def calibration_report(state_dir: Path) -> str:
    """Stringa compatta per il tick log o per Telegram."""
    cal  = load(state_dir)
    tot  = cal.get("total_trades_seen", 0)
    ref  = cal.get("reference_pnl", 0.0)
    lines = [f"SentimentCalibration  trades={tot}  ref_pnl={ref*100:+.3f}%"]
    for bname in BUCKETS:
        b = cal["buckets"][bname]
        lines.append(
            f"  {bname:<15} n={b['n']:3d}  "
            f"ema={b['ema_pnl']*100:+.4f}%  "
            f"mult={b['multiplier']:.3f}  [{b['source']}]"
        )
    return "\n".join(lines)

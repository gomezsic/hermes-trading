"""
walk_forward.py — Walk-forward validation per hermes-trading.

Filosofia: i parametri ottimizzati su dati storici (In-Sample, IS) vengono
validati su dati fuori campione (Out-of-Sample, OOS) mai visti durante
l'ottimizzazione. Se i parametri reggono su OOS → PROMOTE, altrimenti REJECT.

DISABILITATO DI DEFAULT: imposta walk_forward_enabled: true in config per attivare.

Flusso di un ciclo:
  1. Verifica flag enabled + cooldown post-PROMOTE
  2. Separa holdout permanente (ultimo 10% della storia)
  3. Genera finestre IS/OOS con step rolling
  4. Grid search sulla finestra IS più recente (max 2 parametri)
  5. Guardrail: Deflated Sharpe, varianza subwindow, distance penalty
  6. Valida i migliori parametri su TUTTE le finestre OOS
  7. Decisione PROMOTE o REJECT in base ai criteri aggregati
  8. Ogni 6 cicli: verifica aggiuntiva sul holdout permanente
  9. Salva artefatti in state/walkforward/{cycle_id}/

Dipendenze: stdlib, yaml, hermes_trading.backtester, hermes_trading.score
Tutti i commenti in italiano.
"""
from __future__ import annotations

import csv
import itertools
import json
import logging
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import yaml

from hermes_trading import score as score_mod
from hermes_trading.backtester import run_backtest  # type: ignore[import]

log = logging.getLogger(__name__)


# ─── Costanti ────────────────────────────────────────────────────────────────

# Approssimazione: 30 giorni per mese (per le candele giornaliere)
_DAYS_PER_MONTH: int = 30

# Spazio di ricerca parametri in formato decimale (0.05 = 5%).
# strategy.yaml usa la scala percentuale (5.0 = 5%): vedi _YAML_SCALE.
_PARAM_RANGES: dict[str, tuple[float, float, float]] = {
    "stop_loss_pct":           (0.030, 0.070, 0.005),  # 3 % – 7 %
    "partial_exit_pct":        (0.080, 0.200, 0.010),  # 8 % – 20 %
    "trailing_activate_pct":   (0.040, 0.100, 0.005),  # 4 % – 10 %
    "trailing_stop_pct":       (0.025, 0.060, 0.005),  # 2.5 % – 6 %
    "trailing_stop_tight_pct": (0.015, 0.040, 0.005),  # 1.5 % – 4 %
}

# I parametri in strategy.yaml sono espressi in percentuale (5.0 = 5%),
# internamente al walk-forward li trattiamo come decimali (0.05 = 5%).
_YAML_SCALE: float = 100.0

# Goal di default per score.full_report (allineato con strategy.yaml)
_DEFAULT_GOAL: dict[str, Any] = {
    "max_drawdown":           0.15,
    "max_cvar_5pct":          0.03,
    "max_consecutive_losses": 5,
    "target_return_30d":      0.05,
    "min_sharpe":             1.2,
    "failure_below":          -1.0,
}

# Credenziali Telegram (opzionale — nessun crash se assenti)
_TG_TOKEN: str   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")


# ─── Utility generale ─────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Timestamp ISO 8601 UTC del momento corrente."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _frange(lo: float, hi: float, step: float) -> list[float]:
    """
    Lista di float da lo a hi (estremi inclusi) con passo step.
    Usa arrotondamento a 6 decimali per evitare derive floating-point.
    """
    n = round((hi - lo) / step)
    result = [round(lo + i * step, 6) for i in range(n + 1)]
    return result


def _wf_cfg(config: dict, key: str, default: Any) -> Any:
    """
    Legge un parametro di configurazione walk-forward.
    Cerca prima in config["walk_forward"][key], poi in config[key], poi usa default.
    """
    wf_sub = config.get("walk_forward", {})
    if key in wf_sub:
        return wf_sub[key]
    return config.get(key, default)


def _notify_sync(text: str) -> None:
    """
    Invia una notifica Telegram in modo sincrono (non crasha mai).
    Usato per PROMOTE/REJECT al termine del ciclo.
    """
    if not (_TG_TOKEN and _TG_CHAT_ID):
        return
    try:
        url  = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": _TG_CHAT_ID, "text": text}).encode()
        req  = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as exc:  # noqa: BLE001
        log.warning("[walk_forward] Notifica Telegram fallita: %s", exc)


# ─── Stato persistente del walk-forward ───────────────────────────────────────

def _wf_dir(state_dir: Path) -> Path:
    """Directory radice degli artefatti walk-forward."""
    d = state_dir / "walkforward"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_meta(wf_dir: Path) -> dict:
    """Carica il meta-stato del walk-forward (conta cicli, ultimo PROMOTE, ecc.)."""
    meta_file = wf_dir / "meta.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text())
    return {
        "cycle_count":               0,
        "last_promote_at":           None,
        "last_holdout_check_cycle":  0,
        "promoted_params_history":   [],
    }


def _save_meta(wf_dir: Path, meta: dict) -> None:
    """Salva il meta-stato aggiornato."""
    (wf_dir / "meta.json").write_text(json.dumps(meta, indent=2))


# ─── Gestione parametri (conversione scala yaml ↔ decimale) ──────────────────

def _strategy_to_decimal(strategy: dict) -> dict[str, float]:
    """
    Estrae i parametri tunabili dalla strategy e li converte in decimali.
    Ex: stop_loss_pct: 5.0 → 0.05
    """
    result: dict[str, float] = {}
    for name in _PARAM_RANGES:
        if name in strategy:
            result[name] = strategy[name] / _YAML_SCALE
    return result


def _decimal_to_yaml_values(params_decimal: dict[str, float]) -> dict[str, float]:
    """
    Converte i parametri decimali in valori pronti per strategy.yaml.
    Ex: 0.05 → 5.0
    """
    return {k: round(v * _YAML_SCALE, 4) for k, v in params_decimal.items()}


def _build_backtest_params(strategy: dict, overrides_decimal: dict[str, float]) -> dict:
    """
    Costruisce il dict parametri per il backtester.
    Parte dai parametri correnti della strategy (convertiti in decimale)
    e applica gli override della grid search (già in decimale).

    Il backtester riceve tutti i parametri in formato decimale (0.05 = 5%).
    I parametri non tunabili (ema_fast, sizing, ecc.) vengono passati as-is.
    """
    params: dict[str, Any] = {}
    for k, v in strategy.items():
        if k in _PARAM_RANGES:
            params[k] = v / _YAML_SCALE  # converti percentuale → decimale
        else:
            params[k] = v
    params.update(overrides_decimal)
    return params


# ─── Generazione finestre IS / OOS ────────────────────────────────────────────

def _split_holdout(
    candles: list[dict], holdout_pct: float = 0.10
) -> tuple[list[dict], list[dict]]:
    """
    Separa il holdout permanente: l'ultimo holdout_pct della storia.
    Il holdout NON viene mai incluso in IS né in OOS.

    Ritorna (candles_usabili, holdout_candles).
    """
    n_holdout = max(1, int(len(candles) * holdout_pct))
    return candles[:-n_holdout], candles[-n_holdout:]


def _generate_windows(
    candles: list[dict],
    is_days:   int,
    oos_days:  int,
    step_days: int,
) -> list[tuple[list[dict], list[dict]]]:
    """
    Genera tutte le coppie (IS, OOS) valide con finestre rolling.

    Ogni finestra:
      IS  = candles[start : start + is_days]
      OOS = candles[start + is_days : start + is_days + oos_days]

    Lo step fa avanzare la finestra di step_days ad ogni iterazione.
    Ritorna lista di tuple (is_candles, oos_candles) dalla più vecchia alla più recente.
    """
    windows: list[tuple[list[dict], list[dict]]] = []
    total = len(candles)
    start = 0
    while True:
        end_is  = start + is_days
        end_oos = end_is + oos_days
        if end_oos > total:
            break
        windows.append((candles[start:end_is], candles[end_is:end_oos]))
        start += step_days
    return windows


# ─── Grid search ─────────────────────────────────────────────────────────────

def _select_tune_params(config: dict, max_params: int = 2) -> list[str]:
    """
    Determina quali parametri ottimizzare in questo ciclo.

    Se config["tune_params"] è specificato (lista non vuota), usa quelli.
    Altrimenti usa i default: stop_loss_pct + trailing_stop_pct.

    Lancia ValueError se i nomi non sono validi o superano max_params.
    """
    configured: list[str] = _wf_cfg(config, "tune_params", [])
    if configured:
        invalid = [p for p in configured if p not in _PARAM_RANGES]
        if invalid:
            raise ValueError(
                f"[walk_forward] Parametri non tunabili: {invalid}. "
                f"Validi: {list(_PARAM_RANGES)}"
            )
        if len(configured) > max_params:
            raise ValueError(
                f"[walk_forward] max_params_per_cycle={max_params}, "
                f"richiesti {len(configured)}: {configured}"
            )
        return configured
    # Default: i due parametri con l'impatto maggiore sul rischio
    return ["stop_loss_pct", "trailing_stop_pct"]


def _build_param_grid(param_names: list[str]) -> list[dict[str, float]]:
    """
    Genera tutte le combinazioni della grid per i parametri specificati.
    Ritorna lista di dict {param_name: valore_decimale}.
    """
    if not param_names:
        return [{}]
    value_lists: list[list[float]] = []
    for name in param_names:
        lo, hi, step = _PARAM_RANGES[name]
        value_lists.append(_frange(lo, hi, step))
    return [
        dict(zip(param_names, combo))
        for combo in itertools.product(*value_lists)
    ]


# ─── Score e guardrail ────────────────────────────────────────────────────────

def _composite_score(report: dict) -> float:
    """Estrae il composite_score dal dict prodotto da score.full_report."""
    return float(report.get("composite_score", 0.0))


def _passes_is_filters(
    report: dict,
    min_trades: int,
    max_drawdown: float,
    min_calmar: float,
) -> tuple[bool, str]:
    """
    Verifica i filtri IS (In-Sample).

    Ritorna (True, "") se supera tutti i filtri,
    (False, motivo) altrimenti.
    """
    n = report.get("n_trades", 0)
    if n < min_trades:
        return False, f"n_trades={n} < min={min_trades}"

    dd = report.get("survival", {}).get("max_drawdown_pct", 999.0) / 100.0
    if dd > max_drawdown:
        return False, f"max_drawdown={dd:.2%} > limite={max_drawdown:.2%}"

    calmar = report.get("robustness", {}).get("calmar_ratio", 0.0)
    if calmar < min_calmar:
        return False, f"calmar={calmar:.3f} < min={min_calmar}"

    return True, ""


def _deflated_sharpe_ok(sr_max: float, n_trials: int, n_obs: int) -> tuple[bool, float]:
    """
    Guardrail Deflated Sharpe Ratio (DSR).

    DSR = SR_max - sqrt(2 * ln(N_trials) / N_obs)

    Se DSR < 0 il miglior Sharpe trovato è probabilmente artefatto
    del data mining (troppi test su pochi dati).

    Ritorna (True, dsr) se dsr >= 0, (False, dsr) altrimenti.
    """
    if n_obs <= 0 or n_trials <= 1:
        return True, sr_max
    penalty = math.sqrt(2.0 * math.log(n_trials) / n_obs)
    dsr = sr_max - penalty
    return dsr >= 0.0, round(dsr, 4)


def _distance_penalty(
    best_params: dict[str, float],
    current_params_decimal: dict[str, float],
    param_names: list[str],
) -> float:
    """
    Penalità di distanza dai parametri correnti.

    score_final = score - 0.15 * sum_i(|best_i - current_i| / range_i)

    Penalizza cambiamenti parametri troppo bruschi rispetto allo stato attuale.
    """
    total_dist = 0.0
    for name in param_names:
        lo, hi, _ = _PARAM_RANGES[name]
        param_range = hi - lo
        if param_range <= 0:
            continue
        current = current_params_decimal.get(name, best_params.get(name, 0.0))
        delta = abs(best_params[name] - current)
        total_dist += delta / param_range
    return round(0.15 * total_dist, 6)


def _subwindow_variance_ok(
    candles_is: list[dict],
    merged_params: dict,
    goal: dict,
    max_cv: float = 0.30,
) -> tuple[bool, dict]:
    """
    Guardrail varianza subwindow.

    Divide il periodo IS in 3 parti uguali, esegue il backtest su ciascuna
    con i parametri migliori e calcola il coefficient of variation degli score.

    Se CV = stdev(scores) / |mean(scores)| > max_cv → scarta (overfitting locale).

    Ritorna (True, info_dict) se la varianza è accettabile.
    """
    n = len(candles_is)
    chunk = max(1, n // 3)
    sub_scores: list[float] = []

    for i in range(3):
        start = i * chunk
        end   = (i + 1) * chunk if i < 2 else n
        sub_candles = candles_is[start:end]
        if not sub_candles:
            continue
        try:
            sub_trades = run_backtest(candles=sub_candles, strategy=merged_params)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            log.warning("[walk_forward] subwindow backtest fallito: %s", exc)
            continue
        if not sub_trades:
            sub_scores.append(0.0)
            continue
        sub_report = score_mod.full_report(sub_trades, goal)
        sub_scores.append(_composite_score(sub_report))

    if len(sub_scores) < 2:
        # Dati insufficienti per il check — lascia passare
        return True, {"subwindow_scores": sub_scores, "cv": None}

    mu = mean(sub_scores)
    sd = pstdev(sub_scores)
    cv = sd / abs(mu) if abs(mu) > 1e-9 else float("inf")

    info = {
        "subwindow_scores": [round(s, 4) for s in sub_scores],
        "mean":             round(mu, 4),
        "stdev":            round(sd, 4),
        "cv":               round(cv, 4),
        "threshold":        max_cv,
    }
    # Se la media è negativa l'ottimizzazione è già fallita — scarta
    if mu <= 0:
        info["reject_reason"] = "media_subwindow_negativa"
        return False, info
    if cv > max_cv:
        info["reject_reason"] = f"cv={cv:.3f} > soglia={max_cv}"
        return False, info
    return True, info


# ─── Ottimizzazione IS ────────────────────────────────────────────────────────

def _run_is_optimization(
    candles_is:     list[dict],
    strategy:       dict,
    param_names:    list[str],
    goal:           dict,
    min_trades:     int,
    max_drawdown:   float,
    min_calmar:     float,
) -> tuple[dict | None, dict | None, list[dict]]:
    """
    Esegue la grid search sul periodo IS.

    Per ogni combinazione della grid:
      1. Costruisce i parametri completi (strategy + override)
      2. Esegue il backtest
      3. Verifica i filtri IS
      4. Calcola lo score composito

    Ritorna (best_params_decimal, is_report, all_results_list).
    Se nessuna combinazione supera i filtri IS, ritorna (None, None, all_results).
    """
    grid = _build_param_grid(param_names)
    all_results: list[dict] = []
    best_score: float = float("-inf")
    best_params: dict | None = None
    best_report: dict | None = None

    log.info(
        "[walk_forward] Grid search IS: %d combinazioni su %d candele",
        len(grid), len(candles_is),
    )

    for combo in grid:
        merged = _build_backtest_params(strategy, combo)
        try:
            trades = run_backtest(candles=candles_is, strategy=merged)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            log.warning("[walk_forward] backtest IS fallito per %s: %s", combo, exc)
            all_results.append({"params": combo, "error": str(exc)})
            continue

        if not trades:
            all_results.append({"params": combo, "n_trades": 0, "passed_filter": False})
            continue

        report = score_mod.full_report(trades, goal)
        passed, motivo = _passes_is_filters(report, min_trades, max_drawdown, min_calmar)
        cs = _composite_score(report)

        all_results.append({
            "params":           combo,
            "n_trades":         report.get("n_trades", 0),
            "composite_score":  round(cs, 4),
            "max_drawdown_pct": report.get("survival", {}).get("max_drawdown_pct"),
            "calmar":           report.get("robustness", {}).get("calmar_ratio"),
            "sharpe":           report.get("efficiency", {}).get("sharpe"),
            "passed_filter":    passed,
            "filter_reject":    motivo if not passed else None,
        })

        if passed and cs > best_score:
            best_score  = cs
            best_params = dict(combo)
            best_report = report

    return best_params, best_report, all_results


# ─── Validazione OOS ─────────────────────────────────────────────────────────

def _validate_oos_window(
    candles_oos: list[dict],
    merged_params: dict,
    goal: dict,
) -> dict | None:
    """
    Valida i parametri su una singola finestra OOS.
    Ritorna il report completo, o None in caso di errore / nessun trade.
    """
    try:
        trades = run_backtest(candles=candles_oos, strategy=merged_params)  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001
        log.warning("[walk_forward] backtest OOS fallito: %s", exc)
        return None
    if not trades:
        return None
    return score_mod.full_report(trades, goal)


def _aggregate_oos_results(oos_reports: list[dict]) -> dict:
    """
    Aggrega i risultati di più finestre OOS in metriche medie.
    Ritorna un dict con le metriche aggregate.
    """
    if not oos_reports:
        return {}

    scores   = [_composite_score(r) for r in oos_reports]
    dds      = [r.get("survival", {}).get("max_drawdown_pct", 0.0) / 100.0 for r in oos_reports]
    sharpes  = [r.get("efficiency", {}).get("sharpe", 0.0) for r in oos_reports]
    n_trades = [r.get("n_trades", 0) for r in oos_reports]

    return {
        "n_windows":        len(oos_reports),
        "mean_score":       round(mean(scores), 4),
        "mean_drawdown":    round(mean(dds), 4),
        "mean_sharpe":      round(mean(sharpes), 4),
        "min_trades":       min(n_trades),
        "total_trades":     sum(n_trades),
        "scores_per_window": [round(s, 4) for s in scores],
    }


def _passes_oos_criteria(
    agg: dict,
    is_score: float,
    is_dd: float,
    min_score_retention: float = 0.85,
    max_dd_inflation:    float = 1.30,
    min_n_trades:        int   = 20,
) -> tuple[bool, list[str]]:
    """
    Verifica i criteri OOS sul risultato aggregato.

    Criteri:
    - score retention  >= 0.85   (OOS score / IS score)
    - dd inflation     <= 1.30   (OOS dd / IS dd)
    - sharpe           > 0
    - n_trades minimo  >= 20

    Ritorna (True, []) se tutti i criteri sono superati,
    (False, [lista_motivi]) altrimenti.
    """
    failures: list[str] = []

    # Score retention
    if is_score > 0:
        retention = agg.get("mean_score", 0.0) / is_score
        if retention < min_score_retention:
            failures.append(
                f"score_retention={retention:.3f} < {min_score_retention}"
            )
    elif agg.get("mean_score", 0.0) <= 0:
        failures.append("score_oos <= 0 e score_is <= 0")

    # DD inflation
    if is_dd > 0:
        inflation = agg.get("mean_drawdown", 0.0) / is_dd
        if inflation > max_dd_inflation:
            failures.append(
                f"dd_inflation={inflation:.3f} > {max_dd_inflation}"
            )

    # Sharpe OOS > 0
    if agg.get("mean_sharpe", 0.0) <= 0:
        failures.append(f"sharpe_oos={agg.get('mean_sharpe', 0.0):.3f} <= 0")

    # Trade minimi per finestra OOS
    min_t = agg.get("min_trades", 0)
    if min_t < min_n_trades:
        failures.append(f"min_trades_oos={min_t} < {min_n_trades}")

    return len(failures) == 0, failures


# ─── PROMOTE ──────────────────────────────────────────────────────────────────

def _promote_params(
    state_dir:    Path,
    strategy:     dict,
    best_decimal: dict[str, float],
    cycle_id:     str,
) -> dict:
    """
    Aggiorna state/strategy.yaml con i nuovi parametri ottimizzati.

    Salva anche un file diff JSON con i delta rispetto ai valori precedenti.
    Ritorna il dict dei vecchi e nuovi valori per il log.
    """
    strategy_path = state_dir / "strategy.yaml"

    # Leggi il contenuto attuale
    current_yaml = yaml.safe_load(strategy_path.read_text()) if strategy_path.exists() else {}

    new_yaml_values = _decimal_to_yaml_values(best_decimal)
    diff: dict[str, dict] = {}

    for k, new_v in new_yaml_values.items():
        old_v = current_yaml.get(k)
        diff[k] = {"da": old_v, "a": new_v}
        current_yaml[k] = new_v

    # Scrivi il file aggiornato
    strategy_path.write_text(yaml.dump(current_yaml, allow_unicode=True, sort_keys=False))

    # Salva diff nella cartella del ciclo
    diff_path = state_dir / "walkforward" / cycle_id / "promote_diff.json"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(json.dumps({
        "cycle_id":    cycle_id,
        "promoted_at": _now_iso(),
        "diff":        diff,
    }, indent=2))

    log.info("[walk_forward] PROMOTE %s — diff: %s", cycle_id, diff)
    return diff


# ─── Artefatti ───────────────────────────────────────────────────────────────

def _save_artifacts(
    cycle_dir:       Path,
    cycle_id:        str,
    manifest:        dict,
    grid_results:    list[dict],
    is_best:         dict,
    oos_validation:  dict,
    decision:        dict,
) -> None:
    """
    Salva tutti gli artefatti del ciclo nella directory dedicata.

    Struttura:
      state/walkforward/{cycle_id}/
        manifest.json       — metadati del ciclo
        is_grid.csv         — risultati completi della grid search IS
        is_best.json        — migliori parametri IS e report
        oos_validation.json — risultati validazione OOS
        decision.json       — decisione finale (PROMOTE/REJECT)
        report.md           — report leggibile da umano
    """
    cycle_dir.mkdir(parents=True, exist_ok=True)

    # manifest.json
    (cycle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # is_grid.csv
    if grid_results:
        fieldnames = list(grid_results[0].keys())
        with (cycle_dir / "is_grid.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(grid_results)

    # is_best.json
    (cycle_dir / "is_best.json").write_text(json.dumps(is_best, indent=2))

    # oos_validation.json
    (cycle_dir / "oos_validation.json").write_text(json.dumps(oos_validation, indent=2))

    # decision.json
    (cycle_dir / "decision.json").write_text(json.dumps(decision, indent=2))

    # report.md
    report_md = _generate_report_md(cycle_id, manifest, is_best, oos_validation, decision)
    (cycle_dir / "report.md").write_text(report_md)

    log.info("[walk_forward] Artefatti salvati in %s", cycle_dir)


def _generate_report_md(
    cycle_id:       str,
    manifest:       dict,
    is_best:        dict,
    oos_validation: dict,
    decision:       dict,
) -> str:
    """Genera un report Markdown leggibile per il ciclo walk-forward."""
    decisione = decision.get("decision", "N/A")
    emoji = "✅" if decisione == "PROMOTE" else "❌"

    lines: list[str] = [
        f"# Walk-Forward Report — {cycle_id}",
        "",
        f"**Data**: {manifest.get('timestamp', 'N/A')}",
        f"**Strategia**: {manifest.get('strategy_name', 'N/A')}",
        f"**Parametri ottimizzati**: {', '.join(manifest.get('tune_params', []))}",
        f"**Finestre IS**: {manifest.get('n_is_windows', 'N/A')} | "
        f"**Finestre OOS**: {manifest.get('n_oos_windows', 'N/A')}",
        "",
        f"## Decisione: {emoji} {decisione}",
        "",
    ]

    if decisione == "REJECT":
        motivi = decision.get("motivi", [])
        lines.append("**Motivi del rifiuto:**")
        for m in motivi:
            lines.append(f"- {m}")
        lines.append("")

    # Migliori parametri IS
    lines.append("## Migliori parametri (IS)")
    lines.append("")
    best_p = is_best.get("params_decimal", {})
    for k, v in best_p.items():
        lines.append(f"- **{k}**: {v:.4f} ({v * 100:.2f}%)")
    lines.append("")

    # Score IS
    is_score = is_best.get("composite_score", "N/A")
    is_dd    = is_best.get("max_drawdown_pct", "N/A")
    is_cal   = is_best.get("calmar", "N/A")
    is_sh    = is_best.get("sharpe", "N/A")
    is_n     = is_best.get("n_trades", "N/A")
    lines.extend([
        "## Metriche IS (In-Sample)",
        "",
        "| Metrica       | Valore |",
        "|---------------|--------|",
        f"| Score         | {is_score} |",
        f"| Max Drawdown  | {is_dd}% |",
        f"| Calmar        | {is_cal} |",
        f"| Sharpe        | {is_sh} |",
        f"| N trade       | {is_n} |",
        "",
    ])

    # Guardrail
    gr = is_best.get("guardrail", {})
    dsr = gr.get("deflated_sharpe", "N/A")
    cv  = gr.get("subwindow_cv", "N/A")
    dp  = gr.get("distance_penalty", "N/A")
    lines.extend([
        "## Guardrail",
        "",
        "| Guardrail          | Valore |",
        "|--------------------|--------|",
        f"| Deflated Sharpe    | {dsr} |",
        f"| Varianza subwindow (CV) | {cv} |",
        f"| Distance penalty   | {dp} |",
        "",
    ])

    # Metriche OOS aggregate
    agg = oos_validation.get("aggregate", {})
    oos_score   = agg.get("mean_score", "N/A")
    oos_dd      = agg.get("mean_drawdown", "N/A")
    oos_sharpe  = agg.get("mean_sharpe", "N/A")
    oos_trades  = agg.get("total_trades", "N/A")
    oos_windows = agg.get("n_windows", "N/A")
    lines.extend([
        "## Metriche OOS (Out-of-Sample aggregate)",
        "",
        "| Metrica           | Valore |",
        "|-------------------|--------|",
        f"| N finestre        | {oos_windows} |",
        f"| Score medio       | {oos_score} |",
        f"| Drawdown medio    | {oos_dd:.2%} |" if isinstance(oos_dd, float) else f"| Drawdown medio    | {oos_dd} |",
        f"| Sharpe medio      | {oos_sharpe} |",
        f"| Trade totali      | {oos_trades} |",
        "",
    ])

    # Holdout (se disponibile)
    holdout = oos_validation.get("holdout_check")
    if holdout:
        ho_score  = holdout.get("composite_score", "N/A")
        ho_sharpe = holdout.get("efficiency", {}).get("sharpe", "N/A")
        ho_n      = holdout.get("n_trades", "N/A")
        lines.extend([
            "## Holdout permanente",
            "",
            "| Metrica  | Valore |",
            "|----------|--------|",
            f"| Score    | {ho_score} |",
            f"| Sharpe   | {ho_sharpe} |",
            f"| N trade  | {ho_n} |",
            "",
        ])

    lines.append("---")
    lines.append(f"*Generato da walk_forward.py — ciclo {cycle_id}*")

    return "\n".join(lines)


# ─── Entrypoint principale ────────────────────────────────────────────────────

def run_cycle(
    state_dir:   Path | str,
    strategy:    dict,
    candles_1d:  list[dict],
    config:      dict,
) -> dict:
    """
    Esegue un ciclo completo di walk-forward validation.

    Parameters
    ----------
    state_dir   : Path alla directory di stato (es. Path("state/"))
    strategy    : dict con i parametri correnti della strategia (da strategy.yaml)
    candles_1d  : lista di dict con candele giornaliere (fino a 2 anni di storia)
                  Ogni dict deve avere almeno le chiavi: open, high, low, close, volume
                  e un campo timestamp (unix seconds o ISO string).
    config      : dict con la configurazione del bot (include la sezione walk_forward)

    Returns
    -------
    dict con:
        status    : 'disabled' | 'skipped_cooldown' | 'insufficient_data' |
                    'no_valid_is' | 'promoted' | 'rejected'
        cycle_id  : str (solo se il ciclo è stato eseguito)
        decision  : 'PROMOTE' | 'REJECT' (solo se eseguito)
        motivi    : list[str] (motivi di REJECT)
    """
    # ── 1. Flag enabled ────────────────────────────────────────────────────
    if not _wf_cfg(config, "walk_forward_enabled", False):
        return {"status": "disabled"}

    state_dir = Path(state_dir)
    wf_dir    = _wf_dir(state_dir)
    meta      = _load_meta(wf_dir)

    # ── 2. Cooldown post-PROMOTE ───────────────────────────────────────────
    cooldown_days: int = _wf_cfg(config, "cooldown_after_promote_days", 30)
    last_promote  = meta.get("last_promote_at")
    if last_promote:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(
            last_promote.replace("Z", "+00:00")
        )
        if delta < timedelta(days=cooldown_days):
            giorni_rimasti = cooldown_days - delta.days
            log.info(
                "[walk_forward] Cooldown attivo — mancano %d giorni al prossimo ciclo.",
                giorni_rimasti,
            )
            return {
                "status":          "skipped_cooldown",
                "giorni_rimasti":  giorni_rimasti,
                "last_promote_at": last_promote,
            }

    # ── 3. Parametri di configurazione ────────────────────────────────────
    is_months:      int   = _wf_cfg(config, "is_window_months",    12)
    oos_months:     int   = _wf_cfg(config, "oos_window_months",    3)
    step_months:    int   = _wf_cfg(config, "step_months",          3)
    min_hist_months: int  = _wf_cfg(config, "min_history_months",  18)
    holdout_pct:    float = _wf_cfg(config, "holdout_pct",         0.10)
    max_params:     int   = _wf_cfg(config, "max_params_per_cycle", 2)

    # Filtri IS
    min_trades_is:  int   = _wf_cfg(config, "min_trades_is",   100)
    max_dd_is:      float = _wf_cfg(config, "max_drawdown_is", 0.25)
    min_calmar_is:  float = _wf_cfg(config, "min_calmar_is",   1.5)

    # Criteri OOS
    min_score_ret:  float = _wf_cfg(config, "min_score_retention", 0.85)
    max_dd_infl:    float = _wf_cfg(config, "max_dd_inflation",     1.30)
    min_n_oos:      int   = _wf_cfg(config, "min_n_trades_oos",     20)

    # Goal per i report
    goal: dict = {**_DEFAULT_GOAL, **_wf_cfg(config, "goal", {})}

    # ── 4. Verifica storia sufficiente ────────────────────────────────────
    min_candles = min_hist_months * _DAYS_PER_MONTH
    if len(candles_1d) < min_candles:
        log.warning(
            "[walk_forward] Storia insufficiente: %d candele < %d richieste (%d mesi).",
            len(candles_1d), min_candles, min_hist_months,
        )
        return {
            "status":            "insufficient_data",
            "n_candles":         len(candles_1d),
            "min_candles":       min_candles,
            "min_hist_months":   min_hist_months,
        }

    # ── 5. Separa holdout permanente ──────────────────────────────────────
    candles_usabili, candles_holdout = _split_holdout(candles_1d, holdout_pct)
    log.info(
        "[walk_forward] Holdout: %d candele riservate. Usabili: %d.",
        len(candles_holdout), len(candles_usabili),
    )

    # ── 6. Genera finestre IS/OOS ─────────────────────────────────────────
    is_days   = is_months   * _DAYS_PER_MONTH
    oos_days  = oos_months  * _DAYS_PER_MONTH
    step_days = step_months * _DAYS_PER_MONTH

    windows = _generate_windows(candles_usabili, is_days, oos_days, step_days)
    if not windows:
        log.warning(
            "[walk_forward] Nessuna finestra IS/OOS generabile con i parametri attuali."
        )
        return {"status": "insufficient_data", "motivo": "nessuna_finestra_generabile"}

    # ── 7. Seleziona parametri da ottimizzare ─────────────────────────────
    try:
        tune_params = _select_tune_params(config, max_params)
    except ValueError as exc:
        log.error("[walk_forward] Configurazione tune_params non valida: %s", exc)
        return {"status": "error", "motivo": str(exc)}

    log.info(
        "[walk_forward] %d finestre IS/OOS | ottimizzazione su: %s",
        len(windows), tune_params,
    )

    # ── 8. Genera cycle_id e cartella artefatti ───────────────────────────
    cycle_count = meta["cycle_count"] + 1
    cycle_id    = f"wf_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{cycle_count:04d}"
    cycle_dir   = wf_dir / cycle_id

    # ── 9. Grid search sulla finestra IS più recente ──────────────────────
    # L'ottimizzazione si basa sulla finestra più recente (dati più rilevanti).
    candles_is_recent, _ = windows[-1]

    best_params_dec, is_report_best, all_grid_results = _run_is_optimization(
        candles_is     = candles_is_recent,
        strategy       = strategy,
        param_names    = tune_params,
        goal           = goal,
        min_trades     = min_trades_is,
        max_drawdown   = max_dd_is,
        min_calmar     = min_calmar_is,
    )

    if best_params_dec is None or is_report_best is None:
        log.warning(
            "[walk_forward] Nessuna combinazione IS ha superato i filtri. Ciclo REJECT."
        )
        decision = {
            "cycle_id": cycle_id,
            "decision": "REJECT",
            "motivi":   ["nessuna_combo_IS_supera_filtri"],
            "timestamp": _now_iso(),
        }
        manifest = {
            "cycle_id":      cycle_id,
            "timestamp":     _now_iso(),
            "strategy_name": strategy.get("entry", {}).get("indicator", "unknown")
                             if isinstance(strategy.get("entry"), dict)
                             else str(strategy.get("entry", "unknown")),
            "tune_params":   tune_params,
            "n_is_windows":  len(windows),
            "n_oos_windows": len(windows),
            "n_grid_combos": len(all_grid_results),
        }
        _save_artifacts(
            cycle_dir      = cycle_dir,
            cycle_id       = cycle_id,
            manifest       = manifest,
            grid_results   = all_grid_results,
            is_best        = {},
            oos_validation = {},
            decision       = decision,
        )
        meta["cycle_count"] = cycle_count
        _save_meta(wf_dir, meta)
        return {"status": "no_valid_is", "cycle_id": cycle_id, "decision": "REJECT"}

    # ── 10. Guardrail: Deflated Sharpe ────────────────────────────────────
    sr_max   = is_report_best.get("efficiency", {}).get("sharpe", 0.0)
    n_trials = len(all_grid_results)
    n_obs    = is_report_best.get("n_trades", 1)
    dsr_ok, dsr_value = _deflated_sharpe_ok(sr_max, n_trials, n_obs)

    # ── 11. Guardrail: varianza subwindow ─────────────────────────────────
    merged_best = _build_backtest_params(strategy, best_params_dec)
    sub_ok, sub_info = _subwindow_variance_ok(
        candles_is   = candles_is_recent,
        merged_params = merged_best,
        goal         = goal,
    )

    # ── 12. Guardrail: distance penalty ───────────────────────────────────
    current_dec = _strategy_to_decimal(strategy)
    dist_pen    = _distance_penalty(best_params_dec, current_dec, tune_params)
    is_score_raw = _composite_score(is_report_best)
    is_score_adj = round(is_score_raw - dist_pen, 4)

    # Raccolta info guardrail per is_best.json
    guardrail_info = {
        "deflated_sharpe":      dsr_value,
        "deflated_sharpe_ok":   dsr_ok,
        "subwindow_cv":         sub_info.get("cv"),
        "subwindow_ok":         sub_ok,
        "subwindow_detail":     sub_info,
        "distance_penalty":     round(dist_pen, 6),
        "is_score_raw":         round(is_score_raw, 4),
        "is_score_adj":         is_score_adj,
    }

    # Verifica guardrail — se falliscono, REJECT immediato
    guardrail_failures: list[str] = []
    if not dsr_ok:
        guardrail_failures.append(
            f"deflated_sharpe={dsr_value:.4f} < 0 (overfitting)")
    if not sub_ok:
        guardrail_failures.append(
            f"subwindow_variance: {sub_info.get('reject_reason', 'CV troppo alto')}")

    is_best_payload = {
        "params_decimal":   best_params_dec,
        "params_pct":       _decimal_to_yaml_values(best_params_dec),
        "composite_score":  round(is_score_adj, 4),
        "composite_score_raw": round(is_score_raw, 4),
        "n_trades":         is_report_best.get("n_trades"),
        "max_drawdown_pct": is_report_best.get("survival", {}).get("max_drawdown_pct"),
        "calmar":           is_report_best.get("robustness", {}).get("calmar_ratio"),
        "sharpe":           is_report_best.get("efficiency", {}).get("sharpe"),
        "guardrail":        guardrail_info,
    }

    if guardrail_failures:
        log.warning("[walk_forward] Guardrail IS falliti: %s", guardrail_failures)
        decision = {
            "cycle_id":  cycle_id,
            "decision":  "REJECT",
            "motivi":    guardrail_failures,
            "timestamp": _now_iso(),
        }
        manifest = {
            "cycle_id":      cycle_id,
            "timestamp":     _now_iso(),
            "strategy_name": strategy.get("entry", "unknown"),
            "tune_params":   tune_params,
            "n_is_windows":  len(windows),
            "n_oos_windows": len(windows),
            "n_grid_combos": n_trials,
        }
        _save_artifacts(cycle_dir, cycle_id, manifest, all_grid_results,
                        is_best_payload, {}, decision)
        meta["cycle_count"] = cycle_count
        _save_meta(wf_dir, meta)
        return {
            "status":   "rejected",
            "cycle_id": cycle_id,
            "decision": "REJECT",
            "motivi":   guardrail_failures,
        }

    # ── 13. Validazione OOS su tutte le finestre ──────────────────────────
    oos_reports: list[dict] = []
    for _, oos_candles in windows:
        oos_rep = _validate_oos_window(oos_candles, merged_best, goal)
        if oos_rep:
            oos_reports.append(oos_rep)

    if not oos_reports:
        log.warning("[walk_forward] Nessun risultato OOS valido.")
        decision = {
            "cycle_id":  cycle_id,
            "decision":  "REJECT",
            "motivi":    ["nessun_risultato_oos_valido"],
            "timestamp": _now_iso(),
        }
        manifest = {
            "cycle_id":      cycle_id,
            "timestamp":     _now_iso(),
            "strategy_name": strategy.get("entry", "unknown"),
            "tune_params":   tune_params,
            "n_is_windows":  len(windows),
            "n_oos_windows": 0,
            "n_grid_combos": n_trials,
        }
        _save_artifacts(cycle_dir, cycle_id, manifest, all_grid_results,
                        is_best_payload, {}, decision)
        meta["cycle_count"] = cycle_count
        _save_meta(wf_dir, meta)
        return {
            "status":   "rejected",
            "cycle_id": cycle_id,
            "decision": "REJECT",
            "motivi":   ["nessun_risultato_oos_valido"],
        }

    oos_agg = _aggregate_oos_results(oos_reports)

    # IS drawdown di riferimento per calcolo dd_inflation
    is_dd_ref = (is_report_best.get("survival", {}).get("max_drawdown_pct", 0.0) / 100.0)

    oos_ok, oos_failures = _passes_oos_criteria(
        agg                = oos_agg,
        is_score           = is_score_adj,
        is_dd              = is_dd_ref,
        min_score_retention = min_score_ret,
        max_dd_inflation   = max_dd_infl,
        min_n_trades       = min_n_oos,
    )

    # ── 14. Verifica holdout ogni 6 cicli ─────────────────────────────────
    holdout_check_result: dict | None = None
    last_holdout_check = meta.get("last_holdout_check_cycle", 0)
    if cycle_count - last_holdout_check >= 6:
        log.info("[walk_forward] Verifica holdout permanente al ciclo %d.", cycle_count)
        holdout_rep = _validate_oos_window(candles_holdout, merged_best, goal)
        if holdout_rep:
            holdout_check_result = holdout_rep
            meta["last_holdout_check_cycle"] = cycle_count
            if _composite_score(holdout_rep) < 0:
                log.warning(
                    "[walk_forward] Holdout score negativo: %.4f — segnale di degrado.",
                    _composite_score(holdout_rep),
                )

    oos_validation_payload = {
        "aggregate":       oos_agg,
        "n_oos_windows":   len(oos_reports),
        "holdout_check":   holdout_check_result,
        "criteria": {
            "min_score_retention": min_score_ret,
            "max_dd_inflation":    max_dd_infl,
            "min_sharpe":          0.0,
            "min_n_trades":        min_n_oos,
        },
        "passed": oos_ok,
        "failures": oos_failures,
    }

    # ── 15. Decisione PROMOTE / REJECT ────────────────────────────────────
    strategy_name = (
        strategy.get("entry", {}).get("indicator", "unknown")
        if isinstance(strategy.get("entry"), dict)
        else str(strategy.get("entry", "unknown"))
    )

    manifest = {
        "cycle_id":      cycle_id,
        "timestamp":     _now_iso(),
        "strategy_name": strategy_name,
        "tune_params":   tune_params,
        "n_is_windows":  len(windows),
        "n_oos_windows": len(oos_reports),
        "n_grid_combos": n_trials,
        "holdout_pct":   holdout_pct,
        "candles_total": len(candles_1d),
    }

    if oos_ok:
        # ── PROMOTE ───────────────────────────────────────────────────────
        promote_diff = _promote_params(
            state_dir   = state_dir,
            strategy    = strategy,
            best_decimal = best_params_dec,
            cycle_id    = cycle_id,
        )
        decision = {
            "cycle_id":     cycle_id,
            "decision":     "PROMOTE",
            "motivi":       [],
            "promoted_at":  _now_iso(),
            "promote_diff": promote_diff,
            "is_score_adj": is_score_adj,
            "oos_aggregate": oos_agg,
        }
        meta["last_promote_at"] = _now_iso()
        meta["promoted_params_history"].append({
            "cycle_id":  cycle_id,
            "params":    _decimal_to_yaml_values(best_params_dec),
            "timestamp": _now_iso(),
        })

        # Notifica Telegram
        param_str = ", ".join(
            f"{k}={v * 100:.2f}%" for k, v in best_params_dec.items()
        )
        _notify_sync(
            f"[hermes-trading] Walk-Forward PROMOTE {cycle_id}\n"
            f"Parametri promossi: {param_str}\n"
            f"IS score: {is_score_adj:.4f} | OOS score: {oos_agg.get('mean_score', 'N/A')}"
        )
        log.info("[walk_forward] PROMOTE completato per %s.", cycle_id)
        final_status = "promoted"

    else:
        # ── REJECT ────────────────────────────────────────────────────────
        decision = {
            "cycle_id":     cycle_id,
            "decision":     "REJECT",
            "motivi":       oos_failures,
            "rejected_at":  _now_iso(),
            "is_score_adj": is_score_adj,
            "oos_aggregate": oos_agg,
        }
        log.info(
            "[walk_forward] REJECT ciclo %s. Motivi: %s",
            cycle_id, oos_failures,
        )
        final_status = "rejected"

    # ── 16. Salva artefatti e aggiorna meta ───────────────────────────────
    _save_artifacts(
        cycle_dir       = cycle_dir,
        cycle_id        = cycle_id,
        manifest        = manifest,
        grid_results    = all_grid_results,
        is_best         = is_best_payload,
        oos_validation  = oos_validation_payload,
        decision        = decision,
    )

    meta["cycle_count"] = cycle_count
    _save_meta(wf_dir, meta)

    return {
        "status":        final_status,
        "cycle_id":      cycle_id,
        "decision":      decision["decision"],
        "motivi":        decision.get("motivi", []),
        "is_score":      is_score_adj,
        "oos_score":     oos_agg.get("mean_score"),
        "oos_windows":   len(oos_reports),
        "artifacts_dir": str(cycle_dir),
    }

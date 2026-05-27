from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .bootstrap import state_dir
from .score import compute_max_drawdown

WINDOW = 25  # last N closed trades looked at on each reflection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_goal(state: Path) -> dict:
    return yaml.safe_load((state / "goal.yaml").read_text())


def _load_strategy(state: Path) -> dict:
    return yaml.safe_load((state / "strategy.yaml").read_text())


def _load_trades(state: Path, window: int = WINDOW) -> list[dict]:
    tf = state / "trades.jsonl"
    if not tf.exists():
        return []
    lines = [ln for ln in tf.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines[-window:]]


def _bump_version(version: str) -> str:
    try:
        n = int(version)
    except ValueError:
        n = 1
    return f"{n + 1:02d}"


def _archive_prior(state: Path, strategy: dict) -> Path:
    history = state / "history"
    history.mkdir(exist_ok=True)
    dst = history / f"v{strategy.get('version', '00')}.yaml"
    dst.write_text(yaml.safe_dump(strategy, sort_keys=False))
    return dst


def _apply_to_strategy(strategy: dict, dotted_path: str, value) -> None:
    keys = dotted_path.split(".")
    cursor = strategy
    for k in keys[:-1]:
        cursor = cursor[k]
    cursor[keys[-1]] = value


def _read_dotted(strategy: dict, dotted_path: str):
    cursor = strategy
    for k in dotted_path.split("."):
        cursor = cursor[k]
    return cursor


def _write_strategy(state: Path, strategy: dict) -> None:
    (state / "strategy.yaml").write_text(yaml.safe_dump(strategy, sort_keys=False))


def _append_hypothesis(state: Path, hyp: dict) -> None:
    with (state / "hypotheses.jsonl").open("a") as f:
        f.write(json.dumps(hyp) + "\n")


def _fallback_hypothesis(trades: list[dict], strategy: dict, goal: dict) -> dict:
    """Deterministic rule, used before Hermes is installed.

    - If realised return (last 25 trades) < target → loosen entry.threshold by 2.
    - If drawdown > max → tighten stop_loss_pct by 0.2.
    - Pick the more pressing.
    - Always changes exactly ONE variable.
    """
    pnls = [t["pnl_pct"] for t in trades]
    realised_return = sum(pnls)
    realised_dd = compute_max_drawdown(pnls)

    return_gap = goal["target_return_30d"] - realised_return
    dd_gap = realised_dd - goal["max_drawdown"]

    # Pressing-ness score, higher = more pressing.
    return_pressure = max(0.0, return_gap) / max(1e-6, goal["target_return_30d"])
    dd_pressure = max(0.0, dd_gap) / max(1e-6, goal["max_drawdown"])

    if dd_pressure > return_pressure and dd_pressure > 0:
        current = float(_read_dotted(strategy, "stop_loss_pct"))
        proposed = max(0.2, round(current - 0.2, 4))
        return {
            "variable": "stop_loss_pct",
            "current_value": current,
            "proposed_value": proposed,
            "rationale": (
                f"Drawdown {realised_dd:.4f} above max {goal['max_drawdown']}, tighten stop."
            ),
            "predicted_score_delta": 0.05,
            "confidence": 0.6,
            "source": "fallback",
        }

    current = float(_read_dotted(strategy, "entry.threshold"))
    proposed = round(current + 2.0, 2)
    return {
        "variable": "entry.threshold",
        "current_value": current,
        "proposed_value": proposed,
        "rationale": (
            f"Realised return {realised_return:.4f} below target {goal['target_return_30d']}, loosen entry."
        ),
        "predicted_score_delta": 0.05,
        "confidence": 0.55,
        "source": "fallback",
    }


def _hermes_hypothesis(trades: list[dict], strategy: dict, goal: dict) -> dict:
    """Call the local `hermes` CLI with a JSON-mode prompt."""
    from .score import full_report
    report = full_report(trades, goal)

    prompt = (
        "Sei il modulo di auto-miglioramento di un trading bot trend-follower su BTC/USDT.\n\n"
        "FILOSOFIA DI RISCHIO (non negoziabile):\n"
        "  'Si fa prestissimo a perdere tutto, c'e' tempo a guadagnare.'\n"
        "  Priorita' assoluta: sopravvivenza. Mai perdite catastrofiche.\n"
        "  Accettiamo perdite importanti ma non irreparabili.\n\n"
        "ORDINE DI PRIORITA' NELLE METRICHE:\n"
        "  1. SOPRAVVIVENZA: max_drawdown, cvar_5pct, max_consecutive_losses\n"
        "  2. ROBUSTEZZA: calmar_ratio, ulcer_index, tail_ratio\n"
        "  3. EFFICIENZA: sharpe, expectancy, win_rate (secondario)\n\n"
        f"Goal: {json.dumps(goal)}\n"
        f"Strategia attuale: {json.dumps(strategy)}\n"
        f"Report ultimi {len(trades)} trade:\n{json.dumps(report, indent=2)}\n\n"
        "Proponi ESATTAMENTE UNA modifica a UN parametro della strategia.\n"
        "Regola: non toccare piu' di un parametro (test scientifico).\n"
        "Se le metriche di sopravvivenza sono critiche, priorita' assoluta a quelle.\n"
        "Puoi anche proporre esplorazioni creative e fuori dagli schemi.\n\n"
        "Rispondi SOLO con un JSON object:\n"
        '{ "variable": "<dotted.path>", "current_value": <v>, "proposed_value": <v>, '
        '"rationale": "<una frase>", "predicted_score_delta": <float in [-1,1]>, '
        '"confidence": <float in [0,1]>, "priority": "survival|robustness|efficiency|exploration" }'
    )
    try:
        out = subprocess.run(
            ["hermes", "chat", "-q", prompt, "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("hermes CLI not on PATH — install hermes first") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"hermes chat failed: {e.stderr}") from e

    raw = out.stdout.strip()
    try:
        hyp = json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to find a JSON object inside the output
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            hyp = json.loads(raw[start : end + 1])
        else:
            raise RuntimeError(f"hermes returned non-JSON: {raw!r}") from e

    hyp["source"] = "hermes"
    return hyp


def reflect(mode: str) -> dict:
    state = state_dir()
    strategy = _load_strategy(state)
    goal = _load_goal(state)
    trades = _load_trades(state)

    if mode == "fallback":
        hyp = _fallback_hypothesis(trades, strategy, goal)
    elif mode == "hermes":
        hyp = _hermes_hypothesis(trades, strategy, goal)
    else:
        raise ValueError(f"unknown mode: {mode}")

    hyp["timestamp"] = _now_iso()
    hyp["prior_version"] = strategy.get("version", "??")
    hyp["trades_considered"] = len(trades)

    _archive_prior(state, strategy)
    _apply_to_strategy(strategy, hyp["variable"], hyp["proposed_value"])
    strategy["version"] = _bump_version(strategy.get("version", "01"))
    hyp["new_version"] = strategy["version"]

    _write_strategy(state, strategy)
    _append_hypothesis(state, hyp)

    # Also mirror updated strategy into the template so a fresh volume re-seeds with it.
    template = (
        Path("/app/state-template")
        if Path("/app/state-template").exists()
        else Path("state-template")
    )
    if template.exists():
        try:
            shutil.copy2(state / "strategy.yaml", template / "strategy.yaml")
        except Exception:  # noqa: BLE001
            pass

    print(
        f"reflection applied: {hyp['variable']} "
        f"{hyp['current_value']} -> {hyp['proposed_value']} "
        f"(v{hyp['prior_version']} -> v{hyp['new_version']})",
        flush=True,
    )
    return hyp


def main() -> None:
    parser = argparse.ArgumentParser(prog="hermes_trading.reflect")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fallback", action="store_true", help="Deterministic reflection")
    group.add_argument("--hermes", action="store_true", help="Hermes-driven reflection")
    args = parser.parse_args()

    mode = "fallback" if args.fallback else "hermes"
    reflect(mode)


if __name__ == "__main__":
    main()

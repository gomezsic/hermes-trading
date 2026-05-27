import shutil
from pathlib import Path


def seed_state_if_empty() -> None:
    """Seed the persistent state dir from state-template/ on first boot.

    Railway mounts /app/state as an empty volume that shadows anything
    COPYed there in the Dockerfile. We COPY initial files to
    /app/state-template instead and let this function populate the
    volume on startup only when it's empty. Locally we fall back to
    relative paths.
    """
    state = Path("/app/state") if Path("/app/state").exists() else Path("state")
    template = (
        Path("/app/state-template")
        if Path("/app/state-template").exists()
        else Path("state-template")
    )

    state.mkdir(parents=True, exist_ok=True)
    (state / "history").mkdir(exist_ok=True)

    for f in ["goal.yaml", "strategy.yaml"]:
        src = template / f
        dst = state / f
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    for f in ["trades.jsonl", "hypotheses.jsonl"]:
        (state / f).touch(exist_ok=True)

    hb = state / "heartbeat.json"
    if not hb.exists():
        hb.write_text(
            '{"last_tick": null, "trades_total": 0, "trades_open": 0, '
            '"last_error": null, "consecutive_failures": 0}'
        )

    pf = state / "portfolio.json"
    if not pf.exists():
        pf.write_text(
            '{"initial_capital": 100000.0, "balance": 100000.0, "peak_balance": 100000.0}'
        )


def state_dir() -> Path:
    return Path("/app/state") if Path("/app/state").exists() else Path("state")

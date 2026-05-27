from __future__ import annotations

import argparse
import asyncio
import sys

import yaml

from .bootstrap import seed_state_if_empty, state_dir
from .loop import run_loop


def main() -> None:
    seed_state_if_empty()

    parser = argparse.ArgumentParser(prog="hermes_trading")
    parser.add_argument("--asset", default=None, help="Override asset (else read from goal.yaml)")
    args = parser.parse_args()

    goal_path = state_dir() / "goal.yaml"
    goal = yaml.safe_load(goal_path.read_text())
    asset = args.asset or goal["asset"]

    print(f"Booting hermes-trading worker on {asset}", flush=True)
    try:
        asyncio.run(run_loop(asset))
    except KeyboardInterrupt:
        print("Worker stopped by signal.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()

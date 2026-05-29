"""
hermes-bt — CLI per la backtest_suite.

Comandi: fetch, run, grid, evolve, ui.
Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §11.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("hermes-bt")


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes-bt",
                                description="Backtest suite per hermes-trading.")
    sub = p.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("fetch", help="Scarica OHLCV nel data lake locale.")
    pf.add_argument("symbol", type=str)
    pf.add_argument("timeframe", choices=["1m", "5m", "15m", "1h", "4h", "1d"])
    pf.add_argument("--since", required=True, type=_parse_date)
    pf.add_argument("--until", required=True, type=_parse_date)
    pf.add_argument("--force-refresh", action="store_true")
    pf.add_argument("--root", type=Path, default=Path("data/ohlcv"))

    pr = sub.add_parser("run",    help="Esegui un singolo backtest da config.")
    pr.add_argument("config", type=Path)

    pg = sub.add_parser("grid",   help="Esegui una grid search da config.")
    pg.add_argument("config", type=Path)

    pe = sub.add_parser("evolve", help="Esegui un genetic algorithm da config.")
    pe.add_argument("config", type=Path)

    pu = sub.add_parser("ui",     help="(Plan D) Avvia FastAPI UI server.")
    pu.add_argument("--port", type=int, default=8765)
    pu.add_argument("--open", action="store_true")

    return p


def _cmd_fetch(args) -> int:
    from backtest_suite import data_lake
    log.info("Fetching %s %s [%s → %s]",
             args.symbol, args.timeframe, args.since, args.until)
    n = data_lake.fetch(args.symbol, args.timeframe, args.since, args.until,
                        force_refresh=args.force_refresh, root=args.root)
    print(f"Scaricate {n} candele.")
    return 0


def _cmd_not_yet(args) -> int:
    print(f"Comando '{args.command}' non ancora implementato (vedi Plan C/D).",
          file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "fetch":  _cmd_fetch,
        "run":    _cmd_not_yet,
        "grid":   _cmd_not_yet,
        "evolve": _cmd_not_yet,
        "ui":     _cmd_not_yet,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

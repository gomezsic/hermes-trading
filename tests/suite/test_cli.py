"""Test della CLI hermes-bt (argparse)."""
import sys
from unittest.mock import patch

import pytest

from backtest_suite.cli import build_parser, main


def test_parser_recognizes_fetch_command():
    p = build_parser()
    args = p.parse_args(["fetch", "BTCUSDT", "1h",
                         "--since", "2024-01-01", "--until", "2024-01-02"])
    assert args.command == "fetch"
    assert args.symbol == "BTCUSDT"
    assert args.timeframe == "1h"


@patch("backtest_suite.data_lake.fetch")
def test_main_fetch_calls_data_lake(mock_fetch, tmp_path):
    mock_fetch.return_value = 10
    rc = main([
        "fetch", "BTCUSDT", "1h",
        "--since", "2024-01-01", "--until", "2024-01-02",
        "--root", str(tmp_path),
    ])
    assert rc == 0
    mock_fetch.assert_called_once()


def test_parser_errors_on_unknown_command():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["nonexistent"])


import math

from backtest_suite.config import load_run_config
from backtest_suite.orchestrator import RunOrchestrator


def test_orchestrator_evolve_writes_run_to_db(tmp_path):
    # Sintetic candles direttamente in-memory (no data_lake fetch)
    candles = []
    for i in range(400):
        p = 100.0 + 10.0 * math.sin(i / 20.0) + i * 0.05
        candles.append({"t": i * 86400, "o": p, "h": p + 1, "l": p - 1, "c": p, "v": 100.0})

    cfg = load_run_config(__import__("pathlib").Path("tests/suite/fixtures/example_evolve.yaml"))
    # Override walk_forward + ga per tempi compatibili col test
    cfg.walk_forward.is_months = 2  # type: ignore[attr-defined]
    cfg.walk_forward.oos_months = 1  # type: ignore[attr-defined]
    cfg.walk_forward.step_months = 1  # type: ignore[attr-defined]
    cfg.walk_forward.min_trades_oos = 0  # type: ignore[attr-defined]
    cfg.walk_forward.max_drawdown_per_window = 1.0  # type: ignore[attr-defined]
    cfg.ga.n_generations = 2  # type: ignore[attr-defined]
    cfg.ga.pop_size = 4  # type: ignore[attr-defined]
    cfg.n_workers = 1  # type: ignore[attr-defined]

    orch = RunOrchestrator(
        config=cfg,
        candles=candles,
        db_path=tmp_path / "catalog.db",
        runs_dir=tmp_path / "runs",
    )
    result = orch.evolve()
    assert result["run_id"] >= 1
    assert result["status"] == "finished"

    # Verifica DB
    from backtest_suite.persistence.catalog_db import CatalogDB
    db = CatalogDB(tmp_path / "catalog.db")
    runs = db.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "finished"
    assert runs[0]["n_generations"] == 2

    top = db.top_individuals(runs[0]["id"], k=5)
    assert len(top) == 2  # un best per generazione (2 generazioni)

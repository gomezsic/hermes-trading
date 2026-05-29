"""Test del config loader pydantic + YAML."""
from pathlib import Path

import pytest

from backtest_suite.config import load_run_config, RunConfig, EvolveSpec, GridSpec


def test_load_evolve_yaml_parses_correctly():
    cfg = load_run_config(Path("tests/suite/fixtures/example_evolve.yaml"))
    assert isinstance(cfg, RunConfig)
    assert cfg.kind == "ga"
    assert cfg.symbol == "BTCUSDT"
    assert cfg.timeframe == "1h"
    assert isinstance(cfg.ga, EvolveSpec)
    assert cfg.ga.pop_size == 20
    assert cfg.ga.species_quotas["ema_cross"] == 0.5


def test_load_grid_yaml_parses_correctly():
    cfg = load_run_config(Path("tests/suite/fixtures/example_grid.yaml"))
    assert cfg.kind == "grid"
    assert isinstance(cfg.grid, GridSpec)
    assert cfg.grid.strategy_ids == ["ema_cross"]
    assert cfg.grid.max_combos == 100


def test_load_rejects_unknown_kind(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("kind: woops\nsymbol: X\ntimeframe: 1h\nrange:\n  since: '2024-01-01'\n  until: '2024-06-30'\n")
    with pytest.raises(Exception):
        load_run_config(bad)


def test_load_rejects_ga_kind_missing_ga_section(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("""
kind: ga
symbol: BTCUSDT
timeframe: 1h
range:
  since: "2024-01-01"
  until: "2024-06-30"
walk_forward:
  is_months: 3
  oos_months: 1
  step_months: 1
  min_trades_oos: 10
  max_drawdown_per_window: 0.3
""")
    with pytest.raises(Exception):
        load_run_config(bad)

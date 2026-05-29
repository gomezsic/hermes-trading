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

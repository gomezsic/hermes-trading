"""Test artifact_store: save/load equity + trades + manifest."""
from pathlib import Path

import yaml

from backtest_suite.engine.types import Trade
from backtest_suite.persistence.artifact_store import ArtifactStore


def test_save_and_load_equity_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    curve = [{"ts": i, "equity": 1000.0 + i, "drawdown_pct": 0.0} for i in range(50)]
    store.save_equity(run_id=1, individual_id="G050-001", curve=curve)
    loaded = store.load_equity(run_id=1, individual_id="G050-001")
    assert len(loaded) == 50
    assert loaded[0]["equity"] == 1000.0
    assert loaded[-1]["ts"] == 49


def test_save_and_load_trades_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    trades = [
        Trade(side="long", entry_idx=1, exit_idx=5, entry=100.0, exit=110.0,
              pnl_pct=0.10, pnl_pct_gross=0.105, fee_paid=0.0052,
              reason="forced_close", partial_done=False),
        Trade(side="short", entry_idx=10, exit_idx=12, entry=110.0, exit=105.0,
              pnl_pct=0.045, pnl_pct_gross=0.05, fee_paid=0.0052,
              reason="stop_loss", partial_done=True),
    ]
    store.save_trades(run_id=2, individual_id="G050-002", trades=trades)
    loaded = store.load_trades(run_id=2, individual_id="G050-002")
    assert len(loaded) == 2
    assert loaded[0]["side"] == "long"
    assert loaded[1]["reason"] == "stop_loss"
    assert loaded[1]["partial_done"] is True


def test_save_and_load_manifest_roundtrip(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    manifest = {
        "suite_version": "0.1.0",
        "git_commit":    "abcd123",
        "seed":          42,
        "config":        {"kind": "ga", "symbol": "BTCUSDT"},
    }
    store.save_manifest(run_id=3, manifest=manifest)
    loaded = store.load_manifest(run_id=3)
    assert loaded["seed"] == 42
    assert loaded["config"]["symbol"] == "BTCUSDT"

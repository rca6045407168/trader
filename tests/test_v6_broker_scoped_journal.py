"""Tests for the v6.0.x broker-scoped journal migration.

Three artifacts get broker-scoped:
  1. daily_snapshot table (composite PK on date+broker)
  2. deployment_anchor.json (dict keyed by broker)
  3. risk_freeze_state.json (dict keyed by broker)

This commit also migrates the legacy single-tenant formats on first
read so existing journals get tagged as 'alpaca_paper'.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# daily_snapshot composite PK + migration
# ============================================================
def test_daily_snapshot_migration_from_legacy(tmp_path, monkeypatch):
    """A pre-migration daily_snapshot (single-PK on date) gets
    rebuilt with (date, broker) composite PK and rows tagged as
    'alpaca_paper'."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE daily_snapshot (
            date TEXT PRIMARY KEY,
            equity REAL,
            cash REAL,
            positions_json TEXT,
            benchmark_spy_close REAL
        )
    """)
    con.execute(
        "INSERT INTO daily_snapshot VALUES ('2026-04-23', 100000, 50000, '{}', 500)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.config.DB_PATH", db)
    # Reload journal module so init_db uses the patched DB_PATH
    import importlib, trader.journal
    importlib.reload(trader.journal)
    trader.journal.init_db()

    con = sqlite3.connect(str(db))
    cols = {row[1] for row in con.execute("PRAGMA table_info(daily_snapshot)")}
    assert "broker" in cols
    row = con.execute(
        "SELECT date, broker, equity FROM daily_snapshot"
    ).fetchone()
    assert row == ("2026-04-23", "alpaca_paper", 100000.0)


def test_daily_snapshot_migration_is_idempotent(tmp_path, monkeypatch):
    """Running init_db twice doesn't corrupt the table."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE daily_snapshot (
            date TEXT PRIMARY KEY, equity REAL, cash REAL,
            positions_json TEXT, benchmark_spy_close REAL
        )
    """)
    con.execute(
        "INSERT INTO daily_snapshot VALUES ('2026-04-23', 100000, 0, '{}', 0)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.config.DB_PATH", db)
    import importlib, trader.journal
    importlib.reload(trader.journal)
    trader.journal.init_db()
    trader.journal.init_db()  # second call — should not re-migrate
    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT COUNT(*) FROM daily_snapshot").fetchone()[0]
    assert rows == 1


def test_log_daily_snapshot_uses_current_broker(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.config.DB_PATH", tmp_path / "j.db")
    monkeypatch.setenv("BROKER", "public_live")
    import importlib, trader.journal
    importlib.reload(trader.journal)
    trader.journal.log_daily_snapshot(equity=500, cash=100, positions={})
    con = sqlite3.connect(str(tmp_path / "j.db"))
    rows = con.execute(
        "SELECT broker, equity FROM daily_snapshot"
    ).fetchall()
    assert rows == [("public_live", 500.0)]


def test_recent_snapshots_filters_by_broker(tmp_path, monkeypatch):
    """When BROKER=public_live, recent_snapshots returns ONLY
    public_live rows, not the Alpaca-paper ones."""
    monkeypatch.setattr("trader.config.DB_PATH", tmp_path / "j.db")
    import importlib, trader.journal
    importlib.reload(trader.journal)
    # Seed both brokers
    monkeypatch.setenv("BROKER", "alpaca_paper")
    trader.journal.log_daily_snapshot(equity=100000, cash=0, positions={})
    monkeypatch.setenv("BROKER", "public_live")
    trader.journal.log_daily_snapshot(equity=20, cash=20, positions={})

    # alpaca_paper view
    monkeypatch.setenv("BROKER", "alpaca_paper")
    alp = trader.journal.recent_snapshots(days=30)
    assert len(alp) == 1
    assert alp[0]["equity"] == 100000.0

    # public_live view
    monkeypatch.setenv("BROKER", "public_live")
    pub = trader.journal.recent_snapshots(days=30)
    assert len(pub) == 1
    assert pub[0]["equity"] == 20.0

    # 'all' view
    all_snaps = trader.journal.recent_snapshots(days=30, broker="all")
    assert len(all_snaps) == 2


# ============================================================
# deployment_anchor: dict-by-broker + legacy migration
# ============================================================
def test_anchor_migrates_legacy_format(tmp_path, monkeypatch):
    legacy = tmp_path / "deployment_anchor.json"
    legacy.write_text(json.dumps({
        "equity_at_deploy": 100000.0,
        "deploy_timestamp": "2026-04-30T20:39:05.882518",
        "source": "auto",
        "notes": "auto-set",
    }))
    monkeypatch.setenv("BROKER", "alpaca_paper")
    # Patch AFTER any import — reload would reset the module-level path
    from trader import deployment_anchor as da
    monkeypatch.setattr(da, "ANCHOR_PATH", legacy)
    a = da.load_anchor()
    assert a is not None
    assert a.equity_at_deploy == 100000.0
    # File should be rewritten in dict form
    new_data = json.loads(legacy.read_text())
    assert "alpaca_paper" in new_data
    assert new_data["alpaca_paper"]["equity_at_deploy"] == 100000.0


def test_anchor_auto_sets_per_broker(tmp_path, monkeypatch):
    """get_or_set_anchor under BROKER=public_live should NOT clobber
    or read alpaca_paper's anchor."""
    f = tmp_path / "deployment_anchor.json"
    from trader import deployment_anchor as da
    monkeypatch.setattr(da, "ANCHOR_PATH", f)
    # Seed alpaca_paper at $100k
    monkeypatch.setenv("BROKER", "alpaca_paper")
    a1 = da.get_or_set_anchor(100000.0)
    assert a1.equity_at_deploy == 100000.0
    # Now switch to public_live with much smaller equity
    monkeypatch.setenv("BROKER", "public_live")
    a2 = da.get_or_set_anchor(20.0)
    assert a2.equity_at_deploy == 20.0
    # alpaca_paper untouched
    monkeypatch.setenv("BROKER", "alpaca_paper")
    a3 = da.load_anchor()
    assert a3.equity_at_deploy == 100000.0


def test_anchor_drawdown_per_broker(tmp_path, monkeypatch):
    """drawdown_from_deployment under each broker uses that broker's
    anchor, not the other broker's."""
    f = tmp_path / "deployment_anchor.json"
    from trader import deployment_anchor as da
    monkeypatch.setattr(da, "ANCHOR_PATH", f)
    monkeypatch.setenv("BROKER", "alpaca_paper")
    da.get_or_set_anchor(100000.0)
    monkeypatch.setenv("BROKER", "public_live")
    da.get_or_set_anchor(20.0)
    # public_live at $20 vs its own $20 anchor = 0% drawdown
    dd, _ = da.drawdown_from_deployment(20.0)
    assert abs(dd) < 1e-9


# ============================================================
# risk_freeze_state: dict-by-broker
# ============================================================
def test_freeze_state_migrates_legacy_format(tmp_path, monkeypatch):
    legacy = tmp_path / "risk_freeze_state.json"
    legacy.write_text(json.dumps({
        "liquidation_gate_tripped": True,
        "liquidation_tripped_at": "2026-05-12T17:29:55.180149",
    }))
    monkeypatch.setattr("trader.risk_manager.FREEZE_STATE_PATH", legacy)
    monkeypatch.setenv("BROKER", "alpaca_paper")
    from trader.risk_manager import _read_all_freeze, _load_freeze_state
    all_data = _read_all_freeze()
    assert "alpaca_paper" in all_data
    assert all_data["alpaca_paper"]["liquidation_gate_tripped"] is True
    # Under public_live, no freeze state
    monkeypatch.setenv("BROKER", "public_live")
    public_state = _load_freeze_state()
    assert public_state == {}


def test_freeze_state_save_preserves_other_broker(tmp_path, monkeypatch):
    f = tmp_path / "risk_freeze_state.json"
    monkeypatch.setattr("trader.risk_manager.FREEZE_STATE_PATH", f)
    from trader.risk_manager import _save_freeze_state, _load_freeze_state
    monkeypatch.setenv("BROKER", "alpaca_paper")
    _save_freeze_state({"liquidation_gate_tripped": True})
    monkeypatch.setenv("BROKER", "public_live")
    _save_freeze_state({"daily_loss_freeze_until": "2026-05-13T00:00:00"})
    # alpaca_paper state still intact
    monkeypatch.setenv("BROKER", "alpaca_paper")
    assert _load_freeze_state() == {"liquidation_gate_tripped": True}
    monkeypatch.setenv("BROKER", "public_live")
    assert _load_freeze_state() == {"daily_loss_freeze_until": "2026-05-13T00:00:00"}


# ============================================================
# main.py wires the broker filter for the snapshot read
# ============================================================
def test_main_filters_snapshots_by_broker():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # The direct SQL read in main.py must now include the broker filter
    assert "WHERE equity > 0 AND broker = ?" in txt
    assert "from .journal import _conn, _current_broker" in txt

"""Unit tests for journal. Verifies SQLite writes round-trip correctly."""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.db"
        monkeypatch.setattr("trader.journal.DB_PATH", path)
        yield path


def test_log_decision_round_trip():
    from trader.journal import log_decision, recent_decisions
    log_decision("AAPL", "BUY", "MOMENTUM", 0.25, {"trailing_return": 0.25}, None, "AUTO_BUY")
    rows = recent_decisions(days=1)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["action"] == "BUY"
    assert rows[0]["style"] == "MOMENTUM"


def test_log_order_with_error():
    from trader.journal import log_order, _conn
    log_order("NVDA", "BUY", 5000, None, "error", "market closed")
    with _conn() as c:
        rows = c.execute("SELECT * FROM orders").fetchall()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["error"] == "market closed"


def test_log_daily_snapshot():
    from trader.journal import log_daily_snapshot, recent_snapshots
    log_daily_snapshot(equity=100_500, cash=50_000, positions={"AAPL": 50_500})
    snaps = recent_snapshots(days=1)
    assert len(snaps) == 1
    assert snaps[0]["equity"] == 100_500


def test_log_daily_snapshot_replaces_same_day():
    from trader.journal import log_daily_snapshot, recent_snapshots
    log_daily_snapshot(equity=100_000, cash=50_000, positions={})
    log_daily_snapshot(equity=101_000, cash=51_000, positions={})
    snaps = recent_snapshots(days=1)
    assert len(snaps) == 1
    assert snaps[0]["equity"] == 101_000

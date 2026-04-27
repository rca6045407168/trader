"""Tests for v1.3 fixes: runs sentinel (B5) and position_lots (B1, B7)."""
import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.db"
        monkeypatch.setattr("trader.journal.DB_PATH", path)
        yield path


def test_start_run_blocks_duplicate_same_day():
    from trader.journal import start_run
    assert start_run("2026-04-27-100000") is True
    # second start on the same day should be blocked
    assert start_run("2026-04-27-130000") is False


def test_finish_run_marks_completed():
    from trader.journal import start_run, finish_run, _conn
    start_run("2026-04-27-100000")
    finish_run("2026-04-27-100000", status="completed", notes="all good")
    with _conn() as c:
        row = c.execute("SELECT status, notes FROM runs WHERE run_id = '2026-04-27-100000'").fetchone()
    assert row["status"] == "completed"
    assert row["notes"] == "all good"


def test_open_and_close_lot_tracks_realized_pnl():
    from trader.journal import open_lot, close_lots_fifo, _conn
    open_lot("NVDA", "BOTTOM_CATCH", qty=10, open_price=100.00, open_order_id="order1")
    closes = close_lots_fifo("NVDA", "BOTTOM_CATCH", qty=10, close_price=110.00)
    assert len(closes) == 1
    assert closes[0]["realized_pnl"] == 100.00  # 10 shares * $10 gain


def test_partial_close_keeps_remainder_open():
    from trader.journal import open_lot, close_lots_fifo, _conn
    open_lot("NVDA", "BOTTOM_CATCH", qty=10, open_price=100.00)
    close_lots_fifo("NVDA", "BOTTOM_CATCH", qty=4, close_price=110.00)
    with _conn() as c:
        open_remainder = c.execute(
            "SELECT qty FROM position_lots WHERE symbol='NVDA' AND closed_at IS NULL"
        ).fetchone()
    assert open_remainder["qty"] == 6


def test_open_lots_for_sleeve_filters_by_age():
    from trader.journal import open_lot, open_lots_for_sleeve, _conn
    open_lot("NVDA", "BOTTOM_CATCH", qty=10, open_price=100)
    # backdate the lot to 30 days ago
    with _conn() as c:
        c.execute("UPDATE position_lots SET opened_at = datetime('now', '-30 days') WHERE symbol='NVDA'")
    aged = open_lots_for_sleeve("BOTTOM_CATCH", max_age_days=20)
    assert len(aged) == 1
    fresh_only = open_lots_for_sleeve("BOTTOM_CATCH", max_age_days=60)
    assert len(fresh_only) == 0


def test_lots_dont_cross_sleeves():
    from trader.journal import open_lot, open_lots_for_sleeve
    open_lot("NVDA", "MOMENTUM", qty=20, open_price=100)
    open_lot("NVDA", "BOTTOM_CATCH", qty=10, open_price=95)
    bot = open_lots_for_sleeve("BOTTOM_CATCH")
    assert len(bot) == 1
    assert bot[0]["qty"] == 10
    mom = open_lots_for_sleeve("MOMENTUM")
    assert len(mom) == 1
    assert mom[0]["qty"] == 20

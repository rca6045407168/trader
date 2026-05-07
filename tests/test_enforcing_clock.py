"""v3.73.25 — tests for the ENFORCING-mode 30-run clock."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def _seed_runs(db_path: Path, rows):
    """rows: list of (run_id, started_at, status). Inserts into the runs table."""
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS runs ("
        "run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "completed_at TEXT, status TEXT NOT NULL, notes TEXT)"
    )
    for run_id, started, status in rows:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, status) "
            "VALUES (?, ?, ?)",
            (run_id, started, status),
        )
    con.commit()
    con.close()


def test_clock_zero_runs(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.journal.DB_PATH", db)
    from trader.enforcing_clock import get_status
    s = get_status(start_date="2026-05-06")
    assert s.completed_runs == 0
    assert s.halted_runs == 0
    assert s.streak_clean is True
    assert s.gate_cleared is False


def test_clock_counts_completed_runs(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.journal.DB_PATH", db)
    _seed_runs(db, [
        ("2026-05-06-1", "2026-05-06T14:30:00", "completed"),
        ("2026-05-07-1", "2026-05-07T14:30:00", "completed"),
        ("2026-05-08-1", "2026-05-08T14:30:00", "completed"),
        # Pre-window run shouldn't count
        ("2026-05-01-1", "2026-05-01T14:30:00", "completed"),
    ])
    from trader.enforcing_clock import get_status
    s = get_status(start_date="2026-05-06")
    assert s.completed_runs == 3
    assert s.streak_clean is True


def test_clock_halted_run_breaks_streak(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.journal.DB_PATH", db)
    _seed_runs(db, [
        ("2026-05-06-1", "2026-05-06T14:30:00", "completed"),
        ("2026-05-07-1", "2026-05-07T14:30:00", "halted"),
        ("2026-05-08-1", "2026-05-08T14:30:00", "completed"),
    ])
    from trader.enforcing_clock import get_status
    s = get_status(start_date="2026-05-06")
    assert s.completed_runs == 2
    assert s.halted_runs == 1
    assert s.streak_clean is False
    assert s.gate_cleared is False


def test_gate_cleared_at_thirty_clean(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.journal.DB_PATH", db)
    rows = [
        (f"2026-06-{(i % 28)+1:02d}-{i}",
         f"2026-06-{(i % 28)+1:02d}T14:30:00", "completed")
        for i in range(30)
    ]
    _seed_runs(db, rows)
    from trader.enforcing_clock import get_status
    s = get_status(start_date="2026-05-06")
    assert s.completed_runs == 30
    assert s.gate_cleared is True


def test_render_summary_format(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.journal.DB_PATH", db)
    _seed_runs(db, [
        ("2026-05-06-1", "2026-05-06T14:30:00", "completed"),
    ])
    from trader.enforcing_clock import get_status
    s = get_status(start_date="2026-05-06")
    summary = s.render_summary()
    assert "1/30" in summary
    assert "🟢" in summary
    assert "clean" in summary

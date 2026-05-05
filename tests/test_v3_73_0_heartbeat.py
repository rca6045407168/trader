"""Tests for v3.73.0 — daily-orchestrator heartbeat alert.

Per ROUND_2_SYNTHESIS Block A item #6: detect when the daily run
silently didn't fire.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
ET = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _seed_runs_table(db: Path, rows: list[tuple[str, str | None]]):
    """rows = [(started_at, completed_at_or_None), ...]"""
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                notes TEXT
            )
        """)
        for i, (started, completed) in enumerate(rows):
            status = "completed" if completed else "started"
            c.execute(
                "INSERT INTO runs (run_id, started_at, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                (f"r{i}", started, completed, status))
        c.commit()


def test_check_returns_skip_on_weekend(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.check_daily_heartbeat.JOURNAL_DB",
                         tmp_path / "j.db", raising=False)
    monkeypatch.setattr("scripts.check_daily_heartbeat.MARKER_FILE",
                         tmp_path / ".marker", raising=False)
    from scripts import check_daily_heartbeat as h
    monkeypatch.setattr(h, "JOURNAL_DB", tmp_path / "j.db")
    monkeypatch.setattr(h, "MARKER_FILE", tmp_path / ".marker")

    saturday = datetime(2026, 5, 9, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=saturday, dry_run=True)
    assert out["status"] == "skip"
    assert "non-trading day" in out["reason"]


def test_check_returns_skip_on_holiday(tmp_path, monkeypatch):
    from scripts import check_daily_heartbeat as h
    monkeypatch.setattr(h, "JOURNAL_DB", tmp_path / "j.db")
    monkeypatch.setattr(h, "MARKER_FILE", tmp_path / ".marker")

    # Memorial Day 2026 = Mon May 25
    holiday = datetime(2026, 5, 25, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=holiday, dry_run=True)
    assert out["status"] == "skip"


def test_check_returns_ok_when_run_started_today(tmp_path, monkeypatch):
    from scripts import check_daily_heartbeat as h
    db = tmp_path / "j.db"
    monkeypatch.setattr(h, "JOURNAL_DB", db)
    monkeypatch.setattr(h, "MARKER_FILE", tmp_path / ".marker")

    # Tuesday 2026-04-14 11am ET = trading day, RTH
    today_iso = "2026-04-14"
    _seed_runs_table(db, [(f"{today_iso}T13:10:00", None)])
    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=now, dry_run=True)
    assert out["status"] == "ok"
    assert out["fired_today"] is True


def test_check_alerts_when_no_run_today(tmp_path, monkeypatch):
    from scripts import check_daily_heartbeat as h
    db = tmp_path / "j.db"
    monkeypatch.setattr(h, "JOURNAL_DB", db)
    monkeypatch.setattr(h, "MARKER_FILE", tmp_path / ".marker")

    # Last run 5 days ago; today should alert
    _seed_runs_table(db, [
        ("2026-04-09T13:10:00", "2026-04-09T13:11:00"),
    ])
    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)  # Tue
    out = h.check_heartbeat(now=now, dry_run=True)
    assert out["status"] == "alert"
    assert "no daily run started today" in out["reason"]


def test_check_alerts_when_runs_table_missing(tmp_path, monkeypatch):
    """Fresh install — no journal.db exists. Should alert (we expect
    a run on a trading day)."""
    from scripts import check_daily_heartbeat as h
    monkeypatch.setattr(h, "JOURNAL_DB", tmp_path / "missing.db")
    monkeypatch.setattr(h, "MARKER_FILE", tmp_path / ".marker")

    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=now, dry_run=True)
    assert out["status"] == "alert"
    assert out["last_started_at"] is None


def test_idempotent_within_day(tmp_path, monkeypatch):
    """If we already alerted today, second invocation must NOT
    re-alert — preserves SMTP rate and avoids spam."""
    from scripts import check_daily_heartbeat as h
    db = tmp_path / "j.db"
    marker = tmp_path / ".marker"
    monkeypatch.setattr(h, "JOURNAL_DB", db)
    monkeypatch.setattr(h, "MARKER_FILE", marker)

    _seed_runs_table(db, [("2026-04-09T13:10:00", None)])
    today = "2026-04-14"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(today)

    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=now, dry_run=True)
    assert out["status"] == "already_alerted"


def test_marker_resets_on_new_day(tmp_path, monkeypatch):
    """Marker dated yesterday must NOT suppress today's alert."""
    from scripts import check_daily_heartbeat as h
    db = tmp_path / "j.db"
    marker = tmp_path / ".marker"
    monkeypatch.setattr(h, "JOURNAL_DB", db)
    monkeypatch.setattr(h, "MARKER_FILE", marker)

    _seed_runs_table(db, [("2026-04-09T13:10:00", None)])
    # Marker dated yesterday (2026-04-13)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("2026-04-13")

    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)
    out = h.check_heartbeat(now=now, dry_run=True)
    # Yesterday's marker should not suppress today's alert
    assert out["status"] == "alert"


def test_dry_run_does_not_write_marker(tmp_path, monkeypatch):
    """Dry-run path must NOT mutate the marker file (so manual audits
    don't clobber the real-run state)."""
    from scripts import check_daily_heartbeat as h
    db = tmp_path / "j.db"
    marker = tmp_path / ".marker"
    monkeypatch.setattr(h, "JOURNAL_DB", db)
    monkeypatch.setattr(h, "MARKER_FILE", marker)

    _seed_runs_table(db, [("2026-04-09T13:10:00", None)])
    now = datetime(2026, 4, 14, 11, 0, tzinfo=ET)
    h.check_heartbeat(now=now, dry_run=True)
    assert not marker.exists()


# ============================================================
# Plist + script wiring
# ============================================================
def test_plist_fires_only_on_weekdays():
    import plistlib
    p = ROOT / "infra" / "launchd" / "com.trader.daily-heartbeat.plist"
    with open(p, "rb") as f:
        d = plistlib.load(f)
    assert d["Label"] == "com.trader.daily-heartbeat"
    cal = d["StartCalendarInterval"]
    assert isinstance(cal, list)
    weekdays = sorted(entry["Weekday"] for entry in cal)
    assert weekdays == [1, 2, 3, 4, 5]  # Mon-Fri only


def test_plist_runs_after_daily_orchestrator_window():
    """14:30 UTC = 10:30 ET, which is well after the 13:10 UTC
    (= 9:10 ET) daily-run trigger. Healthy daily runs have ~80
    minutes to complete before the heartbeat fires."""
    import plistlib
    p = ROOT / "infra" / "launchd" / "com.trader.daily-heartbeat.plist"
    with open(p, "rb") as f:
        d = plistlib.load(f)
    cal = d["StartCalendarInterval"]
    for entry in cal:
        assert entry["Hour"] == 14
        assert entry["Minute"] == 30


def test_plist_logs_to_user_logs():
    """Per OpenClaw safety + privacy rule — logs to ~/Library/Logs,
    not /tmp. Same pattern as v3.65 onwards."""
    import plistlib
    p = ROOT / "infra" / "launchd" / "com.trader.daily-heartbeat.plist"
    with open(p, "rb") as f:
        d = plistlib.load(f)
    assert "/Library/Logs/" in d["StandardOutPath"]
    assert "/Library/Logs/" in d["StandardErrorPath"]


def test_script_exists_and_has_dry_run():
    p = ROOT / "scripts" / "check_daily_heartbeat.py"
    assert p.exists()
    text = p.read_text()
    assert "--dry-run" in text
    # Reuses notify infra, doesn't reimplement
    assert "from trader.notify import notify" in text
    # Reuses market_session for trading-day detection
    assert "from trader.market_session import market_session_now" in text

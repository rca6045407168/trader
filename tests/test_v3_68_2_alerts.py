"""Tests for v3.68.2 — email alerts for material reactor signals."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Schema migration
# ============================================================
def test_signals_table_has_notified_at_column(tmp_path):
    """v3.68.2 added notified_at via ALTER TABLE migration."""
    from trader.earnings_reactor import _ensure_signals_table
    db = tmp_path / "j.db"
    _ensure_signals_table(db)
    with sqlite3.connect(db) as c:
        cols = [row[1] for row in c.execute(
            "PRAGMA table_info(earnings_signals)").fetchall()]
    assert "notified_at" in cols


def test_migration_idempotent(tmp_path):
    """Calling _ensure_signals_table twice must NOT raise (the
    duplicate-column error from ALTER TABLE is caught)."""
    from trader.earnings_reactor import _ensure_signals_table
    db = tmp_path / "j.db"
    _ensure_signals_table(db)
    _ensure_signals_table(db)  # second call must be a no-op


# ============================================================
# Body / subject formatters — pure, no SMTP
# ============================================================
def test_format_alert_body_meets_anti_stub_min_length():
    """trader.notify._is_stub refuses bodies <80 chars. Our body must
    always exceed that even for sparse signals."""
    from trader.earnings_reactor import (
        ReactionResult, _format_alert_body,
    )
    r = ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-01",
        items=["2.02"], direction="BEARISH", materiality=3,
        guidance_change="LOWERED", surprise_direction="MISSED",
        summary="Q1 missed consensus and management lowered FY guidance.",
        bullish_quotes=[],
        bearish_quotes=['"We are reducing our FY26 outlook"'],
        model="claude-sonnet-4-6",
    )
    body = _format_alert_body(r)
    assert len(body) >= 80
    # Includes all the structured fields
    assert "M3" in body
    assert "BEARISH" in body
    assert "LOWERED" in body
    assert "Q1 missed consensus" in body
    assert "FY26 outlook" in body
    assert "A1" in body  # accession


def test_short_summary_for_subject_trims_long_summaries():
    from trader.earnings_reactor import _short_summary_for_subject
    long_s = "A " * 100
    short = _short_summary_for_subject(long_s, max_chars=50)
    assert len(short) <= 50
    assert short.endswith("…")


def test_short_summary_passthrough_for_short_input():
    from trader.earnings_reactor import _short_summary_for_subject
    assert _short_summary_for_subject("Q1 beat") == "Q1 beat"


# ============================================================
# Threshold gating + idempotency
# ============================================================
def test_alert_skipped_below_threshold(tmp_path, monkeypatch):
    """M2 (below default M3 threshold) must NOT trigger an alert."""
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, _maybe_alert,
    )
    db = tmp_path / "j.db"
    r = ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-01",
        materiality=2, direction="NEUTRAL", summary="minor",
        items=[], model="claude-sonnet-4-6",
    )
    _persist_signal(db, r)
    # Even if SMTP_USER is set, threshold gate should refuse
    monkeypatch.setenv("SMTP_USER", "fake@example.com")
    monkeypatch.setenv("SMTP_PASS", "fake-pass")
    sent = _maybe_alert(r, db, min_materiality=3)
    assert sent is False


def test_alert_skipped_when_signal_has_error(tmp_path):
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, _maybe_alert,
    )
    db = tmp_path / "j.db"
    r = ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-01",
        materiality=5, direction="SURPRISE", summary="bad",
        items=[], model="claude-sonnet-4-6",
        error="claude API rate limited",
    )
    _persist_signal(db, r)
    sent = _maybe_alert(r, db, min_materiality=3)
    assert sent is False


def test_alert_idempotent_per_accession(tmp_path, monkeypatch):
    """Once notified_at is set, _maybe_alert is a no-op for the same
    (symbol, accession) — even if another reactor run encounters the
    same signal."""
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, _maybe_alert,
    )
    db = tmp_path / "j.db"
    r = ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-01",
        materiality=4, direction="BEARISH",
        summary="material event",
        items=["2.02"], model="claude-sonnet-4-6",
    )
    _persist_signal(db, r)

    # Manually mark notified to simulate "already sent"
    from datetime import datetime as _dt
    with sqlite3.connect(db) as c:
        c.execute(
            "UPDATE earnings_signals SET notified_at = ? "
            "WHERE symbol = ? AND accession = ?",
            (_dt.utcnow().isoformat(), "X", "A1"))
        c.commit()

    # Even with valid SMTP env + threshold met, must skip
    monkeypatch.setenv("SMTP_USER", "fake@example.com")
    monkeypatch.setenv("SMTP_PASS", "fake-pass")
    sent = _maybe_alert(r, db, min_materiality=3)
    assert sent is False


def test_threshold_env_default_is_3(monkeypatch):
    monkeypatch.delenv("REACTOR_ALERT_MIN_MATERIALITY", raising=False)
    from trader.earnings_reactor import _alert_threshold
    assert _alert_threshold() == 3


def test_threshold_env_overridable(monkeypatch):
    monkeypatch.setenv("REACTOR_ALERT_MIN_MATERIALITY", "4")
    from trader.earnings_reactor import _alert_threshold
    assert _alert_threshold() == 4


def test_threshold_env_handles_garbage(monkeypatch):
    """Bad env value falls back to 3 instead of raising."""
    monkeypatch.setenv("REACTOR_ALERT_MIN_MATERIALITY", "not-a-number")
    from trader.earnings_reactor import _alert_threshold
    assert _alert_threshold() == 3


# ============================================================
# Backfill helper
# ============================================================
def test_alert_unsent_signals_filters_by_threshold(tmp_path, monkeypatch):
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, alert_unsent_signals,
    )
    db = tmp_path / "j.db"
    # Two signals: one M2 (below threshold), one M4 (above)
    _persist_signal(db, ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-01",
        materiality=2, direction="NEUTRAL", summary="too small",
        items=[], model="m"))
    _persist_signal(db, ReactionResult(
        symbol="Y", accession="A2", filed_at="2026-05-01",
        materiality=4, direction="BEARISH",
        summary="material",
        items=["2.02"], model="m"))
    # Without SMTP env, _maybe_alert returns False — but the FILTER
    # logic must still only consider the M4 row (we test the filter
    # by mocking the send to always succeed)
    monkeypatch.setattr(
        "trader.earnings_reactor._maybe_alert",
        lambda r, db_path, min_materiality=3: True,  # mock send-success
    )
    sent = alert_unsent_signals(journal_db=db, since_days=365,
                                  min_materiality=3)
    # Only the M4 row is eligible (M2 filtered out by min_materiality)
    assert len(sent) == 1
    assert sent[0] == ("Y", "A2")


# ============================================================
# CLI flags
# ============================================================
def test_cli_supports_no_alerts_flag():
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    assert "--no-alerts" in text
    assert "alert=not args.no_alerts" in text


def test_cli_supports_backfill_alerts_flag():
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    assert "--backfill-alerts" in text
    assert "alert_unsent_signals" in text


def test_dashboard_version_v3_68_2():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.68.2 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.68.2" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"

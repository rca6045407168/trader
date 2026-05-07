"""Tests for v3.69.1 — Slack push alongside email.

Covers webhook gating (no env → no-op), payload shape (Block Kit with
header + section), independent channel delivery, idempotency-counts-
either-channel semantics, and doc presence.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# _send_slack — gating + payload shape
# ============================================================
def test_slack_no_op_when_webhook_unset(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK", raising=False)
    # Also clear the module-level constant in case it was set at import
    import trader.notify as n
    monkeypatch.setattr(n, "SLACK_WEBHOOK", "")
    assert n._send_slack("subj", "body" * 30) is False


def test_slack_payload_shape(monkeypatch):
    """Verify the Block Kit envelope we POST has header + section,
    plus a fallback `text` for clients that don't render Block Kit."""
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["method"] = req.method
        captured["headers"] = dict(req.headers)
        return FakeResp()

    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         fake_urlopen)

    import trader.notify as n
    ok = n._send_slack(
        subject="[trader] INTC M3 BEARISH",
        body=("Symbol: INTC\nMateriality: M3\nSummary: Intel raised "
              "$6.5B in senior unsecured notes ..."),
        level="info",
    )
    assert ok is True
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"

    payload = json.loads(captured["data"].decode("utf-8"))
    # Block Kit: header + section
    assert "blocks" in payload
    types = [b["type"] for b in payload["blocks"]]
    assert types == ["header", "section"]
    # Header text includes level emoji + subject
    assert "INTC" in payload["blocks"][0]["text"]["text"]
    # Section uses mrkdwn + wraps body in code block
    assert payload["blocks"][1]["text"]["type"] == "mrkdwn"
    assert "```" in payload["blocks"][1]["text"]["text"]
    assert "Intel raised $6.5B" in payload["blocks"][1]["text"]["text"]
    # Fallback `text` field present (for clients that don't render Block Kit)
    assert "text" in payload


def test_slack_truncates_long_body(monkeypatch):
    """Slack section text caps near 3000 chars; we trim cleanly."""
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["data"] = req.data
        return FakeResp()

    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         fake_urlopen)

    import trader.notify as n
    n._send_slack("subject", "x" * 10_000)
    payload = json.loads(captured["data"].decode("utf-8"))
    section_text = payload["blocks"][1]["text"]["text"]
    assert "[truncated]" in section_text
    # Whole section text well under Slack's 3000-char ceiling
    assert len(section_text) < 3000


def test_slack_handles_network_error(monkeypatch):
    """Slack outage / firewall must not raise — return False so
    email still delivers."""
    import urllib.error
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         fake_urlopen)
    import trader.notify as n
    assert n._send_slack("subj", "body" * 30) is False


def test_slack_handles_http_error(monkeypatch):
    """Bad webhook URL → 404. Must return False, not raise."""
    import urllib.error
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/bad")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         fake_urlopen)
    import trader.notify as n
    assert n._send_slack("subj", "body" * 30) is False


# ============================================================
# notify() — dual channel
# ============================================================
def test_notify_returns_status_per_channel(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    # Both env AND module-level constant must be cleared — _send_email
    # falls back to the module constant (loaded from .env at import) if
    # the env var is unset. Tests can't see the real .env reliably.
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    import trader.notify as n
    monkeypatch.setattr(n, "SMTP_USER", "")
    monkeypatch.setattr(n, "SMTP_PASS", "")

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         lambda req, timeout=None: FakeResp())

    from trader.notify import notify
    result = notify(
        "Substantive body that exceeds 80 chars to bypass the anti-stub guard. "
        "Reactor flagged a material event for review.",
        subject="[trader] test",
    )
    # Email creds missing → email=False, Slack mocked → slack=True
    assert result["email"] is False
    assert result["slack"] is True
    assert result["console"] is True


def test_notify_email_failure_doesnt_block_slack(monkeypatch):
    """Channel independence: SMTP outage must not prevent Slack delivery."""
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    monkeypatch.setenv("SMTP_USER", "fake@example.com")
    monkeypatch.setenv("SMTP_PASS", "fake-pass")

    # Force email to fail
    import smtplib
    def boom(*args, **kwargs):
        raise smtplib.SMTPException("simulated SMTP outage")
    monkeypatch.setattr("trader.notify.smtplib.SMTP", boom)

    # Slack succeeds
    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         lambda req, timeout=None: FakeResp())

    from trader.notify import notify
    result = notify(
        "Substantive body that exceeds 80 chars of real content for the "
        "anti-stub guard. Reactor flagged a material event for review.",
        subject="[trader] failover test",
    )
    assert result["email"] is False
    assert result["slack"] is True


def test_notify_stub_guard_refuses_both_channels(monkeypatch):
    """Anti-stub guard fires before either channel is touched."""
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    from trader.notify import notify
    result = notify("hi", subject="stub")  # < 80 chars → refused
    assert result["email"] is False
    assert result["slack"] is False
    assert "refused" in result


# ============================================================
# Reactor alert idempotency — either channel counts
# ============================================================
def test_reactor_alert_marks_notified_when_only_slack_succeeds(
    tmp_path, monkeypatch,
):
    """The notified_at gate must NOT require BOTH channels to succeed.
    If user has Slack but no email, alerts shouldn't loop forever."""
    monkeypatch.setenv("SLACK_WEBHOOK",
                        "https://hooks.slack.com/services/T/B/xxx")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    import trader.notify as n
    monkeypatch.setattr(n, "SMTP_USER", "")
    monkeypatch.setattr(n, "SMTP_PASS", "")

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("trader.notify.urllib.request.urlopen",
                         lambda req, timeout=None: FakeResp())

    db = tmp_path / "j.db"
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, _maybe_alert,
    )
    r = ReactionResult(
        symbol="X", accession="A1", filed_at="2026-05-04",
        materiality=4, direction="BEARISH",
        summary="material event substantive enough to bypass stub guard "
                "with summary detail describing the situation in real terms",
        items=["2.02"], model="m",
    )
    _persist_signal(db, r)
    sent = _maybe_alert(r, db, min_materiality=3)
    assert sent is True

    # Verify notified_at populated → idempotent on re-run
    with sqlite3.connect(db) as c:
        notified = c.execute(
            "SELECT notified_at FROM earnings_signals "
            "WHERE accession = 'A1'").fetchone()[0]
    assert notified is not None
    # Re-run is no-op
    sent_again = _maybe_alert(r, db, min_materiality=3)
    assert sent_again is False


# ============================================================
# Docs
# ============================================================
def test_dashboard_version_v3_69_1():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.69.1 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.69.1" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"

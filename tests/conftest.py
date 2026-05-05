"""Pytest test guards.

Hard-prevents tests from sending real emails or Slack messages, no
matter how a future test is written.

The v3.69.1 incident: a test cleared SMTP env via monkeypatch.delenv
but didn't clear the module-level SMTP_USER constant. trader.notify._
send_email's `_env("SMTP_USER", SMTP_USER)` fell back to the module
constant (loaded from .env at import) and a real email went out
through real SMTP carrying test fixture data.

Fix: a per-test autouse fixture that clears BOTH (a) env vars AND
(b) module constants, every test. A test that legitimately wants to
exercise the full email/slack path overrides via monkeypatch.setenv
and monkeypatch.setattr — those land AFTER this autouse fixture,
inside the test's scope, so test-level mocks always win.

We deliberately do NOT clear via a session fixture: trader.config
calls load_dotenv() at import time, which can RE-populate env vars
after a session-scoped clear if a test triggers a fresh import.
Per-test scope is the only reliable layer.
"""
from __future__ import annotations

import pytest


_NOTIFY_ENV_KEYS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
    "EMAIL_TO", "EMAIL_FROM", "SLACK_WEBHOOK",
)


@pytest.fixture(autouse=True)
def _block_real_notifications(monkeypatch):
    """Per-test guard: clear env + module constants so notify.* short-
    circuits at the credential check. Tests that want the success
    path can override with their own monkeypatch.setenv +
    monkeypatch.setattr — those run AFTER this autouse fixture
    inside the test scope and win on conflict."""
    # 1. Strip env vars (works even if config.load_dotenv repopulated
    # them at import time — monkeypatch.delenv lands at function scope)
    for k in _NOTIFY_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)

    # 2. Clear module-level constants so the credential-fallback in
    # _send_email and _send_slack short-circuits to "no creds → return
    # False" without opening a socket
    try:
        import trader.notify as n
    except Exception:
        return  # if trader isn't importable, downstream tests will fail anyway
    monkeypatch.setattr(n, "SMTP_USER", "", raising=False)
    monkeypatch.setattr(n, "SMTP_PASS", "", raising=False)
    monkeypatch.setattr(n, "SLACK_WEBHOOK", "", raising=False)

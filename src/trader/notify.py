"""Notifier — console + email + Slack.

v3.69.1: re-added Slack alongside email. Targets the **prismtrading**
workspace via Incoming Webhook. Both channels are attempted independently
— email failure doesn't block Slack and vice versa.

Requires SMTP credentials in .env:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=<sender@gmail.com>
  SMTP_PASS=<app password from https://myaccount.google.com/apppasswords>
  EMAIL_TO=richard.chen.1989@gmail.com
  EMAIL_FROM=<usually same as SMTP_USER>

For Slack alerts to prismtrading workspace, set:
  SLACK_WEBHOOK=https://hooks.slack.com/services/T.../B.../xxx

Create at:
  https://api.slack.com/apps → Create New App → From scratch →
  Pick prismtrading workspace → Incoming Webhooks → Activate →
  "Add New Webhook to Workspace" → pick the channel (e.g. #alerts).

Either channel can be left unconfigured and the other still works.
If both are missing, falls back to console-only.
"""
import json
import os
import smtplib
import socket
import urllib.error
import urllib.request
from email.message import EmailMessage
from datetime import datetime
# Importing config triggers load_dotenv() which populates os.environ
from . import config  # noqa: F401


def _env(key: str, default: str = "") -> str:
    """Read env at call time so we see any .env loaded by config import."""
    return os.getenv(key, default)


# Module-level constants kept for tests / external readers; resolved at import.
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER", "")
SMTP_PASS = _env("SMTP_PASS", "")
EMAIL_TO = _env("EMAIL_TO", "richard.chen.1989@gmail.com")
EMAIL_FROM = _env("EMAIL_FROM", SMTP_USER)
SLACK_WEBHOOK = _env("SLACK_WEBHOOK", "")


def _send_email(subject: str, body: str, level: str = "info") -> bool:
    # Re-read at call time so .env updates take effect without a reimport
    user = _env("SMTP_USER", SMTP_USER)
    password = _env("SMTP_PASS", SMTP_PASS)
    host = _env("SMTP_HOST", SMTP_HOST)
    port = int(_env("SMTP_PORT", str(SMTP_PORT)))
    to = _env("EMAIL_TO", EMAIL_TO)
    sender = _env("EMAIL_FROM", user)
    if not user or not password:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"[trader/{level}] {subject}"
    msg["From"] = sender or user
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, socket.error, OSError) as e:
        print(f"[notify] email send failed: {e}")
        return False


_LEVEL_EMOJI = {
    "info": "ℹ️", "warn": "⚠️", "warning": "⚠️",
    "error": "🚨", "critical": "🚨",
}


def _send_slack(subject: str, body: str, level: str = "info") -> bool:
    """POST a Block Kit message to SLACK_WEBHOOK. Returns True iff
    Slack accepted (HTTP 200). Best-effort — any failure returns False
    after a single short-timeout attempt; the email channel still
    delivers independently."""
    webhook = _env("SLACK_WEBHOOK", SLACK_WEBHOOK)
    if not webhook:
        return False

    emoji = _LEVEL_EMOJI.get(level.lower(), "ℹ️")
    header = f"{emoji} {subject}"[:150]  # Slack header text caps at 150
    body_md = body
    if len(body_md) > 2900:  # Slack section limit ~3000
        body_md = body_md[:2890] + "\n\n…[truncated]"

    payload = {
        "blocks": [
            {"type": "header",
             "text": {"type": "plain_text", "text": header, "emoji": True}},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": f"```\n{body_md}\n```"}},
        ],
        # Fallback text for notifications that don't render Block Kit
        "text": header,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError,
            socket.error, OSError) as e:
        print(f"[notify] slack send failed: {e}")
        return False
    except Exception as e:
        print(f"[notify] slack unexpected error: {type(e).__name__}: {e}")
        return False


_STUB_PHRASES = {"hello", "hi", "test", "ping", "stub", "placeholder", "tbd", "todo"}


def _is_stub(msg: str, subject: str | None) -> str | None:
    """Return reason string if message is a stub; None if substantive."""
    body = (msg or "").strip()
    if not body or len(body) < 80:
        return f"body too short ({len(body)} chars; min 80 of real content)"
    if body.lower() in _STUB_PHRASES:
        return f"body is a stub phrase ({body!r})"
    s = (subject or "").strip().lower()
    if any(t in s for t in ("<task name", "<headline", "<one-line")):
        return "subject contains unfilled template placeholders"
    if any(t in body.lower() for t in ("<key finding", "<recommended action", "<task name")):
        return "body contains unfilled template placeholders"
    return None


def notify(msg: str, level: str = "info", subject: str | None = None,
           allow_stub: bool = False) -> dict:
    """Console-print + email + Slack. Returns delivery status per channel.

    v3.69.1: pushes to BOTH email and Slack (when each is configured).
    Channels are independent — email failure doesn't block Slack and
    vice versa. Either can be disabled by leaving its env unset.

    REFUSES stubs by default. Caller must pass allow_stub=True to bypass
    (the guard is at this level, not just CLI, so direct Python callers
    can't send 'hello' / placeholder messages either).
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{level.upper()}] {msg}")

    if not allow_stub:
        reason = _is_stub(msg, subject)
        if reason:
            print(f"[notify] REFUSED stub message ({reason}); not sending.")
            return {"console": True, "email": False, "slack": False,
                    "refused": reason}

    if subject is None:
        first_line = msg.splitlines()[0] if msg else "trader notification"
        subject = first_line[:80]
    body = f"{timestamp}\n\n{msg}"

    email_sent = _send_email(subject, body, level)
    slack_sent = _send_slack(subject, body, level)

    return {
        "console": True,
        "email": email_sent,
        "slack": slack_sent,
        "to": EMAIL_TO if email_sent else None,
    }


def notify_test() -> dict:
    """Verify email pipeline end-to-end. Bypasses stub guard since the body is intentional."""
    return notify(
        "Email pipeline test — if you see this in your inbox, the trader system can reach you. "
        "This is the only auto-generated test message; production emails carry real trading data.",
        level="info",
        subject="trader email test",
        allow_stub=False,  # body is now long enough not to need bypass
    )

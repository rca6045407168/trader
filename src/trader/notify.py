"""Notifier — console + email.

v2.0: dropped Slack. Email goes to richard.chen.1989@gmail.com (personal,
separate from the FlexHaul Gmail account so this stays a clean personal project).

Requires SMTP credentials in .env:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=<sender@gmail.com>
  SMTP_PASS=<app password from https://myaccount.google.com/apppasswords>
  EMAIL_TO=richard.chen.1989@gmail.com
  EMAIL_FROM=<usually same as SMTP_USER>

If SMTP_USER/PASS are missing, falls back to console-only with a warning.
"""
import os
import smtplib
import socket
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


def notify(msg: str, level: str = "info", subject: str | None = None) -> dict:
    """Console-print + email. Returns delivery status."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{level.upper()}] {msg}")
    if subject is None:
        # First line of msg as subject (truncated), full msg as body
        first_line = msg.splitlines()[0] if msg else "trader notification"
        subject = first_line[:80]
    body = f"{timestamp}\n\n{msg}"
    sent = _send_email(subject, body, level)
    return {"console": True, "email": sent, "to": EMAIL_TO if sent else None}


def notify_test() -> dict:
    """Verify email pipeline end-to-end."""
    return notify(
        "Email pipeline test — if you see this in your inbox, the trader system can reach you.",
        level="info",
        subject="trader email test",
    )

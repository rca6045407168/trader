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


SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_TO = os.getenv("EMAIL_TO", "richard.chen.1989@gmail.com")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)


def _send_email(subject: str, body: str, level: str = "info") -> bool:
    if not SMTP_USER or not SMTP_PASS:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"[trader/{level}] {subject}"
    msg["From"] = EMAIL_FROM or SMTP_USER
    msg["To"] = EMAIL_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
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

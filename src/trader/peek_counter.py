"""Peek counter — tracks manual workflow_dispatch events.

Agent-3's most underrated insight: "the peek IS the override." Most retail
blowups don't start with explicit overriding — they start with curiosity-
driven peeking that escalates to "small adjustments" then full discretionary
trading.

Mechanism: count how often `daily-run` is triggered manually
(workflow_dispatch) vs scheduled (cron). Reset monthly. Alert if peeks > 3
in any rolling 30-day window.

GitHub Actions provides the trigger source via env var:
  - GITHUB_EVENT_NAME = "schedule" (cron) — normal
  - GITHUB_EVENT_NAME = "workflow_dispatch" (manual) — counts as a peek
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

PEEK_LOG_PATH = DATA_DIR / "peek_log.json"
ALERT_THRESHOLD = 3  # peeks per 30-day window before alert


@dataclass
class PeekLog:
    events: list[str] = field(default_factory=list)  # ISO 8601 timestamps of manual triggers


def load_log() -> PeekLog:
    if not PEEK_LOG_PATH.exists():
        return PeekLog()
    try:
        data = json.loads(PEEK_LOG_PATH.read_text())
        return PeekLog(events=data.get("events", []))
    except Exception:
        return PeekLog()


def save_log(log: PeekLog) -> None:
    PEEK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PEEK_LOG_PATH.write_text(json.dumps({"events": log.events}, indent=2))


def record_event_if_manual() -> tuple[bool, int]:
    """If this run is a manual workflow_dispatch, log it.
    Returns (was_manual, count_in_last_30_days).
    """
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_manual = event_name == "workflow_dispatch"
    log = load_log()
    if is_manual:
        log.events.append(datetime.utcnow().isoformat())
        # Trim to last 90 days to keep the file small
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        log.events = [e for e in log.events if e >= cutoff]
        save_log(log)
    # Count peeks in last 30 days
    cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
    count_30d = sum(1 for e in log.events if e >= cutoff_30d)
    return is_manual, count_30d


def peek_alert_message(count: int) -> str | None:
    """Returns alert message if peek count exceeds threshold; else None."""
    if count <= ALERT_THRESHOLD:
        return None
    return (
        f"⚠ PEEK ALERT: {count} manual workflow_dispatch events in last 30 days "
        f"(threshold: {ALERT_THRESHOLD}). Per agent-3 retail-veteran observation: "
        f"'the peek IS the override.' Most retail blowups start with curiosity-"
        f"driven manual triggers that escalate. If you're peeking this often, "
        f"something is driving you to micromanage the system. Identify what's "
        f"driving the urge BEFORE making any changes."
    )

"""v3.73.25 — ENFORCING-mode clean-runs clock.

The user's gate before meaningful capital:
    "30 clean autonomous runs with ENFORCING enabled in paper."

To track this we need:
  1. A start date (when ENFORCING was first flipped in paper).
  2. A query that counts 'completed' runs since that date.
  3. A query that flags any halted/failed run since that date —
     a single non-clean run resets the streak.

The start date is hardcoded for the canonical paper-arming window;
it can be read from .env (ENFORCING_CLOCK_START=YYYY-MM-DD) for
re-arming after a stoppage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from .journal import _conn, init_db

# Canonical start of the v3.73.25 ENFORCING-arming window.
# Paper .env was flipped DRAWDOWN_PROTOCOL_MODE=ENFORCING on this date.
DEFAULT_START = "2026-05-06"
TARGET_RUNS = 30


@dataclass
class EnforcingClockStatus:
    start_date: str
    target_runs: int
    completed_runs: int
    halted_runs: int
    failed_runs: int
    days_elapsed: int
    streak_clean: bool

    @property
    def fraction(self) -> float:
        return self.completed_runs / self.target_runs if self.target_runs else 0.0

    @property
    def gate_cleared(self) -> bool:
        return self.completed_runs >= self.target_runs and self.streak_clean

    def render_summary(self) -> str:
        flag = "✅" if self.gate_cleared else ("🟢" if self.streak_clean else "🔴")
        clean_str = "clean" if self.streak_clean else "BROKEN"
        return (
            f"{flag} ENFORCING clock: {self.completed_runs}/{self.target_runs} "
            f"clean runs ({self.fraction*100:.0f}%) since {self.start_date} "
            f"({self.days_elapsed}d elapsed). Streak: {clean_str}. "
            f"halted={self.halted_runs} failed={self.failed_runs}."
        )


def get_status(start_date: str | None = None,
                target_runs: int = TARGET_RUNS) -> EnforcingClockStatus:
    """Query the runs table for the ENFORCING-arming gate state.

    A run is 'clean' if status=='completed'. Any 'halted' or 'failed'
    row since the start date breaks the streak (the user's bar:
    'survives normal runs without intervention')."""
    if start_date is None:
        start_date = os.getenv("ENFORCING_CLOCK_START", DEFAULT_START)
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, started_at, completed_at, status FROM runs "
            "WHERE started_at >= ? ORDER BY started_at ASC",
            (start_date,),
        ).fetchall()
    completed = sum(1 for r in rows if r["status"] == "completed")
    halted = sum(1 for r in rows if r["status"] == "halted")
    failed = sum(1 for r in rows if r["status"] == "failed")
    streak_clean = halted == 0 and failed == 0

    today = date.today()
    start_d = date.fromisoformat(start_date)
    days_elapsed = (today - start_d).days

    return EnforcingClockStatus(
        start_date=start_date,
        target_runs=target_runs,
        completed_runs=completed,
        halted_runs=halted,
        failed_runs=failed,
        days_elapsed=days_elapsed,
        streak_clean=streak_clean,
    )

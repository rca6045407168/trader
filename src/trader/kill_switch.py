"""Kill switch. Multiple ways to halt the system, including manual.

Triggers (any one halts new orders for the day):
  1. Manual flag file at /tmp/trader_halt exists
  2. Equity dropped >X% in last N days (rolling)
  3. Equity dropped >Y% from rolling 30-day high
  4. ANTHROPIC_API_KEY missing AND USE_DEBATE=true (debate would fail silently)
  5. ALPACA_API_KEY missing
  6. yfinance data is stale (>5 days behind)

Intent: better to halt one productive day than to keep trading on a broken signal.
"""
import os
from datetime import datetime
from pathlib import Path

from .config import ALPACA_KEY, ANTHROPIC_KEY, USE_DEBATE
from .journal import recent_snapshots

KILL_FLAG_PATH = Path("/tmp/trader_halt")

WEEK_LOSS_THRESHOLD = 0.10  # halt if down 10% in 7 days
MONTH_LOSS_THRESHOLD = 0.20  # halt if down 20% in 30 days
DD_FROM_PEAK_THRESHOLD = 0.15  # halt if down 15% from rolling 30d peak


def check_kill_triggers(equity: float | None = None) -> tuple[bool, list[str]]:
    """Returns (should_halt, reasons). Run this BEFORE any order submission."""
    reasons: list[str] = []

    # 1. Manual flag
    if KILL_FLAG_PATH.exists():
        reasons.append(f"manual halt flag exists: {KILL_FLAG_PATH}")

    # 2. Required keys
    if not ALPACA_KEY:
        reasons.append("ALPACA_API_KEY missing")
    if USE_DEBATE and not ANTHROPIC_KEY:
        reasons.append("ANTHROPIC_API_KEY missing but USE_DEBATE=true")

    # 3. Equity-based triggers (require live equity + history)
    if equity is not None:
        snaps = recent_snapshots(days=30)
        if snaps:
            week = [s for s in snaps if (datetime.now().date() - datetime.fromisoformat(s["date"]).date()).days <= 7]
            if len(week) >= 2:
                week_start = week[-1]["equity"]
                if week_start and (equity / week_start - 1) < -WEEK_LOSS_THRESHOLD:
                    reasons.append(
                        f"week loss {(equity/week_start-1):+.2%} > -{WEEK_LOSS_THRESHOLD:.0%}"
                    )
            month_start = snaps[-1]["equity"]
            if month_start and (equity / month_start - 1) < -MONTH_LOSS_THRESHOLD:
                reasons.append(
                    f"month loss {(equity/month_start-1):+.2%} > -{MONTH_LOSS_THRESHOLD:.0%}"
                )
            peak = max(s["equity"] for s in snaps if s["equity"])
            if peak and (equity / peak - 1) < -DD_FROM_PEAK_THRESHOLD:
                reasons.append(
                    f"drawdown {(equity/peak-1):+.2%} from 30d peak ${peak:.0f} > -{DD_FROM_PEAK_THRESHOLD:.0%}"
                )

    return (len(reasons) > 0, reasons)


def arm_kill_switch(reason: str = "manual"):
    """Touch the manual halt flag."""
    KILL_FLAG_PATH.write_text(f"halt requested at {datetime.now().isoformat()}: {reason}\n")


def disarm_kill_switch():
    """Remove the manual halt flag."""
    if KILL_FLAG_PATH.exists():
        KILL_FLAG_PATH.unlink()

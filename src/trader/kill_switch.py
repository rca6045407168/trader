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

# v3.73.27 — data-freshness threshold. The header docstring of this
# module has claimed "yfinance data is stale (>5 days behind)" since
# day one but the actual check was never implemented. Closing that
# gap now: any rebalance that runs against price data older than 3
# business days is a "drifting" failure mode the user explicitly
# called out — picks would be made on stale signals while real
# positions move underneath them.
DATA_STALENESS_BUSINESS_DAYS = 3


def _check_data_freshness() -> tuple[bool, str | None]:
    """Returns (is_fresh, message_if_stale).

    Fetches the most recent SPY close and asserts it's within
    DATA_STALENESS_BUSINESS_DAYS business days of today. yfinance
    failure → not fresh (assume worst case).

    NOTE: This is a positive-confirmation check (we want to PROVE
    data is fresh; absence of confirmation halts).
    """
    try:
        import pandas as pd
        from .data import fetch_history
        # 30-day window so we get at least a few prints even on
        # weekends / holidays
        end_d = pd.Timestamp.today()
        start_d = (end_d - pd.DateOffset(days=30)).strftime("%Y-%m-%d")
        df = fetch_history(["SPY"], start=start_d, force_refresh=True)
        if df is None or df.empty:
            return False, "yfinance returned empty SPY history"
        latest = df.index[-1]
        today_bd = pd.Timestamp.today().normalize()
        # Count business days between latest data and today
        business_days_stale = len(pd.bdate_range(
            latest.normalize() + pd.Timedelta(days=1), today_bd))
        if business_days_stale > DATA_STALENESS_BUSINESS_DAYS:
            return False, (
                f"yfinance SPY data stale: latest={latest.date()}, "
                f"today={today_bd.date()}, "
                f"{business_days_stale} business days behind "
                f"(threshold {DATA_STALENESS_BUSINESS_DAYS})"
            )
        return True, None
    except Exception as e:
        return False, f"data freshness check failed: {type(e).__name__}: {e}"


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

    # 4. v3.73.27 — data freshness. Halt if yfinance is stale.
    # Skippable via SKIP_DATA_FRESHNESS_CHECK=true (e.g. weekend backfills,
    # offline tests). Stays on by default in production paper.
    if os.getenv("SKIP_DATA_FRESHNESS_CHECK", "").lower() != "true":
        is_fresh, msg = _check_data_freshness()
        if not is_fresh:
            reasons.append(msg or "data freshness check unknown failure")

    return (len(reasons) > 0, reasons)


def arm_kill_switch(reason: str = "manual"):
    """Touch the manual halt flag."""
    KILL_FLAG_PATH.write_text(f"halt requested at {datetime.now().isoformat()}: {reason}\n")


def disarm_kill_switch():
    """Remove the manual halt flag."""
    if KILL_FLAG_PATH.exists():
        KILL_FLAG_PATH.unlink()

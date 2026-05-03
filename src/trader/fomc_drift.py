"""[v3.59.0 — V5 Phase 4] Pre-FOMC drift sleeve.

Lucca & Moench (2015) "The Pre-FOMC Announcement Drift" measured a
+49bps single-day SPX drift from market close on FOMC eve through 2pm
ET on FOMC day, on a 1994-2011 sample. Richard's own v1.7 retest on
2015-2025 measured +22bps with Sharpe 2.35 — half-strength but still
highly significant.

This sleeve fires 8x per year (each scheduled FOMC meeting) and is
binary: 100% allocated to SPY from close-of-business T-1 through 2pm
ET on FOMC day, 0% allocated otherwise.

Why it persists (per V5 proposal):
  • Behavioral: leveraged-investor pre-positioning ahead of expected
    dovish surprise.
  • Calendar-driven, not screened. No universe selection means no
    crowding-from-screening.
  • Holding overnight risk algos minimize → retail-accessible.

This module is SHADOW by default (writes to virtual_shadow only).
Promotion to LIVE requires:
  1. 3-gate validation pass on 2015-2025 retest data
  2. 30 days of shadow run on the live system
  3. Adversarial-review CI pass
  4. Override-delay 24h cool-off
  5. Spousal pre-brief (per BEHAVIORAL_PRECOMMIT.md)

Wire to LIVE by setting FOMC_DRIFT_STATUS=LIVE env var AFTER all gates
pass. Default: SHADOW.

Allocation when LIVE: 10% of capital. Sleeve-day footprint = 10% × 8
days/year ≈ 0.7% of capital-days/year.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional


# 2026 FOMC dates — must match events_calendar.FOMC_DATES_2026.
# Refresh annually from https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES_2026: list[date] = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 11, 4),
    date(2026, 12, 16),
]

# 2027 FOMC dates published December 2026 — Q1 only listed at proposal
# write time. Update this list each January when the next year's full
# calendar lands.
FOMC_DATES_2027: list[date] = [
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 16),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 11, 3),
    date(2027, 12, 15),
]

ALL_FOMC_DATES: list[date] = sorted(FOMC_DATES_2026 + FOMC_DATES_2027)


@dataclass
class FomcDriftSignal:
    """One day's signal output."""
    today: date
    in_drift_window: bool
    fomc_date: Optional[date] = None
    target_weight_spy: float = 0.0  # 0.0 or sleeve_capital_pct
    rationale: str = ""


def status() -> str:
    """SHADOW (default) / LIVE / NOT_WIRED — env-controlled.

    SHADOW: signal computes, fills route through virtual_shadow only.
    LIVE: signal computes, fills route through real broker.
    NOT_WIRED: signal does NOT compute (sleeve disabled).
    """
    return os.getenv("FOMC_DRIFT_STATUS", "SHADOW").upper()


def sleeve_capital_pct() -> float:
    """Default 10% per V5 proposal. Tweakable via FOMC_DRIFT_PCT env."""
    try:
        return float(os.getenv("FOMC_DRIFT_PCT", "0.10"))
    except Exception:
        return 0.10


def is_drift_window(today: Optional[date] = None,
                     fomc_dates: Optional[list[date]] = None) -> tuple[bool, Optional[date]]:
    """Is `today` inside the pre-FOMC drift window?

    Window is: from market close on FOMC-eve through 2pm ET on FOMC day.
    For a daily-bar simulation we model this as: enter at close of T-1,
    exit at close of T (FOMC day). The 2pm-ET-cutoff is a refinement
    only intra-day live execution can express.

    Returns (in_window, fomc_date_if_window_else_None).
    """
    today = today or datetime.utcnow().date()
    fomc_dates = fomc_dates or ALL_FOMC_DATES
    for fomc in fomc_dates:
        eve = fomc - timedelta(days=1)
        # On FOMC eve at end-of-day, we're in the window for tomorrow
        if today == eve:
            return True, fomc
        # On FOMC day during the morning, we're still in the window
        if today == fomc:
            return True, fomc
    return False, None


def compute_signal(today: Optional[date] = None) -> FomcDriftSignal:
    """Pure: today's pre-FOMC sleeve target weight."""
    today = today or datetime.utcnow().date()
    in_window, fomc_date = is_drift_window(today)
    weight = sleeve_capital_pct() if in_window else 0.0
    if in_window:
        eve = fomc_date - timedelta(days=1)
        if today == eve:
            rationale = (f"FOMC tomorrow ({fomc_date}); enter SPY long "
                          f"at close.")
        else:
            rationale = (f"FOMC today ({fomc_date}); hold through 2pm ET, "
                          f"exit at close.")
    else:
        rationale = "Outside FOMC drift window."
    return FomcDriftSignal(today=today, in_drift_window=in_window,
                            fomc_date=fomc_date, target_weight_spy=weight,
                            rationale=rationale)


def expected_target() -> dict[str, float]:
    """The targets dict the runner would use — {SPY: weight} or empty.

    Format matches the LIVE momentum sleeve targets so the same
    risk_manager + execute path can consume it.
    """
    sig = compute_signal()
    if sig.target_weight_spy > 0:
        return {"SPY": sig.target_weight_spy}
    return {}


def days_until_next_fomc(today: Optional[date] = None) -> Optional[int]:
    today = today or datetime.utcnow().date()
    future = [d for d in ALL_FOMC_DATES if d >= today]
    if not future:
        return None
    return (future[0] - today).days

"""[v3.60.0] Anti-momentum-crash detector.

Reference: Daniel & Moskowitz (2016) "Momentum crashes." Journal of
Financial Economics. Documents that momentum strategies experience
periodic crashes of -30 to -50% over a few months when the regime
flips (typically: post-recession recovery rallies that benefit
yesterday's losers).

Famous instances:
  • 2009-Q1 (post-GFC bounce): momentum -40%
  • 2020-Q2 (post-COVID rally): momentum -25%
  • 2022-Q2 (rate shock + meme reversal): momentum -15%

Detection mechanism (per the paper): when the prior 24-month equity
market return has been NEGATIVE and the realized vol has been HIGH,
momentum is in a regime where crashes are imminent. The signal:

  CRASH_RISK = (prior_24mo_market_return < 0) AND (12mo_realized_vol > 0.20)

When CRASH_RISK is on, the momentum sleeve gross exposure is cut to
50% of normal (defensive). When off, full exposure.

Why this is different from the killed v3.x regime overlay:
  • v3.x regime overlay used HMM on monthly returns — too lagging,
    cut at panic lows.
  • This signal uses 24mo TRAILING market return — leading indicator
    of "we're in the regime where momentum tends to crash."
  • Documented in academic literature, not derived from one author's
    backtest.

Status: defaults SHADOW. Promotion to LIVE requires comparing the
crash-protected curve vs unprotected on the 2009/2020/2022 episodes
inside the existing stress test framework.
"""
from __future__ import annotations

import math
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Sequence


CRASH_VOL_THRESHOLD = 0.20  # Daniel-Moskowitz used 16% but our period is post-GFC
CRASH_LOOKBACK_MARKET_MONTHS = 24
CRASH_GROSS_MULT = 0.50  # cut to 50% when crash risk on


@dataclass
class CrashSignal:
    asof: str
    market_24mo_return: Optional[float]
    market_12mo_vol_annual: Optional[float]
    crash_risk_on: bool
    suggested_gross_mult: float = 1.0
    rationale: str = ""


def status() -> str:
    """Default SHADOW. Set MOMENTUM_CRASH_STATUS=LIVE to enforce."""
    return os.getenv("MOMENTUM_CRASH_STATUS", "SHADOW").upper()


def _annualized_vol(daily_returns: Sequence[float]) -> Optional[float]:
    if len(daily_returns) < 30:
        return None
    sd = statistics.stdev(daily_returns)
    return sd * math.sqrt(252)


def compute_signal(spy_daily_returns: Sequence[float],
                     asof: Optional[date] = None) -> CrashSignal:
    """Compute today's crash signal.

    spy_daily_returns: at least 24 months of daily SPY returns ending at asof.
    """
    asof = asof or datetime.utcnow().date()
    n_24mo = 24 * 21  # ~504 trading days
    n_12mo = 12 * 21
    if len(spy_daily_returns) < n_24mo:
        return CrashSignal(
            asof=asof.isoformat(),
            market_24mo_return=None,
            market_12mo_vol_annual=None,
            crash_risk_on=False,
            rationale=f"insufficient history ({len(spy_daily_returns)} days)",
        )
    last_24mo = spy_daily_returns[-n_24mo:]
    last_12mo = spy_daily_returns[-n_12mo:]
    cum_24mo = 1.0
    for r in last_24mo:
        cum_24mo *= (1 + r)
    market_return_24mo = cum_24mo - 1
    vol_12mo = _annualized_vol(last_12mo)
    crash_on = (market_return_24mo < 0
                 and vol_12mo is not None
                 and vol_12mo > CRASH_VOL_THRESHOLD)
    if crash_on:
        rationale = (
            f"24mo SPY return {market_return_24mo*100:+.1f}% (negative) AND "
            f"12mo annualized vol {vol_12mo*100:.1f}% > {CRASH_VOL_THRESHOLD*100:.0f}%. "
            f"Daniel-Moskowitz crash regime active."
        )
    else:
        why_no = []
        if market_return_24mo >= 0:
            why_no.append(f"24mo return {market_return_24mo*100:+.1f}% positive")
        if vol_12mo is not None and vol_12mo <= CRASH_VOL_THRESHOLD:
            why_no.append(f"12mo vol {vol_12mo*100:.1f}% calm")
        rationale = "Crash regime OFF: " + " · ".join(why_no)
    return CrashSignal(
        asof=asof.isoformat(),
        market_24mo_return=market_return_24mo,
        market_12mo_vol_annual=vol_12mo,
        crash_risk_on=crash_on,
        suggested_gross_mult=CRASH_GROSS_MULT if crash_on else 1.0,
        rationale=rationale,
    )


def gross_multiplier(spy_daily_returns: Sequence[float],
                       asof: Optional[date] = None) -> float:
    """For consumption by risk_manager: returns 0.50 if crash risk on
    AND status is LIVE; 1.0 otherwise.

    SHADOW status computes the signal but does not affect exposure.
    """
    sig = compute_signal(spy_daily_returns, asof)
    if status() != "LIVE":
        return 1.0
    return sig.suggested_gross_mult

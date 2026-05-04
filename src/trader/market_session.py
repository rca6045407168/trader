"""US-equity market session helper (v3.65.1).

Why this exists: Alpaca's `account.equity` updates with every quote tick,
including extended hours. On weekends and pre-market, `account.equity`
reflects Friday's close while `account.last_equity` still reflects the
*previous* close — producing a phantom "+0.6% day P&L" labeled as
"today" when the market is actually closed.

Rather than show that misleading number, the dashboard checks
`market_session_now()` and either suppresses or relabels the day-P&L
card depending on session state.

NYSE/NASDAQ regular trading hours = 09:30–16:00 America/New_York,
weekdays except market holidays. Holidays are hard-coded for the
visible window (2025–2027). Update the set when the year rolls over.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# US equity market full closures. Half-days (early close 13:00) are
# treated as OPEN until 13:00 ET, then CLOSED_AFTERHOURS — listed
# separately so the session helper can branch on early-close.
NYSE_HOLIDAYS_FULL = frozenset({
    # 2025
    date(2025, 1, 1),  date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4),  date(2025, 9, 1),  date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),  date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3),  date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 9, 7),  date(2026, 11, 26),date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),  date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5),  date(2027, 9, 6),  date(2027, 11, 25),
    date(2027, 12, 24),
})

NYSE_HALF_DAYS = frozenset({
    # Day after Thanksgiving + day before Independence Day + Christmas Eve
    # close at 13:00 ET. Listed for the same window as full closures.
    date(2025, 7, 3),  date(2025, 11, 28),date(2025, 12, 24),
    date(2026, 7, 2),  date(2026, 11, 27),date(2026, 12, 24),
    date(2027, 7, 2),  date(2027, 11, 26),
})


@dataclass(frozen=True)
class SessionState:
    """One-shot snapshot of where we are in the trading week."""
    label: str                    # OPEN | CLOSED_PREMARKET | CLOSED_AFTERHOURS
                                  # | CLOSED_OVERNIGHT | CLOSED_WEEKEND | CLOSED_HOLIDAY
    is_open: bool                 # True only during regular-trading-hours
    last_trading_day: date        # Most recent date when market opened at all
    next_trading_day: date        # Next date market will open
    et_now: datetime              # Wall clock in ET
    reason: str                   # Human-readable reason


def _is_full_holiday(d: date) -> bool:
    return d in NYSE_HOLIDAYS_FULL


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not _is_full_holiday(d)


def _last_trading_day_on_or_before(d: date) -> date:
    cur = d
    while not _is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def _next_trading_day_after(d: date) -> date:
    cur = d + timedelta(days=1)
    while not _is_trading_day(cur):
        cur += timedelta(days=1)
    return cur


def market_session_now(now: Optional[datetime] = None) -> SessionState:
    """Return the current session label + the trading-day anchors.

    Caller can pass `now` (timezone-aware) for testing; otherwise we
    read wall clock in America/New_York."""
    if now is None:
        et_now = datetime.now(ET)
    elif now.tzinfo is None:
        et_now = now.replace(tzinfo=ET)
    else:
        et_now = now.astimezone(ET)

    today = et_now.date()
    weekday = today.weekday()  # 0=Mon, 6=Sun
    t = et_now.time()

    OPEN_T  = time(9, 30)
    CLOSE_T = time(16, 0)
    EARLY_CLOSE_T = time(13, 0)

    if weekday >= 5:
        return SessionState(
            label="CLOSED_WEEKEND", is_open=False,
            last_trading_day=_last_trading_day_on_or_before(today),
            next_trading_day=_next_trading_day_after(today),
            et_now=et_now,
            reason=f"It's {today.strftime('%A')} — markets closed for the weekend.",
        )

    if _is_full_holiday(today):
        return SessionState(
            label="CLOSED_HOLIDAY", is_open=False,
            last_trading_day=_last_trading_day_on_or_before(today - timedelta(days=1)),
            next_trading_day=_next_trading_day_after(today),
            et_now=et_now,
            reason=f"NYSE closed for holiday on {today.isoformat()}.",
        )

    is_half = today in NYSE_HALF_DAYS
    close_t = EARLY_CLOSE_T if is_half else CLOSE_T

    last_td = _last_trading_day_on_or_before(today - timedelta(days=1))
    next_td = _next_trading_day_after(today)

    if t < OPEN_T:
        return SessionState(
            label="CLOSED_PREMARKET", is_open=False,
            last_trading_day=last_td, next_trading_day=today,
            et_now=et_now,
            reason=f"Pre-market — opens at 09:30 ET.",
        )
    if t >= close_t:
        return SessionState(
            label="CLOSED_AFTERHOURS", is_open=False,
            last_trading_day=today,
            next_trading_day=next_td,
            et_now=et_now,
            reason=(f"After-hours — close was {close_t.strftime('%H:%M')} ET"
                    + (" (early close)" if is_half else "") + "."),
        )
    return SessionState(
        label="OPEN", is_open=True,
        last_trading_day=today, next_trading_day=next_td,
        et_now=et_now,
        reason=f"Market open until {close_t.strftime('%H:%M')} ET.",
    )


def is_market_open_now() -> bool:
    return market_session_now().is_open


def last_trading_day(now: Optional[datetime] = None) -> date:
    """Date of the most recent session where market was open at all
    (today if it's a weekday between 09:30 and 16:00 ET, else the most
    recent prior trading day)."""
    s = market_session_now(now)
    return s.last_trading_day

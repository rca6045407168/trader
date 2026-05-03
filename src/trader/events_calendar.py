"""Upcoming events for held names (Bloomberg EVTS-style).

Fetches earnings dates + ex-div for our 15 held names + hard-coded FOMC
meeting calendar + OPEX (third-Friday) detection. Returns events sorted
by date, next 30 days.

Why this matters: a monthly trader needs to see "earnings on 3 of my 15
holdings this week" at-a-glance — those are the days to watch. The
strategy itself doesn't react to single-day events (that's a different
sleeve), but the OPERATOR uses this to anticipate volatility.

Data sources:
  - earnings dates: yfinance Ticker.get_earnings_dates() (free, no auth)
  - ex-div: yfinance Ticker.get_dividends() (free, no auth)
  - FOMC: hard-coded 2026 calendar from federalreserve.gov (8 meetings/yr,
    well-known publication; refreshed annually)
  - OPEX: third Friday of each month (deterministic)
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


# 2026 FOMC meeting dates (final day of each 2-day meeting).
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Refresh annually — these are well-known and don't change.
FOMC_DATES_2026 = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 11, 4),
    date(2026, 12, 16),
]


@dataclass
class UpcomingEvent:
    date: date
    days_until: int
    event_type: str  # earnings | ex_div | fomc | opex
    symbol: Optional[str] = None  # None for FOMC/OPEX (portfolio-wide)
    note: str = ""
    confidence: str = "high"  # high (FOMC, OPEX) | medium (yfinance) | low


def _next_third_friday(start: date, n_months: int = 2) -> list[date]:
    """Returns the next n_months third-Fridays from start."""
    out = []
    cur = date(start.year, start.month, 1)
    while len(out) < n_months:
        # Find first Friday of this month
        first_fri = cur + timedelta(days=(4 - cur.weekday()) % 7)
        third_fri = first_fri + timedelta(days=14)
        if third_fri >= start:
            out.append(third_fri)
        # advance to next month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _fetch_yf_events(symbol: str, today: date, days_ahead: int = 30) -> list[UpcomingEvent]:
    """Earnings + ex-div for one symbol. Returns events in [today, today+days]."""
    events: list[UpcomingEvent] = []
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
    except Exception:
        return events

    # Earnings dates
    try:
        ed = t.get_earnings_dates(limit=8)
        if ed is not None and not ed.empty:
            for ts, _row in ed.iterrows():
                dt = ts.date() if hasattr(ts, "date") else None
                if dt is None:
                    continue
                d_until = (dt - today).days
                if 0 <= d_until <= days_ahead:
                    events.append(UpcomingEvent(
                        date=dt, days_until=d_until,
                        event_type="earnings", symbol=symbol,
                        note="earnings release", confidence="medium",
                    ))
    except Exception:
        pass

    # Ex-dividend
    try:
        cal = t.get_calendar()
        if cal:
            ex_div = cal.get("Ex-Dividend Date")
            if ex_div:
                dt = ex_div if isinstance(ex_div, date) else (ex_div.date() if hasattr(ex_div, "date") else None)
                if dt:
                    d_until = (dt - today).days
                    if 0 <= d_until <= days_ahead:
                        events.append(UpcomingEvent(
                            date=dt, days_until=d_until,
                            event_type="ex_div", symbol=symbol,
                            note="ex-dividend", confidence="medium",
                        ))
    except Exception:
        pass

    return events


def compute_upcoming_events(symbols: list[str], days_ahead: int = 30,
                             today: Optional[date] = None) -> list[UpcomingEvent]:
    """All events for held names + portfolio-wide events, sorted by date."""
    if today is None:
        today = datetime.utcnow().date()
    events: list[UpcomingEvent] = []

    # FOMC
    for d in FOMC_DATES_2026:
        d_until = (d - today).days
        if 0 <= d_until <= days_ahead:
            events.append(UpcomingEvent(
                date=d, days_until=d_until, event_type="fomc",
                note="FOMC meeting (rate decision)", confidence="high",
            ))

    # OPEX
    for d in _next_third_friday(today, n_months=2):
        d_until = (d - today).days
        if 0 <= d_until <= days_ahead:
            events.append(UpcomingEvent(
                date=d, days_until=d_until, event_type="opex",
                note="monthly options expiration (third Friday)",
                confidence="high",
            ))

    # Per-symbol (earnings + ex-div) — best-effort
    for sym in (symbols or [])[:30]:  # cap so we don't spam yfinance
        try:
            events.extend(_fetch_yf_events(sym, today, days_ahead))
        except Exception:
            continue

    events.sort(key=lambda e: (e.date, e.event_type, e.symbol or ""))
    return events

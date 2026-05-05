"""Per-symbol poll cadence — HOT around earnings, WARM otherwise (v3.70.0).

User insight: 8-K earnings releases (Item 2.02) are pre-announced.
Companies confirm dates 2-4 weeks ahead. We can poll FASTER on
earnings days (catch the print within seconds) without paying for it
on the other 60+ days/year per name.

Hybrid design — best of both:

- **HOT** (60s cadence): symbol is within ±2 calendar days of its
  next scheduled earnings release. Catches the earnings 8-K within
  a minute of EDGAR mirroring.
- **WARM** (300s cadence, the v3.68.3 default): every other day.
  Still catches unscheduled 8-Ks (debt raises, officer changes,
  M&A) without pretending they don't happen.

The reactor's UNIQUE constraint on (symbol, accession) makes faster
polling free in token cost — re-polling 8-Ks we've already analyzed
costs zero Claude tokens.

Why ±2 days, not just the date-of:
- Earnings 8-Ks usually file AT 4:00-4:30pm ET on the earnings date,
  but companies sometimes file the press release the following morning
  or pre-market the day before
- Two days catches both ends without bleeding into the next quarter
- The follow-up 8-K (e.g. CFO commentary, conference-call transcript
  filings) often comes T+1 or T+2

Why we still keep WARM polling on non-earnings days:
- ~50% of materially-flagged 8-Ks in our v3.68.x runs were
  unscheduled (Item 5.02 officer changes, 8.01 other events, 1.01
  material agreements, 7.01 Reg FD). Earnings-only would miss them all.
- INTC's $6.5B debt raise (the only M3 we've flagged) was Item 8.01,
  filed off-cycle. WARM caught it.

Refresh cadence: schedules are looked up once per day. Reading
earnings_calendar.next_earnings_date is cheap but cache-tier-paid
(Polygon/Finnhub call) — once-per-day amortizes across 1440
60-second iters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


HOT_CADENCE_SECONDS = 60
WARM_CADENCE_SECONDS = 300
HOT_WINDOW_DAYS = 2  # symbol is HOT for [earnings_date - 2, earnings_date + 2]


@dataclass
class SymbolSchedule:
    """One symbol's polling state, kept in memory by the daemon."""
    symbol: str
    next_earnings_date: Optional[date] = None
    cadence: str = "WARM"           # "HOT" or "WARM"
    cadence_seconds: int = WARM_CADENCE_SECONDS
    next_poll_at: datetime = field(default_factory=datetime.utcnow)
    last_polled_at: Optional[datetime] = None

    def is_due(self, now: datetime) -> bool:
        return now >= self.next_poll_at

    def mark_polled(self, now: datetime) -> None:
        self.last_polled_at = now
        self.next_poll_at = now + timedelta(seconds=self.cadence_seconds)


def classify(next_earnings: Optional[date],
              today: Optional[date] = None) -> tuple[str, int]:
    """Return ("HOT", 60) or ("WARM", 300) based on earnings proximity.

    HOT iff today is within ±HOT_WINDOW_DAYS of next_earnings.
    Returns WARM for None earnings_date (i.e. no scheduled event known).
    """
    if next_earnings is None:
        return ("WARM", WARM_CADENCE_SECONDS)
    if today is None:
        today = datetime.utcnow().date()
    days_out = (next_earnings - today).days
    if -HOT_WINDOW_DAYS <= days_out <= HOT_WINDOW_DAYS:
        return ("HOT", HOT_CADENCE_SECONDS)
    return ("WARM", WARM_CADENCE_SECONDS)


def build_schedule(
    symbols: list[str],
    next_earnings_fn=None,
    today: Optional[date] = None,
) -> dict[str, SymbolSchedule]:
    """Build the initial schedule for a list of symbols.

    `next_earnings_fn(symbol) -> Optional[date]` is the lookup hook —
    defaults to trader.earnings_calendar.next_earnings_date. Tests
    inject a fake to control classification without hitting the
    paid earnings calendar API.

    Returns dict keyed by symbol with SymbolSchedule values. Each
    schedule's next_poll_at is set to NOW so the first iter polls
    every symbol immediately."""
    if next_earnings_fn is None:
        try:
            from .earnings_calendar import next_earnings_date as _nxt
            next_earnings_fn = lambda sym: _nxt(sym, days_ahead=60)
        except Exception:
            next_earnings_fn = lambda sym: None

    if today is None:
        today = datetime.utcnow().date()

    out: dict[str, SymbolSchedule] = {}
    now = datetime.utcnow()
    for sym in symbols:
        try:
            ed = next_earnings_fn(sym)
        except Exception:
            ed = None
        cadence, secs = classify(ed, today=today)
        out[sym] = SymbolSchedule(
            symbol=sym,
            next_earnings_date=ed,
            cadence=cadence,
            cadence_seconds=secs,
            next_poll_at=now,  # immediate first poll
        )
    return out


def refresh_classifications(
    schedule: dict[str, SymbolSchedule],
    today: Optional[date] = None,
) -> int:
    """Re-classify every symbol based on `today`. Used after a daily
    schedule refresh to handle dates rolling through the HOT window
    (e.g. NVDA's earnings tomorrow → HOT today; AAPL's earnings was
    last week → back to WARM).

    Does NOT alter next_poll_at — that's set when the symbol is
    actually polled. Just updates the cadence_seconds so the NEXT
    poll uses the right interval.

    Returns the number of symbols whose cadence changed."""
    n_changed = 0
    for sched in schedule.values():
        new_cadence, new_secs = classify(sched.next_earnings_date,
                                            today=today)
        if new_cadence != sched.cadence:
            sched.cadence = new_cadence
            sched.cadence_seconds = new_secs
            n_changed += 1
    return n_changed


def hot_symbols(schedule: dict[str, SymbolSchedule]) -> list[str]:
    """List of symbols currently in HOT mode (for dashboard display)."""
    return [s.symbol for s in schedule.values() if s.cadence == "HOT"]


def due_symbols(
    schedule: dict[str, SymbolSchedule],
    now: Optional[datetime] = None,
) -> list[str]:
    """List of symbols whose next_poll_at has passed."""
    if now is None:
        now = datetime.utcnow()
    return [s.symbol for s in schedule.values() if s.is_due(now)]

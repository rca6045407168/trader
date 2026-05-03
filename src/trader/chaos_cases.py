"""[v3.59.5 — TESTING_PRACTICES Cat 8] Chaos / failure injection cases.

Three categories of edge condition that don't exist on a happy path:

  • DST transitions (clock skips/repeats; intraday math breaks)
  • Market holidays + half-days (no fills available; cron may fire on
    a closed market and produce confusing logs)
  • Library version drift (yfinance schema flip 2024-08, Adj Close
    removal; Alpaca SDK field renames)

These are PURE detection helpers. They don't mutate state — they tell
you whether today is a "be careful" day so the orchestrator can defer
non-essential operations (e.g. skip slippage TCA on a half-day, defer
rebalance by 1 day if today is a market holiday, etc).

Pure functions; no external deps required for the date logic. The
library-drift detector imports yfinance/alpaca lazily.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional


# ============================================================
# Market calendar — US equities
# ============================================================

# 2024-2027 NYSE full-day closures (federal + market holidays).
# Source: https://www.nyse.com/markets/hours-calendars
US_MARKET_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
    date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
    date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28),
    date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 9),  # Jimmy Carter day of mourning
    date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027 (estimated; refresh annually)
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25),
    date(2027, 12, 24),
}

# Half-day (early close at 1pm ET): day before Independence Day if
# Jul 4 falls on Tue-Fri; day after Thanksgiving; day before Christmas.
US_MARKET_HALF_DAYS: set[date] = {
    date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24),
    date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26), date(2027, 12, 23),
}


def is_market_holiday(d: Optional[date] = None) -> bool:
    """Is the US equity market closed all day on `d`?"""
    d = d or datetime.utcnow().date()
    if d.weekday() >= 5:  # Sat or Sun
        return True
    return d in US_MARKET_HOLIDAYS


def is_half_day(d: Optional[date] = None) -> bool:
    """Half-day (early close at 1pm ET) — execution must adjust."""
    d = d or datetime.utcnow().date()
    return d in US_MARKET_HALF_DAYS


def next_trading_day(start: Optional[date] = None) -> date:
    """Returns the next non-holiday weekday at-or-after `start`."""
    d = start or datetime.utcnow().date()
    for _ in range(15):  # bound the search
        if not is_market_holiday(d):
            return d
        d += timedelta(days=1)
    return d  # give up after 2 weeks


def prev_trading_day(start: Optional[date] = None) -> date:
    """Last full trading day strictly before `start`."""
    d = (start or datetime.utcnow().date()) - timedelta(days=1)
    for _ in range(15):
        if not is_market_holiday(d):
            return d
        d -= timedelta(days=1)
    return d


# ============================================================
# DST transition detection
# ============================================================

def is_dst_transition_day(d: Optional[date] = None) -> tuple[bool, Optional[str]]:
    """Returns (is_transition, direction). direction is "spring_forward"
    or "fall_back" or None.

    US DST: 2nd Sunday of March (spring forward) + 1st Sunday of November
    (fall back). On these days, intraday time arithmetic can produce
    non-existent or duplicated hours.

    NB: NYSE itself uses Eastern Time, which is DST-aware. The system
    should defer any time-of-day-dependent computation by a day on these
    transitions to be safe.
    """
    d = d or datetime.utcnow().date()
    if d.month == 3:
        # 2nd Sunday of March
        first = date(d.year, 3, 1)
        # find first sunday
        offset = (6 - first.weekday()) % 7
        first_sun = first + timedelta(days=offset)
        second_sun = first_sun + timedelta(days=7)
        if d == second_sun:
            return True, "spring_forward"
    elif d.month == 11:
        # 1st Sunday of November
        first = date(d.year, 11, 1)
        offset = (6 - first.weekday()) % 7
        first_sun = first + timedelta(days=offset)
        if d == first_sun:
            return True, "fall_back"
    return False, None


# ============================================================
# Library drift detection
# ============================================================

def yfinance_schema_check() -> dict:
    """Verify yfinance returns the expected schema. Catches the 2024
    Adj Close removal + the 2024-08 Ticker.history() flip.

    Returns {ok, message, columns}. ok=False if schema is unexpected.
    """
    out = {"ok": True, "message": "schema OK", "columns": []}
    try:
        import yfinance as yf
        t = yf.Ticker("SPY")
        df = t.history(period="5d")
        if df is None or df.empty:
            out["ok"] = False
            out["message"] = "yfinance returned empty"
            return out
        out["columns"] = list(df.columns)
        # Required columns we depend on
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in df.columns:
                out["ok"] = False
                out["message"] = f"missing required column: {col}"
                return out
        # Adj Close was removed in 2024 — system should not depend on it.
        # If it exists that's fine; if it doesn't that's also fine (we
        # use Close with auto_adjust=True).
    except Exception as e:
        out["ok"] = False
        out["message"] = f"yfinance call failed: {type(e).__name__}: {e}"
    return out


def alpaca_sdk_check() -> dict:
    """Verify Alpaca SDK has the entrypoints we depend on."""
    out = {"ok": True, "message": "Alpaca SDK OK"}
    try:
        from alpaca.trading.requests import MarketOrderRequest  # noqa
        from alpaca.trading.enums import OrderSide, TimeInForce  # noqa
        # The TimeInForce.CLS enum we added in v3.59.0 must exist
        if not hasattr(TimeInForce, "CLS"):
            out["ok"] = False
            out["message"] = "TimeInForce.CLS missing — MOC orders won't work"
    except Exception as e:
        out["ok"] = False
        out["message"] = f"Alpaca SDK import failed: {type(e).__name__}: {e}"
    return out


# ============================================================
# Composite "is today a be-careful day"
# ============================================================

def todays_caveats(d: Optional[date] = None) -> list[str]:
    """Returns a list of human-readable caveats for `d`. Empty list = clean day."""
    d = d or datetime.utcnow().date()
    out = []
    if d.weekday() >= 5:
        out.append("weekend (markets closed)")
    elif is_market_holiday(d):
        out.append(f"market holiday ({d})")
    elif is_half_day(d):
        out.append(f"half-day (early close at 1pm ET)")
    is_dst, direction = is_dst_transition_day(d)
    if is_dst:
        out.append(f"DST transition ({direction}); time-of-day math may be off")
    return out

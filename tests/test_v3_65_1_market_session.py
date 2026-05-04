"""Tests for v3.65.1 — market-session awareness fix.

Bug surfaced 2026-05-03: dashboard showed +1.13% "day P&L" on a Sunday
when no trading had occurred. Root cause: Alpaca's
`account.equity` − `account.last_equity` doesn't represent "today"
when the market is closed; `last_equity` doesn't roll over until the
next session opens, so on Sat/Sun/pre-Monday-open you see Thursday →
Friday's full move attributed to "today".

Fix: detect session state, suppress the day-P&L label when closed.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
ET = ZoneInfo("America/New_York")


# ============================================================
# market_session module
# ============================================================
def test_market_session_module_imports():
    from trader.market_session import (
        market_session_now, is_market_open_now, last_trading_day,
        SessionState,
    )
    assert callable(market_session_now)


def test_market_session_open_during_rth():
    """Tuesday 11am ET in 2026-04 (no holiday) → OPEN."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 4, 14, 11, 0, tzinfo=ET))
    assert s.label == "OPEN"
    assert s.is_open is True


def test_market_session_premarket():
    """Tuesday 7am ET → CLOSED_PREMARKET."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 4, 14, 7, 0, tzinfo=ET))
    assert s.label == "CLOSED_PREMARKET"
    assert s.is_open is False


def test_market_session_afterhours():
    """Tuesday 5pm ET → CLOSED_AFTERHOURS."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 4, 14, 17, 0, tzinfo=ET))
    assert s.label == "CLOSED_AFTERHOURS"
    assert s.is_open is False
    assert s.last_trading_day == date(2026, 4, 14)


def test_market_session_weekend_sunday():
    """The bug case: Sunday 2026-05-03 9am ET → CLOSED_WEEKEND."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 5, 3, 9, 0, tzinfo=ET))
    assert s.label == "CLOSED_WEEKEND"
    assert s.is_open is False
    # Last trading day was Friday May 1
    assert s.last_trading_day == date(2026, 5, 1)
    # Next trading day is Monday May 4
    assert s.next_trading_day == date(2026, 5, 4)


def test_market_session_weekend_saturday():
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 5, 2, 11, 0, tzinfo=ET))
    assert s.label == "CLOSED_WEEKEND"
    assert s.last_trading_day == date(2026, 5, 1)


def test_market_session_holiday_christmas():
    """2026-12-25 is Christmas — CLOSED_HOLIDAY."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 12, 25, 11, 0, tzinfo=ET))
    assert s.label == "CLOSED_HOLIDAY"
    assert s.is_open is False
    # Christmas 2026 is Friday; last trading day was Thursday Dec 24
    # (which is a half-day) — but as a trading day it counts
    assert s.last_trading_day == date(2026, 12, 24)


def test_market_session_holiday_memorial_day_2026():
    """Memorial Day 2026 = Monday May 25."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 5, 25, 11, 0, tzinfo=ET))
    assert s.label == "CLOSED_HOLIDAY"


def test_market_session_holiday_juneteenth_2026():
    """Juneteenth 2026 = Friday June 19."""
    from trader.market_session import market_session_now
    s = market_session_now(datetime(2026, 6, 19, 11, 0, tzinfo=ET))
    assert s.label == "CLOSED_HOLIDAY"


def test_last_trading_day_during_session():
    from trader.market_session import last_trading_day
    # Tuesday April 14 2026, 11am ET — market open
    d = last_trading_day(datetime(2026, 4, 14, 11, 0, tzinfo=ET))
    assert d == date(2026, 4, 14)


def test_last_trading_day_pre_market_returns_prior_session():
    """Tuesday 7am ET — last trading day is Monday."""
    from trader.market_session import last_trading_day
    d = last_trading_day(datetime(2026, 4, 14, 7, 0, tzinfo=ET))
    assert d == date(2026, 4, 13)  # Monday


def test_last_trading_day_skips_weekend_and_holiday():
    """Tuesday 2026-01-20 7am ET — last trading day is Friday Jan 16
    because Mon Jan 19 is MLK Day."""
    from trader.market_session import last_trading_day
    d = last_trading_day(datetime(2026, 1, 20, 7, 0, tzinfo=ET))
    assert d == date(2026, 1, 16)


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_version_bumped_to_v3_65_1():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The v3.65.1 release tag must still appear in changelog comments;
    # sidebar caption may have moved to a later patch.
    assert "v3.65.1" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"


def test_dashboard_has_market_session_helper():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def _market_session" in text
    assert "from trader.market_session import market_session_now" in text


def test_price_headline_branches_on_session():
    """Price headline must branch on session.is_open (skip day delta
    when market is closed). v3.66.0+: variable was renamed to `sess`."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    headline_idx = text.index("def _render_price_headline")
    next_def_idx = text.index("\ndef ", headline_idx + 1)
    body = text[headline_idx:next_def_idx]
    # Either old name (`session.is_open`) or refactored name (`sess.is_open`)
    assert ("session.is_open" in body) or ("sess.is_open" in body)
    assert "Markets closed" in body


def test_live_positions_relabels_day_pl_when_closed():
    """view_live_positions must show 'Last session ({date})' instead of
    'Day P&L' when market is closed. v3.66.0+: the OPEN-vs-CLOSED branch
    moved into the shared _render_day_pl_card helper, so the view now
    just delegates to it."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    view_idx = text.index("def view_live_positions")
    next_def_idx = text.index("\ndef ", view_idx + 1)
    body = text[view_idx:next_def_idx]
    # Either inlined the branch (pre-v3.66.0) or delegates to the helper
    delegates_to_helper = "_render_day_pl_card" in body
    inlined_branch = "Last session" in body and "session.is_open" in body
    assert delegates_to_helper or inlined_branch, \
        "view_live_positions must either inline the closed-market relabel " \
        "or call _render_day_pl_card"
    # Either way, the helper itself must contain the relabel (catches
    # the case where the helper got broken)
    helper_idx = text.index("def _render_day_pl_card")
    helper_next = text.index("\ndef ", helper_idx + 1)
    helper_body = text[helper_idx:helper_next]
    assert "Last session" in helper_body
    assert "is_open" in helper_body


def test_market_ribbon_shows_closed_badge():
    """Market ribbon must show MARKET OPEN or CLOSED · last close badge."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    ribbon_idx = text.index("def _render_market_ribbon")
    next_def_idx = text.index("\ndef ", ribbon_idx + 1)
    body = text[ribbon_idx:next_def_idx]
    assert "MARKET OPEN" in body
    assert "CLOSED" in body
    assert "last close" in body

"""Tests for v3.70.0 — per-symbol poll cadence.

HOT: symbol is within ±2 days of scheduled earnings → 60s cadence.
WARM: every other case → 300s cadence (the v3.68.3 default).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# classify() — direction × proximity → cadence
# ============================================================
def test_classify_hot_when_earnings_today():
    from trader.poll_schedule import classify
    today = date(2026, 5, 5)
    cad, secs = classify(today, today=today)
    assert cad == "HOT"
    assert secs == 60


def test_classify_hot_within_two_days_either_side():
    from trader.poll_schedule import classify
    today = date(2026, 5, 5)
    # T-2 through T+2 are HOT
    for offset in (-2, -1, 0, 1, 2):
        ed = today + timedelta(days=offset)
        cad, _ = classify(ed, today=today)
        assert cad == "HOT", f"offset {offset:+d} should be HOT"
    # T-3 and T+3 are WARM
    for offset in (-3, 3):
        ed = today + timedelta(days=offset)
        cad, _ = classify(ed, today=today)
        assert cad == "WARM", f"offset {offset:+d} should be WARM"


def test_classify_warm_when_far_out():
    from trader.poll_schedule import classify
    today = date(2026, 5, 5)
    # 30 days out → WARM
    cad, secs = classify(today + timedelta(days=30), today=today)
    assert cad == "WARM"
    assert secs == 300


def test_classify_warm_when_no_earnings_date():
    """Symbols whose earnings calendar lookup returned None (yfinance
    silently empty for some tickers) must default to WARM, not HOT."""
    from trader.poll_schedule import classify
    cad, secs = classify(None)
    assert cad == "WARM"
    assert secs == 300


def test_classify_handles_post_earnings_day():
    """Day-after-earnings (T+1) is still HOT — catches the post-earnings
    follow-up 8-K (often filed T+1 morning with conference-call
    transcript exhibits)."""
    from trader.poll_schedule import classify
    today = date(2026, 5, 5)
    yesterday_earnings = date(2026, 5, 4)
    cad, _ = classify(yesterday_earnings, today=today)
    assert cad == "HOT"


# ============================================================
# build_schedule() with mocked next_earnings_fn
# ============================================================
def test_build_schedule_uses_injected_fn():
    """Test must NOT hit the real earnings calendar (paid API) — inject
    a fake lookup that returns deterministic dates."""
    from trader.poll_schedule import build_schedule
    today = date(2026, 5, 5)
    # AAPL reports today, NVDA in 30 days, AMD has no scheduled date
    fake_dates = {
        "AAPL": today,
        "NVDA": today + timedelta(days=30),
        "AMD": None,
    }
    sched = build_schedule(
        ["AAPL", "NVDA", "AMD"],
        next_earnings_fn=lambda s: fake_dates.get(s),
        today=today,
    )
    assert sched["AAPL"].cadence == "HOT"
    assert sched["AAPL"].cadence_seconds == 60
    assert sched["NVDA"].cadence == "WARM"
    assert sched["AMD"].cadence == "WARM"
    # next_earnings_date stored (for dashboard display)
    assert sched["AAPL"].next_earnings_date == today
    assert sched["AMD"].next_earnings_date is None


def test_build_schedule_handles_lookup_exception():
    """If the earnings calendar throws (rate limit, network), classify
    that symbol as WARM — no exception propagates to the daemon."""
    from trader.poll_schedule import build_schedule

    def failing_fn(sym):
        raise RuntimeError("simulated rate limit")

    sched = build_schedule(
        ["NVDA"], next_earnings_fn=failing_fn,
        today=date(2026, 5, 5),
    )
    assert sched["NVDA"].cadence == "WARM"
    assert sched["NVDA"].next_earnings_date is None


def test_build_schedule_first_poll_is_immediate():
    """Daemon's first iter should poll EVERY symbol, regardless of
    cadence. The schedule sets next_poll_at = now so the loop's
    is_due() returns True for all."""
    from trader.poll_schedule import build_schedule
    sched = build_schedule(
        ["A", "B"], next_earnings_fn=lambda s: None,
    )
    now = datetime.utcnow()
    for s in sched.values():
        assert s.is_due(now)


# ============================================================
# refresh_classifications — daily roll
# ============================================================
def test_refresh_classifications_rolls_into_hot():
    """Symbol whose earnings date was 5 days out yesterday becomes
    HOT today (4 days out crosses T-3, then T-2 next day)."""
    from trader.poll_schedule import build_schedule, refresh_classifications
    earnings = date(2026, 5, 8)
    sched = build_schedule(
        ["NVDA"], next_earnings_fn=lambda s: earnings,
        today=date(2026, 5, 5),  # T-3 → WARM
    )
    assert sched["NVDA"].cadence == "WARM"
    n_changed = refresh_classifications(sched, today=date(2026, 5, 6))
    # T-2 → HOT
    assert sched["NVDA"].cadence == "HOT"
    assert sched["NVDA"].cadence_seconds == 60
    assert n_changed == 1


def test_refresh_classifications_rolls_out_of_hot():
    """Symbol that was HOT yesterday rolls back to WARM once outside
    the window (e.g. T+3 after earnings)."""
    from trader.poll_schedule import build_schedule, refresh_classifications
    earnings = date(2026, 5, 5)
    sched = build_schedule(
        ["NVDA"], next_earnings_fn=lambda s: earnings,
        today=date(2026, 5, 6),  # T+1, HOT
    )
    assert sched["NVDA"].cadence == "HOT"
    n_changed = refresh_classifications(sched, today=date(2026, 5, 8))
    # T+3 → WARM
    assert sched["NVDA"].cadence == "WARM"
    assert n_changed == 1


def test_refresh_classifications_no_change_no_count():
    """Reclassifying a stable symbol returns 0 changed — used for
    daemon log output."""
    from trader.poll_schedule import build_schedule, refresh_classifications
    sched = build_schedule(
        ["X"], next_earnings_fn=lambda s: None,
    )
    n = refresh_classifications(sched, today=date(2026, 5, 6))
    assert n == 0


# ============================================================
# is_due / mark_polled — outer loop primitives
# ============================================================
def test_is_due_initial_immediate():
    from trader.poll_schedule import build_schedule
    sched = build_schedule(["A"], next_earnings_fn=lambda s: None)
    s = sched["A"]
    assert s.is_due(datetime.utcnow())


def test_is_due_false_after_mark_polled():
    """After mark_polled, the next poll is cadence_seconds away."""
    from trader.poll_schedule import build_schedule
    sched = build_schedule(["A"], next_earnings_fn=lambda s: None)
    s = sched["A"]
    now = datetime.utcnow()
    s.mark_polled(now)
    # Just after polling, not due
    assert not s.is_due(now + timedelta(seconds=10))
    assert not s.is_due(now + timedelta(seconds=299))
    # After cadence_seconds passes, due again
    assert s.is_due(now + timedelta(seconds=301))


def test_hot_symbol_due_60s_after_poll():
    """HOT symbol next poll = 60s after last."""
    from trader.poll_schedule import build_schedule
    sched = build_schedule(
        ["NVDA"], next_earnings_fn=lambda s: date(2026, 5, 5),
        today=date(2026, 5, 5),
    )
    s = sched["NVDA"]
    assert s.cadence == "HOT"
    now = datetime.utcnow()
    s.mark_polled(now)
    assert not s.is_due(now + timedelta(seconds=30))
    assert s.is_due(now + timedelta(seconds=61))


# ============================================================
# Helper queries
# ============================================================
def test_hot_symbols_returns_subset():
    from trader.poll_schedule import build_schedule, hot_symbols
    today = date(2026, 5, 5)
    fake = {"AAPL": today, "NVDA": today + timedelta(days=30), "AMD": None}
    sched = build_schedule(
        list(fake.keys()),
        next_earnings_fn=lambda s: fake[s],
        today=today,
    )
    assert hot_symbols(sched) == ["AAPL"]


def test_due_symbols_filters_by_time():
    from trader.poll_schedule import build_schedule, due_symbols
    sched = build_schedule(
        ["A", "B"], next_earnings_fn=lambda s: None,
    )
    now = datetime.utcnow()
    # Both due at start
    assert set(due_symbols(sched, now)) == {"A", "B"}
    # Mark A polled — only B due
    sched["A"].mark_polled(now)
    assert due_symbols(sched, now + timedelta(seconds=10)) == ["B"]


# ============================================================
# Daemon wiring
# ============================================================
def test_watch_loop_uses_per_symbol_schedule():
    """The CLI script's _watch_loop must consume poll_schedule, not
    the old single-cadence sleep loop."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "earnings_reactor.py"
    text = p.read_text()
    assert "from trader.poll_schedule import" in text
    assert "build_schedule" in text
    assert "due_symbols" in text
    # Per-symbol mark_polled instead of single global sleep
    assert "mark_polled" in text


def test_watch_loop_emits_hot_warm_split_in_iter_lines():
    p = Path(__file__).resolve().parent.parent / "scripts" / "earnings_reactor.py"
    text = p.read_text()
    # The iter-line print must show H/W split so log readers can
    # see HOT vs WARM activity at a glance
    assert "1H/14W" in text or "(hot_count" in text or "H/" in text


def test_watch_loop_refreshes_schedule_at_midnight():
    """Daily UTC-midnight roll triggers schedule rebuild + reclassification."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "earnings_reactor.py"
    text = p.read_text()
    assert "last_schedule_refresh" in text
    assert "now.date() > last_schedule_refresh" in text


def test_dashboard_shows_polling_schedule():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "Polling schedule" in text
    assert "from trader.poll_schedule import" in text


def test_dashboard_version_v3_70_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.70.0 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.70.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"

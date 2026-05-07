"""Tests for v3.60.1 verification audit + new backtest scripts."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def test_chaos_holiday_dates_against_known():
    """Spot-check holiday calendar against authoritative US Federal Reserve /
    NYSE-published 2024-2026 dates.
    Source: nyse.com/markets/hours-calendars and federalreserve.gov."""
    from trader.chaos_cases import is_market_holiday
    from datetime import date as _date
    # 2024 — confirmed correct
    assert is_market_holiday(_date(2024, 1, 1))   # New Year
    assert is_market_holiday(_date(2024, 12, 25)) # Christmas
    assert is_market_holiday(_date(2024, 7, 4))   # Independence
    # 2025 — Carter day of mourning Jan 9 + standard
    assert is_market_holiday(_date(2025, 1, 9))   # Carter
    assert is_market_holiday(_date(2025, 1, 20))  # MLK
    assert is_market_holiday(_date(2025, 4, 18))  # Good Friday
    # 2026 — observed Jul 3 since Jul 4 is Saturday
    assert is_market_holiday(_date(2026, 7, 3))   # Independence observed
    assert is_market_holiday(_date(2026, 5, 25))  # Memorial Day
    # Non-holidays
    assert not is_market_holiday(_date(2025, 5, 5))   # random Monday
    assert not is_market_holiday(_date(2026, 5, 4))   # random Monday


def test_dst_dates_correct():
    """Verify DST transition dates against US convention.
    Source: nist.gov + 15 USC 260a (DST runs 2nd Sun of March → 1st Sun of November)."""
    from trader.chaos_cases import is_dst_transition_day
    from datetime import date as _date
    # 2025: 2nd Sun of March = March 9; 1st Sun of November = November 2
    is_dst, direction = is_dst_transition_day(_date(2025, 3, 9))
    assert is_dst and direction == "spring_forward"
    is_dst, direction = is_dst_transition_day(_date(2025, 11, 2))
    assert is_dst and direction == "fall_back"
    # 2026: 2nd Sun of March = March 8; 1st Sun of November = November 1
    is_dst, direction = is_dst_transition_day(_date(2026, 3, 8))
    assert is_dst and direction == "spring_forward"
    is_dst, direction = is_dst_transition_day(_date(2026, 11, 1))
    assert is_dst and direction == "fall_back"


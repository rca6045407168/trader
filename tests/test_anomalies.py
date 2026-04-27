"""Tests for the anomaly scanner. Each detector has expected fire / no-fire dates."""
from datetime import date
from trader.anomalies import (
    detect_turn_of_month, detect_opex_week, detect_pre_fomc,
    detect_year_end_reversal, scan_anomalies,
)


def test_turn_of_month_fires_near_month_end():
    # April 30 is one day before May 1 — within the 0-2 day window
    a = detect_turn_of_month(date(2026, 4, 30))
    assert a is not None
    assert a.target_symbol == "SPY"


def test_turn_of_month_silent_mid_month():
    a = detect_turn_of_month(date(2026, 4, 15))
    assert a is None


def test_opex_week_fires_in_third_week():
    # Third Friday of April 2026 is the 17th. Apr 13 is Mon of OPEX week.
    a = detect_opex_week(date(2026, 4, 13))
    assert a is not None


def test_pre_fomc_fires_one_day_before():
    a = detect_pre_fomc(date(2026, 4, 28))  # FOMC is Apr 29 in our 2026 list
    assert a is not None
    assert "FOMC" in a.rationale
    assert a.confidence == "high"


def test_pre_fomc_silent_far_from_meeting():
    a = detect_pre_fomc(date(2026, 4, 10))
    assert a is None


def test_year_end_reversal_fires_late_december():
    a = detect_year_end_reversal(date(2026, 12, 22))
    assert a is not None


def test_year_end_reversal_silent_january():
    a = detect_year_end_reversal(date(2026, 1, 15))
    assert a is None


def test_scan_combines_detectors():
    # April 28 2026: pre-FOMC fires, nothing else
    anomalies = scan_anomalies(date(2026, 4, 28))
    assert any(a.name == "Pre-FOMC drift" for a in anomalies)

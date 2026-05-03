"""Tests for v3.60.1 verification audit + new backtest scripts."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def test_audit_doc_exists():
    p = Path(__file__).resolve().parent.parent / "docs" / "VERIFICATION_AUDIT_2026_05_03.md"
    assert p.exists()
    text = p.read_text()
    # Must mention each refuted claim
    for refuted in ("MomentumCrashDetector", "Residual momentum",
                     "TrailingStop", "SectorNeutralizer", "EarningsRule"):
        assert refuted in text


def test_crash_detector_backtest_module_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "backtest_crash_detector.py"
    assert p.exists()
    text = p.read_text()
    assert "compute_signal_history" in text
    assert "regime_stats" in text


def test_residual_momentum_backtest_module_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "backtest_residual_momentum.py"
    assert p.exists()


def test_overlay_backtest_module_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "backtest_overlays.py"
    assert p.exists()


def test_walkforward_significance_module_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "verify_walkforward_significance.py"
    assert p.exists()


def test_cost_impact_marks_refuted_items():
    """The cost-impact report must now mark refuted items as such."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir

    refuted_count = sum(1 for f in cir.FLIPS
                         if f.verification_status == "REFUTED")
    untested_count = sum(1 for f in cir.FLIPS
                          if f.verification_status == "UNTESTED")
    # At least 4 should be marked REFUTED post-audit
    assert refuted_count >= 4, f"only {refuted_count} REFUTED entries; expected ≥4"


def test_cost_impact_no_unverified_recommendations():
    """Recommended flips must NOT include any REFUTED items."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir
    # Apply the same filter as the script's main():
    rec = [f for f in cir.FLIPS
           if f.annual_bps_estimate > 0
           and f.confidence in ("high", "medium")
           and not f.requires_more_data]
    for f in rec:
        assert f.verification_status != "REFUTED", \
            f"recommended flip {f.name!r} is REFUTED — should not be in recommended set"


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


def test_walkforward_sharpe_significantly_positive_documented():
    """The audit doc must record the bootstrap-CI verdict on Sharpe."""
    p = Path(__file__).resolve().parent.parent / "docs" / "VERIFICATION_AUDIT_2026_05_03.md"
    text = p.read_text()
    assert "+0.55" in text  # the corrected aggregate Sharpe
    assert "[+0.12, +0.98]" in text  # 95% CI

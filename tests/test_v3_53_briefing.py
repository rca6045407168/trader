"""Tests for copilot_briefing module."""
from __future__ import annotations

import pytest


def test_briefing_renders_markdown():
    from trader.copilot_briefing import MorningBriefing
    b = MorningBriefing(headline="Test")
    md = b.to_markdown()
    assert "Test" in md


def test_briefing_with_data_includes_metrics():
    from trader.copilot_briefing import MorningBriefing
    b = MorningBriefing(
        headline="🟡 Transition regime",
        equity_now=106503.15,
        day_pl_pct=0.0067,
        spy_today_pct=0.0028,
        excess_today_pct=0.0039,
        regime="transition",
        regime_overlay_mult=0.94,
        regime_enabled=False,
    )
    md = b.to_markdown()
    assert "$106,503" in md
    assert "+0.67%" in md  # day P&L
    assert "+0.39%" in md  # excess
    assert "TRANSITION" in md
    # v3.62.0: replaced "[DISABLED]" with friendlier wording
    assert "not enforcing" in md


def test_briefing_freeze_renders_alert():
    from trader.copilot_briefing import MorningBriefing
    b = MorningBriefing(
        headline="🚨 Action required",
        freeze_active=True,
        freeze_reason="DAILY-LOSS FREEZE until 2026-05-04",
    )
    md = b.to_markdown()
    assert "FREEZE ACTIVE" in md
    assert "DAILY-LOSS" in md


def test_compute_briefing_runs_without_crashing():
    """compute_briefing must produce SOMETHING even with broken broker creds."""
    from trader.copilot_briefing import compute_briefing
    b = compute_briefing()
    assert b is not None
    assert b.headline  # always has a headline
    md = b.to_markdown()
    assert len(md) > 10


def test_briefing_to_markdown_handles_no_data():
    from trader.copilot_briefing import MorningBriefing
    b = MorningBriefing()
    md = b.to_markdown()
    # Should not crash even if all fields are None
    assert isinstance(md, str)

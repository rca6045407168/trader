"""Tests for v3.46 new modules: deployment_anchor, override_delay, peek_counter."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ============================================================================
# deployment_anchor
# ============================================================================

class TestDeploymentAnchor:

    @pytest.fixture(autouse=True)
    def isolated_anchor(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trader.deployment_anchor.ANCHOR_PATH", tmp_path / "anchor.json")

    def test_first_call_sets_anchor(self):
        from trader.deployment_anchor import get_or_set_anchor
        a = get_or_set_anchor(100_000)
        assert a.equity_at_deploy == 100_000
        assert a.source == "auto"

    def test_second_call_returns_existing(self):
        from trader.deployment_anchor import get_or_set_anchor
        a = get_or_set_anchor(100_000)
        b = get_or_set_anchor(75_000)  # different equity
        assert b.equity_at_deploy == 100_000  # original preserved

    def test_drawdown_calculation(self):
        from trader.deployment_anchor import get_or_set_anchor, drawdown_from_deployment
        get_or_set_anchor(100_000)
        dd, anchor = drawdown_from_deployment(75_000)
        assert abs(dd - (-0.25)) < 1e-6
        assert anchor.equity_at_deploy == 100_000

    def test_reset_requires_long_reason(self):
        from trader.deployment_anchor import reset_anchor
        with pytest.raises(ValueError, match="≥50 chars"):
            reset_anchor(100_000, "too short")
        # Long enough reason should work
        a = reset_anchor(50_000,
                          "post-mortem completed for the -33% drawdown event in scenario X" * 1,
                          "docs/POST_MORTEM_test.md")
        assert a.equity_at_deploy == 50_000
        assert a.source == "post_mortem_reset"


# ============================================================================
# override_delay
# ============================================================================

class TestOverrideDelay:

    @pytest.fixture(autouse=True)
    def isolated_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trader.override_delay.STATE_PATH", tmp_path / "live_config_sha.json")
        monkeypatch.setattr("trader.override_delay.BYPASS_PATH", tmp_path / "override_delay_bypass")

    def test_first_run_records_and_proceeds(self):
        from trader.override_delay import check_override_delay
        allowed, reason = check_override_delay()
        assert allowed
        assert "first run" in reason.lower() or "recorded" in reason.lower()

    def test_unchanged_config_proceeds(self):
        from trader.override_delay import check_override_delay
        check_override_delay()  # first call records
        allowed, reason = check_override_delay()
        assert allowed
        assert "unchanged" in reason.lower()

    def test_bypass_file_overrides(self, tmp_path, monkeypatch):
        from trader.override_delay import check_override_delay, BYPASS_PATH
        # Create the bypass file
        BYPASS_PATH.parent.mkdir(parents=True, exist_ok=True)
        BYPASS_PATH.write_text("emergency override")
        allowed, reason = check_override_delay()
        assert allowed
        assert "BYPASS" in reason


# ============================================================================
# peek_counter
# ============================================================================

class TestPeekCounter:

    @pytest.fixture(autouse=True)
    def isolated_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trader.peek_counter.PEEK_LOG_PATH", tmp_path / "peek_log.json")

    def test_scheduled_event_not_recorded(self, monkeypatch):
        from trader.peek_counter import record_event_if_manual
        monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
        was_manual, count = record_event_if_manual()
        assert not was_manual
        assert count == 0

    def test_manual_event_recorded(self, monkeypatch):
        from trader.peek_counter import record_event_if_manual
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        was_manual, count = record_event_if_manual()
        assert was_manual
        assert count == 1

    def test_alert_triggers_above_threshold(self, monkeypatch):
        from trader.peek_counter import record_event_if_manual, peek_alert_message
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        for _ in range(5):
            record_event_if_manual()
        # 5 manual triggers > threshold of 3
        _, count = record_event_if_manual()
        assert count >= 4
        msg = peek_alert_message(count)
        assert msg is not None
        assert "PEEK" in msg

    def test_alert_silent_below_threshold(self):
        from trader.peek_counter import peek_alert_message
        assert peek_alert_message(2) is None
        assert peek_alert_message(3) is None
        assert peek_alert_message(4) is not None

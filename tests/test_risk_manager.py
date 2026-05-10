"""Unit tests for risk_manager. Each kill switch and cap has a passing + failing case.

v3.46 update: tightened to 16% per-position, -6% daily, -25% deploy-DD freeze,
-33% liquidation gate. Tests use monkeypatched freeze-state and deployment-anchor
modules to avoid touching real state files.
"""
import json
import pytest
from pathlib import Path
from trader.risk_manager import (
    check_account_risk, vol_scale,
    MAX_POSITION_PCT, MAX_GROSS_EXPOSURE,
    MAX_DEPLOY_DD_FREEZE_PCT, MAX_DEPLOY_DD_LIQUIDATION_PCT,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect freeze-state + anchor files to a tmp dir per-test for isolation."""
    monkeypatch.setattr("trader.risk_manager.FREEZE_STATE_PATH", tmp_path / "freeze.json")
    monkeypatch.setattr("trader.deployment_anchor.ANCHOR_PATH", tmp_path / "anchor.json")
    yield


def test_vol_scale_low_vix():
    assert vol_scale(10) == 1.0
    assert vol_scale(14.9) == 1.0


def test_vol_scale_med_vix():
    assert vol_scale(18) == 0.85
    assert vol_scale(22) == 0.70
    assert vol_scale(28) == 0.50


def test_vol_scale_high_vix():
    assert vol_scale(35) == 0.30
    assert vol_scale(50) == 0.30


def test_vol_scale_none():
    assert vol_scale(None) == 1.0


def test_position_cap_applied(monkeypatch):
    """v3.46: cap is now 16%. Targets of 0.18 exceed; 0.10 don't."""
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=10_000: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    # 0.18 > 0.16 → would REFUSE per safety margin check
    # Use 0.10 + 0.05 (safe) to test that clipping doesn't fire when nothing exceeds
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    assert decision.adjusted_targets["AAPL"] == 0.10
    assert decision.adjusted_targets["MSFT"] == 0.05


def test_gross_exposure_cap(monkeypatch):
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=10_000: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    # 20 names at the position cap = 320% gross, must scale down to 95%
    targets = {f"T{i}": MAX_POSITION_PCT for i in range(20)}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    total = sum(decision.adjusted_targets.values())
    assert total <= MAX_GROSS_EXPOSURE + 0.0001


def test_daily_loss_halt(monkeypatch):
    """v3.46: daily-loss is now -6% (was -3%). 90k → 100k yest is -10% → HALT."""
    snaps = [
        {"date": "2026-04-25", "equity": 90_000},
        {"date": "2026-04-24", "equity": 100_000},
    ]
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    decision = check_account_risk(equity=90_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "daily loss" in decision.reason.lower()
    assert "freeze" in decision.reason.lower()


def test_daily_loss_below_threshold_proceeds(monkeypatch):
    """v3.46: 95k from 100k yesterday is -5% → above -6% threshold → proceed.

    v3.73.24: also disable the v3.58 all-time-peak circuit breaker for
    this test — it would otherwise trip on the synthetic snapshots.
    """
    snaps = [
        {"date": "2026-04-25", "equity": 95_000},
        {"date": "2026-04-24", "equity": 100_000},
    ]
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    monkeypatch.setattr("trader.journal.recent_snapshots",
                         lambda days=180: snaps)
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    decision = check_account_risk(equity=95_000, targets={"AAPL": 0.05}, vix=15)
    assert decision.proceed


def test_drawdown_halt(monkeypatch):
    snaps = [
        {"date": f"2026-04-{i:02d}", "equity": 100_000 + (15_000 if i == 1 else 0)}
        for i in range(1, 26)
    ]
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    # equity 80k vs peak 115k = -30% drawdown — but daily-loss check would fire first
    # Use a smoother path so daily-loss isn't tripped: 80k vs 80k yesterday (no day-loss)
    snaps[0] = {"date": "2026-04-25", "equity": 80_000}
    snaps[1] = {"date": "2026-04-24", "equity": 80_500}
    decision = check_account_risk(equity=80_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "drawdown" in decision.reason.lower()


def test_slow_drawdown_caught_by_180d_window(monkeypatch):
    """v3.27 regression: a slow 60-day drawdown was masked when peak window
    was 30 days. With 180-day window, peak is preserved."""
    snaps = []
    for i in range(180, 0, -1):
        if i > 90:
            equity = 100_000
        else:
            equity = 120_000 - ((90 - i) / 90) * 11_000
        snaps.append({"date": f"day-{i}", "equity": equity})
    snaps.reverse()
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    decision = check_account_risk(equity=109_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed, "Slow drawdown -9% from 90-day peak must HALT"
    assert "drawdown" in decision.reason.lower()
    assert "180" in decision.reason


def test_position_safety_margin_rejects_excessive_target(monkeypatch):
    """v3.46: tightened cap is 16%. Target of 18% must REFUSE."""
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=10_000: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    targets = {"AAPL": 0.18, "MSFT": 0.05}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert not decision.proceed
    assert "MAX_POSITION_PCT" in decision.reason or "position" in decision.reason.lower()


def test_position_near_cap_warns(monkeypatch):
    """v3.46: warns within 2% of cap. 0.15 is within margin of 0.16."""
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=10_000: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    targets = {"AAPL": 0.15, "MSFT": 0.05}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    assert any("cap" in w.lower() for w in decision.warnings)


# v3.46 NEW: deployment-DD gate tests

def test_deploy_dd_freeze_at_minus_25pct(monkeypatch, tmp_path):
    """v3.46: -25% from deployment anchor triggers 30-day freeze.

    v3.73.24: also disable the v3.58 all-time-peak circuit breaker for
    this test — it would otherwise trip first on the synthetic equity.
    """
    # Set up deployment anchor at $100k
    anchor_data = {
        "equity_at_deploy": 100_000.0,
        "deploy_timestamp": "2026-01-01T00:00:00",
        "source": "auto",
        "notes": "test",
    }
    (tmp_path / "anchor.json").write_text(json.dumps(anchor_data))
    monkeypatch.setattr("trader.deployment_anchor.ANCHOR_PATH", tmp_path / "anchor.json")
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=180: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    # Equity at $74k = -26% from $100k → triggers freeze
    decision = check_account_risk(equity=74_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "deployment" in decision.reason.lower()
    assert "freeze" in decision.reason.lower()


def test_deploy_dd_liquidation_at_minus_33pct(monkeypatch, tmp_path):
    """v3.46: -33% from deployment anchor triggers liquidation gate."""
    anchor_data = {
        "equity_at_deploy": 100_000.0,
        "deploy_timestamp": "2026-01-01T00:00:00",
        "source": "auto",
        "notes": "test",
    }
    (tmp_path / "anchor.json").write_text(json.dumps(anchor_data))
    monkeypatch.setattr("trader.deployment_anchor.ANCHOR_PATH", tmp_path / "anchor.json")
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=180: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    # Equity at $66k = -34% from $100k → liquidation gate
    decision = check_account_risk(equity=66_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "liquidation" in decision.reason.lower()


def test_deploy_dd_warns_at_minus_15pct(monkeypatch, tmp_path):
    """v3.46: warns at -15% (approaching freeze) but proceeds."""
    anchor_data = {
        "equity_at_deploy": 100_000.0,
        "deploy_timestamp": "2026-01-01T00:00:00",
        "source": "auto",
        "notes": "test",
    }
    (tmp_path / "anchor.json").write_text(json.dumps(anchor_data))
    monkeypatch.setattr("trader.deployment_anchor.ANCHOR_PATH", tmp_path / "anchor.json")
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    monkeypatch.setattr("trader.journal.recent_snapshots", lambda days=180: [])
    monkeypatch.setenv("DRAWDOWN_BREAKER_STATUS", "SHADOW")
    decision = check_account_risk(equity=83_000, targets={"AAPL": 0.05}, vix=15)
    assert decision.proceed
    assert any("deployment dd" in w.lower() for w in decision.warnings)

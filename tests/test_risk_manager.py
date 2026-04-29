"""Unit tests for risk_manager. Each kill switch and cap has a passing + failing case."""
import pytest
from trader.risk_manager import (
    check_account_risk, vol_scale,
    MAX_POSITION_PCT, MAX_GROSS_EXPOSURE,
)


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
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=30: [])
    targets = {"AAPL": 0.30, "MSFT": 0.05}  # AAPL exceeds cap
    # Use vix=10 — well below the 15 vol-scaling threshold so no scale is applied
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    assert decision.adjusted_targets["AAPL"] == MAX_POSITION_PCT
    assert decision.adjusted_targets["MSFT"] == 0.05


def test_gross_exposure_cap(monkeypatch):
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=30: [])
    # 20 names at the position cap = 200% gross, must scale down
    targets = {f"T{i}": MAX_POSITION_PCT for i in range(20)}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    total = sum(decision.adjusted_targets.values())
    assert total <= MAX_GROSS_EXPOSURE + 0.0001


def test_daily_loss_halt(monkeypatch):
    snaps = [
        {"date": "2026-04-25", "equity": 90_000},
        {"date": "2026-04-24", "equity": 100_000},
    ]
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=30: snaps)
    decision = check_account_risk(equity=90_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "daily loss" in decision.reason.lower()


def test_drawdown_halt(monkeypatch):
    snaps = [
        {"date": f"2026-04-{i:02d}", "equity": 100_000 + (15_000 if i == 1 else 0)}
        for i in range(1, 26)
    ]
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    # equity 80k vs peak 115k = -30% drawdown
    decision = check_account_risk(equity=80_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed
    assert "drawdown" in decision.reason.lower()


def test_slow_drawdown_caught_by_180d_window(monkeypatch):
    """v3.27 regression: a slow 60-day drawdown was masked when peak window
    was 30 days (peak walked down with the drawdown). With 180-day window,
    peak is preserved and -8% threshold fires correctly."""
    # Build snapshots: peak at $120k 90 days ago, declining to $109k today
    # = -9.2% from true peak, should HALT
    # But 30-day window's max would be ~$115k → only -5.2% → would NOT halt (the bug)
    snaps = []
    for i in range(180, 0, -1):
        # Linear decline from peak 90 days ago
        if i > 90:
            equity = 100_000  # before peak
        elif i > 0:
            # Peak at i=90, then linear decline to today (i=0)
            equity = 120_000 - ((90 - i) / 90) * 11_000  # 120k → 109k over 90 days
        snaps.append({"date": f"day-{i}", "equity": equity})
    snaps.reverse()  # newest-first per recent_snapshots contract
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: snaps)
    decision = check_account_risk(equity=109_000, targets={"AAPL": 0.05}, vix=15)
    assert not decision.proceed, "Slow drawdown -9% from 90-day peak must HALT"
    assert "drawdown" in decision.reason.lower()
    assert "180" in decision.reason  # confirms longer window is reported


def test_position_safety_margin_rejects_excessive_target(monkeypatch):
    """v3.27 safety margin: variant requesting 35% per name (above the 30% cap)
    should be REFUSED rather than silently clipped."""
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    # 0.35 > MAX_POSITION_PCT (0.30) — must REFUSE
    targets = {"AAPL": 0.35, "MSFT": 0.10}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert not decision.proceed
    assert "MAX_POSITION_PCT" in decision.reason or "position" in decision.reason.lower()


def test_position_near_cap_warns(monkeypatch):
    """v3.27: targets near the cap (within safety margin) should WARN but proceed."""
    monkeypatch.setattr("trader.risk_manager.recent_snapshots", lambda days=180: [])
    # 0.28 is within MAX_POSITION_SAFETY_MARGIN (0.03) of 0.30 — warn but proceed
    targets = {"AAPL": 0.28, "MSFT": 0.10}
    decision = check_account_risk(equity=100_000, targets=targets, vix=10)
    assert decision.proceed
    assert any("cap" in w.lower() for w in decision.warnings)

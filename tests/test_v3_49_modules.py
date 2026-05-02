"""Tests for v3.49 world-class build modules:
  - regime_overlay (HMM + macro + GARCH composite)
  - meta_allocator (capital allocation across LIVE sleeves)
  - intraday_risk (defensive intraday DD monitor)

These tests run in the CI Docker container — local arm64 has a numpy
architecture mismatch. The clean Docker image (python:3.11-slim) is what
we ship to prod, so we test there.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# -------------------- regime_overlay --------------------

def test_overlay_disabled_by_default_returns_one():
    """When REGIME_OVERLAY_ENABLED is unset, get_gross_multiplier returns 1.0
    no matter what the underlying signals are. Critical: existing LIVE
    behavior unchanged unless someone explicitly opts in."""
    from trader.regime_overlay import get_gross_multiplier
    # Default-no-flag should return 1.0
    if "REGIME_OVERLAY_ENABLED" in os.environ:
        del os.environ["REGIME_OVERLAY_ENABLED"]
    # Force module reload to pick up env
    import importlib
    import trader.regime_overlay
    importlib.reload(trader.regime_overlay)
    from trader.regime_overlay import get_gross_multiplier as get_mult_fresh
    val = get_mult_fresh()
    assert val == 1.0, f"disabled overlay must return 1.0, got {val}"


def test_overlay_signal_dataclass_fields_present():
    """OverlaySignal must expose the rationale + sub-multipliers for logging."""
    from trader.regime_overlay import OverlaySignal
    s = OverlaySignal()
    # Defaults are safe (1.0 multipliers)
    assert s.hmm_mult == 1.0
    assert s.macro_mult == 1.0
    assert s.garch_mult == 1.0
    assert s.final_mult == 1.0
    assert isinstance(s.rationale, str)
    assert "hmm" in s.rationale
    assert "macro" in s.rationale
    assert "garch" in s.rationale


def test_overlay_failsafe_returns_one_when_signals_error():
    """When all 3 signal computations fail, final_mult must be 1.0
    (composition of 1.0 sub-multipliers). Never break LIVE on error."""
    from trader import regime_overlay
    with patch.object(regime_overlay, "_compute_hmm_mult",
                      return_value=(1.0, "error", 0.0, "mock")), \
         patch.object(regime_overlay, "_compute_macro_mult",
                      return_value=(1.0, False, False, "mock")), \
         patch.object(regime_overlay, "_compute_garch_mult",
                      return_value=(1.0, None, "mock")):
        sig = regime_overlay.compute_overlay()
        assert sig.final_mult == 1.0


def test_overlay_combines_multiplicatively_clamped():
    """Final = product, clamped to [0, 1.20]."""
    from trader import regime_overlay
    with patch.object(regime_overlay, "_compute_hmm_mult",
                      return_value=(1.15, "bull", 0.9, None)), \
         patch.object(regime_overlay, "_compute_macro_mult",
                      return_value=(1.0, False, False, None)), \
         patch.object(regime_overlay, "_compute_garch_mult",
                      return_value=(1.10, 0.13, None)):
        sig = regime_overlay.compute_overlay()
        # 1.15 * 1.0 * 1.10 = 1.265 -> clamped to 1.20
        assert sig.final_mult == 1.20


def test_overlay_bear_macro_garch_compounds_to_floor():
    """Bear HMM + macro stress + high vol shouldn't amplify cuts beyond
    a sensible floor. Final still >= 0."""
    from trader import regime_overlay
    with patch.object(regime_overlay, "_compute_hmm_mult",
                      return_value=(0.30, "bear", 0.8, None)), \
         patch.object(regime_overlay, "_compute_macro_mult",
                      return_value=(0.55, True, True, None)), \
         patch.object(regime_overlay, "_compute_garch_mult",
                      return_value=(0.50, 0.30, None)):
        sig = regime_overlay.compute_overlay()
        # 0.30 * 0.55 * 0.50 = 0.0825 — small but not zero
        assert 0.05 < sig.final_mult < 0.15


# -------------------- meta_allocator --------------------

def test_allocator_single_live_default():
    """Default mode 'single_live' returns 100% to the lone LIVE variant.
    This is the bit-for-bit-unchanged path."""
    if "META_ALLOCATOR_MODE" in os.environ:
        del os.environ["META_ALLOCATOR_MODE"]
    import importlib
    import trader.meta_allocator
    importlib.reload(trader.meta_allocator)
    # Variants registry will be populated from variants.py import
    from trader import variants  # noqa: F401  registers
    from trader.meta_allocator import allocate
    decision = allocate()
    assert decision.mode == "single_live"
    # Sleeve weights sum to 1.0 (full gross to the one LIVE)
    assert sum(decision.sleeve_weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_allocator_handles_no_live_variants():
    """When no LIVE variant registered, allocator returns empty weights
    with a clear rationale (does NOT crash)."""
    from trader.meta_allocator import allocate, AllocatorDecision
    from trader import ab
    saved = dict(ab._REGISTRY)
    try:
        # Strip all LIVE variants
        for k in list(ab._REGISTRY.keys()):
            if ab._REGISTRY[k].status == "live":
                ab._REGISTRY[k].status = "paper"
        decision = allocate()
        assert decision.sleeve_weights == {}
        assert "no LIVE" in decision.rationale.lower()
    finally:
        ab._REGISTRY.clear()
        ab._REGISTRY.update(saved)


def test_apply_meta_allocation_combines_sleeves():
    """Verify per-sleeve targets combine correctly using the allocator weights."""
    from trader.meta_allocator import apply_meta_allocation, AllocatorDecision
    decision = AllocatorDecision(
        mode="equal_risk",
        sleeve_weights={"sleeve_a": 0.5, "sleeve_b": 0.5},
        rationale="test",
    )
    per_sleeve = {
        "sleeve_a": {"AAPL": 0.4, "MSFT": 0.4},  # 80% within sleeve
        "sleeve_b": {"GOOGL": 0.5, "AAPL": 0.3},  # AAPL appears in both
    }
    combined = apply_meta_allocation(per_sleeve, decision)
    # AAPL: 0.5 * 0.4 + 0.5 * 0.3 = 0.35
    # MSFT: 0.5 * 0.4 = 0.20
    # GOOGL: 0.5 * 0.5 = 0.25
    assert combined["AAPL"] == pytest.approx(0.35)
    assert combined["MSFT"] == pytest.approx(0.20)
    assert combined["GOOGL"] == pytest.approx(0.25)


def test_apply_meta_allocation_single_live_passes_through():
    """Single-LIVE mode: sleeve_a's targets become the final targets unchanged
    (since its sleeve_weight is 1.0)."""
    from trader.meta_allocator import apply_meta_allocation, AllocatorDecision
    decision = AllocatorDecision(
        mode="single_live",
        sleeve_weights={"sleeve_a": 1.0},
    )
    per_sleeve = {"sleeve_a": {"AAPL": 0.16, "MSFT": 0.10}}
    combined = apply_meta_allocation(per_sleeve, decision)
    assert combined == {"AAPL": 0.16, "MSFT": 0.10}


# -------------------- intraday_risk --------------------

def test_intraday_check_returns_dataclass():
    """check() returns an IntradayCheck dataclass with the expected fields."""
    from trader.intraday_risk import IntradayCheck
    c = IntradayCheck()
    assert c.action == "ok"
    assert c.equity_now == 0.0
    assert hasattr(c, "intraday_pnl_pct")
    assert hasattr(c, "deploy_dd_pct")
    assert hasattr(c, "rationale")
    assert hasattr(c, "timestamp")


def test_intraday_freeze_fires_when_intraday_dd_exceeds_threshold(tmp_path, monkeypatch):
    """Synthetic equity drop > 8% from day-open must fire freeze_intraday."""
    from trader import intraday_risk, risk_manager
    # Redirect data dir to tmp so we don't pollute real state
    monkeypatch.setattr(intraday_risk, "INTRADAY_LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(risk_manager, "FREEZE_STATE_PATH", tmp_path / "freeze.json")
    # Mock broker fetch + day-open lookup
    monkeypatch.setattr(intraday_risk, "_fetch_broker_equity",
                        lambda: (90_000.0, None))
    monkeypatch.setattr(intraday_risk, "_fetch_day_open_equity_from_log",
                        lambda: 100_000.0)
    # Mock deployment_anchor to avoid file I/O
    def mock_dd(eq):
        from trader.deployment_anchor import DeploymentAnchor
        anchor = DeploymentAnchor(equity_at_deploy=100_000.0,
                                   deploy_timestamp="2026-01-01T00:00:00",
                                   source="test", notes="")
        return -0.10, anchor
    monkeypatch.setattr("trader.deployment_anchor.drawdown_from_deployment", mock_dd)
    result = intraday_risk.check()
    assert result.action == "freeze_intraday"
    assert result.intraday_pnl_pct == pytest.approx(-0.10)
    # Freeze state file written
    assert (tmp_path / "freeze.json").exists()
    state = json.loads((tmp_path / "freeze.json").read_text())
    assert "daily_loss_freeze_until" in state


def test_intraday_warn_fires_below_freeze_threshold(tmp_path, monkeypatch):
    """-5% intraday DD: warn but no freeze."""
    from trader import intraday_risk, risk_manager
    monkeypatch.setattr(intraday_risk, "INTRADAY_LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(risk_manager, "FREEZE_STATE_PATH", tmp_path / "freeze.json")
    monkeypatch.setattr(intraday_risk, "_fetch_broker_equity",
                        lambda: (95_000.0, None))
    monkeypatch.setattr(intraday_risk, "_fetch_day_open_equity_from_log",
                        lambda: 100_000.0)
    def mock_dd(eq):
        from trader.deployment_anchor import DeploymentAnchor
        anchor = DeploymentAnchor(equity_at_deploy=100_000.0,
                                   deploy_timestamp="2026-01-01T00:00:00",
                                   source="test", notes="")
        return -0.05, anchor
    monkeypatch.setattr("trader.deployment_anchor.drawdown_from_deployment", mock_dd)
    result = intraday_risk.check()
    assert result.action == "warn"
    assert not (tmp_path / "freeze.json").exists()


def test_intraday_ok_when_equity_flat(tmp_path, monkeypatch):
    """Equity unchanged: action is 'ok', no freeze, log written."""
    from trader import intraday_risk, risk_manager
    monkeypatch.setattr(intraday_risk, "INTRADAY_LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(risk_manager, "FREEZE_STATE_PATH", tmp_path / "freeze.json")
    monkeypatch.setattr(intraday_risk, "_fetch_broker_equity",
                        lambda: (100_500.0, None))
    monkeypatch.setattr(intraday_risk, "_fetch_day_open_equity_from_log",
                        lambda: 100_000.0)
    def mock_dd(eq):
        from trader.deployment_anchor import DeploymentAnchor
        anchor = DeploymentAnchor(equity_at_deploy=100_000.0,
                                   deploy_timestamp="2026-01-01T00:00:00",
                                   source="test", notes="")
        return 0.005, anchor
    monkeypatch.setattr("trader.deployment_anchor.drawdown_from_deployment", mock_dd)
    result = intraday_risk.check()
    assert result.action == "ok"
    # Log written
    assert (tmp_path / "log.json").exists()


def test_intraday_liquidation_gate_fires_at_33pct_deploy_dd(tmp_path, monkeypatch):
    """Cumulative -33% from deployment anchor trips the liquidation gate."""
    from trader import intraday_risk, risk_manager
    monkeypatch.setattr(intraday_risk, "INTRADAY_LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(risk_manager, "FREEZE_STATE_PATH", tmp_path / "freeze.json")
    monkeypatch.setattr(intraday_risk, "_fetch_broker_equity",
                        lambda: (66_000.0, None))
    monkeypatch.setattr(intraday_risk, "_fetch_day_open_equity_from_log",
                        lambda: 70_000.0)
    def mock_dd(eq):
        from trader.deployment_anchor import DeploymentAnchor
        anchor = DeploymentAnchor(equity_at_deploy=100_000.0,
                                   deploy_timestamp="2026-01-01T00:00:00",
                                   source="test", notes="")
        return -0.34, anchor  # below -33%
    monkeypatch.setattr("trader.deployment_anchor.drawdown_from_deployment", mock_dd)
    result = intraday_risk.check()
    assert result.action == "freeze_liquidation"
    state = json.loads((tmp_path / "freeze.json").read_text())
    assert state.get("liquidation_gate_tripped") is True


def test_intraday_handles_broker_fetch_failure(tmp_path, monkeypatch):
    """When broker fetch errors, action stays default but error is recorded."""
    from trader import intraday_risk
    monkeypatch.setattr(intraday_risk, "INTRADAY_LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(intraday_risk, "_fetch_broker_equity",
                        lambda: (None, "ConnectionError: refused"))
    result = intraday_risk.check()
    assert result.error is not None
    assert "ConnectionError" in result.error

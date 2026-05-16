"""Tests for the v6.1.2 HMM live-overlay.

The overlay is walk-forward-validated (5 windows, 2021-26, OOS training).
Default mode is INERT. SHADOW logs only. LIVE mutates targets.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Mode-gating tests (no HMM training needed, mocked cache)
# ============================================================
def _seed_cache(tmp_path, scale: float = 0.6, regime: str = "transition",
                 p_bull: float = 0.3, p_trans: float = 0.6, p_bear: float = 0.1):
    """Write a fake cache file so compute_hmm_overlay doesn't refit."""
    cache = tmp_path / "hmm_live_overlay_cache.json"
    cache.write_text(json.dumps({
        "_cached_at": datetime.utcnow().isoformat(),
        "scale": scale,
        "regime": regime,
        "posterior_max": max(p_bull, p_trans, p_bear),
        "p_bull": p_bull,
        "p_transition": p_trans,
        "p_bear": p_bear,
        "trained_on_days": 3000,
    }))
    return cache


def test_default_mode_is_inert(monkeypatch, tmp_path):
    """No env var set → mode INERT → scale=1.0 effectively (no mutation)."""
    monkeypatch.delenv("HMM_REGIME_MODE", raising=False)
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition")
    import importlib, trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    sig = hlo.compute_hmm_overlay()
    assert sig.mode == "INERT"
    assert sig.scale == 1.0  # INERT never applies scale
    assert sig.is_active() is False


def test_shadow_mode_reports_but_does_not_apply(monkeypatch, tmp_path):
    """SHADOW: scale reported in metadata, is_active() False."""
    monkeypatch.setenv("HMM_REGIME_MODE", "SHADOW")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition")
    import importlib, trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    sig = hlo.compute_hmm_overlay()
    assert sig.mode == "SHADOW"
    assert sig.scale == 1.0  # SHADOW reports the regime, applies no cut
    assert sig.regime == "transition"
    assert sig.p_transition == 0.6
    assert sig.is_active() is False


def test_live_mode_applies_scale(monkeypatch, tmp_path):
    """LIVE: scale < 1.0 → is_active() True → risk_manager will mutate."""
    monkeypatch.setenv("HMM_REGIME_MODE", "LIVE")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition",
                p_trans=0.6, p_bull=0.4, p_bear=0.0)
    import importlib, trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    sig = hlo.compute_hmm_overlay()
    assert sig.mode == "LIVE"
    # Posterior-weighted scale: 0.4*1.0 + 0.6*0.6 + 0.0*0.0 = 0.76
    # But cache seeded 0.6 directly — so scale is what cache says (0.6)
    assert sig.scale == pytest.approx(0.6, abs=1e-6)
    assert sig.is_active() is True


def test_live_in_bull_regime_does_not_apply(monkeypatch, tmp_path):
    """When HMM says bull with high posterior, scale ≈ 1.0 → is_active False."""
    monkeypatch.setenv("HMM_REGIME_MODE", "LIVE")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=1.0, regime="bull",
                p_bull=0.95, p_trans=0.05, p_bear=0.0)
    import importlib, trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    sig = hlo.compute_hmm_overlay()
    assert sig.mode == "LIVE"
    assert sig.scale == 1.0
    assert sig.is_active() is False  # is_active requires scale < 1.0


def test_rationale_format(monkeypatch, tmp_path):
    """The rationale string should embed mode + regime + posteriors + scale."""
    monkeypatch.setenv("HMM_REGIME_MODE", "SHADOW")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition", p_trans=0.6, p_bull=0.4)
    import importlib, trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    sig = hlo.compute_hmm_overlay()
    r = sig.rationale()
    assert "hmm_live[SHADOW]" in r
    assert "transition" in r
    assert "p_bull=0.40" in r


# ============================================================
# Risk-manager integration: HMM overlay slot in check_account_risk
# ============================================================
def test_risk_manager_includes_hmm_warning_in_shadow_mode(monkeypatch, tmp_path):
    """When HMM_REGIME_MODE=SHADOW, risk_manager's warnings list includes
    the hmm_live rationale (so daemon logs show what would-have-fired)."""
    monkeypatch.setenv("HMM_REGIME_MODE", "SHADOW")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition")
    import importlib
    import trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    import trader.risk_manager as rm
    decision = rm.check_account_risk(
        equity=200_000,  # above journal 180d peak to bypass drawdown HALT
        targets={"AAPL": 0.15, "MSFT": 0.15, "GOOGL": 0.15, "AMZN": 0.15},  # under per-name cap
        vix=18.0,
    )
    assert decision.proceed is True
    hmm_warnings = [w for w in decision.warnings if "hmm_live" in w]
    assert len(hmm_warnings) >= 1, (
        f"Expected hmm_live rationale in warnings, got: {decision.warnings}"
    )


def test_risk_manager_does_not_mutate_targets_in_shadow(monkeypatch, tmp_path):
    """SHADOW must NOT mutate targets even if cache says scale=0.6."""
    monkeypatch.setenv("HMM_REGIME_MODE", "SHADOW")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.3, regime="bear", p_bear=0.9)
    import importlib
    import trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    import trader.risk_manager as rm
    decision = rm.check_account_risk(
        equity=200_000,  # above journal 180d peak to bypass drawdown HALT
        targets={"AAPL": 0.15, "MSFT": 0.15, "GOOGL": 0.15, "AMZN": 0.15},  # under per-name cap  # 60% deployed
        vix=15.0,  # low VIX so vol_scale doesn't kick in
    )
    # Without HMM in shadow, gross should still be ~60% (modulo other overlays)
    final_gross = sum(decision.adjusted_targets.values())
    # We expect ~60% (no HMM mutation). Allow some tolerance for other overlays.
    assert final_gross >= 0.5, (
        f"SHADOW HMM should not cut targets, but final gross is {final_gross:.2f}"
    )


def test_risk_manager_mutates_in_live_mode(monkeypatch, tmp_path):
    """LIVE mode with scale=0.6 must shrink targets by ~40%."""
    monkeypatch.setenv("HMM_REGIME_MODE", "LIVE")
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    _seed_cache(tmp_path, scale=0.6, regime="transition")
    import importlib
    import trader.hmm_live_overlay as hlo
    importlib.reload(hlo)
    import trader.risk_manager as rm
    decision = rm.check_account_risk(
        equity=200_000,  # above journal 180d peak to bypass drawdown HALT
        targets={"AAPL": 0.15, "MSFT": 0.15, "GOOGL": 0.15, "AMZN": 0.15},  # under per-name cap
        vix=15.0,
    )
    final_gross = sum(decision.adjusted_targets.values())
    # 0.6 input × 0.6 HMM scale = 0.36; allow other overlays to slightly adjust
    assert final_gross < 0.55, (
        f"LIVE HMM with scale=0.6 should cut targets, but final gross is {final_gross:.2f}"
    )
    assert final_gross > 0.20, "Cut should be ~40%, not catastrophic"


# ============================================================
# Sanity: walk-forward-validated multipliers are exactly as expected
# ============================================================
def test_scaling_constants_match_walk_forward():
    """Walk-forward result uses BULL=1.0, TRANSITION=0.6, BEAR=0.0.
    If these constants ever change, ALL prior backtest evidence is
    invalidated and the Research Backlog must be re-run."""
    from trader.hmm_live_overlay import SCALE_BULL, SCALE_TRANSITION, SCALE_BEAR
    assert SCALE_BULL == 1.0
    assert SCALE_TRANSITION == 0.6
    assert SCALE_BEAR == 0.0


def test_training_history_at_least_10_years():
    """OOS HMM result requires 10+ years of training data."""
    from trader.hmm_live_overlay import TRAIN_HISTORY_DAYS
    assert TRAIN_HISTORY_DAYS >= 252 * 10, (
        f"TRAIN_HISTORY_DAYS={TRAIN_HISTORY_DAYS} < 10y; the OOS HMM "
        f"result requires deep history. If you shorten this, re-validate "
        f"with scripts/walk_forward_proposals.py."
    )

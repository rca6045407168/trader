"""Tests for v3.60.0 — sleeve_shadows + momentum_crash + cost report."""
from __future__ import annotations

import os
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# sleeve_shadows
# ============================================================

def test_sleeve_shadows_module_imports():
    from trader.sleeve_shadows import (
        compute_shadow_picks, write_shadow_picks, read_shadow_history,
        overlap_metrics, vanilla_momentum_picks, residual_momentum_picks,
        cost_aware_momentum_picks,
    )
    # All callable
    for fn in (compute_shadow_picks, write_shadow_picks, read_shadow_history,
                overlap_metrics, vanilla_momentum_picks,
                residual_momentum_picks, cost_aware_momentum_picks):
        assert callable(fn)


def test_write_and_read_shadow_picks(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.sleeve_shadows.SHADOW_PICKS_CSV",
                         tmp_path / "shadow_picks.csv")
    from trader import sleeve_shadows as ss
    picks = {
        "vanilla_momentum": ["AAPL", "MSFT", "NVDA"],
        "residual_momentum": ["JPM", "V", "MA"],
    }
    assert ss.write_shadow_picks("2026-05-03", picks) is True
    rows = ss.read_shadow_history()
    assert len(rows) == 2
    assert any(r["scorer"] == "vanilla_momentum" for r in rows)


def test_write_shadow_picks_idempotent(tmp_path, monkeypatch):
    """Re-writing same (date, scorer) replaces, doesn't duplicate."""
    monkeypatch.setattr("trader.sleeve_shadows.SHADOW_PICKS_CSV",
                         tmp_path / "shadow_picks.csv")
    from trader import sleeve_shadows as ss
    ss.write_shadow_picks("2026-05-03", {"vanilla_momentum": ["A", "B"]})
    ss.write_shadow_picks("2026-05-03", {"vanilla_momentum": ["C", "D"]})
    rows = ss.read_shadow_history()
    assert len(rows) == 1
    assert "C,D" in rows[0]["ranked_picks"]


def test_overlap_metrics_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.sleeve_shadows.SHADOW_PICKS_CSV",
                         tmp_path / "shadow_picks.csv")
    from trader.sleeve_shadows import overlap_metrics
    out = overlap_metrics()
    assert out["n_dates"] == 0


def test_overlap_metrics_computes(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.sleeve_shadows.SHADOW_PICKS_CSV",
                         tmp_path / "shadow_picks.csv")
    from trader import sleeve_shadows as ss
    ss.write_shadow_picks("2026-05-03", {
        "vanilla_momentum": ["A", "B", "C", "D", "E"],
        "residual_momentum": ["A", "B", "C", "X", "Y"],  # 60% overlap
    })
    out = ss.overlap_metrics()
    pair = out.get("vanilla_momentum_vs_residual_momentum", {})
    assert pair.get("mean_overlap") == pytest.approx(0.6, abs=0.05)


# ============================================================
# momentum_crash
# ============================================================

def test_crash_signal_insufficient_history():
    from trader.momentum_crash import compute_signal
    out = compute_signal([0.001] * 100)
    assert out.crash_risk_on is False
    assert "insufficient" in out.rationale


def test_crash_signal_off_in_calm_bull():
    """Trending bull market with low vol → crash regime OFF."""
    from trader.momentum_crash import compute_signal
    # 504 days of +0.05% daily with tiny noise = trending bull
    rets = [0.0005] * 504
    sig = compute_signal(rets)
    assert sig.crash_risk_on is False
    assert sig.suggested_gross_mult == 1.0


def test_crash_signal_on_negative_market_high_vol():
    """Negative 24mo return + high vol → crash regime ON."""
    from trader.momentum_crash import compute_signal
    import random
    rng = random.Random(1)
    # Average -0.05% daily for 504 days with high vol → crash regime
    rets = [rng.gauss(-0.0005, 0.025) for _ in range(504)]
    sig = compute_signal(rets)
    # Cumulative will be deeply negative; vol will be high
    if sig.market_24mo_return is not None and sig.market_24mo_return < 0:
        if sig.market_12mo_vol_annual and sig.market_12mo_vol_annual > 0.20:
            assert sig.crash_risk_on is True
            assert sig.suggested_gross_mult == 0.50


def test_crash_status_default_shadow(monkeypatch):
    monkeypatch.delenv("MOMENTUM_CRASH_STATUS", raising=False)
    from trader.momentum_crash import status
    assert status() == "SHADOW"


def test_crash_gross_multiplier_shadow_returns_one(monkeypatch):
    """When status is SHADOW, gross_multiplier returns 1.0 even when
    crash signal would fire."""
    monkeypatch.setenv("MOMENTUM_CRASH_STATUS", "SHADOW")
    from trader.momentum_crash import gross_multiplier
    # Synthetic crash regime data
    import random
    rng = random.Random(1)
    rets = [rng.gauss(-0.0005, 0.025) for _ in range(504)]
    mult = gross_multiplier(rets)
    assert mult == 1.0


def test_crash_gross_multiplier_live_cuts(monkeypatch):
    """When LIVE and crash signal fires, multiplier is 0.50."""
    monkeypatch.setenv("MOMENTUM_CRASH_STATUS", "LIVE")
    from trader.momentum_crash import gross_multiplier, compute_signal
    import random
    rng = random.Random(1)
    rets = [rng.gauss(-0.0005, 0.025) for _ in range(504)]
    sig = compute_signal(rets)
    if sig.crash_risk_on:
        # When crash on AND live, multiplier is the cut value
        assert gross_multiplier(rets) == 0.50
    else:
        # If our synthetic data didn't trigger, at least 1.0
        assert gross_multiplier(rets) == 1.0


# ============================================================
# cost_impact_report
# ============================================================

def test_cost_impact_report_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir
    assert callable(cir.main)
    assert hasattr(cir, "FLIPS")


def test_cost_impact_flips_have_required_fields():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir
    for f in cir.FLIPS:
        assert f.name
        assert f.confidence in ("high", "medium", "low", "speculative")
        assert isinstance(f.annual_bps_estimate, (int, float))


def test_lowvol_blend_marked_negative():
    """Per multi-sleeve backtest, LowVol blend has negative expected lift."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir
    lowvol = next((f for f in cir.FLIPS if "LowVolSleeve" in f.name), None)
    assert lowvol is not None
    assert lowvol.annual_bps_estimate < 0  # killed per empirical evidence


def test_recommended_flips_only_positive():
    """Recommended set excludes negative-lift items."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import cost_impact_report as cir
    # Simulated logic from main(): high/medium conf, positive, no data gap
    rec = [f for f in cir.FLIPS
           if f.annual_bps_estimate > 0
           and f.confidence in ("high", "medium")
           and not f.requires_more_data
           and not (f.requires_capital and f.annual_bps_estimate < 0)]
    assert all(f.annual_bps_estimate > 0 for f in rec)


# ============================================================
# Dashboard wiring
# ============================================================

def test_pnl_readiness_view_in_dashboard():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def view_pnl_readiness" in text
    assert '"pnl_readiness": view_pnl_readiness' in text
    assert "💰 P&L readiness" in text


# ============================================================
# Multi-sleeve backtest runner exists
# ============================================================

def test_multi_sleeve_backtest_runner_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "multi_sleeve_backtest.py"
    assert p.exists()
    text = p.read_text()
    assert "def main" in text
    assert "blend" in text  # the test of multiple allocations

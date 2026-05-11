"""Tests for v6 correlation calibration of the uplift Monte Carlo.

The round-2 self-critique flagged that the correlations were hand-
set fiction. This commit fit the TLH + quality + universe-expansion
correlations from 5 years of historical data, and annotated the rest
as literature-based.

Tests verify:
  1. COMPONENTS array contains the fitted values (provenance preserved)
  2. Sign-flip on quality factor is captured (-0.15, not +0.10)
  3. The calibration script imports cleanly + the helper functions
     have the right shape
  4. Monte Carlo output still produces a sensible band (no regression)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ============================================================
# Fitted correlations preserved in COMPONENTS
# ============================================================
def test_tlh_correlation_fitted_value():
    from trader.uplift_monte_carlo import COMPONENTS
    tlh = next(c for c in COMPONENTS if c.name == "TLH tax shelter")
    assert tlh.equity_stress_correlation == pytest.approx(-0.51)


def test_quality_correlation_sign_flipped():
    """The round-1 hand-set value was +0.10 (mild procyclical).
    The fitted value is -0.15 (defensive). Sign-flip captures the
    real-world behavior per Asness-Frazzini-Pedersen 2019."""
    from trader.uplift_monte_carlo import COMPONENTS
    quality = next(c for c in COMPONENTS if "Quality" in c.name)
    assert quality.equity_stress_correlation == pytest.approx(-0.15)
    assert quality.equity_stress_correlation < 0, \
        "quality must be negatively correlated with stress (defensive)"


def test_universe_expansion_correlation_fitted():
    from trader.uplift_monte_carlo import COMPONENTS
    ue = next(c for c in COMPONENTS if "Universe expansion" in c.name)
    assert ue.equity_stress_correlation == pytest.approx(-0.36)


def test_literature_correlations_unchanged():
    """Insider, PEAD, calendar, sec-lending stay literature-based
    because we don't have fit data."""
    from trader.uplift_monte_carlo import COMPONENTS
    insider = next(c for c in COMPONENTS if "Insider" in c.name)
    pead = next(c for c in COMPONENTS if "PEAD" in c.name)
    calendar = next(c for c in COMPONENTS if "Calendar" in c.name)
    lending = next(c for c in COMPONENTS if "Stock lending" in c.name)
    assert insider.equity_stress_correlation == pytest.approx(0.20)
    assert pead.equity_stress_correlation == pytest.approx(0.40)
    assert calendar.equity_stress_correlation == pytest.approx(0.00)
    assert lending.equity_stress_correlation == pytest.approx(0.05)


# ============================================================
# Monte Carlo output still produces a sensible band
# ============================================================
def test_monte_carlo_mean_in_plausible_range():
    """After recalibration, the median should still be in the +6-9 %/yr
    range (the means dominate; correlations move the CI marginally)."""
    from trader.uplift_monte_carlo import simulate, percentiles
    samples = simulate(n_iter=5000, seed=42)
    median = percentiles(samples, [50])[50]
    assert 6.0 < median < 9.0


def test_monte_carlo_80_ci_bounded():
    """80% CI should be within reasonable bounds — not absurdly tight
    or wide given the data we have."""
    from trader.uplift_monte_carlo import simulate, percentiles
    samples = simulate(n_iter=5000, seed=42)
    pcs = percentiles(samples, [10, 90])
    width = pcs[90] - pcs[10]
    assert 3.0 < width < 8.0, \
        f"80% CI width = {width:.2f}, expected 3-8 %/yr"


def test_monte_carlo_pessimistic_tail_positive():
    """5th percentile should still be positive — the platform's
    structural edges (TLH, quality-defensive, calendar) keep the
    floor above zero in 95% of scenarios."""
    from trader.uplift_monte_carlo import simulate, percentiles
    samples = simulate(n_iter=10000, seed=42)
    p5 = percentiles(samples, [5])[5]
    assert p5 > 2.0, \
        f"5th-percentile uplift {p5:.2f}%/yr — should be > 2%"


# ============================================================
# Calibration script structure
# ============================================================
def test_fit_script_exists():
    script = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "fit_uplift_correlations.py"
    )
    assert script.exists()


def test_fit_script_module_has_helpers():
    """The script exposes the helper functions used in fitting."""
    import importlib.util
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "fit_uplift_correlations.py"
    )
    spec = importlib.util.spec_from_file_location("fit_uplift", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "equity_stress_series")
    assert hasattr(module, "fit_tlh_correlation")
    assert hasattr(module, "fit_quality_correlation")
    assert hasattr(module, "HAND_SET")


def test_equity_stress_series_math():
    """Stress = -(drawdown from 12-mo trailing peak). Positive when
    below peak (in stress); 0 at all-time highs."""
    import pandas as pd
    import importlib.util
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts" / "fit_uplift_correlations.py"
    )
    spec = importlib.util.spec_from_file_location("fit_uplift", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Build a synthetic SPY series: rises monotonically then drops 10%
    dates = pd.bdate_range("2024-01-01", periods=300)
    prices = pd.Series(range(100, 400), index=dates)
    # Crash on day 250: drop 10%
    prices.iloc[250:] = prices.iloc[250] * 0.90
    stress = module.equity_stress_series(prices, window_days=252)
    # During the climb, stress should be ~0
    assert stress.iloc[:6].abs().max() < 0.01
    # After the crash, stress should be ~10%
    crash_period = stress.iloc[-3:]
    assert crash_period.iloc[-1] > 0.05


# ============================================================
# Provenance — docstring + comments
# ============================================================
def test_uplift_module_documents_fitting():
    from pathlib import Path as _P
    src = _P(__file__).resolve().parent.parent.joinpath(
        "src/trader/uplift_monte_carlo.py"
    ).read_text()
    # Calibration provenance section present
    assert "FITTED" in src
    assert "LITERATURE" in src
    assert "fit_uplift_correlations.py" in src
    # Sign-flip noted on quality
    assert "SIGN-FLIP" in src or "wrong sign" in src.lower()

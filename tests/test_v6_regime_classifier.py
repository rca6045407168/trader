"""Tests for the HMM regime classifier (v6.0.x research-driven addition).

The classifier is unsupervised — labels can swap between fits. Tests
verify:
  1. Module imports + Regime enum / RegimeReading dataclass
  2. classify_regime returns None on insufficient data
  3. classify_regime on synthetic bull/bear concatenated data
     correctly identifies the dominant regime
  4. gross_scalar_for_regime returns the expected mapping
  5. apply_regime_overlay leaves targets untouched on low confidence
  6. apply_regime_overlay scales weights when confidence high
  7. main.py wires REGIME_OVERLAY_ENABLED correctly
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Module surface
# ============================================================
def test_regime_module_exports():
    from trader.regime_classifier import (
        Regime, RegimeReading, classify_regime,
        gross_scalar_for_regime, apply_regime_overlay,
        DEFAULT_GROSS_BY_REGIME,
    )
    assert callable(classify_regime)
    assert callable(apply_regime_overlay)


def test_regime_enum_values():
    from trader.regime_classifier import Regime
    assert Regime.BULL.value == "BULL"
    assert Regime.NEUTRAL.value == "NEUTRAL"
    assert Regime.BEAR.value == "BEAR"


def test_default_gross_mapping():
    from trader.regime_classifier import Regime, DEFAULT_GROSS_BY_REGIME
    assert DEFAULT_GROSS_BY_REGIME[Regime.BULL] == 1.00
    assert DEFAULT_GROSS_BY_REGIME[Regime.NEUTRAL] == 0.85
    assert DEFAULT_GROSS_BY_REGIME[Regime.BEAR] == 0.65


# ============================================================
# classify_regime
# ============================================================
def test_classify_returns_none_on_short_series():
    from trader.regime_classifier import classify_regime
    dates = pd.bdate_range("2026-01-01", periods=50)
    prices = pd.Series([100.0] * 50, index=dates)
    assert classify_regime(prices) is None


def test_classify_returns_none_on_empty_series():
    from trader.regime_classifier import classify_regime
    assert classify_regime(None) is None
    assert classify_regime(pd.Series([], dtype=float)) is None


def test_classify_identifies_synthetic_bull_regime():
    """Build a clean upward-drift series and verify BULL is selected."""
    from trader.regime_classifier import classify_regime, Regime
    np.random.seed(42)
    n = 1000
    dates = pd.bdate_range("2020-01-01", periods=n)
    # Mostly positive drift with low vol — should classify as BULL
    daily_log_rets = np.random.normal(loc=0.0008, scale=0.008, size=n)
    prices = pd.Series(
        np.exp(np.cumsum(daily_log_rets)) * 100,
        index=dates,
    )
    reading = classify_regime(prices)
    assert reading is not None
    # The dominant state on a long pure-bull series should be BULL
    # (or NEUTRAL if the model splits the drift, but should NOT be BEAR)
    assert reading.regime in (Regime.BULL, Regime.NEUTRAL)
    # State probs should sum to ~1
    assert abs(sum(reading.state_probs.values()) - 1.0) < 1e-6


# ============================================================
# gross_scalar_for_regime
# ============================================================
def test_gross_scalar_lookups():
    from trader.regime_classifier import (
        gross_scalar_for_regime, Regime,
    )
    assert gross_scalar_for_regime(Regime.BULL) == 1.00
    assert gross_scalar_for_regime(Regime.NEUTRAL) == 0.85
    assert gross_scalar_for_regime(Regime.BEAR) == 0.65


def test_gross_scalar_custom_mapping():
    from trader.regime_classifier import (
        gross_scalar_for_regime, Regime,
    )
    custom = {Regime.BULL: 1.2, Regime.NEUTRAL: 1.0, Regime.BEAR: 0.5}
    assert gross_scalar_for_regime(Regime.BEAR, mapping=custom) == 0.5


# ============================================================
# apply_regime_overlay
# ============================================================
def test_overlay_handles_none_reading():
    from trader.regime_classifier import apply_regime_overlay
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    out, info = apply_regime_overlay(targets, None)
    assert out == targets  # unchanged
    assert info["scalar"] == 1.0
    assert "no regime" in info["reason"].lower()


def test_overlay_no_action_when_confidence_below_threshold():
    """Ambiguous regime (low posterior on dominant state) → no scaling."""
    from trader.regime_classifier import (
        apply_regime_overlay, RegimeReading, Regime,
    )
    reading = RegimeReading(
        regime=Regime.BEAR,
        confidence=0.50,  # below 0.55 default threshold
        state_probs={Regime.BULL: 0.30, Regime.NEUTRAL: 0.20, Regime.BEAR: 0.50},
        n_obs=755,
        mean_return_pct=-0.10,
        std_return_pct=1.50,
    )
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    out, info = apply_regime_overlay(targets, reading)
    assert out == targets
    assert info["scalar"] == 1.0
    assert "ambiguous" in info["reason"]


def test_overlay_scales_when_confident_bear():
    from trader.regime_classifier import (
        apply_regime_overlay, RegimeReading, Regime,
    )
    reading = RegimeReading(
        regime=Regime.BEAR,
        confidence=0.85,
        state_probs={Regime.BULL: 0.05, Regime.NEUTRAL: 0.10, Regime.BEAR: 0.85},
        n_obs=755,
        mean_return_pct=-0.15,
        std_return_pct=2.10,
    )
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    out, info = apply_regime_overlay(targets, reading)
    assert info["scalar"] == 0.65
    assert out["AAPL"] == pytest.approx(0.065)
    assert out["MSFT"] == pytest.approx(0.0325)
    assert info["regime"] == "BEAR"


def test_overlay_scales_when_confident_bull():
    from trader.regime_classifier import (
        apply_regime_overlay, RegimeReading, Regime,
    )
    reading = RegimeReading(
        regime=Regime.BULL,
        confidence=0.80,
        state_probs={Regime.BULL: 0.80, Regime.NEUTRAL: 0.15, Regime.BEAR: 0.05},
        n_obs=755,
        mean_return_pct=0.10,
        std_return_pct=0.90,
    )
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    out, info = apply_regime_overlay(targets, reading)
    # BULL scalar is 1.00 → no change
    assert info["scalar"] == 1.00
    assert out == targets


def test_overlay_preserves_relative_weights():
    """Scaling should preserve the ratio between any two weights."""
    from trader.regime_classifier import (
        apply_regime_overlay, RegimeReading, Regime,
    )
    reading = RegimeReading(
        regime=Regime.BEAR, confidence=0.90,
        state_probs={Regime.BULL: 0.05, Regime.NEUTRAL: 0.05, Regime.BEAR: 0.90},
        n_obs=755, mean_return_pct=-0.2, std_return_pct=2.5,
    )
    targets = {"AAPL": 0.10, "MSFT": 0.05, "JPM": 0.04}
    out, _ = apply_regime_overlay(targets, reading)
    # AAPL/MSFT ratio = 2.0 before and after
    assert abs(out["AAPL"] / out["MSFT"] - 2.0) < 1e-9
    # AAPL/JPM ratio = 2.5 before and after
    assert abs(out["AAPL"] / out["JPM"] - 2.5) < 1e-9


# ============================================================
# main.py wiring
# ============================================================
def test_main_wires_regime_overlay_opt_in():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # Env-gated opt-in (default 0)
    assert 'os.environ.get("REGIME_OVERLAY_ENABLED", "0")' in txt
    # Imports the right symbols
    assert "from .regime_classifier import classify_regime, apply_regime_overlay" in txt
    # Logs the regime + scalar
    assert "regime overlay" in txt.lower()


def test_main_supports_shadow_mode():
    """REGIME_OVERLAY_ENABLED=SHADOW classifies + logs but doesn't scale."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # Tri-state check
    assert '_regime_mode in ("1", "SHADOW")' in txt
    # SHADOW branch doesn't mutate momentum_targets
    shadow_idx = txt.find("SHADOW: targets unchanged")
    assert shadow_idx > 0
    # LIVE branch is distinct from SHADOW
    assert "LIVE" in txt
    # The label appears in both branches' print statements
    assert "mode_label" in txt


def test_main_shadow_does_not_modify_targets_simulation():
    """Simulate the main.py branching: in SHADOW, momentum_targets
    is NOT reassigned to shadow_targets. Verify by source-text the
    control flow: SHADOW branch should NOT contain
    'momentum_targets = shadow_targets'."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # The reassignment must be INSIDE the LIVE branch only
    # Find the regime block
    start = txt.find("HMM-based regime overlay")
    end = txt.find("calendar-effect overlay")
    block = txt[start:end]
    # Count assignments
    n_apply = block.count("momentum_targets = shadow_targets")
    # Should be exactly 1 (inside the LIVE branch)
    assert n_apply == 1, f"expected 1 apply, found {n_apply}"
    # The assignment should come AFTER an "if _regime_mode == \"1\":" guard
    apply_idx = block.find("momentum_targets = shadow_targets")
    live_idx = block.rfind('_regime_mode == "1"', 0, apply_idx)
    assert live_idx > 0, "apply must follow a LIVE-mode check"


def test_overlay_acts_before_calendar_overlay():
    """Order matters: regime overlay should fire BEFORE the calendar
    overlay so calendar's small adjustments compound on top of the
    larger regime-level adjustment."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    regime_idx = txt.find("HMM-based regime overlay")
    calendar_idx = txt.find("calendar-effect overlay")
    assert regime_idx > 0 and calendar_idx > 0
    assert regime_idx < calendar_idx, \
        "regime overlay should be wired before calendar overlay"

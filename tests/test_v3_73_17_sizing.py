"""Tests for v3.73.17 — sizing primitives.

Four layers tested:
  1. realized_portfolio_vol() math
  2. vol_target_scalar() — scaling logic + safety floors
  3. inverse_vol_weights() — vol-parity within score-weighting
  4. max_loss_check() — pre-trade gate

Plus: integration tests for the 2 new eval-harness candidates
(xs_top15_vol_targeted, score_weighted_vol_parity).
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# 1. realized_portfolio_vol
# ============================================================
def test_realized_vol_returns_none_below_min_obs():
    from trader.sizing import realized_portfolio_vol
    assert realized_portfolio_vol([], min_obs=6) is None
    assert realized_portfolio_vol([0.01, 0.02, 0.03], min_obs=6) is None


def test_realized_vol_constant_returns_zero():
    from trader.sizing import realized_portfolio_vol
    # Constant 1% returns → variance 0 → annualized vol 0
    v = realized_portfolio_vol([0.01] * 12)
    assert abs(v) < 1e-9, f"expected ~0, got {v}"


def test_realized_vol_known_case():
    """A monthly series alternating +5% and -5% has stdev ~5% per
    month, annualized to ~17.3% (5% × √12)."""
    from trader.sizing import realized_portfolio_vol
    rets = [0.05, -0.05] * 6  # 12 months alternating
    v = realized_portfolio_vol(rets)
    # std of [+0.05, -0.05] alternation ≈ 0.0522 (sample std for 12 obs)
    # × √12 ≈ 0.181
    assert 0.16 < v < 0.20, f"expected ~0.18, got {v:.4f}"


def test_realized_vol_daily_annualization():
    """Daily 1% std → annualized via √252 ≈ 15.9%."""
    from trader.sizing import realized_portfolio_vol_daily
    import random
    random.seed(42)
    daily = [random.gauss(0.0005, 0.01) for _ in range(60)]
    v = realized_portfolio_vol_daily(daily)
    # std ≈ 0.01, × √252 ≈ 0.159
    assert 0.13 < v < 0.18, f"expected ~0.16, got {v:.4f}"


# ============================================================
# 2. vol_target_scalar
# ============================================================
def test_vol_target_returns_one_when_below_target():
    from trader.sizing import vol_target_scalar
    # Realized 12%, target 18% → scalar 1.0 (don't lever up)
    assert vol_target_scalar(0.12, target_vol=0.18) == 1.0


def test_vol_target_scales_down_when_above_target():
    from trader.sizing import vol_target_scalar
    # Realized 30%, target 18% → scalar 0.6
    s = vol_target_scalar(0.30, target_vol=0.18)
    assert abs(s - 0.6) < 1e-6


def test_vol_target_handles_none_safely():
    from trader.sizing import vol_target_scalar
    assert vol_target_scalar(None) == 1.0
    assert vol_target_scalar(0) == 1.0
    assert vol_target_scalar(-0.05) == 1.0


def test_vol_target_max_scale_caps_levering():
    from trader.sizing import vol_target_scalar
    # Realized 9%, target 18%, max_scale 1.0 → return 1.0 (no levering)
    assert vol_target_scalar(0.09, target_vol=0.18, max_scale=1.0) == 1.0
    # With max_scale 1.5, should return 1.5 (levered up to target)
    assert vol_target_scalar(0.09, target_vol=0.18, max_scale=1.5) == 1.5


def test_apply_vol_target_preserves_relative_weights():
    """Scaling should multiply every weight by the same factor."""
    from trader.sizing import apply_vol_target
    targets = {"AAPL": 0.10, "MSFT": 0.05, "JPM": 0.03}
    out = apply_vol_target(targets, realized_vol=0.36, target_vol=0.18)
    # Scalar = 0.5 → all halved
    assert abs(out["AAPL"] - 0.05) < 1e-9
    assert abs(out["MSFT"] - 0.025) < 1e-9
    assert abs(out["JPM"] - 0.015) < 1e-9
    # Relative ratios preserved
    assert abs(out["AAPL"] / out["MSFT"] - 2.0) < 1e-9


# ============================================================
# 3. inverse_vol_weights (per-name vol-parity)
# ============================================================
def test_inverse_vol_high_vol_name_gets_less_weight():
    """Two names same score, one twice the vol → low-vol gets 2x weight."""
    from trader.sizing import inverse_vol_weights
    scored = [("LOW", 0.10), ("HIGH", 0.10)]
    vols = {"LOW": 0.15, "HIGH": 0.30}
    w = inverse_vol_weights(scored, vols, target_gross=0.80, min_shift=False)
    # weight ∝ score / vol; same score so ratio = 1/vol
    # LOW: 0.10/0.15 = 0.667; HIGH: 0.10/0.30 = 0.333; total 1.0
    # Normalized to 0.80: LOW = 0.533, HIGH = 0.267
    assert abs(w["LOW"] - 0.533) < 0.01
    assert abs(w["HIGH"] - 0.267) < 0.01
    assert abs(w["LOW"] / w["HIGH"] - 2.0) < 0.01


def test_inverse_vol_normalizes_to_target_gross():
    from trader.sizing import inverse_vol_weights
    scored = [("A", 0.20), ("B", 0.10), ("C", 0.05)]
    vols = {"A": 0.20, "B": 0.15, "C": 0.10}
    w = inverse_vol_weights(scored, vols, target_gross=0.80)
    assert abs(sum(w.values()) - 0.80) < 1e-6


def test_inverse_vol_floor_protects_against_div_zero():
    from trader.sizing import inverse_vol_weights
    scored = [("STABLE", 0.10), ("NORMAL", 0.10)]
    vols = {"STABLE": 0.001, "NORMAL": 0.20}  # near-zero vol on STABLE
    w = inverse_vol_weights(scored, vols, target_gross=0.80,
                              min_vol=0.05, min_shift=False)
    # min_vol=5% prevents STABLE from dominating
    # STABLE divisor = max(0.001, 0.05) = 0.05 → component 2.0
    # NORMAL divisor = 0.20 → component 0.5
    # Total 2.5; STABLE gets 2.0/2.5 = 80% of 80% = 64%; NORMAL gets 16%
    assert abs(w["STABLE"] - 0.64) < 0.01
    assert abs(w["NORMAL"] - 0.16) < 0.01


def test_inverse_vol_empty_input():
    from trader.sizing import inverse_vol_weights
    assert inverse_vol_weights([], {}, target_gross=0.80) == {}


# ============================================================
# 4. max_loss_check
# ============================================================
def test_max_loss_clean_when_all_under_threshold():
    from trader.sizing import max_loss_check
    # 5% × 25% stress = 1.25% — under 1.5% threshold
    targets = {"A": 0.05, "B": 0.05, "C": 0.05}
    violations = max_loss_check(targets, max_loss_pct=0.015, stress_pct=0.25)
    assert violations == []


def test_max_loss_flags_oversized_position():
    from trader.sizing import max_loss_check
    # 8% × 25% = 2% — over 1.5% threshold
    targets = {"BIG": 0.08, "SMALL": 0.04}
    violations = max_loss_check(targets, max_loss_pct=0.015, stress_pct=0.25)
    assert len(violations) == 1
    assert violations[0].ticker == "BIG"
    assert abs(violations[0].stress_loss_pct - 0.02) < 1e-9


def test_max_loss_implied_max_weight_at_default_params():
    """Default params: max_loss=1.5%, stress=25% → implied max
    weight = 6%. Position at 6% should be exactly at the line."""
    from trader.sizing import max_loss_check
    targets = {"AT_LINE": 0.060001, "UNDER": 0.05999}
    violations = max_loss_check(targets, max_loss_pct=0.015, stress_pct=0.25)
    assert len(violations) == 1
    assert violations[0].ticker == "AT_LINE"


# ============================================================
# Integration: 2 new eval-harness strategies
# ============================================================
def test_total_strategies_now_thirtytwo():
    """v6.0.x: 30 prior + xs_top10_insider_edgar_30d + xs_top10_pead_5d = 32."""
    from trader import eval_strategies
    specs = eval_strategies.all_strategies()
    assert len(specs) == 32, \
        f"expected 32 strategies, got {len(specs)}"


def test_new_sizing_strategies_registered():
    from trader import eval_strategies
    names = {s.name for s in eval_strategies.all_strategies()}
    assert "xs_top15_vol_targeted" in names
    assert "score_weighted_vol_parity" in names
    assert "xs_top15_reactor_trimmed" in names
    assert "xs_top15_dd_recovery_aware" in names
    assert "xs_top15_dd_recovery_reduced_gross" in names


def test_vol_targeted_strategy_returns_picks_on_synthetic():
    """Smoke test: vol-targeted variant returns valid weights."""
    import pandas as pd
    import numpy as np
    from trader.eval_strategies import xs_top15_vol_targeted
    from trader.sectors import SECTORS

    np.random.seed(7)
    cols = list(SECTORS.keys())[:30]
    dates = pd.bdate_range("2024-01-01", periods=400)
    data = 100 * np.cumprod(1 + np.random.randn(len(dates), len(cols)) * 0.01, axis=0)
    prices = pd.DataFrame(data, index=dates, columns=cols)

    result = xs_top15_vol_targeted(dates[-1], prices)
    assert isinstance(result, dict)
    assert len(result) > 0
    # All weights positive (no shorts)
    assert all(w > 0 for w in result.values())


def test_vol_parity_strategy_returns_picks_on_synthetic():
    import pandas as pd
    import numpy as np
    from trader.eval_strategies import score_weighted_vol_parity
    from trader.sectors import SECTORS

    np.random.seed(11)
    cols = list(SECTORS.keys())[:30]
    dates = pd.bdate_range("2024-01-01", periods=400)
    data = 100 * np.cumprod(1 + np.random.randn(len(dates), len(cols)) * 0.01, axis=0)
    prices = pd.DataFrame(data, index=dates, columns=cols)

    result = score_weighted_vol_parity(dates[-1], prices)
    assert isinstance(result, dict)
    assert len(result) > 0
    # Should sum to ~80% gross
    assert abs(sum(result.values()) - 0.80) < 0.01


# ============================================================
# main.py wiring
# ============================================================
def test_main_imports_sizing_module():
    """Verify the v3.73.17 main.py wiring is in place."""
    text = (ROOT / "src" / "trader" / "main.py").read_text()
    assert "VOL_TARGET_ENABLED" in text
    assert "from .sizing import" in text
    assert "max_loss_check" in text


def test_dashboard_version_v3_73_17():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    # accept v3.73.17 or any later patch
    import re
    assert re.search(r'v3\.73\.\d+', text)

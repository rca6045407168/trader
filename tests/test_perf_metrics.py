"""Tests for perf_metrics: beta/alpha and drawdown."""
import math
import pytest
from trader.perf_metrics import compute_beta_alpha, compute_drawdown_stats


def test_beta_alpha_perfect_correlation_beta_one():
    """If portfolio returns equal SPY returns, beta should be ~1 and alpha ~0."""
    spy = [0.01, 0.02, -0.01, 0.005, 0.015, -0.008]
    port = list(spy)
    r = compute_beta_alpha(port, spy)
    assert abs(r["beta"] - 1.0) < 1e-9
    assert abs(r["alpha_per_period"]) < 1e-9
    assert r["r_squared"] > 0.99


def test_beta_alpha_double_leverage():
    """If portfolio is 2x SPY, beta should be 2.0."""
    spy = [0.01, 0.02, -0.01, 0.005, 0.015]
    port = [r * 2 for r in spy]
    r = compute_beta_alpha(port, spy)
    assert abs(r["beta"] - 2.0) < 1e-6


def test_beta_alpha_constant_alpha_added():
    """If port = SPY + constant, beta=1 and alpha=constant per period."""
    spy = [0.01, 0.02, -0.01, 0.005, 0.015]
    port = [r + 0.001 for r in spy]
    r = compute_beta_alpha(port, spy)
    assert abs(r["beta"] - 1.0) < 1e-6
    assert abs(r["alpha_per_period"] - 0.001) < 1e-6


def test_beta_alpha_handles_short_input():
    r = compute_beta_alpha([0.01, 0.02], [0.01, 0.02])
    assert math.isnan(r["beta"])
    assert "insufficient" in r["message"]


def test_beta_alpha_handles_zero_spy_variance():
    """If SPY returns are all zero, can't compute beta."""
    r = compute_beta_alpha([0.01, 0.02, 0.03, 0.04, 0.05], [0.0] * 5)
    assert math.isnan(r["beta"])


def test_drawdown_simple_case():
    """Equity went 100 → 110 → 90. Max DD = (90-110)/110 = -18.18%."""
    eq = [100, 105, 110, 100, 95, 90, 95, 100]
    dd = compute_drawdown_stats(eq)
    assert abs(dd["max_dd"] - ((90 - 110) / 110)) < 1e-9
    assert dd["all_time_high"] == 110
    assert dd["current_dd"] < 0  # currently at 100, ATH was 110


def test_drawdown_at_ath():
    """Currently at all-time high → current_dd = 0."""
    eq = [100, 105, 110]
    dd = compute_drawdown_stats(eq)
    assert dd["current_dd"] == 0
    assert dd["max_dd"] == 0


def test_drawdown_empty_input():
    dd = compute_drawdown_stats([])
    assert dd["current_dd"] == 0
    assert dd["max_dd"] == 0

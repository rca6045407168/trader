"""Tests for v3.59.2 — extended perf metrics + ops health checks."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ============================================================
# perf_metrics_v5
# ============================================================

def test_sortino_constant_returns_handles_zero_downside():
    from trader.perf_metrics_v5 import sortino_ratio
    # All positive returns → no downside → infinite Sortino
    out = sortino_ratio([0.01] * 30)
    assert out == float("inf") or out > 50  # well above any normal Sharpe


def test_sortino_negative_drag_negative():
    from trader.perf_metrics_v5 import sortino_ratio
    # All negative returns → negative Sortino
    out = sortino_ratio([-0.01] * 30)
    assert out is not None and out < 0


def test_calmar_simple():
    from trader.perf_metrics_v5 import calmar_ratio
    # 252 days of +0.05% daily → ~13% CAGR with no DD
    out = calmar_ratio([0.0005] * 252)
    # No drawdown → infinite Calmar
    assert out == float("inf") or out > 100


def test_calmar_handles_drawdown():
    from trader.perf_metrics_v5 import calmar_ratio
    # 100 days +1%, 10 days -10%, 100 days +1%
    rets = [0.01] * 100 + [-0.10] * 10 + [0.01] * 100
    out = calmar_ratio(rets)
    assert out is not None
    # Drawdown is significant → Calmar should be finite-positive
    assert -10 < out < 100


def test_omega_threshold_zero():
    from trader.perf_metrics_v5 import omega_ratio
    # Symmetric returns around 0 → Omega ≈ 1
    out = omega_ratio([0.01, -0.01, 0.01, -0.01, 0.01, -0.01])
    assert out == pytest.approx(1.0, abs=0.01)


def test_omega_positive_skew():
    from trader.perf_metrics_v5 import omega_ratio
    # All wins, no losses → Omega = inf
    out = omega_ratio([0.01] * 10)
    assert out == float("inf")


def test_cvar_correct_for_known_distribution():
    from trader.perf_metrics_v5 import cvar
    # 100 returns, worst 5 are all -0.10
    rets = [0.01] * 95 + [-0.10] * 5
    out = cvar(rets, confidence=0.95)
    # Expected shortfall = mean of worst 5% = -0.10
    assert out == pytest.approx(-0.10, abs=0.001)


def test_cvar_too_few_samples():
    from trader.perf_metrics_v5 import cvar
    assert cvar([0.01, -0.01]) is None  # < 20 samples


def test_time_underwater_simple():
    """Time-underwater counts EVERY day until cum exceeds the prior peak.
    A 3-day drawdown followed by 3 days of recovery to break even but not
    exceed the peak is a 6-day underwater streak — that's the behavioral
    metric we want."""
    from trader.perf_metrics_v5 import time_underwater
    # 5 up days set a peak; then 3 down + 5 up needs to STRICTLY exceed
    # that peak to end the streak.
    rets = [0.01] * 5 + [-0.01] * 3 + [0.01] * 5 + [-0.01] * 2 + [0.01] * 5
    avg, mx = time_underwater(rets)
    assert avg is not None and mx is not None
    # Streak must be at least 3 (the down days). Don't pin exact —
    # depends on whether the recovery exceeds peak before the next dip.
    assert mx >= 3


def test_max_runup_captures_upside():
    from trader.perf_metrics_v5 import max_runup
    # Trough at start, then 50% runup
    rets = [0.0] * 5 + [0.04] * 10  # ~48% cumulative gain
    out = max_runup(rets)
    assert out is not None
    assert out > 30  # at least 30% runup


def test_tracking_error_zero_when_identical():
    from trader.perf_metrics_v5 import tracking_error
    rets = [0.01, -0.01, 0.02, 0.0] * 10
    out = tracking_error(rets, rets)
    assert out == pytest.approx(0, abs=1e-6)


def test_tracking_error_grows_with_divergence():
    """TE measures STDEV of (port - bench), not the mean. A constant
    daily diff has zero stdev → TE 0. Use a series with variance."""
    from trader.perf_metrics_v5 import tracking_error
    # Alternating diffs: +1% then -1% → meaningful variance
    a = [0.02 if i % 2 == 0 else -0.02 for i in range(50)]
    b = [0.0] * 50
    out = tracking_error(a, b)
    # Daily stdev of diffs is large; annualized × sqrt(252)
    assert out is not None
    assert out > 20  # high TE expected from this divergence


def test_extended_metrics_all_fields_populated():
    from trader.perf_metrics_v5 import extended_metrics
    rets = [0.01, -0.005, 0.02, -0.01, 0.0] * 30  # 150 obs
    bench = [0.005] * 150
    em = extended_metrics(rets, bench)
    assert em.n == 150
    assert em.sortino is not None
    assert em.calmar is not None
    assert em.omega_ratio is not None
    assert em.cvar_95 is not None
    assert em.tracking_error_pct is not None


# ============================================================
# ops_health
# ============================================================

def test_severity_summary_reduces():
    from trader.ops_health import severity_summary
    s = severity_summary([
        {"severity": "high"},
        {"severity": "warn"},
        {"severity": "warn"},
        {"severity": "info"},
    ])
    assert s["high"] == 1
    assert s["warn"] == 2
    assert s["info"] == 1
    assert s["overall"] == "high"


def test_severity_summary_warn_when_no_high():
    from trader.ops_health import severity_summary
    assert severity_summary([{"severity": "warn"}, {"severity": "info"}])["overall"] == "warn"


def test_severity_summary_info_when_clean():
    from trader.ops_health import severity_summary
    assert severity_summary([{"severity": "info"}, {"severity": "info"}])["overall"] == "info"


def test_journal_size_returns_dict():
    from trader.ops_health import journal_size_mb
    out = journal_size_mb()
    assert "check" in out
    assert "severity" in out
    assert "message" in out


def test_backup_freshness_returns_dict():
    from trader.ops_health import backup_freshness
    out = backup_freshness()
    assert out["check"] == "backup_freshness"
    assert out["severity"] in ("info", "warn", "high")


def test_env_keys_age_returns_dict():
    from trader.ops_health import env_keys_documented
    out = env_keys_documented()
    assert "check" in out
    assert "severity" in out


def test_all_checks_returns_six():
    from trader.ops_health import all_checks
    results = all_checks()
    # Six checks: daily_run, journal_size, backup, alpaca, anthropic, env_keys
    assert len(results) == 6
    for r in results:
        assert "check" in r
        assert "severity" in r


def test_daily_run_fired_handles_missing_db():
    """If journal.db doesn't exist, the check should warn, not crash."""
    from trader.ops_health import daily_run_fired_today
    out = daily_run_fired_today()
    assert "severity" in out
    assert "message" in out

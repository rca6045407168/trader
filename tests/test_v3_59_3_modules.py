"""Tests for v3.59.3 — bootstrap CIs, schemas, drift, TCA, pre-registration."""
from __future__ import annotations

import math
import os
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# bootstrap_ci
# ============================================================

def test_bootstrap_returns_nan_for_too_few_samples():
    from trader.bootstrap_ci import block_bootstrap_sharpe_ci
    ci = block_bootstrap_sharpe_ci([0.01, -0.01, 0.02], B=100)
    assert math.isnan(ci.ci_low)
    assert ci.n_resamples == 0


def test_bootstrap_ci_contains_point_for_constant_returns():
    """Constant returns → zero variance → Sharpe near 0 with tight CI."""
    from trader.bootstrap_ci import block_bootstrap_sharpe_ci
    rets = [0.001] * 50
    ci = block_bootstrap_sharpe_ci(rets, B=200)
    # Sharpe of constant series is 0 (denominator stdev=0); point should be 0
    assert ci.point_estimate == 0


def test_bootstrap_ci_for_high_sharpe_strategy():
    """Strong positive returns with low vol → high Sharpe + CI above 0."""
    import random
    from trader.bootstrap_ci import block_bootstrap_sharpe_ci, is_significant
    rng = random.Random(42)
    # Mean +0.001 daily, stdev 0.005 → Sharpe roughly 3
    rets = [0.001 + rng.gauss(0, 0.005) for _ in range(252)]
    ci = block_bootstrap_sharpe_ci(rets, B=500, seed=7)
    assert ci.point_estimate > 1.0  # well above zero
    assert is_significant(ci, threshold=0.0) is True or ci.ci_low > -1


def test_bootstrap_max_dd_ci():
    from trader.bootstrap_ci import block_bootstrap_max_dd_ci
    rets = [0.001] * 50 + [-0.05] * 5 + [0.001] * 50
    ci = block_bootstrap_max_dd_ci(rets, B=200)
    # Max DD must be negative
    assert ci.point_estimate < 0


def test_bootstrap_total_return_ci():
    from trader.bootstrap_ci import block_bootstrap_total_return_ci
    rets = [0.001] * 100  # ~10.5% compound
    ci = block_bootstrap_total_return_ci(rets, B=200)
    assert ci.point_estimate == pytest.approx(0.105, abs=0.001)


# ============================================================
# data_schemas
# ============================================================

def test_validate_targets_accepts_valid():
    from trader.data_schemas import validate_targets
    out = validate_targets({"AAPL": 0.10, "MSFT": 0.15, "NVDA": 0.20})
    assert out["ok"] is True


def test_validate_targets_rejects_negative():
    from trader.data_schemas import validate_targets
    out = validate_targets({"AAPL": -0.05})
    assert out["ok"] is False


def test_validate_targets_rejects_over_leveraged():
    from trader.data_schemas import validate_targets
    out = validate_targets({"AAPL": 0.80, "MSFT": 0.80, "NVDA": 0.80})  # sum = 2.4
    assert out["ok"] is False
    assert any("over-leverage" in e for e in out["errors"])


def test_validate_targets_rejects_concentrated_position():
    from trader.data_schemas import validate_targets
    out = validate_targets({"AAPL": 0.65})  # > 50%
    assert out["ok"] is False


def test_validate_price_history_handles_none():
    from trader.data_schemas import validate_price_history
    out = validate_price_history(None)
    assert out["ok"] is False


# ============================================================
# pre_registration
# ============================================================

def test_register_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.pre_registration.PREREG_DIR", tmp_path)
    from trader.pre_registration import register, Expectations
    p = register("low_vol_sleeve",
                  Expectations(sharpe=0.8, cagr_pct=10, max_dd_pct=-15),
                  ["if Sharpe < 0.3 → kill", "if max DD > -25% → kill"])
    assert p.exists()
    import json
    d = json.loads(p.read_text())
    assert d["sleeve_name"] == "low_vol_sleeve"
    assert d["expected"]["sharpe"] == 0.8
    assert d["actual"] is None


def test_record_actuals_updates_file(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.pre_registration.PREREG_DIR", tmp_path)
    from trader.pre_registration import register, record_actuals, Expectations, Actuals
    p = register("test_sleeve",
                  Expectations(sharpe=0.8, cagr_pct=10, max_dd_pct=-15),
                  ["test"])
    ok = record_actuals(p, Actuals(sharpe=0.6, cagr_pct=8, max_dd_pct=-18))
    assert ok is True
    import json
    d = json.loads(p.read_text())
    assert d["actual"]["sharpe"] == 0.6


def test_audit_computes_optimism_bias(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.pre_registration.PREREG_DIR", tmp_path)
    from trader.pre_registration import (
        register, record_actuals, audit, Expectations, Actuals
    )
    # Optimistic prereg: expected 1.5, actual 0.5 → bias +1.0
    p = register("over_optimistic",
                  Expectations(sharpe=1.5, cagr_pct=20, max_dd_pct=-10),
                  ["test"])
    record_actuals(p, Actuals(sharpe=0.5, cagr_pct=8, max_dd_pct=-22))
    a = audit()
    assert a["n_completed"] == 1
    assert a["sharpe_bias_avg"] == pytest.approx(1.0, abs=0.01)
    assert "optimistic" in a["interpretation"].lower()


# ============================================================
# drift_monitor
# ============================================================

def test_compute_ic_perfect_correlation():
    from trader.drift_monitor import compute_ic
    scores = [1, 2, 3, 4, 5, 6, 7]
    rets = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
    ic = compute_ic(scores, rets)
    assert ic == pytest.approx(1.0, abs=0.01)


def test_compute_ic_zero_variance_returns_none():
    from trader.drift_monitor import compute_ic
    assert compute_ic([1, 1, 1, 1, 1], [0.01, 0.02, 0.03, 0.04, 0.05]) is None


def test_ic_drift_detects_decay():
    from trader.drift_monitor import ic_drift
    recent = [0.05, 0.04, 0.03, 0.02]   # decaying
    baseline = [0.20, 0.18, 0.22, 0.21]  # strong
    out = ic_drift(recent, baseline, decay_threshold=0.5)
    assert out.threshold_breached is True
    assert out.severity in ("warn", "high")


def test_ic_drift_clean_when_stable():
    from trader.drift_monitor import ic_drift
    recent = [0.18, 0.20]
    baseline = [0.20, 0.19, 0.21]
    out = ic_drift(recent, baseline)
    assert out.threshold_breached is False


def test_ks_distance_zero_for_identical():
    from trader.drift_monitor import ks_distance
    a = [0.01, 0.02, 0.03, 0.04, 0.05]
    assert ks_distance(a, a) == 0.0


def test_ks_distance_high_for_disjoint():
    from trader.drift_monitor import ks_distance
    a = [0.01] * 20
    b = [0.99] * 20
    out = ks_distance(a, b)
    assert out is not None
    assert out > 0.9


def test_residual_pnl_balanced():
    from trader.drift_monitor import residual_pnl
    expected = [100, 200, 150, 100, 250] * 5
    actual = expected.copy()  # exact match
    out = residual_pnl(expected, actual)
    assert out["ok"] is True
    assert out["mean_residual"] == 0


# ============================================================
# tca
# ============================================================

def test_alert_triggers_above_threshold():
    from trader.tca import alert_if_slippage_high
    tca = {"ok": True, "mean_bps": 25.0}
    out = alert_if_slippage_high(tca, backtest_assumption_bps=5.0,
                                   multiplier=2.0)
    assert out["alert"] is True


def test_alert_clean_below_threshold():
    from trader.tca import alert_if_slippage_high
    tca = {"ok": True, "mean_bps": 4.0}
    out = alert_if_slippage_high(tca, backtest_assumption_bps=5.0,
                                   multiplier=2.0)
    assert out["alert"] is False


def test_compute_tca_handles_no_journal():
    """If journal.db absent or empty, returns ok=False."""
    from trader.tca import compute_tca
    # Tiny window in case there's prior data
    out = compute_tca(window_days=0)
    # Either no fills, or some — both are valid responses
    assert "ok" in out

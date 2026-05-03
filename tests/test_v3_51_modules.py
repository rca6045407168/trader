"""Tests for v3.51.0 Tier B modules:
  - sleeve_health (correlation monitor + decay auto-demote)
  - adversarial_review (pre-promotion CI gate)

Tests run in the Docker container (Dockerfile.test).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# -------------------- sleeve_health: stats math --------------------

def test_annualized_sharpe_basic():
    from trader.sleeve_health import _annualized_sharpe
    # 30 daily returns, ~+10bp mean, ~50bp std → Sharpe ~ 0.2 * sqrt(252) ~ 3.2
    rets = [0.001, 0.002, -0.001, 0.003, 0.0, 0.001, -0.002, 0.004, 0.0, 0.001,
            0.002, -0.001, 0.003, 0.0, 0.001, -0.002, 0.004, 0.0, 0.001, 0.002,
            -0.001, 0.003, 0.0, 0.001, -0.002, 0.004, 0.0, 0.001, 0.002, -0.001]
    sharpe = _annualized_sharpe(rets)
    assert sharpe is not None
    assert sharpe > 0


def test_sharpe_returns_none_for_short_series():
    from trader.sleeve_health import _annualized_sharpe
    assert _annualized_sharpe([0.001, 0.002]) is None
    assert _annualized_sharpe([]) is None


def test_sharpe_returns_none_for_zero_variance():
    from trader.sleeve_health import _annualized_sharpe
    rets = [0.001] * 30
    assert _annualized_sharpe(rets) is None


def test_pearson_correlation_perfect_positive():
    from trader.sleeve_health import _pearson_correlation
    a = [i * 0.001 for i in range(30)]
    b = [i * 0.001 for i in range(30)]
    corr = _pearson_correlation(a, b)
    assert corr is not None
    assert abs(corr - 1.0) < 1e-9


def test_pearson_correlation_perfect_negative():
    from trader.sleeve_health import _pearson_correlation
    a = [i * 0.001 for i in range(30)]
    b = [-i * 0.001 for i in range(30)]
    corr = _pearson_correlation(a, b)
    assert corr is not None
    assert abs(corr + 1.0) < 1e-9


def test_pearson_correlation_returns_none_short_series():
    from trader.sleeve_health import _pearson_correlation
    assert _pearson_correlation([1, 2], [3, 4]) is None


def test_annualized_sortino_only_penalizes_downside():
    from trader.sleeve_health import _annualized_sortino
    # Same magnitude positive vs negative — sortino should differ from sharpe
    rets = [0.005, -0.001, 0.004, -0.002, 0.003] * 6  # 30 obs
    sortino = _annualized_sortino(rets)
    assert sortino is not None
    assert sortino > 0


# -------------------- sleeve_health: report shape --------------------

def test_compute_health_handles_empty_journal(tmp_path, monkeypatch):
    """With no variants registered, health is yellow and rationale explains."""
    from trader import sleeve_health
    # Point DB_PATH to nonexistent file
    monkeypatch.setattr(sleeve_health, "DB_PATH", tmp_path / "missing.db")
    rep = sleeve_health.compute_health()
    assert rep.overall_health in ("yellow", "green")
    assert isinstance(rep.per_sleeve, list)
    assert isinstance(rep.correlations, list)


def test_compute_health_returns_dataclass_with_required_fields():
    from trader.sleeve_health import compute_health, SleeveHealthReport
    rep = compute_health()
    assert isinstance(rep, SleeveHealthReport)
    assert hasattr(rep, "timestamp")
    assert hasattr(rep, "per_sleeve")
    assert hasattr(rep, "correlations")
    assert hasattr(rep, "demote_recommendations")
    assert hasattr(rep, "correlation_alerts")
    assert hasattr(rep, "overall_health")


def test_health_report_to_dict_serializable():
    from trader.sleeve_health import compute_health
    rep = compute_health()
    d = rep.to_dict()
    # Must be JSON-serializable
    json.dumps(d, default=str)


def test_write_health_report_persists_to_disk(tmp_path, monkeypatch):
    from trader import sleeve_health
    monkeypatch.setattr(sleeve_health, "SLEEVE_HEALTH_PATH",
                        tmp_path / "sleeve_health.json")
    out = sleeve_health.write_health_report()
    assert out.exists()
    data = json.loads(out.read_text())
    assert "timestamp" in data
    assert "overall_health" in data


# -------------------- adversarial_review --------------------

def test_review_returns_block_without_api_key(monkeypatch):
    """No ANTHROPIC_API_KEY -> default-deny BLOCK with clear error."""
    from trader import adversarial_review
    monkeypatch.setattr(adversarial_review, "ANTHROPIC_API_KEY", "")
    result = adversarial_review.review_promotion(
        variant_id="test_v1",
        proposed_status="live",
        description="Test variant",
    )
    assert result.recommendation == "BLOCK"
    assert "ANTHROPIC_API_KEY" in (result.error or "")


def test_review_parses_approve_response(monkeypatch):
    """A clean APPROVE response is parsed correctly."""
    from trader import adversarial_review
    fake_response_text = """
RECOMMENDATION: APPROVE
CHECKS_PASSED: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
CHECKS_FAILED:
CHECKS_INCONCLUSIVE:
NOTES: All 10 checks pass. PIT-validated, momentum-weighted, sensible position caps.
"""
    parsed = adversarial_review._parse_response(fake_response_text)
    assert parsed["recommendation"] == "APPROVE"
    assert parsed["checks_passed"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_review_parses_block_response():
    from trader import adversarial_review
    fake_response_text = """
RECOMMENDATION: BLOCK
CHECKS_PASSED: 1, 2, 4, 5
CHECKS_FAILED: 3 (concentration: top name 22% > 16% cap), 8 (no slippage modeled)
CHECKS_INCONCLUSIVE: 7 (CPCV result not in description)
NOTES: Variant fails position-cap check.
"""
    parsed = adversarial_review._parse_response(fake_response_text)
    assert parsed["recommendation"] == "BLOCK"
    assert 1 in parsed["checks_passed"]


def test_review_unparseable_response_defaults_to_block():
    from trader import adversarial_review
    parsed = adversarial_review._parse_response("some random unstructured text")
    assert parsed["recommendation"] == "BLOCK"


def test_review_dataclass_has_expected_fields():
    from trader.adversarial_review import ReviewResult
    r = ReviewResult(variant_id="x", recommendation="BLOCK")
    assert r.variant_id == "x"
    assert r.recommendation == "BLOCK"
    assert r.checks_passed == []
    assert r.timestamp

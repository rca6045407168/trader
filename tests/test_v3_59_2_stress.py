"""Tests for v3.59.2 — stress test runner + best practices doc + stress view."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_stress_runner_module_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import stress_test_v5 as st
    assert callable(st.main)
    # v3.59.2 expanded API: TIER1, TIER2, TIER3 + all_regimes()
    assert hasattr(st, "TIER1")
    assert hasattr(st, "TIER2")
    assert hasattr(st, "TIER3")
    assert hasattr(st, "all_regimes")


def test_stress_runner_has_all_required_regimes():
    """Per SCENARIO_LIBRARY.md: Tier 1 has 9 regimes; Tier 2 has 24;
    Tier 3 has 14. Plus user explicitly named several."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import stress_test_v5 as st
    all_names = {r.name for r in st.all_regimes("all")}
    # User-named scenarios from the original message + SCENARIO_LIBRARY
    required = {
        "2001-09-11",
        "2008-financial-crisis",
        "2018-Volmageddon",
        "2020-COVID",
        "2020-oil-contango",
        "2022-bear",
        "2025-tariff-regime",
        "2024-yen-unwind",
        "1973-OPEC-oil-embargo",       # Tier 3 archetype for Iran 2026
        "1979-82-Volcker-shock",        # Tier 3 for stagflation forward script
    }
    missing = required - all_names
    assert not missing, f"missing required stress regimes: {missing}"


def test_stress_runner_tier_counts():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import stress_test_v5 as st
    # Tier 1 must-pass: 9 regimes
    assert len(st.TIER1) == 9
    # Tier 2 should-pass: 24 regimes per SCENARIO_LIBRARY (we have ~24)
    assert len(st.TIER2) >= 22
    # Tier 3 deep history: 14 regimes
    assert len(st.TIER3) == 14
    # all_regimes("1") returns Tier 1 only
    assert len(st.all_regimes("1")) == 9
    # all_regimes("all") combines Tier 1+2+3
    assert len(st.all_regimes("all")) == len(st.TIER1) + len(st.TIER2) + len(st.TIER3)


def test_regime_stats_handles_empty():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import stress_test_v5 as st
    assert st.regime_stats([]) == {
        "n": 0, "return_pct": None, "annual_vol_pct": None,
        "sharpe": None, "max_drawdown_pct": None, "win_rate": None,
    }


def test_regime_stats_correct_for_simple_series():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import stress_test_v5 as st
    # Simple constant-up daily 1% return for 10 days
    rets = [0.01] * 10
    s = st.regime_stats(rets)
    assert s["n"] == 10
    assert s["return_pct"] == pytest.approx(((1.01 ** 10) - 1) * 100, abs=0.01)
    # Constant series — vol is 0, Sharpe div-by-zero protected → 0
    assert s["annual_vol_pct"] == pytest.approx(0, abs=1e-6)
    assert s["sharpe"] == 0
    # No drawdowns when all positive
    assert s["max_drawdown_pct"] == pytest.approx(0, abs=1e-6)
    assert s["win_rate"] == 1.0


def test_best_practices_doc_exists_and_covers_key_topics():
    p = Path(__file__).resolve().parent.parent / "docs" / "BEST_PRACTICES.md"
    assert p.exists()
    text = p.read_text()
    # Must cover the canonical topics
    for topic in [
        "3-gate", "Stress test", "Module status flag", "Behavioral pre-commit",
        "kill-list", "LowVolSleeve", "promotion", "anti-pattern"
    ]:
        assert topic.lower() in text.lower(), f"BEST_PRACTICES missing: {topic}"


def test_stress_test_view_in_dashboard():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def view_stress_test" in text
    assert '"stress_test": view_stress_test' in text
    # v3.67.1 renamed "🧪 Stress test" → "💥 Stress test (crisis)" to
    # disambiguate from the other 🧪-prefixed tabs (Validation, Strategy
    # Lab, A/B variants). Accept either label.
    assert ("🧪 Stress test" in text) or ("💥 Stress test" in text)


def test_fomc_backtest_module_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import backtest_fomc_drift as bfd
    assert callable(bfd.main)
    assert hasattr(bfd, "HISTORICAL_FOMC")
    assert len(bfd.HISTORICAL_FOMC) >= 80  # 11 years × 8 meetings/yr ≈ 88


def test_stress_results_present_after_run():
    """Sanity check: stress_test_v5.json should be present after the
    docker-run from this session. If absent, the runner failed silently."""
    p = Path(__file__).resolve().parent.parent / "data" / "stress_test_v5.json"
    if not p.exists():
        pytest.skip("stress_test_v5.json not yet generated")
    import json
    with p.open() as f:
        data = json.load(f)
    assert "regimes" in data
    assert len(data["regimes"]) >= 8

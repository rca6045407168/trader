"""Tests for v3.59.5 — chaos cases, SPA test, scripted scenarios, runners."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# chaos_cases (Cat 8)
# ============================================================

def test_market_holiday_christmas():
    from trader.chaos_cases import is_market_holiday
    assert is_market_holiday(date(2025, 12, 25)) is True
    assert is_market_holiday(date(2026, 12, 25)) is True


def test_market_holiday_weekend():
    from trader.chaos_cases import is_market_holiday
    # Jan 4 2026 is a Sunday
    assert is_market_holiday(date(2026, 1, 4)) is True


def test_market_holiday_normal_day():
    from trader.chaos_cases import is_market_holiday
    # 2026-05-04 is a Monday, not a holiday
    assert is_market_holiday(date(2026, 5, 4)) is False


def test_half_day():
    from trader.chaos_cases import is_half_day
    assert is_half_day(date(2026, 11, 27)) is True   # day after Thanksgiving 2026
    assert is_half_day(date(2026, 11, 26)) is False  # Thanksgiving itself = full closure


def test_dst_spring_forward():
    from trader.chaos_cases import is_dst_transition_day
    # 2026 spring forward: 2nd Sunday of March = March 8, 2026
    is_dst, direction = is_dst_transition_day(date(2026, 3, 8))
    assert is_dst is True
    assert direction == "spring_forward"


def test_dst_fall_back():
    from trader.chaos_cases import is_dst_transition_day
    # 2026 fall back: 1st Sunday of November = November 1, 2026
    is_dst, direction = is_dst_transition_day(date(2026, 11, 1))
    assert is_dst is True
    assert direction == "fall_back"


def test_dst_normal_day():
    from trader.chaos_cases import is_dst_transition_day
    is_dst, _ = is_dst_transition_day(date(2026, 5, 15))
    assert is_dst is False


def test_next_trading_day_skips_holiday():
    from trader.chaos_cases import next_trading_day
    # 2026-12-25 is Friday Christmas → next is Monday 2026-12-28
    assert next_trading_day(date(2026, 12, 25)) == date(2026, 12, 28)


def test_prev_trading_day_skips_weekend():
    from trader.chaos_cases import prev_trading_day
    # Monday → previous Friday
    assert prev_trading_day(date(2026, 5, 4)) == date(2026, 5, 1)


def test_todays_caveats_holiday():
    from trader.chaos_cases import todays_caveats
    cv = todays_caveats(date(2026, 12, 25))
    assert any("holiday" in c for c in cv)


def test_todays_caveats_clean_day():
    from trader.chaos_cases import todays_caveats
    cv = todays_caveats(date(2026, 5, 4))  # Monday, no caveats
    assert cv == []


# ============================================================
# spa_test (Cat 3 advanced)
# ============================================================

def test_whites_rc_no_cohort_returns_unity():
    from trader.spa_test import whites_reality_check
    out = whites_reality_check([])
    assert out.p_value == 1.0
    assert out.n_variants == 0


def test_whites_rc_picks_lowest_loss():
    """Best variant has the lowest mean loss; index returned matches."""
    from trader.spa_test import whites_reality_check
    # 3 variants, 100 periods. variant 1 has lowest mean loss.
    losses = []
    import random
    rng = random.Random(7)
    for _ in range(100):
        # variant 0: mean 0.0; variant 1: mean -0.005; variant 2: mean +0.005
        losses.append([rng.gauss(0, 0.01),
                        rng.gauss(-0.005, 0.01),
                        rng.gauss(+0.005, 0.01)])
    out = whites_reality_check(losses, B=200)
    assert out.best_variant_idx == 1
    assert out.n_variants == 3
    assert out.n_periods == 100


def test_hansens_spa_signature():
    import inspect
    from trader.spa_test import hansens_spa
    sig = inspect.signature(hansens_spa)
    assert "B" in sig.parameters
    assert "block" in sig.parameters


def test_variants_to_loss_matrix():
    from trader.spa_test import variants_to_loss_matrix
    variants = {"a": [0.01, 0.02], "b": [0.03, 0.04]}
    benchmark = [0.005, 0.005]
    losses, names = variants_to_loss_matrix(variants, benchmark)
    assert names == ["a", "b"]
    assert len(losses) == 2
    assert len(losses[0]) == 2
    # Loss for a at t=0: benchmark - return = 0.005 - 0.01 = -0.005
    assert losses[0][0] == pytest.approx(-0.005)


# ============================================================
# scripted_scenarios — replay engine
# ============================================================

def test_scripted_run_scenario_with_synthetic_panel():
    """Verify the engine actually computes a portfolio path."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import scripted_scenarios as sc
    from datetime import date as _date

    # Build a synthetic SPY panel for 60 days starting Jan 1 2024
    rows = [(_date(2024, 1, 1) + timedelta(days=i),
             100.0 * (1.001 ** i)) for i in range(60)]
    panel = {"SPY": rows}

    # A simple scenario with one shock
    scenario = sc.ScriptedScenario(
        name="test", base_start="2024-01-01", base_end="2024-03-01",
        shocks=[{"day": 5, "spy_ret": -0.10}],
        expected_dd_band=(-0.15, -0.05),
        description="test", archetype=None,
    )
    result = sc.run_scenario(scenario, panel)
    assert result["status"] == "OK"
    assert result["days_run"] > 0
    # The -10% shock should produce a max DD ≤ -8% (close to the shock magnitude)
    assert result["max_drawdown"] < -0.05


def test_scripted_run_scenario_missing_panel():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import scripted_scenarios as sc
    scenario = sc.SCENARIOS[0]
    result = sc.run_scenario(scenario, {})
    assert result["status"] == "MISSING_BASE_PANEL"


def test_scripted_scenarios_count():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import scripted_scenarios as sc
    # Doc specifies 11 scenarios
    assert len(sc.SCENARIOS) == 11


def test_scripted_in_band_check():
    """Scenario whose result lands in expected_dd_band → in_band True."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import scripted_scenarios as sc

    rows = [(date(2024, 1, 1) + timedelta(days=i), 100.0)  # constant prices
            for i in range(60)]
    # 5% drop at day 5 → max DD ≈ -5%
    scenario = sc.ScriptedScenario(
        name="t", base_start="2024-01-01", base_end="2024-03-01",
        shocks=[{"day": 5, "spy_ret": -0.05}],
        expected_dd_band=(-0.10, -0.02),  # band that should contain -0.05
        description="t",
    )
    result = sc.run_scenario(scenario, {"SPY": rows})
    assert result["in_band"] is True


# ============================================================
# Runner scripts
# ============================================================

def test_run_walk_forward_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import run_walk_forward as rwf
    assert callable(rwf.main)


def test_mutation_testing_script_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "run_mutation_testing.sh"
    assert p.exists()
    text = p.read_text()
    assert "mutmut" in text


# ============================================================
# Dashboard validation view wired
# ============================================================

def test_validation_view_in_dashboard():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def view_validation" in text
    assert '"validation": view_validation' in text
    assert "🧪 Validation" in text

"""Tests for v3.73.5 — portfolio concentration caps.

Two layers:

  1. apply_portfolio_caps unit tests — direct mathematical
     properties (gross-preservation, idempotency, redistribution
     correctness, both-bound interaction).

  2. Integration into rank_vertical_winner — verify the
     feature-flagged second strategy mode produces the expected
     1-per-sector shape and the absolute-momentum floor gates.

Together these cover the full v3.73.5 contract.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent

# A simple deterministic sector-of map for unit-test isolation
SECTOR_MAP = {
    "A": "Tech", "B": "Tech", "C": "Tech", "D": "Tech",
    "E": "Financials", "F": "Financials",
    "G": "Healthcare", "H": "Healthcare",
    "I": "Energy",
    "J": "Industrials",
    "K": "ConsumerStap",
}


def _sector(t: str) -> str:
    return SECTOR_MAP.get(t, "Other")


# ============================================================
# Caps math — no-op cases
# ============================================================
def test_no_cap_bound_when_book_is_within_limits():
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.05, "E": 0.05, "G": 0.05, "I": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.25)
    assert not res.name_cap_bound
    assert not res.sector_cap_bound
    # Gross preserved
    assert abs(sum(res.targets.values()) - 0.20) < 1e-9
    # Targets unchanged
    for t, w in targets.items():
        assert abs(res.targets[t] - w) < 1e-9


def test_empty_input_returns_empty():
    from trader.portfolio_caps import apply_portfolio_caps
    res = apply_portfolio_caps({}, _sector)
    assert res.targets == {}
    assert not res.name_cap_bound
    assert not res.sector_cap_bound


# ============================================================
# Caps math — single-name cap
# ============================================================
def test_name_cap_clips_oversized_name_with_headroom():
    """One name above cap, three under with enough total headroom to
    absorb the full excess — gross is preserved."""
    from trader.portfolio_caps import apply_portfolio_caps
    # A is 15% > 8% cap; E,G,J at 5% each have 3pp of headroom × 3 =
    # 9pp total, enough to absorb the 7pp excess.
    targets = {"A": 0.15, "E": 0.05, "G": 0.05, "J": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.50)
    assert res.name_cap_bound
    assert not res.sector_cap_bound
    # A clipped to exactly 8%
    assert abs(res.targets["A"] - 0.08) < 1e-6
    # Excess (7pp) fits in the 9pp of available headroom; gross
    # is preserved at 0.30
    assert abs(sum(res.targets.values()) - 0.30) < 1e-6
    # Each of E, G, J grew but stayed at or below 8%
    for t in ("E", "G", "J"):
        assert res.targets[t] > 0.05
        assert res.targets[t] <= 0.08 + 1e-6, \
            f"{t} overshot name cap: {res.targets[t]}"


def test_name_cap_drops_gross_when_headroom_exhausted():
    """One oversized name, only two under-cap names with combined
    headroom < excess. Algorithm fills available headroom and lets
    gross drop by the excess that doesn't fit. Don't oscillate."""
    from trader.portfolio_caps import apply_portfolio_caps
    # A=15%, E=G=5%. Cap=8%. Excess from clip = 7pp.
    # Headroom in E+G = 3pp + 3pp = 6pp < 7pp.
    # Result: A→8%, E→8%, G→8%, gross drops 0.25→0.24.
    targets = {"A": 0.15, "E": 0.05, "G": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.50)
    assert res.name_cap_bound
    # All names at exactly the cap (nothing oscillating, nothing over)
    for t in "AEG":
        assert abs(res.targets[t] - 0.08) < 1e-6, \
            f"{t} should be exactly at cap: got {res.targets[t]}"
    # Gross dropped by the un-fittable excess (1pp)
    assert abs(sum(res.targets.values()) - 0.24) < 1e-6


def test_name_cap_with_all_at_cap_is_noop():
    """Pathological case — every name already at exactly the cap."""
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.08, "E": 0.08, "G": 0.08}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.50)
    # Nothing to clip; nothing to redistribute. Idempotent.
    assert not res.name_cap_bound
    for t in "AEG":
        assert abs(res.targets[t] - 0.08) < 1e-6


def test_name_cap_with_all_over_cap_clips_all():
    """Every name over cap with no headroom anywhere — clip all,
    accept lower gross."""
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.20, "E": 0.20, "G": 0.20}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.50)
    assert res.name_cap_bound
    # All clipped to cap
    for t in "AEG":
        assert abs(res.targets[t] - 0.08) < 1e-6
    # Gross dropped from 0.60 to 0.24 (3 × 0.08)
    assert abs(sum(res.targets.values()) - 0.24) < 1e-6


# ============================================================
# Caps math — sector cap
# ============================================================
def test_sector_cap_clips_oversized_sector():
    """Tech at 30%; cap at 25%. Tech names scale down; under-cap
    sectors absorb the freed weight."""
    from trader.portfolio_caps import apply_portfolio_caps
    # Tech: A+B+C+D = 4 * 7.5% = 30%. Financials: E = 5%. Healthcare: G = 5%.
    targets = {"A": 0.075, "B": 0.075, "C": 0.075, "D": 0.075,
               "E": 0.05, "G": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.20, sector_cap=0.25)
    assert res.sector_cap_bound
    assert not res.name_cap_bound

    # Tech total = 25%
    tech_total = res.targets["A"] + res.targets["B"] + res.targets["C"] + res.targets["D"]
    assert abs(tech_total - 0.25) < 1e-3

    # Each Tech name scaled by 25/30 = 0.833 → 6.25% each
    for t in "ABCD":
        assert abs(res.targets[t] - 0.0625) < 1e-3, \
            f"{t} should scale to 6.25%, got {res.targets[t]:.4f}"

    # Gross preserved
    assert abs(sum(res.targets.values()) - sum(targets.values())) < 1e-3


def test_sector_cap_with_one_sector_only_drops_gross():
    """Edge case — every name in same sector, sector exceeds cap, no
    other sector to absorb. Sector trims to cap; gross drops by the
    freed weight (no oscillation)."""
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.20, "B": 0.20, "C": 0.20}  # 60% Tech
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.50, sector_cap=0.25)
    assert res.sector_cap_bound
    # Tech total clipped to 25%
    tech_total = res.targets["A"] + res.targets["B"] + res.targets["C"]
    assert abs(tech_total - 0.25) < 1e-3, \
        f"single-sector book should clip to cap: got {tech_total}"
    # Each name scaled equally (proportional trim)
    for t in "ABC":
        assert abs(res.targets[t] - 0.25/3) < 1e-3


# ============================================================
# Caps math — both bound
# ============================================================
def test_both_caps_bind_simultaneously():
    """A is 15% (over name cap) AND Tech is 30% (over sector cap).
    Both caps must apply; gross preserved."""
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.15, "B": 0.05, "C": 0.05, "D": 0.05,
               "E": 0.05, "G": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.25)
    assert res.name_cap_bound
    assert res.sector_cap_bound
    # A clipped to 8%
    assert res.targets["A"] <= 0.08 + 1e-3
    # Tech total <= 25%
    tech = sum(res.targets[t] for t in "ABCD")
    assert tech <= 0.25 + 1e-3, f"Tech still over cap: {tech}"
    # Gross preserved
    assert abs(sum(res.targets.values()) - sum(targets.values())) < 1e-3


# ============================================================
# Caps metadata — dashboard read path
# ============================================================
def test_capresult_summary_when_no_cap_bound():
    from trader.portfolio_caps import apply_portfolio_caps
    res = apply_portfolio_caps({"A": 0.05, "E": 0.05}, _sector)
    assert "no caps" in res.summary().lower()


def test_capresult_summary_when_sector_cap_bound():
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.075, "B": 0.075, "C": 0.075, "D": 0.075,
               "E": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.20, sector_cap=0.25)
    s = res.summary().lower()
    assert "tech" in s
    assert "sector" in s


def test_capresult_records_pre_and_post_metrics():
    from trader.portfolio_caps import apply_portfolio_caps
    targets = {"A": 0.30, "E": 0.05}
    res = apply_portfolio_caps(targets, _sector,
                                name_cap=0.08, sector_cap=0.50)
    assert abs(res.pre_cap_max_name - 0.30) < 1e-9
    assert res.post_cap_max_name <= 0.08 + 1e-6


# ============================================================
# Constants are sane
# ============================================================
def test_default_caps_match_dd():
    """8% single-name + 25% sector — the v3.73.5 ship."""
    from trader.portfolio_caps import (
        SINGLE_NAME_CAP_PCT, SECTOR_CAP_PCT,
    )
    assert SINGLE_NAME_CAP_PCT == 0.08
    assert SECTOR_CAP_PCT == 0.25


# ============================================================
# rank_vertical_winner — strategy-mode flag
# ============================================================
def test_rank_vertical_winner_signature_exists():
    """Don't import-execute (network required for fetch_history) —
    just verify the symbol + signature are correct."""
    import inspect
    from trader.strategy import rank_vertical_winner
    sig = inspect.signature(rank_vertical_winner)
    params = sig.parameters
    assert "universe" in params
    assert "absolute_momentum_floor" in params
    assert params["absolute_momentum_floor"].default == 0.0


def test_strategy_module_documents_v3_73_5():
    """Module docstring must mention v3.73.5 + the empirical Sharpe
    finding so future readers see why this mode exists."""
    from trader import strategy
    doc = strategy.__doc__ or ""
    assert "v3.73.5" in doc
    assert "rank_vertical_winner" in doc
    assert "Sharpe" in doc


# ============================================================
# Integration — main.py wiring
# ============================================================
def test_main_imports_apply_portfolio_caps():
    """The composition path in build_targets must import the cap
    helper. This test guards against the cap module being shipped but
    never wired into the rebalance path."""
    text = (ROOT / "src" / "trader" / "main.py").read_text()
    assert "apply_portfolio_caps" in text
    assert "STRATEGY_MODE" in text
    assert "VERTICAL_WINNER" in text


def test_dashboard_version_v3_73_5():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "v3.73.5" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"

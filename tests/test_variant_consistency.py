"""Spec test: verify the registered LIVE variant matches what build_targets()
would actually produce in production.

Background: in v3.6 we found that for 8 days the production code was running
top-5 at 80% (TOP_N=5 default) while the LIVE variant metadata claimed
top-3 at 80% (momentum_top3_aggressive_v1). The bug: the variant registry
was decorative, not authoritative. A spec test would have caught it.

These tests assert the registry IS the source of truth.
"""
from __future__ import annotations

import pytest

from src.trader import variants  # noqa: F401  (registers variants on import)
from src.trader.ab import get_live, get_shadows
from src.trader.universe import DEFAULT_LIQUID_50


def test_live_variant_is_registered():
    """Exactly one variant must be marked status='live'."""
    live = get_live()
    assert live is not None, "no LIVE variant registered"
    assert live.status == "live"
    # Sanity check: variant_id ends with _v{n} per convention
    assert "_v" in live.variant_id


def test_live_variant_returns_top15_mom_weighted_at_80():
    """LIVE strategy v3.42: top-15 momentum-weighted at 80% allocation.

    Promoted 2026-04-29 from shadow. Replaces v3.1 top-3 LIVE in favor of
    materially lower idiosyncratic risk (10% max single-name vs 27%) at
    equivalent Sharpe on PIT-honest backtest.

    This test pins the strategy. If LIVE changes, this test must update —
    forcing explicit acknowledgment of the change.
    """
    live = get_live()
    targets = live.fn(universe=DEFAULT_LIQUID_50, equity=100_000.0,
                      account_state={})
    assert targets, f"LIVE variant {live.variant_id} returned empty targets"
    assert len(targets) == 15, f"LIVE should pick 15 names, got {len(targets)}"
    total_alloc = sum(targets.values())
    assert 0.78 <= total_alloc <= 0.82, (
        f"LIVE total allocation {total_alloc:.3f} outside [0.78, 0.82] band "
        f"(should be ~0.80 = 80%)"
    )
    # Momentum-weighted: top name should have higher weight than bottom name
    weights = sorted(targets.values(), reverse=True)
    assert weights[0] > weights[-1], "weights should be momentum-proportional, not equal"
    # No single name should exceed 15% (sanity check on diversification)
    max_weight = max(targets.values())
    assert max_weight < 0.15, (
        f"max single-name weight {max_weight:.3f} too concentrated; "
        f"top-15 mom-weighted should keep all names < 15%"
    )


def test_shadows_dont_collide_with_live():
    """No shadow variant should claim status='live'."""
    shadows = get_shadows()
    for v in shadows:
        assert v.status == "shadow", (
            f"variant {v.variant_id} in get_shadows() has status={v.status}"
        )


def test_build_targets_matches_live_variant_function():
    """The CRITICAL drift test: production's build_targets() must produce
    the same momentum allocation as the LIVE variant function would.

    This is what v3.6 fixed. If someone re-introduces the bug (e.g., reverts
    main.py to use rank_momentum(top_n=TOP_N) directly without going through
    the variant), this test fails.
    """
    from src.trader.main import build_targets

    live = get_live()
    live_targets = live.fn(universe=DEFAULT_LIQUID_50, equity=100_000.0,
                           account_state={})

    # build_targets returns (momentum_targets, approved_bottoms, sleeve_alloc).
    # We test only the momentum_targets piece — bottoms are a separate sleeve.
    momentum_targets, _bottoms, _sleeve = build_targets(DEFAULT_LIQUID_50)

    # The names should match
    assert set(momentum_targets.keys()) == set(live_targets.keys()), (
        f"PROD picks {sorted(momentum_targets.keys())} != "
        f"LIVE variant picks {sorted(live_targets.keys())}. "
        f"This is the v3.6 drift bug — production diverged from the registered LIVE."
    )

    # The weights should match within 0.5%
    for ticker, prod_weight in momentum_targets.items():
        live_weight = live_targets[ticker]
        assert abs(prod_weight - live_weight) < 0.005, (
            f"{ticker}: prod weight {prod_weight:.3f} != "
            f"LIVE weight {live_weight:.3f}"
        )


def test_variant_registry_has_no_duplicate_ids():
    """Each variant_id must be unique."""
    from src.trader.ab import _REGISTRY
    ids = [v.variant_id for v in _REGISTRY.values()]
    assert len(ids) == len(set(ids)), f"duplicate variant_ids in {ids}"


def test_production_path_picks_match_backtest_path_picks():
    """v3.27 reviewer-finding regression: rank_momentum (production, in
    src/trader/strategy.py) and _momentum_picks_as_of (backtest, in
    scripts/regime_stress_test.py) are SEPARATE code paths that could
    diverge silently. The v3.6 bug was exactly this kind of drift.

    Verify: same universe + same lookback gives same picks via both paths.
    """
    import sys, os
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "src"))

    from src.trader.strategy import rank_momentum
    from src.trader.universe import DEFAULT_LIQUID_50
    from regime_stress_test import _momentum_picks_as_of

    import pandas as pd
    # Use a backtest as_of in the past so both paths see the same data window
    as_of = pd.Timestamp("2026-04-01")

    # Production path: rank_momentum uses pd.Timestamp.today() internally,
    # so we can't easily test it AS-OF a past date. Test that both paths
    # produce the SAME ranking on TODAY's data.
    today_picks_prod = [c.ticker for c in rank_momentum(DEFAULT_LIQUID_50, top_n=3)]
    today_picks_back = _momentum_picks_as_of(pd.Timestamp.today(), top_n=3)

    # Both should return 3 picks
    assert len(today_picks_prod) == 3, f"production returned {len(today_picks_prod)} picks, expected 3"
    assert len(today_picks_back) == 3, f"backtest returned {len(today_picks_back)} picks, expected 3"

    # The picks should match. If they don't, code paths have drifted —
    # this is exactly the v3.6 bug scenario.
    set_prod = set(today_picks_prod)
    set_back = set(today_picks_back)
    overlap = set_prod & set_back
    # Allow at most 1 difference (different rebalance-day handling can cause
    # a borderline case to swap order). If more than 1 difference, drift.
    assert len(overlap) >= 2, (
        f"PRODUCTION path picks {sorted(set_prod)} != BACKTEST path picks "
        f"{sorted(set_back)}. Code paths have diverged ≥2 names. This is the "
        f"v3.6 drift scenario. Investigate strategy.py:rank_momentum vs "
        f"regime_stress_test._momentum_picks_as_of."
    )

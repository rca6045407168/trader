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


def test_live_variant_returns_top3_at_80():
    """LIVE strategy is top-3 momentum at 80% allocation per v3.1+ docs.

    This test pins the strategy. If we deliberately change LIVE, this test
    must update — and that's a feature, not a bug. It forces explicit
    acknowledgment whenever LIVE changes.
    """
    live = get_live()
    targets = live.fn(universe=DEFAULT_LIQUID_50, equity=100_000.0,
                      account_state={})
    assert targets, f"LIVE variant {live.variant_id} returned empty targets"
    assert len(targets) == 3, f"LIVE should pick 3 names, got {len(targets)}"
    total_alloc = sum(targets.values())
    assert 0.78 <= total_alloc <= 0.82, (
        f"LIVE total allocation {total_alloc:.3f} outside [0.78, 0.82] band "
        f"(should be ~0.80 = 80%)"
    )
    # Each name should be roughly equal-weight
    expected_per = total_alloc / 3
    for ticker, weight in targets.items():
        assert abs(weight - expected_per) < 0.01, (
            f"{ticker} weight {weight:.3f} too far from expected "
            f"equal-weight {expected_per:.3f}"
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

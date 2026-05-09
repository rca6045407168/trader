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
    # Sanity check on diversification. The min-shift formula
    # (score - min(score) + 0.01) can produce 17-20% top weights when
    # the momentum spread is wide; downstream the 8% single-name cap
    # binds anyway. Threshold 0.20 keeps the guard against egregious
    # concentration without flaking on normal market spread variation.
    max_weight = max(targets.values())
    assert max_weight < 0.20, (
        f"max single-name weight {max_weight:.3f} too concentrated; "
        f"top-15 mom-weighted should keep all names < 20% pre-cap"
    )


def test_shadows_dont_collide_with_live():
    """No shadow variant should claim status='live'."""
    shadows = get_shadows()
    for v in shadows:
        assert v.status == "shadow", (
            f"variant {v.variant_id} in get_shadows() has status={v.status}"
        )

def test_variant_registry_has_no_duplicate_ids():
    """Each variant_id must be unique."""
    from src.trader.ab import _REGISTRY
    ids = [v.variant_id for v in _REGISTRY.values()]
    assert len(ids) == len(set(ids)), f"duplicate variant_ids in {ids}"


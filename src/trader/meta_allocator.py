"""Meta-allocator — capital allocation across LIVE sleeves.

Today: only one LIVE variant (top-15 mom-weighted) gets 100% of allocated
capital. World-class shops (AQR, Bridgewater, Citadel multi-strat) run several
uncorrelated sleeves in parallel, with capital reallocated based on:
  - Equal-risk-weight (volatility-balanced; AQR-style)
  - Rolling Sharpe (more capital to recently-strong sleeves; Citadel-style)
  - Risk-parity across sleeves (HRP applied at sleeve level)

This module is the SLEEVE-LEVEL allocator. The per-sleeve variant function
still chooses NAMES + intra-sleeve weights. The meta-allocator chooses how
much CAPITAL each sleeve receives.

ENV flag: META_ALLOCATOR_MODE in {"single_live", "equal_risk", "rolling_sharpe"}
Default: "single_live" — bit-for-bit identical behavior to v3.48.

Why default to single_live: until we have multiple LIVE sleeves with
independently-PIT-validated edge, multi-sleeve allocation IS overengineering.
This module is the wiring; the sleeves themselves come in Tier C.

Decision rules (when active):
  - equal_risk: each LIVE sleeve gets 1/N gross at equal risk (vol-scaled).
    Falls back to 1/N nominal if vol history unavailable.
  - rolling_sharpe: weight ∝ max(0, rolling 60-day Sharpe). Min weight 5%
    each so no sleeve goes fully dark. Cap any single sleeve at 60% gross.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .ab import Variant, _REGISTRY


META_ALLOCATOR_MODE = os.getenv("META_ALLOCATOR_MODE", "single_live").lower()
ROLLING_SHARPE_DAYS = int(os.getenv("META_ALLOCATOR_SHARPE_DAYS", "60"))
MIN_SLEEVE_WEIGHT = 0.05
MAX_SLEEVE_WEIGHT = 0.60
TARGET_GROSS = 0.95  # matches risk_manager.MAX_GROSS_EXPOSURE


@dataclass
class AllocatorDecision:
    mode: str
    sleeve_weights: dict[str, float] = field(default_factory=dict)  # variant_id → fraction of TOTAL gross
    rationale: str = ""
    sleeve_sharpes: dict[str, float] = field(default_factory=dict)
    sleeve_vols: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


def _get_live_variants() -> list[Variant]:
    return [v for v in _REGISTRY.values() if v.status == "live"]


def _compute_sleeve_perf_from_journal(variant_id: str, lookback_days: int) -> tuple[Optional[float], Optional[float]]:
    """Compute rolling Sharpe + ann vol of a sleeve from its shadow_decisions
    history + daily_snapshots. Returns (sharpe, vol_annual) or (None, None)
    if insufficient history.

    Sharpe = mean(daily_ret) * 252 / (std(daily_ret) * sqrt(252))
    Daily ret per sleeve = sum over names of (target_weight * name_daily_ret).

    NOTE: This is approximate. Sleeve "returns" reflect TARGETS not REALIZED
    fills. For LIVE sleeve we'd use realized journal data; for shadows we
    use the targets. Good enough for relative ranking across sleeves.
    """
    try:
        from .journal import _conn
        import numpy as np
        # For now: use SPY return as a proxy weight × return calc
        # Real implementation would walk shadow_decisions × name_returns
        # Stub: return None to fall back to equal-weight in v3.49.0; the
        # full impl lands in v3.50 with proper sleeve-PnL backfill.
        return None, None
    except Exception:
        return None, None


def allocate(target_gross: float = TARGET_GROSS) -> AllocatorDecision:
    """Compute capital allocation across LIVE sleeves.

    Output: variant_id → fraction of total gross (sums to ≤ target_gross).
    """
    live = _get_live_variants()
    n = len(live)

    if n == 0:
        return AllocatorDecision(
            mode=META_ALLOCATOR_MODE,
            rationale="no LIVE sleeves registered",
        )

    if META_ALLOCATOR_MODE == "single_live" or n == 1:
        # Default: single LIVE sleeve gets all the capacity.
        # The variant function itself encodes its own gross (e.g. 0.80).
        return AllocatorDecision(
            mode=META_ALLOCATOR_MODE,
            sleeve_weights={live[0].variant_id: 1.0},
            rationale=f"single_live: {live[0].variant_id} → 100% of allocated gross",
        )

    if META_ALLOCATOR_MODE == "equal_risk":
        # Equal-risk weight: each sleeve gets target_gross/N nominal.
        # When sleeve PnL history is wired, switch to vol-scaled equal-risk.
        per = target_gross / n
        weights = {v.variant_id: per / target_gross for v in live}
        return AllocatorDecision(
            mode="equal_risk",
            sleeve_weights=weights,
            rationale=f"equal_risk over {n} LIVE sleeves: each {per*100:.1f}% gross",
        )

    if META_ALLOCATOR_MODE == "rolling_sharpe":
        sharpes: dict[str, float] = {}
        vols: dict[str, float] = {}
        for v in live:
            sh, vol = _compute_sleeve_perf_from_journal(v.variant_id, ROLLING_SHARPE_DAYS)
            if sh is not None:
                sharpes[v.variant_id] = sh
            if vol is not None:
                vols[v.variant_id] = vol
        # Use max(0, sharpe) so negative-Sharpe sleeves get min weight only
        positive = {k: max(0, s) for k, s in sharpes.items()}
        if not positive or sum(positive.values()) == 0:
            # Fall back to equal-risk if no Sharpe history
            per = target_gross / n
            weights = {v.variant_id: per / target_gross for v in live}
            return AllocatorDecision(
                mode="rolling_sharpe (fallback: equal_risk)",
                sleeve_weights=weights,
                sleeve_sharpes=sharpes,
                sleeve_vols=vols,
                rationale=f"no rolling Sharpe history; equal-risk over {n} sleeves",
            )
        total = sum(positive.values())
        raw = {k: s / total for k, s in positive.items()}
        # Apply MIN/MAX bounds + fill missing variants with MIN
        for v in live:
            raw.setdefault(v.variant_id, MIN_SLEEVE_WEIGHT)
        # Clip to [MIN, MAX] then renormalize
        clipped = {k: max(MIN_SLEEVE_WEIGHT, min(MAX_SLEEVE_WEIGHT, w)) for k, w in raw.items()}
        s = sum(clipped.values())
        weights = {k: w / s for k, w in clipped.items()}
        return AllocatorDecision(
            mode="rolling_sharpe",
            sleeve_weights=weights,
            sleeve_sharpes=sharpes,
            sleeve_vols=vols,
            rationale=f"rolling_sharpe ({ROLLING_SHARPE_DAYS}d): "
                      + ", ".join(f"{k}={w*100:.1f}%" for k, w in weights.items()),
        )

    # Unknown mode → safe default
    return AllocatorDecision(
        mode="single_live (fallback: unknown mode)",
        sleeve_weights={live[0].variant_id: 1.0},
        rationale=f"unknown META_ALLOCATOR_MODE='{META_ALLOCATOR_MODE}'; defaulting to single_live",
    )


def apply_meta_allocation(per_sleeve_targets: dict[str, dict[str, float]],
                          allocator: Optional[AllocatorDecision] = None) -> dict[str, float]:
    """Combine per-sleeve target dicts using the meta-allocator weights.

    Args:
        per_sleeve_targets: {variant_id: {ticker: weight_within_sleeve}}
                            where each inner dict represents one sleeve's
                            allocation. Inner weights are fractions of the
                            sleeve's allocated capital, NOT of total equity.
        allocator: pre-computed AllocatorDecision; if None, computes fresh.

    Returns:
        {ticker: weight_of_total_equity} after combining all sleeves per the
        meta-allocator weighting.
    """
    if allocator is None:
        allocator = allocate()
    combined: dict[str, float] = {}
    for variant_id, sleeve_weight in allocator.sleeve_weights.items():
        sleeve_targets = per_sleeve_targets.get(variant_id, {})
        for ticker, weight in sleeve_targets.items():
            combined[ticker] = combined.get(ticker, 0.0) + weight * sleeve_weight
    return combined

"""Strategy variants registered with the A/B framework.

Pattern: each variant is a function returning {ticker: portfolio_pct} target weights.
Registration happens at module import.

CURRENT REGISTRATIONS (v2.9):
  - momentum_top5_eq_v1 — LIVE — 12-month momentum, top-5 from liquid-50, equal-weight
  - momentum_top5_sector_capped_v1 — SHADOW — same picks but max 25% per GICS sector
  - momentum_top10_diluted_v1 — SHADOW — top-10 instead of top-5 (less concentrated)

Why these shadows: the 20-agent debate showed sector cap was a real trade-off
(reduces drawdown but lowers CAGR). Shadow lets us measure on LIVE data over 30+ days
before committing capital. Top-10 dilution is a simpler diversification play.

Each shadow is logged but emits NO orders. After 30+ days of evidence,
scripts/compare_variants.py determines which (if any) to promote.
"""
from __future__ import annotations

from typing import Any

from .ab import register_variant
from .strategy import rank_momentum
from .sectors import get_sector


def momentum_top5_eq(universe: list[str], equity: float,
                    account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """Live variant: top-5 by 12m momentum, equal-weight."""
    picks = rank_momentum(universe, top_n=5)
    if not picks:
        return {}
    weight = 0.40 / len(picks)  # MOMENTUM_ALLOC=0.40 from main.py
    return {c.ticker: weight for c in picks}


def momentum_top5_sector_capped(universe: list[str], equity: float,
                                 account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-5 momentum but max 1 per GICS sector (effective 20% sector cap)."""
    candidates = rank_momentum(universe, top_n=20)
    selected: list = []
    sectors_used: set[str] = set()
    for c in candidates:
        sec = get_sector(c.ticker)
        if sec in sectors_used:
            continue
        sectors_used.add(sec)
        selected.append(c)
        if len(selected) >= 5:
            break
    if not selected:
        return {}
    weight = 0.40 / len(selected)
    return {c.ticker: weight for c in selected}


def momentum_top10_diluted(universe: list[str], equity: float,
                            account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-10 instead of top-5 — naive diversification."""
    picks = rank_momentum(universe, top_n=10)
    if not picks:
        return {}
    weight = 0.40 / len(picks)
    return {c.ticker: weight for c in picks}


# Register variants on import
register_variant(
    variant_id="momentum_top5_eq_v1",
    name="momentum_top5_eq",
    version="1.0",
    status="live",
    fn=momentum_top5_eq,
    description="12-month cross-sectional momentum, top-5, equal-weight, monthly rebal. Walk-forward OOS Sharpe 0.76.",
    params={"top_n": 5, "lookback_months": 12, "weighting": "equal", "alloc": 0.40},
)

register_variant(
    variant_id="momentum_top5_sector_capped_v1",
    name="momentum_top5_sector_capped",
    version="1.0",
    status="shadow",
    fn=momentum_top5_sector_capped,
    description="Same top-5 momentum, but 1-per-sector. Backtest 2015-2025 showed -4.4% CAGR / Sharpe-neutral / -8.4% MaxDD better. Trade-off; testing live.",
    params={"top_n": 5, "max_per_sector": 1, "alloc": 0.40},
)

register_variant(
    variant_id="momentum_top10_diluted_v1",
    name="momentum_top10_diluted",
    version="1.0",
    status="shadow",
    fn=momentum_top10_diluted,
    description="Top-10 instead of top-5 — naive diversification across more names.",
    params={"top_n": 10, "weighting": "equal", "alloc": 0.40},
)

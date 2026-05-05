"""v3.73.7 — Candidate strategies for constant evaluation.

Distinct from strategy_registry.py (which catalogs production-routed
strategies). This module holds the pure-function candidates used by
the eval runner to score 10 alternatives in parallel.

Each strategy is:

    fn(asof: pd.Timestamp, prices: pd.DataFrame) -> dict[ticker, weight]

All strategies share the same universe + same momentum signal so the
comparison isolates SELECTION + WEIGHTING differences only.

Today: 10 strategies covering the design space (concentrated vs.
diversified, equal vs. weighted, single-name signal vs. sector-stratified):

  1. xs_top15               XS top-15 equal-weight @ 80% (current baseline)
  2. xs_top15_capped        XS top-15 + 8% name cap + 25% sector cap
  3. vertical_winner        top-1 per sector, abs-momentum floor (≥0)
  4. xs_top8                concentrated (top-8)
  5. xs_top25               diversified (top-25)
  6. score_weighted_xs      XS top-15, weights ∝ score
  7. inv_vol_xs             XS top-15, weights ∝ 1/realized-vol (60d)
  8. dual_momentum          XS top-15 ∩ abs-return-positive
  9. sector_rotation_top3   top-3 sectors by avg score; top-half names
 10. equal_weight_universe  1/N over universe (no signal — sanity check)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import pandas as pd

from .signals import momentum_score
from .sectors import get_sector


@dataclass
class StrategySpec:
    name: str
    description: str
    fn: Callable
    target_gross: float = 0.80


_REGISTRY: dict[str, StrategySpec] = {}


def register(name: str, description: str, target_gross: float = 0.80):
    def deco(fn):
        _REGISTRY[name] = StrategySpec(name, description, fn, target_gross)
        return fn
    return deco


def all_strategies() -> list[StrategySpec]:
    return list(_REGISTRY.values())


def get(name: str) -> Optional[StrategySpec]:
    return _REGISTRY.get(name)


# ============================================================
# Shared helpers
# ============================================================
def _score_universe(asof, prices, lookback: int = 12, skip: int = 1):
    p = prices[prices.index <= asof]
    out: list[tuple[str, float]] = []
    for t in p.columns:
        s = p[t].dropna()
        m = momentum_score(s, lookback, skip)
        if not pd.isna(m):
            out.append((t, float(m)))
    out.sort(key=lambda x: -x[1])
    return out


def _realized_vol(asof, prices, ticker: str, window: int = 60) -> float:
    p = prices[prices.index <= asof][ticker].dropna()
    if len(p) < 5:
        return 0.0
    rets = p.pct_change().dropna().tail(window)
    if len(rets) < 5:
        return 0.0
    return float(rets.std())


# ============================================================
# 1. XS top-15
# ============================================================
@register("xs_top15", "Cross-sectional top-15 equal-weight @ 80% (baseline)")
def xs_top15(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 2. XS top-15 with caps
# ============================================================
@register("xs_top15_capped", "XS top-15 + 8% name cap + 25% sector cap")
def xs_top15_capped(asof, prices):
    from .portfolio_caps import apply_portfolio_caps
    base = xs_top15(asof, prices)
    if not base:
        return {}
    return apply_portfolio_caps(base, get_sector).targets


# ============================================================
# 3. Vertical winner
# ============================================================
@register("vertical_winner",
          "Top-1 per sector with absolute-momentum floor")
def vertical_winner(asof, prices):
    scored = _score_universe(asof, prices)
    best: dict[str, tuple[str, float]] = {}
    for t, m in scored:
        if m < 0:
            continue
        s = get_sector(t)
        if s not in best or m > best[s][1]:
            best[s] = (t, m)
    if not best:
        return {}
    w = 0.80 / len(best)
    return {t: w for t, _ in best.values()}


# ============================================================
# 4. XS top-8 (concentrated)
# ============================================================
@register("xs_top8", "Cross-sectional top-8 equal-weight")
def xs_top8(asof, prices):
    picks = _score_universe(asof, prices)[:8]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 5. XS top-25 (diversified)
# ============================================================
@register("xs_top25", "Cross-sectional top-25 equal-weight")
def xs_top25(asof, prices):
    picks = _score_universe(asof, prices)[:25]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 6. Score-weighted XS top-15
# ============================================================
@register("score_weighted_xs",
          "XS top-15 with weights proportional to momentum score")
def score_weighted_xs(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    raw = {t: max(m, 0.0) for t, m in picks}
    total = sum(raw.values())
    if total <= 0:
        w = 0.80 / len(picks)
        return {t: w for t, _ in picks}
    return {t: 0.80 * (raw[t] / total) for t in raw}


# ============================================================
# 7. Inverse-vol weighted XS top-15
# ============================================================
@register("inv_vol_xs",
          "XS top-15 with weights ∝ 1/realized-vol (60d)")
def inv_vol_xs(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    inv = {}
    for t, _ in picks:
        v = _realized_vol(asof, prices, t)
        inv[t] = 1.0 / v if v > 1e-6 else 0.0
    total = sum(inv.values())
    if total <= 0:
        w = 0.80 / len(picks)
        return {t: w for t, _ in picks}
    return {t: 0.80 * (inv[t] / total) for t in inv}


# ============================================================
# 8. Dual momentum
# ============================================================
@register("dual_momentum",
          "XS top-15 BUT skip names with absolute 12-1 return < 0")
def dual_momentum(asof, prices):
    picks = [(t, m) for t, m in _score_universe(asof, prices)[:15] if m > 0]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 9. Sector rotation: top-3 sectors, top-half names within
# ============================================================
@register("sector_rotation_top3",
          "Top-3 sectors by avg score; equal-weight top-half names")
def sector_rotation_top3(asof, prices):
    scored = _score_universe(asof, prices)
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for t, m in scored:
        by_sector.setdefault(get_sector(t), []).append((t, m))
    avg = {
        s: sum(m for _, m in members) / len(members)
        for s, members in by_sector.items()
    }
    top3 = sorted(avg.items(), key=lambda x: -x[1])[:3]
    selected: list[str] = []
    for s, _ in top3:
        sector_picks = sorted(by_sector[s], key=lambda x: -x[1])
        keep = max(1, len(sector_picks) // 2)
        selected.extend(t for t, _ in sector_picks[:keep])
    if not selected:
        return {}
    w = 0.80 / len(selected)
    return {t: w for t in selected}


# ============================================================
# 10. Equal-weight universe (no signal — sanity floor)
# ============================================================
@register("equal_weight_universe",
          "Naive 1/N over entire universe (passive baseline)")
def equal_weight_universe(asof, prices):
    p = prices[prices.index <= asof]
    available = [t for t in p.columns if not p[t].dropna().empty]
    if not available:
        return {}
    w = 0.80 / len(available)
    return {t: w for t in available}

"""v3.73.17 — Sizing primitives.

Four sizing layers added in response to the question "are you taking
sizing into consideration":

  1. realized_portfolio_vol(returns, window=63) → float
       Compute the annualized realized vol of the portfolio's
       monthly return series. Used by vol-targeting.

  2. vol_target_scalar(realized_vol, target=0.18) → float
       Returns min(target / realized, 1.0) — only ever scales DOWN.
       18% target matches the strategy's design vol expectation
       (12-1 momentum on US large-cap typically realizes 16-22%).

  3. inverse_vol_weights(scores, vols, gross) → dict
       Per-name vol-parity weighting: weight ∝ score / σ(name),
       normalized to target gross. Sizes down high-vol names so
       they contribute equally to portfolio vol as low-vol names
       at the same score.

  4. max_loss_check(targets, equity, max_loss_pct=0.015,
                     stress_pct=0.25) → list[violations]
       Pre-trade gate: if any name's weight × stress_loss exceeds
       max_loss_pct of book, return a violation. Default: no
       single position should be able to cost > 1.5% of book on
       a -25% adverse move.

These are pure functions. The strategies + main.py do the wiring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Trading days per year (annualization)
TRADING_DAYS_PER_YEAR = 252
MONTHS_PER_YEAR = 12


# ============================================================
# 1. Portfolio vol from monthly returns
# ============================================================
def realized_portfolio_vol(monthly_returns: list[float],
                            min_obs: int = 6) -> Optional[float]:
    """Annualized realized vol from a monthly return series.

    Returns None if too few observations (default min 6). Uses
    sample std (ddof=1) and √12 annualization (monthly → annual).
    """
    n = len(monthly_returns)
    if n < min_obs:
        return None
    mean = sum(monthly_returns) / n
    variance = sum((r - mean) ** 2 for r in monthly_returns) / (n - 1)
    return (variance ** 0.5) * (MONTHS_PER_YEAR ** 0.5)


def realized_portfolio_vol_daily(daily_returns: list[float],
                                   min_obs: int = 30) -> Optional[float]:
    """Same idea, daily frequency. √252 annualization."""
    n = len(daily_returns)
    if n < min_obs:
        return None
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    return (variance ** 0.5) * (TRADING_DAYS_PER_YEAR ** 0.5)


# ============================================================
# 2. Vol-target scalar
# ============================================================
def vol_target_scalar(realized_vol: Optional[float],
                       target_vol: float = 0.18,
                       max_scale: float = 1.0) -> float:
    """Return the gross-scaling factor for vol targeting.

    If realized vol is below target → return 1.0 (don't lever up).
    If realized vol exceeds target → scale gross down so projected
    vol matches target.

    Args:
        realized_vol: annualized vol estimate (None → 1.0 fallback)
        target_vol: design vol level (default 18%)
        max_scale: cap on scale-up (default 1.0 = no levering up).
                   Set >1.0 to allow vol-targeting to add gross
                   when realized vol is below target — generally
                   inadvisable for retail.

    Returns: scalar in (0, max_scale]
    """
    if realized_vol is None or realized_vol <= 0:
        return 1.0
    return min(target_vol / realized_vol, max_scale)


def apply_vol_target(targets: dict, realized_vol: Optional[float],
                      target_vol: float = 0.18) -> dict:
    """Scale every weight in `targets` by the vol-target scalar.

    The TOTAL gross of the input is preserved when realized_vol ≤
    target. When realized_vol > target, gross is scaled down
    proportionally; cash buffer absorbs the difference.

    Returns a new dict; does not mutate input.
    """
    scalar = vol_target_scalar(realized_vol, target_vol=target_vol)
    return {t: w * scalar for t, w in targets.items()}


# ============================================================
# 3. Inverse-vol (vol-parity) weighting
# ============================================================
def inverse_vol_weights(scored: list[tuple[str, float]],
                         vols: dict[str, float],
                         target_gross: float = 0.80,
                         min_vol: float = 0.05,
                         min_shift: bool = True) -> dict:
    """Weights ∝ score / vol(name), normalized to target_gross.

    Args:
        scored: list of (ticker, score) pairs, sorted desc by score
        vols: dict ticker → annualized vol (e.g., 0.30 = 30% ann)
        target_gross: total weight to allocate (default 80%)
        min_vol: floor on vol divisor to avoid div-by-zero or
                 weighting a near-zero-vol name to 100%
        min_shift: if True, apply min-shift to scores first
                   (matches the production scheme); falls back to
                   max(score, 0) otherwise.

    Returns: dict ticker → weight, sum ≈ target_gross
    """
    if not scored:
        return {}

    # Score component: same min-shift as production for apples-to-apples
    if min_shift:
        min_s = min(s for _, s in scored)
        score_components = {t: (s - min_s + 0.01) for t, s in scored}
    else:
        score_components = {t: max(s, 0.0) for t, s in scored}

    # Vol-adjusted component
    raw = {}
    for t, sc in score_components.items():
        v = max(vols.get(t, min_vol), min_vol)
        raw[t] = sc / v

    total = sum(raw.values())
    if total <= 0:
        # Fallback: equal-weight at target gross
        return {t: target_gross / len(scored) for t, _ in scored}
    return {t: target_gross * (raw[t] / total) for t in raw}


# ============================================================
# 4. Per-trade max-loss check
# ============================================================
@dataclass
class MaxLossViolation:
    ticker: str
    weight: float
    stress_loss_pct: float  # weight × stress_pct
    max_allowed_pct: float


def max_loss_check(targets: dict,
                    max_loss_pct: float = 0.015,
                    stress_pct: float = 0.25) -> list[MaxLossViolation]:
    """For each target, check `weight × stress_pct ≤ max_loss_pct`.

    Default: refuse any allocation where the position can lose
    1.5%+ of book on a -25% adverse move. With max_loss_pct=0.015
    and stress_pct=0.25, the implied max single-name weight is 6%
    — tighter than the v3.73.5 8% concentration cap.

    Returns list of violations (empty list = clean).
    """
    violations = []
    for t, w in targets.items():
        stress_loss = abs(w) * stress_pct
        if stress_loss > max_loss_pct:
            violations.append(MaxLossViolation(
                ticker=t,
                weight=w,
                stress_loss_pct=stress_loss,
                max_allowed_pct=max_loss_pct,
            ))
    return violations


# ============================================================
# Helper: realized vol per name (for inverse-vol weighting)
# ============================================================
def per_name_vol(prices_panel, asof, window_days: int = 60) -> dict:
    """Compute annualized realized vol per name from daily prices.

    Falls back to 0.20 (20%) when a name doesn't have enough data.
    """
    import pandas as pd
    out = {}
    p = prices_panel[prices_panel.index <= asof]
    for sym in p.columns:
        s = p[sym].dropna()
        if len(s) < 5:
            out[sym] = 0.20
            continue
        rets = s.pct_change().dropna().tail(window_days)
        if len(rets) < 5 or rets.std() == 0:
            out[sym] = 0.20
            continue
        out[sym] = float(rets.std() * (TRADING_DAYS_PER_YEAR ** 0.5))
    return out

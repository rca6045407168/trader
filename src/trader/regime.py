"""Regime classifier for the meta-allocator.

Classifies the current market into one of three regimes using widely-known,
non-curve-fit signals:

  TREND    — SPY above 200d MA, VIX < 25, 3mo/12mo signals agree
             → use 12mo lookback (current LIVE behavior)

  ROTATION — SPY above 200d MA, but 3mo and 12mo top-picks disagree (<2 of 3 overlap)
             → use 3/6/12mo blend (catches sector rotations like 2023 AI rally)

  STRESS   — SPY below 200d MA AND VIX > 25
             → 50% SPY only (defensive cut)

Why these three:
  - 200d MA: simplest trend filter, ~150 years of history. Robust.
  - VIX: implied vol; threshold 25 marks "elevated stress" historically.
  - 3mo/12mo overlap: heuristic for "is the leadership stable or churning?"
    Catches regime CHANGES (rotation) without trying to time them.

Risks:
  - Three rules with thresholds = 3 degrees of overfit freedom. Test discipline:
    must dominate LIVE in ≥3 of 5 regimes; cannot have worse worst-MaxDD.
  - 200d MA + VIX can give false STRESS signals (e.g., 2018-Q4 was choppy but
    not catastrophic). The cut to 50% SPY trades upside for safety.

This module is referenced by:
  - scripts/regime_stress_test.py: variant_regime_aware (backtest)
  - src/trader/variants.py: momentum_regime_aware_v1 (live shadow once registered)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class Regime(str, Enum):
    TREND = "trend"        # bullish, stable leadership
    ROTATION = "rotation"  # bullish, but top-picks churning
    STRESS = "stress"      # bearish + high vol


@dataclass
class RegimeSignal:
    regime: Regime
    spy_above_200ma: bool
    vix: float | None
    momentum_overlap: int       # how many of top-3 (3mo) overlap with top-3 (12mo)
    rationale: str


# Tunables. Conservative defaults; documented in module docstring.
VIX_STRESS_THRESHOLD = 25.0
MOMENTUM_AGREEMENT_MIN = 2  # ≥2 of 3 top picks overlap → stable; else rotation


def _spy_above_200ma(spy_prices: pd.Series, asof: pd.Timestamp) -> bool:
    """True if SPY close on `asof` is above its trailing 200-day moving average."""
    s = spy_prices[spy_prices.index <= asof].dropna()
    if len(s) < 200:
        # Insufficient history — bias toward TREND so we don't accidentally
        # trip STRESS during the first 200 days of any series.
        return True
    ma200 = s.iloc[-200:].mean()
    return float(s.iloc[-1]) > float(ma200)


def _momentum_overlap(picks_3mo: list[str], picks_12mo: list[str]) -> int:
    """Count of names appearing in both the 3mo and 12mo top-N picks."""
    return len(set(picks_3mo) & set(picks_12mo))


def classify_regime(
    spy_prices: pd.Series,
    asof: pd.Timestamp,
    vix: float | None,
    picks_3mo: list[str],
    picks_12mo: list[str],
) -> RegimeSignal:
    """Classify the current market regime.

    Args:
        spy_prices: SPY daily close series (index = pd.Timestamp).
        asof: classification date.
        vix: current VIX level (None ⇒ treat as 20).
        picks_3mo: top-N momentum picks under 3mo lookback.
        picks_12mo: top-N momentum picks under 12mo lookback.

    Returns:
        RegimeSignal with the chosen regime and full feature snapshot.
    """
    above_200ma = _spy_above_200ma(spy_prices, asof)
    overlap = _momentum_overlap(picks_3mo, picks_12mo)
    vix_eff = vix if vix is not None else 20.0

    # 1) Stress regime: bearish trend + elevated vol → cut risk
    if not above_200ma and vix_eff > VIX_STRESS_THRESHOLD:
        return RegimeSignal(
            regime=Regime.STRESS,
            spy_above_200ma=False,
            vix=vix,
            momentum_overlap=overlap,
            rationale=f"SPY < 200d MA + VIX {vix_eff:.1f} > {VIX_STRESS_THRESHOLD}",
        )

    # 2) Rotation regime: bullish trend but leadership churning → diversify horizons
    if above_200ma and overlap < MOMENTUM_AGREEMENT_MIN:
        return RegimeSignal(
            regime=Regime.ROTATION,
            spy_above_200ma=True,
            vix=vix,
            momentum_overlap=overlap,
            rationale=f"SPY > 200d MA but only {overlap}/3 top-pick overlap (3mo vs 12mo) — leadership rotating",
        )

    # 3) Default: trend regime → use stable 12mo signal
    return RegimeSignal(
        regime=Regime.TREND,
        spy_above_200ma=above_200ma,
        vix=vix,
        momentum_overlap=overlap,
        rationale=f"SPY {'>' if above_200ma else '<'} 200d MA, VIX {vix_eff:.1f}, "
                  f"top-pick overlap {overlap}/3",
    )

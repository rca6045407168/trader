"""3-state Gaussian HMM regime classifier on SPY daily returns.

The trader's auto-router currently picks variants by rolling-IR
with no explicit regime awareness. The eligibility filter has β /
DD / evidence constraints but doesn't say "prefer defensive
variants in a bear regime."

This module adds that. Inspired by:
  - Hamilton 1989 (original regime-switching paper)
  - "Regime-Switching Factor Investing with Hidden Markov Models"
    (MDPI 2020) — shows HMM-classified regimes produce higher Sharpe
    when factor strategies are conditional on regime vs unconditional
  - "Forest of Opinions" (2025) — ensemble HMM voting for robustness

Architecture: a single 3-state Gaussian HMM on SPY daily log-returns,
fit on a trailing 3-year window. States are labeled by their mean
return after sorting:
  - BULL: highest-mean state (typical regime: positive drift, low vol)
  - NEUTRAL: middle state (mixed signal)
  - BEAR: lowest-mean state (negative drift, often high vol)

Output: a Regime enum + confidence. Used by:
  - main.py overlay: scales gross by regime
    (BULL 1.0x / NEUTRAL 0.85x / BEAR 0.65x)
  - Future: auto-router eligibility filter can prefer defensive
    variants when current_regime == BEAR

Limitations (honest):
  - HMM state labels are unsupervised; the fit can swap label orders
    between runs. We re-sort by mean each call.
  - 3-year window is heuristic; shorter windows are noisier, longer
    miss regime shifts.
  - Single-asset HMM (SPY only). A multi-asset / multi-factor HMM
    would be more robust but adds substantial complexity.
  - Daily returns; monthly resampling would smooth but lose timing.

Env gates:
  REGIME_OVERLAY_ENABLED=0  → overlay off (default, sandbox mode)
  REGIME_OVERLAY_ENABLED=1  → overlay active in production
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class Regime(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"


@dataclass
class RegimeReading:
    """Current regime + confidence + per-state probabilities."""
    regime: Regime
    confidence: float   # posterior probability of the assigned state
    state_probs: dict[Regime, float]
    n_obs: int
    mean_return_pct: float  # state's fitted mean daily return, in %
    std_return_pct: float   # state's fitted std daily return, in %


# Gross-scalar mapping. The literature suggests larger reductions
# in BEAR; we're conservative because untested regime classifiers
# can mis-classify and a big de-grossing on a false-positive bear
# would compound losses.
DEFAULT_GROSS_BY_REGIME = {
    Regime.BULL: 1.00,
    Regime.NEUTRAL: 0.85,
    Regime.BEAR: 0.65,
}


def _label_states_by_mean(model) -> dict[int, Regime]:
    """Sort fitted HMM states by their mean and assign BEAR/NEUTRAL/BULL.

    hmmlearn's GaussianHMM has `.means_` of shape (n_components, n_features).
    For our 1-feature (SPY daily return) model, means_[:, 0] is what we sort.
    """
    means = model.means_[:, 0]
    order = np.argsort(means)  # ascending → bear, neutral, bull
    return {
        int(order[0]): Regime.BEAR,
        int(order[1]): Regime.NEUTRAL,
        int(order[2]): Regime.BULL,
    }


def classify_regime(spy_prices: pd.Series,
                     window_years: float = 3.0,
                     n_states: int = 3,
                     random_state: int = 42) -> Optional[RegimeReading]:
    """Fit a Gaussian HMM on the trailing `window_years` of SPY daily
    log-returns and return the current regime.

    Returns None if:
      - prices series is too short (<252 obs)
      - HMM fitting fails (rare; usually a degenerate input)
      - hmmlearn not installed

    `spy_prices` should be a pd.Series of SPY closing prices indexed
    by date, ascending order.
    """
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return None
    if spy_prices is None or len(spy_prices) < 252:
        return None

    # Restrict to trailing window
    n_keep = int(window_years * 252)
    s = spy_prices.tail(n_keep).copy()
    rets = np.log(s / s.shift(1)).dropna().values
    if len(rets) < 252:
        return None

    X = rets.reshape(-1, 1)
    try:
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="full",
            n_iter=200,
            random_state=random_state,
        )
        model.fit(X)
    except Exception:
        return None

    # Get the posterior state probabilities for the LAST observation
    try:
        post = model.predict_proba(X)
    except Exception:
        return None
    last_post = post[-1]

    state_labels = _label_states_by_mean(model)
    state_probs = {Regime.BEAR: 0.0, Regime.NEUTRAL: 0.0, Regime.BULL: 0.0}
    for i, p in enumerate(last_post):
        state_probs[state_labels[i]] = float(p)

    # Assigned regime = argmax of state probabilities
    current_regime = max(state_probs, key=state_probs.get)
    confidence = state_probs[current_regime]

    # Stats for the assigned state — useful for the operator UX
    current_state_idx = None
    for idx, lbl in state_labels.items():
        if lbl == current_regime:
            current_state_idx = idx
            break
    mean_pct = float(model.means_[current_state_idx, 0]) * 100
    var_pct_sq = float(model.covars_[current_state_idx][0, 0]) * 10_000
    std_pct = var_pct_sq ** 0.5

    return RegimeReading(
        regime=current_regime,
        confidence=confidence,
        state_probs=state_probs,
        n_obs=len(rets),
        mean_return_pct=mean_pct,
        std_return_pct=std_pct,
    )


def gross_scalar_for_regime(
    regime: Regime,
    mapping: Optional[dict[Regime, float]] = None,
) -> float:
    """Look up the gross-scaling factor for the given regime."""
    m = mapping or DEFAULT_GROSS_BY_REGIME
    return m.get(regime, 1.0)


def apply_regime_overlay(targets: dict[str, float],
                          reading: Optional[RegimeReading],
                          min_confidence: float = 0.55) -> tuple[dict, dict]:
    """Multiplicatively scale weights by the regime scalar.

    The min_confidence threshold prevents acting on weak signal —
    when the HMM's posterior is < 55% on any one state, the regime
    is ambiguous and we leave gross unchanged.

    Returns (new_targets, info_dict) for logging.
    """
    if reading is None:
        return targets, {
            "regime": None,
            "scalar": 1.0,
            "reason": "no regime reading available",
        }
    if reading.confidence < min_confidence:
        return targets, {
            "regime": reading.regime.value,
            "confidence": round(reading.confidence, 3),
            "scalar": 1.0,
            "reason": (f"confidence {reading.confidence:.2f} < threshold "
                        f"{min_confidence:.2f}; regime ambiguous"),
        }
    scalar = gross_scalar_for_regime(reading.regime)
    new_targets = {t: w * scalar for t, w in targets.items()}
    return new_targets, {
        "regime": reading.regime.value,
        "confidence": round(reading.confidence, 3),
        "scalar": scalar,
        "before_gross": round(sum(targets.values()), 4),
        "after_gross": round(sum(new_targets.values()), 4),
        "mean_daily_pct": round(reading.mean_return_pct, 3),
        "std_daily_pct": round(reading.std_return_pct, 3),
    }

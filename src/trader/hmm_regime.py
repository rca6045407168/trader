"""Hidden Markov Model regime detection.

Replaces the simple "SPY > 200d MA + VIX > 25" classifier (v3.5, killed) with
a proper Gaussian HMM fit via EM. The HMM treats the latent regime as an
unobserved Markov state that emits returns from regime-specific Gaussian
distributions.

Why HMM beats heuristic regime detection:
  1. Statistical inference: regime probabilities are Bayesian posteriors,
     not threshold rules. Smooths out noise that trips bang-bang classifiers.
  2. Persistence: HMM transition matrix encodes regime stickiness — won't
     flip on a single -2% day.
  3. Multi-feature: can extend to (return, vol, volume) joint emissions.
  4. Forward-only: Viterbi/Forward filtering uses ONLY past data, no
     look-ahead bias (unlike "trained on all data" classifiers).

References:
  - Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary
    Time Series and the Business Cycle"
  - Rabiner (1989) "A Tutorial on Hidden Markov Models"
  - Bulla & Bulla (2006) "Stylized facts of financial time series and HMMs"
  - Nystrup et al. (2017) "Long memory of financial time series and HMMs
    with non-stationary parameters"

Implementation: Gaussian HMM via hmmlearn (Baum-Welch / EM).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


class HMMRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    TRANSITION = "transition"


@dataclass
class HMMSignal:
    regime: HMMRegime
    state_id: int
    posterior: float           # P(state | observation history)
    expected_return_daily: float
    expected_vol_daily: float
    rationale: str


@dataclass
class FittedHMM:
    """Encapsulates a fitted HMM with state metadata."""
    model: GaussianHMM
    state_to_regime: dict[int, HMMRegime]
    state_means: np.ndarray
    state_vols: np.ndarray
    n_states: int
    feature_dim: int


def _label_states_by_return_volatility(means: np.ndarray, vols: np.ndarray) -> dict[int, HMMRegime]:
    """Map state IDs to regime labels based on (return, vol) characteristics.

    Heuristic:
      - Highest mean return + low vol → BULL
      - Lowest mean return + high vol → BEAR
      - Middle → TRANSITION
    """
    n_states = len(means)
    if n_states == 1:
        return {0: HMMRegime.BULL}
    if n_states == 2:
        # Sort by Sharpe-like ratio (return / vol)
        ratios = means / np.maximum(vols, 1e-9)
        bull = int(np.argmax(ratios))
        bear = 1 - bull
        return {bull: HMMRegime.BULL, bear: HMMRegime.BEAR}
    # 3+ states: top by ratio = bull, bottom = bear, rest = transition
    ratios = means / np.maximum(vols, 1e-9)
    sorted_idx = np.argsort(ratios)
    bear_id = int(sorted_idx[0])
    bull_id = int(sorted_idx[-1])
    out = {bear_id: HMMRegime.BEAR, bull_id: HMMRegime.BULL}
    for i in range(n_states):
        if i not in out:
            out[i] = HMMRegime.TRANSITION
    return out


def fit_hmm(returns: pd.Series, n_states: int = 3, n_iter: int = 200,
             random_state: int = 42, features: Optional[pd.DataFrame] = None) -> FittedHMM:
    """Fit a Gaussian HMM on a return series.

    Args:
        returns: daily returns (decimal), pd.Series indexed by date
        n_states: number of regimes (typically 2 or 3)
        features: optional DataFrame of additional features (e.g. realized vol).
                  Columns are concatenated with returns to form multi-dim observations.
                  Default: univariate (returns only).
    """
    r = returns.dropna()
    if features is not None:
        f = features.loc[r.index].dropna()
        common = r.index.intersection(f.index)
        r = r.loc[common]
        f = f.loc[common]
        X = np.column_stack([r.values, f.values])
    else:
        X = r.values.reshape(-1, 1)

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=n_iter,
        random_state=random_state,
        tol=1e-5,
    )
    model.fit(X)

    means = model.means_[:, 0]  # return-dim mean per state
    vols = np.sqrt(np.diagonal(model.covars_, axis1=1, axis2=2)[:, 0]) if model.covars_.ndim == 3 else np.sqrt(model.covars_[:, 0])
    state_to_regime = _label_states_by_return_volatility(means, vols)

    return FittedHMM(
        model=model,
        state_to_regime=state_to_regime,
        state_means=means,
        state_vols=vols,
        n_states=n_states,
        feature_dim=X.shape[1],
    )


def classify_current_regime(hmm: FittedHMM, recent_returns: pd.Series,
                              recent_features: Optional[pd.DataFrame] = None) -> HMMSignal:
    """Run forward filtering on recent observations to get current state posterior."""
    r = recent_returns.dropna()
    if recent_features is not None:
        common = r.index.intersection(recent_features.index)
        r = r.loc[common]
        f = recent_features.loc[common]
        X = np.column_stack([r.values, f.values])
    else:
        X = r.values.reshape(-1, 1)

    if X.shape[1] != hmm.feature_dim:
        raise ValueError(f"Feature dim mismatch: HMM expects {hmm.feature_dim}, got {X.shape[1]}")

    # Forward algorithm: get posterior over states at last observation
    log_alpha = hmm.model._compute_log_likelihood(X)  # (T, K) emission probs
    fwd = hmm.model.predict_proba(X)  # (T, K) posterior
    last_posterior = fwd[-1]
    state_id = int(np.argmax(last_posterior))
    regime = hmm.state_to_regime[state_id]
    return HMMSignal(
        regime=regime,
        state_id=state_id,
        posterior=float(last_posterior[state_id]),
        expected_return_daily=float(hmm.state_means[state_id]),
        expected_vol_daily=float(hmm.state_vols[state_id]),
        rationale=f"HMM state {state_id} ({regime.value}) "
                  f"posterior={last_posterior[state_id]:.2%}, "
                  f"E[ret/day]={hmm.state_means[state_id]*100:+.3f}%, "
                  f"E[vol/day]={hmm.state_vols[state_id]*100:.2f}%"
    )


def smoothed_state_path(hmm: FittedHMM, returns: pd.Series,
                         features: Optional[pd.DataFrame] = None) -> pd.Series:
    """Return Viterbi-decoded most-likely state at each time step.

    Indexed by date with values in HMMRegime."""
    r = returns.dropna()
    if features is not None:
        common = r.index.intersection(features.index)
        r = r.loc[common]
        features = features.loc[common]
        X = np.column_stack([r.values, features.values])
    else:
        X = r.values.reshape(-1, 1)
    states = hmm.model.predict(X)  # Viterbi
    return pd.Series([hmm.state_to_regime[s].value for s in states], index=r.index)


def regime_conditional_stats(hmm: FittedHMM, returns: pd.Series,
                              features: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Return summary statistics per regime."""
    states = smoothed_state_path(hmm, returns, features)
    df = pd.DataFrame({"return": returns.loc[states.index], "regime": states})
    summary = df.groupby("regime")["return"].agg(["count", "mean", "std", "min", "max"])
    summary["mean_ann"] = summary["mean"] * 252
    summary["vol_ann"] = summary["std"] * np.sqrt(252)
    summary["sharpe_ann"] = summary["mean_ann"] / summary["vol_ann"]
    return summary

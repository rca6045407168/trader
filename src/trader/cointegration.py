"""Cointegration-based statistical arbitrage.

Find cointegrated pairs (Engle-Granger 1987) where the spread is
mean-reverting. When spread diverges from its mean (z-score > 2), short
the rich asset, long the cheap one. When spread reverts (z-score < 0.5),
close the trade.

Why cointegration vs simple correlation:
  - Correlated assets can still drift apart over long periods
  - Cointegrated assets have a stable LINEAR relationship (residuals are
    stationary I(0)) — by definition mean-reverting
  - Engle-Granger 2-step: regress Y on X, test residuals for stationarity
    via Augmented Dickey-Fuller (ADF) test

References:
  - Engle, R.F. & Granger, C.W.J. (1987) "Co-integration and Error
    Correction: Representation, Estimation, and Testing"
  - Vidyamurthy, G. (2004) "Pairs Trading: Quantitative Methods and
    Analysis" — definitive practitioner reference
  - Gatev, Goetzmann, Rouwenhorst (2006) "Pairs Trading: Performance of
    a Relative-Value Arbitrage Rule" — classic pairs-trading study,
    documented ~11% annual excess returns 1962-2002 (since arb'd down)

Risk: pairs that USED TO cointegrate may stop cointegrating (regime change,
M&A, bankruptcy). Need rolling cointegration tests + stop-loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller


@dataclass
class CointPair:
    ticker_y: str          # dependent (long when spread cheap)
    ticker_x: str          # independent (short when spread cheap)
    beta: float            # hedge ratio (Y = beta*X + alpha + eps)
    alpha: float
    adf_pvalue: float      # ADF test on residuals; lower = more confident
    spread_mean: float
    spread_std: float
    correlation: float


def find_cointegrated_pair(prices_y: pd.Series, prices_x: pd.Series,
                            adf_threshold: float = 0.05) -> Optional[CointPair]:
    """Engle-Granger 2-step test for cointegration.

    Step 1: OLS regression Y_t = beta * X_t + alpha + e_t
    Step 2: ADF test on residuals e_t. If p-value < threshold, cointegrated.

    Returns CointPair if cointegrated, None otherwise.
    """
    common = prices_y.index.intersection(prices_x.index)
    if len(common) < 60:
        return None
    y = prices_y.loc[common].dropna()
    x = prices_x.loc[common].dropna()
    common = y.index.intersection(x.index)
    if len(common) < 60:
        return None
    y = y.loc[common]
    x = x.loc[common]
    if y.std() == 0 or x.std() == 0:
        return None
    try:
        X = add_constant(x.values)
        model = OLS(y.values, X).fit()
        alpha, beta = float(model.params[0]), float(model.params[1])
        residuals = y.values - (alpha + beta * x.values)
        adf = adfuller(residuals, autolag="AIC", maxlag=10)
        adf_pvalue = float(adf[1])
    except Exception:
        return None
    if adf_pvalue > adf_threshold:
        return None
    spread_mean = float(np.mean(residuals))
    spread_std = float(np.std(residuals))
    if spread_std == 0:
        return None
    correlation = float(y.corr(x))
    return CointPair(
        ticker_y=prices_y.name,
        ticker_x=prices_x.name,
        beta=beta,
        alpha=alpha,
        adf_pvalue=adf_pvalue,
        spread_mean=spread_mean,
        spread_std=spread_std,
        correlation=correlation,
    )


def find_cointegrated_pairs(prices: pd.DataFrame,
                             adf_threshold: float = 0.05,
                             min_correlation: float = 0.5,
                             max_pairs: int = 50) -> list[CointPair]:
    """Scan all pairwise combinations in `prices` for cointegration.

    Pre-filters by minimum Pearson correlation to avoid testing uncorrelated
    pairs (saves O(N^2) ADF tests).

    Returns list of CointPair sorted by ADF p-value (most cointegrated first).
    """
    tickers = [c for c in prices.columns if prices[c].dropna().shape[0] >= 60]
    if len(tickers) < 2:
        return []
    pairs = []
    # Pre-compute correlation matrix
    corr = prices[tickers].pct_change().dropna().corr()
    for i, t_y in enumerate(tickers):
        for t_x in tickers[i + 1:]:
            if t_y == t_x:
                continue
            try:
                rho = float(corr.loc[t_y, t_x])
            except Exception:
                continue
            if abs(rho) < min_correlation:
                continue
            pair = find_cointegrated_pair(prices[t_y], prices[t_x], adf_threshold)
            if pair:
                pairs.append(pair)
                if len(pairs) >= max_pairs * 2:
                    break  # don't keep scanning if we found enough
        if len(pairs) >= max_pairs * 2:
            break
    pairs.sort(key=lambda p: p.adf_pvalue)
    return pairs[:max_pairs]


def current_spread_z_score(pair: CointPair, latest_y: float,
                            latest_x: float) -> float:
    """Z-score of current spread vs historical mean. Used to time entry/exit:
    z > +2: spread is rich (Y too high vs X) → short Y, long beta*X
    z < -2: spread is cheap (Y too low vs X) → long Y, short beta*X
    |z| < 0.5: close any position
    """
    spread = latest_y - (pair.alpha + pair.beta * latest_x)
    return (spread - pair.spread_mean) / pair.spread_std

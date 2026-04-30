"""Hierarchical Risk Parity (Lopez de Prado 2016).

HRP is a portfolio construction technique that:
  1. Builds a hierarchical clustering tree from the asset correlation matrix
  2. Quasi-diagonalizes the covariance matrix via the cluster ordering
  3. Recursively bisects the tree, allocating inverse-vol to each subset

Why it beats Markowitz mean-variance optimization (MVO):
  - MVO requires inverting Σ (covariance). For ~50 names with limited history,
    Σ is near-singular and the inverse is unstable. Tiny return-estimate
    perturbations cause violent weight shifts.
  - HRP requires NO inversion. It exploits the hierarchical structure of
    correlations to allocate stably.
  - Empirically (Lopez de Prado 2016), HRP delivers similar in-sample
    Sharpe to MVO but ~30% lower turnover and far better OOS robustness.

Reference: Lopez de Prado, M. (2016) "Building Diversified Portfolios that
Outperform Out-of-Sample" Journal of Portfolio Management, 42(4): 59-69.

Algorithm:
  1. correlation matrix C → distance D = sqrt(0.5(1-C))
  2. linkage Z = scipy.cluster.linkage(D, "single") on the distance matrix
  3. quasi-diagonalize: get the ordered leaves
  4. recursive bisection: at each split, allocate inverse to relative cluster vol
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform


def correlation_to_distance(corr: pd.DataFrame) -> pd.DataFrame:
    """Lopez de Prado distance: D = sqrt(0.5(1-corr))."""
    return ((1 - corr) / 2.0) ** 0.5


def get_quasi_diagonal_order(link: np.ndarray) -> list[int]:
    """Recover the leaf ordering from a scipy linkage matrix that puts
    similar items next to each other."""
    link = link.astype(int)
    sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
    num_items = link[-1, 3]
    while sort_ix.max() >= num_items:
        sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
        df0 = sort_ix[sort_ix >= num_items]
        i = df0.index
        j = df0.values - num_items
        sort_ix[i] = link[j, 0]
        df0 = pd.Series(link[j, 1], index=i + 1)
        sort_ix = pd.concat([sort_ix, df0]).sort_index()
        sort_ix.index = range(sort_ix.shape[0])
    return sort_ix.tolist()


def cluster_variance(cov: pd.DataFrame, items: list) -> float:
    """Inverse-variance weighted portfolio variance for items in cluster."""
    cov_ = cov.loc[items, items]
    ivp = 1.0 / np.diag(cov_)
    ivp /= ivp.sum()
    return float(ivp @ cov_ @ ivp)


def recursive_bisection(cov: pd.DataFrame, sort_ix: list) -> pd.Series:
    """Recursively bisect the sorted index, allocating inversely to cluster vol."""
    w = pd.Series(1.0, index=sort_ix)
    cluster_items = [sort_ix]
    while cluster_items:
        cluster_items = [
            c[start:stop]
            for c in cluster_items
            for start, stop in (
                (0, len(c) // 2),
                (len(c) // 2, len(c)),
            )
            if len(c) > 1
        ]
        for i in range(0, len(cluster_items), 2):
            c1 = cluster_items[i]
            c2 = cluster_items[i + 1]
            v1 = cluster_variance(cov, c1)
            v2 = cluster_variance(cov, c2)
            alpha = 1 - v1 / (v1 + v2)
            w[c1] *= alpha
            w[c2] *= 1 - alpha
    return w


def hrp_weights(returns: pd.DataFrame) -> pd.Series:
    """Compute HRP weights from a returns DataFrame.

    Args:
        returns: T x N DataFrame of asset returns (rows = time, cols = tickers)

    Returns:
        Series of weights indexed by ticker, summing to 1.0.
    """
    if returns.empty:
        return pd.Series(dtype=float)
    if returns.shape[1] < 2:
        # Single asset — can't cluster
        return pd.Series([1.0], index=returns.columns)
    cov = returns.cov()
    corr_vals = np.array(returns.corr().fillna(0).values, dtype=float)  # writable copy
    np.fill_diagonal(corr_vals, 1.0)
    corr = pd.DataFrame(corr_vals, index=returns.columns, columns=returns.columns)
    dist = correlation_to_distance(corr)
    # squareform expects a 1D condensed distance vector
    try:
        condensed = squareform(np.array(dist.values, dtype=float), checks=False)
    except Exception:
        # Fall back to equal weights
        n = returns.shape[1]
        return pd.Series([1.0 / n] * n, index=returns.columns)
    link = linkage(condensed, method="single")
    sort_ix = get_quasi_diagonal_order(link)
    sorted_tickers = [returns.columns[i] for i in sort_ix]
    cov_sorted = cov.loc[sorted_tickers, sorted_tickers]
    weights = recursive_bisection(cov_sorted, sorted_tickers)
    # Normalize and reindex to original order
    weights = weights / weights.sum()
    return weights.reindex(returns.columns).fillna(0)


def hrp_portfolio_for_picks(prices: pd.DataFrame, picks: list[str],
                             lookback_days: int = 90,
                             gross_leverage: float = 0.80) -> dict[str, float]:
    """Build HRP portfolio from a set of momentum picks.

    Args:
        prices: T x M DataFrame of prices (M can include picks + others)
        picks: subset of tickers to include
        lookback_days: window for covariance estimation
        gross_leverage: total portfolio gross (e.g., 0.80 = 80%)
    """
    available = [t for t in picks if t in prices.columns]
    if len(available) < 2:
        if available:
            return {available[0]: gross_leverage}
        return {}
    sub = prices[available].iloc[-lookback_days:].dropna()
    rets = sub.pct_change().dropna()
    if len(rets) < 30:
        # Insufficient data — equal weight
        n = len(available)
        return {t: gross_leverage / n for t in available}
    weights = hrp_weights(rets)
    return {t: float(weights[t]) * gross_leverage for t in available}

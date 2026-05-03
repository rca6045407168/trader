"""[v3.59.3 — TESTING_PRACTICES Cat 3] Block-bootstrap confidence intervals.

A point Sharpe of 1.5 means nothing without uncertainty. This module
computes 95% confidence intervals via stationary block bootstrap, the
right primitive for serial-correlated daily returns.

Usage:
    from trader.bootstrap_ci import block_bootstrap_sharpe_ci
    lo, hi = block_bootstrap_sharpe_ci(daily_returns, B=1000, block=21)

Algorithm: Politis-Romano (1994) stationary bootstrap.
  1. Sample blocks of (geometric-distributed) length L̂ ~ Geom(1/block)
  2. Concatenate sampled blocks until we have N observations
  3. Compute statistic on the resampled series
  4. Repeat B times to get a distribution
  5. CI = empirical 2.5th and 97.5th percentile

Pure Python; no numpy/scipy import overhead.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass
class BootstrapCI:
    """One bootstrap result with full distribution stats."""
    point_estimate: float
    ci_low: float       # 2.5th percentile
    ci_high: float      # 97.5th percentile
    se: float           # standard error (stdev of bootstrap distribution)
    n_resamples: int    # B
    block_length: int


def _stationary_block_indices(n: int, block: int, rng: random.Random) -> list[int]:
    """Generate n indices via stationary block bootstrap.
    block is the EXPECTED block length (geometric mean)."""
    p = 1.0 / max(block, 1)
    out = [rng.randrange(n)]
    for _ in range(n - 1):
        if rng.random() < p:
            # Start a new block
            out.append(rng.randrange(n))
        else:
            # Continue current block
            out.append((out[-1] + 1) % n)
    return out


def _sharpe(rets: Sequence[float], periods_per_year: int = 252) -> float:
    if len(rets) < 2:
        return 0.0
    mean = statistics.mean(rets)
    sd = statistics.stdev(rets)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(periods_per_year)


def _max_drawdown(rets: Sequence[float]) -> float:
    cum, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        cum *= (1 + r)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum / peak - 1)
    return max_dd


def block_bootstrap(rets: Sequence[float], statistic_fn,
                     B: int = 1000, block: int = 21,
                     seed: int = 42) -> BootstrapCI:
    """Generic block-bootstrap engine. statistic_fn(seq) → float.
    Returns a BootstrapCI."""
    if len(rets) < 30:
        # Sample too small for stable bootstrap
        return BootstrapCI(
            point_estimate=statistic_fn(rets),
            ci_low=float("nan"), ci_high=float("nan"), se=float("nan"),
            n_resamples=0, block_length=block,
        )

    rng = random.Random(seed)
    n = len(rets)
    estimates: list[float] = []
    point = statistic_fn(rets)

    for _ in range(B):
        idx = _stationary_block_indices(n, block, rng)
        sample = [rets[i] for i in idx]
        try:
            estimates.append(statistic_fn(sample))
        except Exception:
            continue

    estimates.sort()
    if not estimates:
        return BootstrapCI(point, float("nan"), float("nan"), float("nan"),
                            0, block)

    n_est = len(estimates)
    lo_idx = max(int(n_est * 0.025), 0)
    hi_idx = min(int(n_est * 0.975), n_est - 1)
    se = statistics.stdev(estimates) if len(estimates) > 1 else 0.0
    return BootstrapCI(
        point_estimate=point,
        ci_low=estimates[lo_idx], ci_high=estimates[hi_idx],
        se=se, n_resamples=n_est, block_length=block,
    )


def block_bootstrap_sharpe_ci(rets: Sequence[float], B: int = 1000,
                                block: int = 21, periods_per_year: int = 252,
                                seed: int = 42) -> BootstrapCI:
    """Block-bootstrap CI for annualized Sharpe ratio."""
    return block_bootstrap(rets,
                            lambda s: _sharpe(s, periods_per_year),
                            B=B, block=block, seed=seed)


def block_bootstrap_max_dd_ci(rets: Sequence[float], B: int = 1000,
                                block: int = 21, seed: int = 42) -> BootstrapCI:
    """Block-bootstrap CI for max drawdown (negative number)."""
    return block_bootstrap(rets, _max_drawdown,
                            B=B, block=block, seed=seed)


def block_bootstrap_total_return_ci(rets: Sequence[float], B: int = 1000,
                                      block: int = 21, seed: int = 42) -> BootstrapCI:
    """Block-bootstrap CI for total compounded return."""
    def _tr(s):
        cum = 1.0
        for r in s:
            cum *= (1 + r)
        return cum - 1
    return block_bootstrap(rets, _tr, B=B, block=block, seed=seed)


def is_significant(ci: BootstrapCI, threshold: float = 0.0) -> bool:
    """Is the lower bound of the CI strictly above the threshold?
    Conservative test for "edge is real, not noise." """
    if math.isnan(ci.ci_low):
        return False
    return ci.ci_low > threshold

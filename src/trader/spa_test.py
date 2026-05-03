"""[v3.59.5 — TESTING_PRACTICES Cat 3] White's Reality Check + Hansen's SPA.

When you've tested N variants and report the best one, the best's
Sharpe is biased upward by the maximum order statistic. White's Reality
Check (2000) and Hansen's Superior Predictive Ability test (2005) are
the canonical multiple-testing-correction frameworks.

This module implements stationary-block-bootstrap versions of both,
following Hansen-Lunde-Nason (2011) "The Model Confidence Set" methodology.

  • whites_reality_check(losses_matrix, B=1000) → p-value
        H0: best variant is no better than benchmark
        Output p < 0.05 → best variant truly outperforms

  • hansens_spa(losses_matrix, B=1000) → p-value
        Same H0 but with Hansen's recentering correction (less
        conservative; doesn't lose power to obviously-bad variants)

Inputs: a (n_periods, n_variants) matrix where each cell is the LOSS
of variant j at period t, relative to the benchmark. Negative loss =
variant beat benchmark in that period.

Pure Python; no scipy. Uses our existing block-bootstrap primitive.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Optional


@dataclass
class SpaResult:
    p_value: float                  # H0: no variant beats benchmark
    best_variant_idx: int            # index of variant with min mean loss
    best_mean_loss: float            # mean loss of best variant
    n_variants: int
    n_periods: int
    test_statistic: float
    n_bootstraps: int


def _stationary_block_indices(n: int, block: int, rng: random.Random) -> list[int]:
    p = 1.0 / max(block, 1)
    out = [rng.randrange(n)]
    for _ in range(n - 1):
        if rng.random() < p:
            out.append(rng.randrange(n))
        else:
            out.append((out[-1] + 1) % n)
    return out


def _column_means(matrix: list[list[float]]) -> list[float]:
    """matrix is rows×cols. Returns col means."""
    if not matrix:
        return []
    n_rows = len(matrix)
    n_cols = len(matrix[0])
    sums = [0.0] * n_cols
    for row in matrix:
        for j in range(n_cols):
            sums[j] += row[j]
    return [s / n_rows for s in sums]


def whites_reality_check(losses: list[list[float]],
                          B: int = 1000, block: int = 21,
                          seed: int = 42) -> SpaResult:
    """White's Reality Check (2000). H0: best variant ≤ benchmark.

    losses: list of T period rows, each containing K variant losses
    relative to benchmark. Negative = variant outperformed in that period.

    Returns p-value of H0. p < 0.05 = best variant is REAL outperformance,
    not lucky-best-of-K.
    """
    if not losses or not losses[0]:
        return SpaResult(p_value=1.0, best_variant_idx=-1,
                          best_mean_loss=0, n_variants=0,
                          n_periods=0, test_statistic=0, n_bootstraps=0)

    T = len(losses)
    K = len(losses[0])
    means = _column_means(losses)
    best_idx = min(range(K), key=lambda j: means[j])
    best_mean = means[best_idx]

    # Test statistic: -sqrt(T) * min(mean_loss). Negative because we
    # want best (lowest) loss; multiplied by -1 to make outperformance
    # produce a POSITIVE statistic.
    test_stat = -math.sqrt(T) * best_mean

    # Bootstrap: re-sample with replacement (block bootstrap), recompute
    # the test statistic on each resample, count how often resample stat
    # ≥ observed stat. Center each resample by subtracting the original
    # column mean (White's recentering).
    rng = random.Random(seed)
    n_exceed = 0
    for _ in range(B):
        idx = _stationary_block_indices(T, block, rng)
        # Resampled losses, column-wise
        sampled_means = [0.0] * K
        for i in idx:
            for j in range(K):
                sampled_means[j] += losses[i][j]
        sampled_means = [s / T for s in sampled_means]
        # Centered (subtract original mean to simulate H0)
        centered = [sampled_means[j] - means[j] for j in range(K)]
        bs_stat = -math.sqrt(T) * min(centered)
        if bs_stat >= test_stat:
            n_exceed += 1

    p_value = n_exceed / B
    return SpaResult(
        p_value=p_value,
        best_variant_idx=best_idx, best_mean_loss=best_mean,
        n_variants=K, n_periods=T,
        test_statistic=test_stat, n_bootstraps=B,
    )


def hansens_spa(losses: list[list[float]],
                 B: int = 1000, block: int = 21,
                 seed: int = 42) -> SpaResult:
    """Hansen's Superior Predictive Ability test (2005).

    Differs from White's RC in that it RECENTERS the bootstrap
    distribution to remove obviously-bad variants from the comparison.
    Less conservative; keeps power even when the cohort contains
    several losers.

    Same return shape as whites_reality_check.
    """
    if not losses or not losses[0]:
        return SpaResult(p_value=1.0, best_variant_idx=-1,
                          best_mean_loss=0, n_variants=0,
                          n_periods=0, test_statistic=0, n_bootstraps=0)

    T = len(losses)
    K = len(losses[0])
    means = _column_means(losses)
    best_idx = min(range(K), key=lambda j: means[j])
    best_mean = means[best_idx]
    test_stat = -math.sqrt(T) * best_mean

    # Hansen's centering: only re-center variants that are NOT
    # obviously inferior. Threshold: mean_loss > sqrt(2*log(log(T)) * var)
    # We use a simpler version: variants whose mean_loss is more than
    # one standard error above zero are dropped from the centering.
    # For each variant compute std-err of the column.
    col_se = []
    for j in range(K):
        col_vals = [losses[t][j] for t in range(T)]
        sd = statistics.stdev(col_vals) if T > 1 else 0
        col_se.append(sd / math.sqrt(T) if T > 0 else 0)

    rng = random.Random(seed)
    n_exceed = 0
    for _ in range(B):
        idx = _stationary_block_indices(T, block, rng)
        sampled_means = [0.0] * K
        for i in idx:
            for j in range(K):
                sampled_means[j] += losses[i][j]
        sampled_means = [s / T for s in sampled_means]
        # Hansen recentering: subtract original mean only for variants
        # that pass the inferiority screen
        centered = []
        for j in range(K):
            if means[j] - 2 * col_se[j] > 0:
                # obviously inferior — exclude (set to a large value)
                centered.append(float("inf"))
            else:
                centered.append(sampled_means[j] - means[j])
        if all(math.isinf(c) for c in centered):
            continue
        bs_stat = -math.sqrt(T) * min(c for c in centered if not math.isinf(c))
        if bs_stat >= test_stat:
            n_exceed += 1

    p_value = n_exceed / B if B > 0 else 1.0
    return SpaResult(
        p_value=p_value,
        best_variant_idx=best_idx, best_mean_loss=best_mean,
        n_variants=K, n_periods=T,
        test_statistic=test_stat, n_bootstraps=B,
    )


def variants_to_loss_matrix(variant_returns: dict[str, list[float]],
                              benchmark_returns: list[float]) -> tuple[list[list[float]], list[str]]:
    """Convert {variant_name: [daily_returns]} + benchmark → (loss_matrix, names).

    Loss = -variant_return + benchmark_return (so negative loss = variant won).
    Lengths must align."""
    names = sorted(variant_returns.keys())
    if not names:
        return [], []
    T = len(benchmark_returns)
    losses = []
    for t in range(T):
        row = []
        for name in names:
            v = variant_returns[name]
            if t >= len(v):
                row.append(0.0)
            else:
                row.append(benchmark_returns[t] - v[t])
        losses.append(row)
    return losses, names

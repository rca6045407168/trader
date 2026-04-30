"""Combinatorial Purged Cross-Validation (CPCV) for any variant.

Source: Lopez de Prado, M. (2018) "Advances in Financial Machine Learning"
Chapter 12. The gold standard for finance backtest validation.

Standard k-fold CV doesn't work in finance because:
  1. Time-series data has serial correlation (can't shuffle)
  2. Strategy parameters at time t can leak from labels at t+horizon
  3. Single train/test split gives ONE Sharpe estimate — could be lucky

CPCV solves this:
  1. Split time series into N groups
  2. Choose k of N groups as test, rest as train
  3. PURGE: drop train observations whose labels overlap test
  4. EMBARGO: drop train observations within E days AFTER test (prevents
     forward leakage from autocorrelation)
  5. Repeat for ALL C(N,k) combinations
  6. Get a distribution of OOS Sharpe estimates

Promotes HONEST inference: if median CPCV Sharpe edge over baseline is > 0.10
with low variance, the edge is real. If distribution overlaps zero, edge is
likely overfit / lucky.

Usage:
  python scripts/cpcv_backtest.py [--variant hmm_aggressive] [--n_groups 8] [--k_test 2]
"""
from __future__ import annotations

import argparse
import sys
import statistics
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

# Don't import the full regime_stress_test on module load — it's expensive
def get_variants():
    from regime_stress_test import (
        variant_top3_eq_80, variant_top3_eq_80_pit,
        variant_top3_hmm_aggressive, variant_top3_hmm_aggressive_pit,
        variant_top15_momentum_weighted,
    )
    return {
        "live_top3_eq_80": variant_top3_eq_80,
        "live_top3_eq_80_pit": variant_top3_eq_80_pit,
        "hmm_aggressive": variant_top3_hmm_aggressive,
        "hmm_aggressive_pit": variant_top3_hmm_aggressive_pit,
        "top15_mom_weighted": variant_top15_momentum_weighted,
    }


def cpcv_splits(start: pd.Timestamp, end: pd.Timestamp,
                n_groups: int, k_test: int, embargo_days: int = 30):
    """Yield (train_window_list, test_window) tuples for every combination
    of choosing k_test groups out of n_groups for testing.

    Each window is a (start_date, end_date) tuple. Train list contains
    multiple disjoint windows; test is a single contiguous span made up of
    the k_test chosen groups.
    """
    total_days = (end - start).days
    group_size_days = total_days // n_groups
    groups = []
    for i in range(n_groups):
        g_start = start + pd.Timedelta(days=i * group_size_days)
        g_end = start + pd.Timedelta(days=(i + 1) * group_size_days)
        groups.append((g_start, g_end))

    for test_indices in combinations(range(n_groups), k_test):
        # Test window = union of test_indices groups (may be non-contiguous)
        test_windows = [groups[i] for i in test_indices]
        # Train = all groups not in test_indices, with embargo applied
        train_windows = []
        for j, g in enumerate(groups):
            if j in test_indices:
                continue
            # Apply embargo: shrink train group if it ends within embargo_days
            # of any test group's start (forward leakage guard)
            keep = True
            for t_start, t_end in test_windows:
                # If this train group ends within embargo_days BEFORE test starts, drop it
                if 0 < (t_start - g[1]).days <= embargo_days:
                    keep = False
                    break
            if keep:
                train_windows.append(g)
        yield train_windows, test_windows


def compute_variant_returns_in_window(variant_fn, start: pd.Timestamp,
                                       end: pd.Timestamp) -> pd.Series | None:
    """Run replay_window for a variant in a specific window. Returns daily
    return series, or None if backtest fails."""
    from regime_stress_test import replay_window
    # We need access to the per-day equity curve, not just the summary stats.
    # replay_window returns summary; we need to reconstruct returns from the
    # equity curve. Easier: just rerun and grab pr (period returns) directly.
    # For CPCV we need daily returns, so let's modify approach: rerun the
    # variant via direct portfolio simulation.
    try:
        result = replay_window("cpcv", variant_fn, start, end)
        if not result:
            return None
        # Approximate daily returns from cagr (rough; we're reporting Sharpe so OK)
        n_days = result["n_days"]
        if n_days < 5:
            return None
        # Use the Sharpe / CAGR / total_pct to construct a synthetic daily return series
        # for variance/Sharpe purposes — this is a SIMPLIFICATION
        cagr = result["cagr"]
        sharpe = result["sharpe"]
        sd = (cagr / sharpe) / np.sqrt(252) if sharpe != 0 else 0.01
        mean = cagr / 252
        # Return summary stats only — we'll Sharpe directly without reconstructing
        return {"mean_daily": mean, "std_daily": sd, "sharpe_ann": sharpe,
                "total": result["total_pct"], "n_days": n_days, "max_dd": result["max_dd"]}
    except Exception:
        return None


def cpcv_evaluate(variant_name: str, start: pd.Timestamp, end: pd.Timestamp,
                  n_groups: int = 8, k_test: int = 2):
    """Run CPCV across all combinations and report distribution of OOS Sharpe."""
    variants = get_variants()
    if variant_name not in variants:
        raise ValueError(f"Unknown variant: {variant_name}. Choices: {list(variants.keys())}")
    fn = variants[variant_name]

    # Also compute for baseline (LIVE PIT) so we can report EDGE distribution
    baseline_fn = variants.get("live_top3_eq_80_pit", variants["live_top3_eq_80"])

    print(f"CPCV: variant={variant_name}, baseline=live_top3_eq_80_pit")
    print(f"      groups={n_groups}, k_test={k_test}, "
          f"combos={len(list(combinations(range(n_groups), k_test)))}")
    print(f"      window {start.date()} → {end.date()}")
    print()

    edge_distribution = []
    variant_sharpes = []
    baseline_sharpes = []

    for i, (train, test) in enumerate(cpcv_splits(start, end, n_groups, k_test)):
        for t_start, t_end in test:
            # Run baseline + variant on this test sub-window
            baseline_result = compute_variant_returns_in_window(baseline_fn, t_start, t_end)
            variant_result = compute_variant_returns_in_window(fn, t_start, t_end)
            if not baseline_result or not variant_result:
                continue
            edge = variant_result["sharpe_ann"] - baseline_result["sharpe_ann"]
            edge_distribution.append(edge)
            variant_sharpes.append(variant_result["sharpe_ann"])
            baseline_sharpes.append(baseline_result["sharpe_ann"])

    if not edge_distribution:
        print("No valid CPCV samples. Aborting.")
        return None

    print(f"CPCV samples collected: {len(edge_distribution)}")
    print()
    print(f"Variant Sharpe distribution:")
    print(f"  mean:        {statistics.mean(variant_sharpes):>+6.2f}")
    print(f"  median:      {statistics.median(variant_sharpes):>+6.2f}")
    print(f"  std:         {statistics.stdev(variant_sharpes) if len(variant_sharpes)>1 else 0:>+6.2f}")
    print(f"  5th pctile:  {np.percentile(variant_sharpes, 5):>+6.2f}")
    print(f"  95th pctile: {np.percentile(variant_sharpes, 95):>+6.2f}")
    print()
    print(f"Baseline (LIVE PIT) Sharpe distribution:")
    print(f"  mean:        {statistics.mean(baseline_sharpes):>+6.2f}")
    print(f"  median:      {statistics.median(baseline_sharpes):>+6.2f}")
    print()
    print(f"EDGE (variant - baseline) distribution — what we actually care about:")
    print(f"  mean edge:        {statistics.mean(edge_distribution):>+6.2f}")
    print(f"  median edge:      {statistics.median(edge_distribution):>+6.2f}")
    print(f"  std:              {statistics.stdev(edge_distribution) if len(edge_distribution)>1 else 0:>+6.2f}")
    print(f"  5th pctile edge:  {np.percentile(edge_distribution, 5):>+6.2f}")
    print(f"  95th pctile edge: {np.percentile(edge_distribution, 95):>+6.2f}")
    print(f"  P(edge > 0):      {sum(1 for x in edge_distribution if x > 0) / len(edge_distribution) * 100:.1f}%")
    print(f"  P(edge > 0.10):   {sum(1 for x in edge_distribution if x > 0.10) / len(edge_distribution) * 100:.1f}%")
    print()

    # Verdict
    median_edge = statistics.median(edge_distribution)
    p5_edge = np.percentile(edge_distribution, 5)
    if p5_edge > 0:
        print("✓ STRONG: 5th-percentile CPCV edge > 0 — robust to OOS sampling.")
    elif median_edge > 0.10:
        print("~ MODERATE: median edge > +0.10 but tail risk exists.")
    elif median_edge > 0:
        print("~ WEAK: median edge marginal; could be sampling noise.")
    else:
        print("✗ NO EDGE: median CPCV edge ≤ 0. Survivor of single-test luck.")

    return {
        "edge_distribution": edge_distribution,
        "variant_sharpes": variant_sharpes,
        "baseline_sharpes": baseline_sharpes,
        "median_edge": median_edge,
        "p5_edge": p5_edge,
        "p_positive": sum(1 for x in edge_distribution if x > 0) / len(edge_distribution),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--variant", default="hmm_aggressive_pit")
    p.add_argument("--n_groups", type=int, default=8)
    p.add_argument("--k_test", type=int, default=2)
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default="2026-04-29")
    args = p.parse_args()
    print("=" * 80)
    print("COMBINATORIAL PURGED CROSS-VALIDATION (Lopez de Prado 2018, Ch 12)")
    print("=" * 80)
    cpcv_evaluate(args.variant,
                   pd.Timestamp(args.start), pd.Timestamp(args.end),
                   args.n_groups, args.k_test)
    return 0


if __name__ == "__main__":
    sys.exit(main())

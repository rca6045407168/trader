"""Bootstrap confidence intervals on Sharpe ratio.

Our regime stress test reports Mean Sharpe across 5 windows. But 5 is a small
sample. A reported +1.54 Sharpe could be a lucky draw from a true distribution
centered on 0.5. This script answers: what's the 95% CI on the LIVE strategy's
Sharpe?

Method (per Lo 2002, Bailey-Lopez de Prado 2012):
  1. Concatenate all daily returns from the 5 regime windows
  2. Bootstrap N samples (default 1000), each by drawing daily returns with
     replacement, sample size = original
  3. Compute Sharpe of each bootstrap sample
  4. Report 5th, 50th, 95th percentile

If the 95% CI lower bound is ≥ 0.3, we have plausible edge.
If it's ≤ 0, the strategy's edge is statistically suspect.

Caveat: standard bootstrap assumes IID returns. Equity returns have weak
autocorrelation (~5% at lag 1) which inflates Sharpe estimates. Use the
moving-block bootstrap (block_size=20) to preserve some autocorrelation
structure. Reported result is conservative-ish vs IID.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50

# Same regime windows as regime_stress_test.py
REGIMES = [
    ("2018-Q4 selloff",   pd.Timestamp("2018-09-01"), pd.Timestamp("2019-03-31")),
    ("2020-Q1 COVID",     pd.Timestamp("2020-01-15"), pd.Timestamp("2020-06-30")),
    ("2022 bear",         pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    ("2023 AI-rally",     pd.Timestamp("2023-04-01"), pd.Timestamp("2023-10-31")),
    ("recent 3 months",   pd.Timestamp.today() - pd.Timedelta(days=95), pd.Timestamp.today()),
]


def _momentum_picks_as_of(as_of, top_n=3, lookback_months=12, skip_months=1):
    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return []
    if prices.empty or len(prices) < L + S:
        return []
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    return rets.nlargest(top_n).index.tolist()


def _live_returns_for_window(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Return the LIVE strategy's daily returns over the window."""
    bdays = pd.bdate_range(start, end)
    decisions = []
    for d in bdays:
        next_d = d + pd.Timedelta(days=1)
        if next_d.month != d.month:
            picks = _momentum_picks_as_of(d, 3)
            if picks:
                decisions.append((d, {p: 0.80 / 3 for p in picks}))
    if not decisions:
        return pd.Series(dtype=float)

    all_t = set()
    for _, t in decisions:
        all_t.update(t.keys())
    try:
        prices = fetch_history(sorted(all_t),
                               start=(start - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                               end=(end + pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    except Exception:
        return pd.Series(dtype=float)
    daily_rets = prices.pct_change().fillna(0)
    daily_idx = prices.index
    weights = pd.DataFrame(0.0, index=daily_idx, columns=prices.columns)
    for i, (d, t) in enumerate(decisions):
        ei = daily_idx.searchsorted(d) + 1
        if ei >= len(daily_idx):
            continue
        if i + 1 < len(decisions):
            xi = min(daily_idx.searchsorted(decisions[i + 1][0]) + 1, len(daily_idx))
        else:
            xi = len(daily_idx)
        for sym, w in t.items():
            if sym in weights.columns:
                weights.iloc[ei:xi, weights.columns.get_loc(sym)] = w
    pr = (weights.shift(1) * daily_rets).sum(axis=1).fillna(0)
    pr = pr[pr.index >= start]
    return pr


def annualized_sharpe(returns: np.ndarray) -> float:
    if len(returns) < 5:
        return 0.0
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std <= 0:
        return 0.0
    return (mean * 252) / (std * math.sqrt(252))


def moving_block_bootstrap(returns: np.ndarray, block_size: int = 20,
                            n_iter: int = 1000) -> list[float]:
    """Sample blocks of `block_size` consecutive returns, with replacement,
    until we have n_obs returns. Compute Sharpe on each sample."""
    n = len(returns)
    if n < block_size * 2:
        block_size = max(2, n // 4)
    sharpes: list[float] = []
    n_blocks = math.ceil(n / block_size)
    for _ in range(n_iter):
        sample = []
        for _ in range(n_blocks):
            start = random.randint(0, n - block_size)
            sample.extend(returns[start:start + block_size])
        sample = np.array(sample[:n])
        sharpes.append(annualized_sharpe(sample))
    return sharpes


def main():
    print("=" * 80)
    print("BOOTSTRAP SHARPE CONFIDENCE INTERVAL — LIVE strategy (top-3 at 80%)")
    print("=" * 80)
    print("Method: moving-block bootstrap (block_size=20 days, n_iter=1000)")
    print()

    # Concatenate daily returns across all 5 regime windows
    all_returns = []
    for name, start, end in REGIMES:
        pr = _live_returns_for_window(start, end)
        if len(pr) > 0:
            all_returns.append(pr.values)
            sample_sharpe = annualized_sharpe(pr.values)
            print(f"  {name:25s}  n={len(pr):>4}  Sharpe={sample_sharpe:>+5.2f}")
    if not all_returns:
        print("No data — aborting.")
        return

    full_returns = np.concatenate(all_returns)
    n = len(full_returns)
    point_sharpe = annualized_sharpe(full_returns)
    print(f"\n  POOLED:                   n={n:>4}  Sharpe={point_sharpe:>+5.2f} (point estimate)")

    print("\nBootstrapping...")
    random.seed(42)
    sharpes = moving_block_bootstrap(full_returns, block_size=20, n_iter=1000)
    sharpes_arr = np.array(sharpes)

    p2_5 = float(np.percentile(sharpes_arr, 2.5))
    p5 = float(np.percentile(sharpes_arr, 5))
    p50 = float(np.percentile(sharpes_arr, 50))
    p95 = float(np.percentile(sharpes_arr, 95))
    p97_5 = float(np.percentile(sharpes_arr, 97.5))
    mean_boot = float(np.mean(sharpes_arr))
    std_boot = float(np.std(sharpes_arr))
    pct_above_zero = float(np.mean(sharpes_arr > 0))
    pct_above_05 = float(np.mean(sharpes_arr > 0.5))

    print()
    print(f"  Bootstrap mean Sharpe:        {mean_boot:>+5.2f}")
    print(f"  Bootstrap std:                {std_boot:>+5.2f}")
    print(f"  90% CI:                       [{p5:>+5.2f}, {p95:>+5.2f}]")
    print(f"  95% CI:                       [{p2_5:>+5.2f}, {p97_5:>+5.2f}]")
    print(f"  P(Sharpe > 0):                {pct_above_zero*100:>5.1f}%")
    print(f"  P(Sharpe > 0.5):              {pct_above_05*100:>5.1f}%")

    print()
    print("INTERPRETATION:")
    if p2_5 > 0.5:
        print("  ✓ STRONG: 95% CI lower bound > 0.5. Edge is statistically robust.")
    elif p2_5 > 0:
        print("  ~ PLAUSIBLE: 95% CI lower bound > 0 but < 0.5. Edge is real but small.")
    else:
        print("  ✗ WEAK: 95% CI includes 0. Edge is not statistically distinguishable from luck.")

    if pct_above_zero > 0.95:
        print(f"  ≥{pct_above_zero*100:.0f}% bootstrap probability the strategy has positive edge.")

    print()
    print("CAVEATS:")
    print("  - Bootstrap assumes the historical return distribution is representative")
    print("    of the future. Regime changes (e.g., end of momentum era) violate this.")
    print("  - Block bootstrap preserves short-term autocorrelation but not long-term")
    print("    regime structure (e.g., year-long bear markets get fragmented).")
    print("  - This uses the SURVIVOR universe (DEFAULT_LIQUID_50). The PIT version")
    print("    (v3.8) showed +0.96 OOS-honest Sharpe. Re-run on PIT for honest numbers.")


if __name__ == "__main__":
    main()

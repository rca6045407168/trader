"""Honest variance estimation on the +5-9 %/yr uplift forecast.

The RUNBOOK_MAX_RETURN.md headline number is a sum of independent
point estimates. That math is wrong — the component edges are NOT
independent. TLH harvest only fires when prices fall; quality factor
underperforms in junk-rallies (early-cycle bull markets) when TLH is
ALSO dormant; insider buying clusters in regime shifts; PEAD drift
varies with attention cycles.

This module estimates the JOINT DISTRIBUTION of total uplift across
plausible regime paths via a simple Monte Carlo. Component edges are
modeled with their own mean/std plus pairwise correlations grounded
in the published literature (or honest "I don't know" defaults).

The output is a band, not a number. The previous "+5-9 %/yr" point
estimate has been replaced in RUNBOOK_MAX_RETURN.md with the
appropriate percentile range from this simulation.

Run as: python -m trader.uplift_monte_carlo
"""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass


@dataclass
class EdgeComponent:
    """One source of expected uplift, parameterized for Monte Carlo."""
    name: str
    mean_pct: float              # expected annual return in pct
    std_pct: float               # 1-sigma annual std in pct
    # Correlation with the overall equity-stress regime (-1..1).
    # Positive = edge collapses in equity drawdowns. Negative = edge
    # GAINS in drawdowns. TLH harvest is the prime example of negative
    # correlation: market falls → MORE harvest opportunities.
    equity_stress_correlation: float


# Component edges. Means are literature-anchored; correlations are
# either FIT from 5 years of historical data (TLH, quality, universe-
# expansion) or LITERATURE-CITED (insider, PEAD, calendar, lending)
# where no fitting data exists in our journal.
#
# Calibration provenance: run `python scripts/fit_uplift_correlations.py`
# to re-fit. The script computes:
#   - TLH: monthly realized losses from a 5-yr simulation, correlated
#     with SPY drawdown-from-12-mo-peak. 64 monthly observations.
#   - Quality: top-1/3 by QUALITY_SCORES minus bottom-1/3 monthly
#     returns, correlated with stress. 63 monthly obs.
#   - Universe expansion: inherits TLH × 0.7 (amplifier).
#
# Last fit: 2026-05-10 against 2021-01 → 2026-04 universe history.
# TLH corr -0.51 (was hand-set -0.60).
# Quality corr -0.15 (was hand-set +0.10 — wrong sign; quality is
#   defensive, outperforms in stress per AFP 2019 "Quality Minus Junk").
# Universe expansion -0.36 (was hand-set -0.40).
COMPONENTS = [
    EdgeComponent(
        name="TLH tax shelter",
        mean_pct=1.75,
        std_pct=0.50,
        # FITTED from 5-yr historical TLH-simulation harvest activity:
        # monthly realized loss vs SPY drawdown, ρ = -0.51 on 64 obs.
        # Direction confirmed (negative = harvest fires more in stress).
        equity_stress_correlation=-0.51,
    ),
    EdgeComponent(
        name="Quality factor (Novy-Marx)",
        mean_pct=0.50,
        std_pct=0.80,
        # FITTED ρ = -0.15. SIGN-FLIP from prior hand-set +0.10.
        # Quality is defensive (top-quality basket outperforms in
        # equity drawdowns). Backed by Asness-Frazzini-Pedersen 2019
        # "Quality Minus Junk". My prior intuition was wrong.
        # Note: fitted mean over 5yr was only +0.25%/yr (the AI bubble
        # period favored growth/junk). Mean stays at +0.50% (literature
        # long-run); the calibration is conservative on direction.
        equity_stress_correlation=-0.15,
    ),
    EdgeComponent(
        name="Insider buying (EDGAR 30d)",
        mean_pct=2.50,
        std_pct=1.50,
        # LITERATURE: no Form-4 history in our journal to fit. Cohen-
        # Malloy-Pomorski 2012 suggests insiders cluster-buy post-
        # drawdown, but the SIGNAL EDGE decays in stress (every insider
        # buys, noise rises). Net mild positive correlation.
        equity_stress_correlation=0.20,
    ),
    EdgeComponent(
        name="PEAD (post-earnings drift)",
        mean_pct=1.50,
        std_pct=0.80,
        # LITERATURE: PEAD requires attention asymmetry. In stress,
        # everyone reads earnings → drift compresses. Bernard-Thomas
        # 1989 + Chordia-Shivakumar 2006 post-pub decay support
        # positive correlation (edge decays in stress).
        equity_stress_correlation=0.40,
    ),
    EdgeComponent(
        name="Calendar-effect overlay",
        mean_pct=0.40,
        std_pct=0.30,
        # LITERATURE / theoretical: anomalies are date-driven, not
        # regime-driven. Near-zero correlation by construction.
        equity_stress_correlation=0.00,
    ),
    EdgeComponent(
        name="Universe expansion (TLH scope)",
        mean_pct=0.45,
        std_pct=0.20,
        # FITTED via TLH ρ × 0.7 dampening (it's an amplifier on TLH
        # harvest; the marginal effect is smaller than TLH's primary).
        equity_stress_correlation=-0.36,
    ),
    EdgeComponent(
        name="Stock lending + cash interest",
        mean_pct=0.50,
        std_pct=0.30,
        # ECONOMIC LOGIC: stock lending fees are mildly procyclical
        # (more short demand in stress) but the effect on retail
        # rebate income is small. Cash interest tracks SOFR which is
        # weakly correlated with stress. Net: near-zero.
        equity_stress_correlation=0.05,
    ),
]


def simulate(components: list[EdgeComponent] = COMPONENTS,
              n_iter: int = 10_000,
              equity_stress_mean: float = 0.0,
              equity_stress_std: float = 1.0,
              seed: int | None = 42) -> list[float]:
    """Run Monte Carlo. Returns list of n_iter total-uplift samples.

    Each iteration:
      1. Draw an equity-stress factor z ~ N(0, 1). Positive z = bad
         year (drawdown). Negative z = bull year.
      2. For each component:
           edge_return = mean + std × (component_indep_noise +
                                         corr × z)
         Component_indep_noise ~ N(0, 1) independent.
      3. Sum component returns = total uplift for this iteration.
    """
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_iter):
        z = rng.gauss(equity_stress_mean, equity_stress_std)
        total = 0.0
        for c in components:
            indep = rng.gauss(0, 1)
            # Variance decomposition: rho^2 from stress, (1-rho^2) from indep
            corr = c.equity_stress_correlation
            combined = corr * z + ((1 - corr ** 2) ** 0.5) * indep
            edge_return = c.mean_pct + c.std_pct * combined
            total += edge_return
        samples.append(total)
    return samples


def percentiles(samples: list[float],
                  pcts: list[float] = [5, 10, 25, 50, 75, 90, 95]) -> dict[float, float]:
    """Empirical percentiles of the sample distribution."""
    s = sorted(samples)
    n = len(s)
    out = {}
    for p in pcts:
        idx = max(0, min(n - 1, int(p / 100.0 * n)))
        out[p] = s[idx]
    return out


def render_report(samples: list[float]) -> str:
    """Operator-readable distribution summary."""
    pcs = percentiles(samples)
    mean = sum(samples) / len(samples)
    var = sum((s - mean) ** 2 for s in samples) / len(samples)
    std = var ** 0.5
    n_below_zero = sum(1 for s in samples if s < 0)
    n_above_10 = sum(1 for s in samples if s > 10)
    lines = [
        "=" * 70,
        "UPLIFT MONTE CARLO — distribution over correlated edges",
        "=" * 70,
        f"  Iterations:          {len(samples):>10,}",
        f"  Mean uplift:         {mean:>+9.2f} %/yr",
        f"  Std deviation:       {std:>9.2f} %/yr",
        "",
        "  Percentile bands (annual uplift over SPY):",
        f"    5 % (bad year):      {pcs[5]:>+9.2f} %/yr",
        f"   10 %:                 {pcs[10]:>+9.2f} %/yr",
        f"   25 %:                 {pcs[25]:>+9.2f} %/yr",
        f"   50 % (median):        {pcs[50]:>+9.2f} %/yr",
        f"   75 %:                 {pcs[75]:>+9.2f} %/yr",
        f"   90 %:                 {pcs[90]:>+9.2f} %/yr",
        f"   95 % (best year):     {pcs[95]:>+9.2f} %/yr",
        "",
        f"  Pr(negative uplift): {n_below_zero / len(samples) * 100:>9.1f} %",
        f"  Pr(uplift > +10 %):   {n_above_10 / len(samples) * 100:>9.1f} %",
        "",
        "  Honest reading:",
        f"    Median expected uplift: {pcs[50]:+.1f} %/yr over SPY (after-tax)",
        f"    80 % CI (10th-90th):    {pcs[10]:+.1f} % to {pcs[90]:+.1f} %",
        f"    Pessimistic tail (5 %): worst-case ~{pcs[5]:+.1f} %/yr",
        "",
        "  Component assumptions (means / stds / corr-with-equity-stress):",
    ]
    for c in COMPONENTS:
        lines.append(
            f"    {c.name:<35}  mean={c.mean_pct:+.2f} std={c.std_pct:.2f} "
            f"rho={c.equity_stress_correlation:+.2f}"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--iter", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    samples = simulate(n_iter=args.iter, seed=args.seed)
    print(render_report(samples))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

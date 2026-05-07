#!/usr/bin/env python3
"""v3.73.24 — Universe V0 vs V1 regression test.

Build the merged V1 universe (V0 SECTORS + qualifying candidates from
build_universe_v1.py output) and run the LIVE strategy
(xs_top15_min_shifted) over the 25-year hostile-regime panel on
both. Report:
  - Cum return delta
  - Max DD delta
  - IR delta
  - Number of names changed in current top-15

Decision rule: ship V1 if it doesn't destroy IR (within 0.10 of V0)
AND doesn't worsen max-DD by more than 5pp.
"""
from __future__ import annotations

import math
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from trader.data import fetch_history  # noqa: E402
from trader.sectors import SECTORS  # noqa: E402
from trader.signals import momentum_score  # noqa: E402

# Augmented set from build_universe_v1.py (77 qualifying names)
V1_ADDITIONS = {
    "IBM", "HPQ", "EBAY", "ADP", "CMCSA", "SBUX", "LOW", "TGT", "TJX", "F",
    "CL", "MO", "GIS", "HSY", "KMB", "ADM", "STZ", "AMGN", "BMY", "LLY",
    "GILD", "MDT", "CVS", "BAX", "SYK", "BIIB", "AXP", "USB", "PNC", "BK",
    "COF", "TRV", "MET", "SCHW", "PRU", "C", "AIG", "CVX", "COP", "SLB",
    "EOG", "GE", "UPS", "RTX", "LMT", "NOC", "MMM", "DE", "EMR", "FDX",
    "UNP", "CSX", "NSC", "GD", "WM", "APD", "ECL", "NEM", "NUE", "FCX",
    "NEE", "DUK", "SO", "AEP", "EXC", "D", "ED", "O", "AMT", "PSA", "EQR",
    "VNO", "INTU", "AMAT", "MU", "LRCX", "KLAC",
}


def picks_live(asof, prices, universe):
    """Replica of the LIVE strategy: top-15 12-1 momentum, min-shift weighting."""
    avail = [c for c in universe if c in prices.columns]
    p = prices[avail]
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, 12, 1)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top15 = scored[:15]
    if not top15:
        return {}
    min_s = min(s for _, s in top15)
    shifted = [(t, s - min_s + 0.01) for t, s in top15]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: 0.80 / len(top15) for t, _ in top15}
    return {t: 0.80 * (s / total) for t, s in shifted}


def simulate(prices, dates, universe):
    eq = 1.0
    rets = []
    peak = 1.0
    max_dd = 0.0
    for i in range(len(dates) - 1):
        t0 = dates[i]
        t1 = dates[i + 1]
        weights = picks_live(t0, prices, universe)
        if not weights:
            continue
        period_ret = 0.0
        for sym, w in weights.items():
            s = prices[sym].dropna()
            lo = s[s.index >= t0]
            hi = s[s.index <= t1]
            if lo.empty or hi.empty:
                continue
            p0 = float(lo.iloc[0])
            p1 = float(hi.iloc[-1])
            if p0 <= 0:
                continue
            period_ret += w * (p1 / p0 - 1)
        eq *= 1 + period_ret
        rets.append(period_ret)
        peak = max(peak, eq)
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
    return eq, max_dd, rets


def ir(rets):
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var) if var > 0 else 1e-9
    # Annualize: monthly → sqrt(12)
    return mean / sd * math.sqrt(12)


def main():
    v0 = list(SECTORS.keys())
    v1 = list(set(v0) | V1_ADDITIONS)

    print(f"V0: {len(v0)} names")
    print(f"V1: {len(v1)} names ({len(v1) - len(v0)} additions)")

    print("\nFetching prices...")
    prices = fetch_history(v1 + ["SPY"], start="2000-01-01")
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.85))

    # Filter universes to actually-available names
    v0_avail = [c for c in v0 if c in prices.columns]
    v1_avail = [c for c in v1 if c in prices.columns]
    print(f"V0 available: {len(v0_avail)}")
    print(f"V1 available: {len(v1_avail)}")

    dates = [d for d in pd.date_range(prices.index[0], prices.index[-1], freq="BME")]

    print("\nSimulating V0...")
    v0_eq, v0_dd, v0_rets = simulate(prices, dates, v0_avail)
    v0_ir = ir(v0_rets)

    print("Simulating V1...")
    v1_eq, v1_dd, v1_rets = simulate(prices, dates, v1_avail)
    v1_ir = ir(v1_rets)

    # Most recent picks comparison
    last_t = dates[-1]
    v0_picks = picks_live(last_t, prices, v0_avail)
    v1_picks = picks_live(last_t, prices, v1_avail)

    print("\n" + "=" * 70)
    print(f"{'Metric':<25} {'V0':>15} {'V1':>15} {'Delta':>15}")
    print("-" * 70)
    print(f"{'Universe size':<25} {len(v0_avail):>15} "
          f"{len(v1_avail):>15} {len(v1_avail)-len(v0_avail):>+15}")
    print(f"{'Cum return (×)':<25} {v0_eq:>15.4f} {v1_eq:>15.4f} "
          f"{v1_eq-v0_eq:>+15.4f}")
    print(f"{'Max DD':<25} {v0_dd*100:>14.2f}% {v1_dd*100:>14.2f}% "
          f"{(v1_dd-v0_dd)*100:>+14.2f}%")
    print(f"{'IR (annualized)':<25} {v0_ir:>15.3f} {v1_ir:>15.3f} "
          f"{v1_ir-v0_ir:>+15.3f}")
    print("=" * 70)

    print(f"\nV0 picks ({last_t.date()}): {sorted(v0_picks.keys())}")
    print(f"V1 picks ({last_t.date()}): {sorted(v1_picks.keys())}")
    new_in_v1 = set(v1_picks) - set(v0_picks)
    dropped_from_v0 = set(v0_picks) - set(v1_picks)
    print(f"  New in V1: {sorted(new_in_v1)}")
    print(f"  Dropped from V0: {sorted(dropped_from_v0)}")

    # Decision: ship V1?
    ir_drop = v0_ir - v1_ir
    dd_worsen = v0_dd - v1_dd  # both negative; worse means lower
    ship = (ir_drop < 0.10) and (dd_worsen < 0.05)

    out = []
    out.append("# Universe V0 vs V1 — Regression Test\n\n")
    out.append("**Date:** 2026-05-07  \n")
    out.append("**Goal:** test whether the 77-name expansion proposed in "
                "UNIVERSE_V1_2026_05_07.md preserves the LIVE strategy's "
                "alpha and risk profile.\n\n")
    out.append("## Test setup\n\n")
    out.append("- Same LIVE strategy (xs_top15 12-1 momentum, min-shift "
                "weighting, 80% gross)\n")
    out.append("- 25-year panel (2000-01-01 → today)\n")
    out.append("- Monthly rebalance, multiplicative compounding\n\n")

    out.append("## Results\n\n")
    out.append("| Metric | V0 (existing) | V1 (broader) | Delta |\n"
                "|---|---:|---:|---:|\n")
    out.append(f"| Universe size | {len(v0_avail)} | {len(v1_avail)} | "
                f"{len(v1_avail)-len(v0_avail):+d} |\n")
    out.append(f"| Cum return (×) | {v0_eq:.4f} | {v1_eq:.4f} | "
                f"{v1_eq-v0_eq:+.4f} |\n")
    out.append(f"| Max drawdown | {v0_dd*100:.2f}% | {v1_dd*100:.2f}% | "
                f"{(v1_dd-v0_dd)*100:+.2f}pp |\n")
    out.append(f"| IR (annualized) | {v0_ir:.3f} | {v1_ir:.3f} | "
                f"{v1_ir-v0_ir:+.3f} |\n\n")

    out.append("## Most recent picks comparison\n\n")
    out.append(f"As of {last_t.date()}:\n\n")
    out.append(f"- V0 chose: `{sorted(v0_picks.keys())}`\n")
    out.append(f"- V1 chose: `{sorted(v1_picks.keys())}`\n")
    out.append(f"- New in V1: `{sorted(new_in_v1)}`\n")
    out.append(f"- Dropped from V0: `{sorted(dropped_from_v0)}`\n\n")

    out.append("## Decision rule\n\n")
    out.append("Ship V1 if:\n")
    out.append("- IR doesn't drop more than 0.10\n")
    out.append("- Max-DD doesn't worsen by more than 5pp\n\n")

    out.append(f"- IR drop: {ir_drop:+.3f} → "
                f"{'✅ pass' if ir_drop < 0.10 else '❌ fail'}\n")
    out.append(f"- DD worsening: {dd_worsen*100:+.2f}pp → "
                f"{'✅ pass' if dd_worsen < 0.05 else '❌ fail'}\n\n")

    if ship:
        out.append("## Verdict: ✅ SHIP\n\n")
        out.append("V1 preserves the LIVE strategy's profile within the "
                    "decision threshold. Recommend swapping `sectors.SECTORS` "
                    "to the merged V1 universe in a follow-up commit.\n")
    else:
        out.append("## Verdict: ❌ DO NOT SHIP\n\n")
        out.append("V1 fails the regression threshold. The broader universe "
                    "either drops IR > 0.10 or worsens max-DD > 5pp. Either "
                    "the new names dilute the signal too much, or they add "
                    "GFC-era casualties (C, AIG) that drag the panel.\n")

    out_path = ROOT / "docs" / "UNIVERSE_V0_V1_REGRESSION_2026_05_07.md"
    out_path.write_text("".join(out))
    print(f"\nWrote {out_path}")
    print(f"Decision: {'SHIP' if ship else 'DO NOT SHIP'}")


if __name__ == "__main__":
    main()

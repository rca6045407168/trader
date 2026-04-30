"""Backtest the OTM call barbell sleeve across the 5 regime windows.

Strategy:
  - Take top-3 momentum names at quarterly rebalance dates
  - Buy 6-month 25%-OTM call options on each (10% capital total / 3 names)
  - Hold to expiry (or simulate exit at terminal spot)
  - Aggregate cycle pnl across 5 regimes

This tests the hypothesis: capped-downside / unlimited-upside barbell on
momentum names produces positive expected return because real-world
right-tail kurtosis exceeds Black-Scholes assumption.

LIMITATIONS:
  - Uses 60d realized vol as IV proxy. Real IV typically HIGHER than realized
    (vol risk premium). So real-world option premiums are MORE expensive
    than this backtest assumes. Backtest is OPTIMISTIC.
  - No bid-ask spread modeling. Real options have 1-3% spreads.
  - No early exit logic — holds to expiry. In practice you'd exit at 50%
    profit or 30 days to expiry.
"""
from __future__ import annotations

import sys
import statistics
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from trader.universe import DEFAULT_LIQUID_50
from trader.data import fetch_history
from trader.options_barbell import (
    backtest_barbell_sleeve, simulate_call_payoff, select_otm_calls,
    black_scholes_call,
)


REGIMES = [
    # Extended span 2018-2026, quarterly rebalance → ~32 cycles
    ("Full span 2018-2026", pd.Timestamp("2018-01-01"), pd.Timestamp("2026-04-29")),
]

EQUITY = 100_000.0
ALLOCATION = 0.10  # 10% to barbell
OTM_PCT = 0.25     # 25% OTM
DTE_TARGET = 180   # 6 months

# v3.44 realistic-IV adjustment: real implied vol typically 1.3-1.7x realized
# (vol risk premium + skew premium). Multiply backtest premiums by this factor
# to estimate real-world option costs.
IV_PREMIUM_MULTIPLIER = 1.5

# Plus bid-ask spread (typical for liquid mega-cap names):
BID_ASK_SPREAD_BPS = 100  # 1% per side = 2% round-trip on premium


def main():
    print("=" * 80)
    print("OTM CALL BARBELL BACKTEST")
    print("=" * 80)
    print(f"Equity: ${EQUITY:,.0f}  Allocation: {ALLOCATION*100:.0f}%  "
          f"OTM: {OTM_PCT*100:.0f}%  DTE: {DTE_TARGET}")
    print()

    # Pre-fetch all price data
    earliest = min(s for _, s, _ in REGIMES) - pd.Timedelta(days=400)
    latest = max(e for _, _, e in REGIMES) + pd.Timedelta(days=200)
    print(f"Fetching prices {earliest.date()} to {latest.date()}...")
    try:
        prices = fetch_history(DEFAULT_LIQUID_50,
                               start=earliest.strftime("%Y-%m-%d"),
                               end=latest.strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"Failed: {e}")
        return 1

    spot_history = {t: prices[t].dropna() for t in prices.columns
                    if prices[t].dropna().shape[0] > 252}
    print(f"  {len(spot_history)} tickers with usable data\n")

    print()
    print(f"REALISTIC ADJUSTMENT: applying {IV_PREMIUM_MULTIPLIER}x premium markup "
          f"(vol risk premium) + {BID_ASK_SPREAD_BPS} bps round-trip spread")
    print()

    overall_results = []
    for regime_name, start, end in REGIMES:
        # Quarterly rebalance dates within this regime
        rebalance_dates = pd.date_range(start, end - pd.Timedelta(days=DTE_TARGET),
                                        freq="QE").tolist()
        rebalance_dates = [pd.Timestamp(d).tz_localize(None) if d.tz else pd.Timestamp(d)
                           for d in rebalance_dates]
        if not rebalance_dates:
            print(f">>> {regime_name}: no rebalance dates in window")
            continue

        result = backtest_barbell_sleeve(
            spot_history, EQUITY, rebalance_dates,
            allocation=ALLOCATION, otm_pct=OTM_PCT, dte_target=DTE_TARGET,
        )

        # v3.44: apply realistic IV adjustment to each cycle's PnL
        if "cycle_details" in result:
            for cycle in result["cycle_details"]:
                # Each cycle's PnL = sum of (terminal_payoff - cost_paid) per option
                # We need to inflate cost_paid by IV_PREMIUM_MULTIPLIER
                # cycle pnl = total_payoff - total_cost
                # adjusted pnl = total_payoff - (total_cost * IV_PREMIUM_MULTIPLIER)
                # delta = total_payoff - total_cost*MULT - (total_payoff - total_cost) = -total_cost*(MULT-1)
                total_cost = sum(d["premium"] * d["contracts"] * 100 for d in cycle["details"])
                # additional cost from IV markup
                extra_cost = total_cost * (IV_PREMIUM_MULTIPLIER - 1.0)
                # additional cost from bid-ask spread (entry + exit, but we hold to expiry so only entry):
                spread_cost = total_cost * (BID_ASK_SPREAD_BPS / 10000)
                cycle["pnl_realistic"] = cycle["pnl"] - extra_cost - spread_cost
            # Recompute aggregate with realistic
            cycles = result["cycle_details"]
            realistic_pnls = [c["pnl_realistic"] for c in cycles]
            result["realistic_total_pnl"] = sum(realistic_pnls)
            result["realistic_mean_cycle_pnl"] = sum(realistic_pnls) / len(cycles)
            result["realistic_win_rate"] = sum(1 for p in realistic_pnls if p > 0) / len(cycles)
            result["realistic_best_cycle"] = max(realistic_pnls)
            result["realistic_worst_cycle"] = min(realistic_pnls)
        if "error" in result:
            print(f">>> {regime_name}: {result['error']}")
            continue

        overall_results.append({"regime": regime_name, **result})
        print(f">>> {regime_name}")
        print(f"    Cycles:                {result['n_cycles']}")
        print()
        print(f"    OPTIMISTIC (BS-priced premiums):")
        print(f"      Total PnL:           ${result['total_pnl']:>+12,.0f}")
        print(f"      Mean cycle:          ${result['mean_cycle_pnl']:>+12,.0f}")
        print(f"      Win rate:            {result['win_rate']*100:>5.1f}%")
        print(f"      Best/worst cycle:    ${result['best_cycle_pnl']:>+12,.0f} / ${result['worst_cycle_pnl']:>+12,.0f}")
        if "realistic_total_pnl" in result:
            print()
            print(f"    REALISTIC (1.5x IV markup + 1% spread):")
            print(f"      Total PnL:           ${result['realistic_total_pnl']:>+12,.0f}")
            print(f"      Mean cycle:          ${result['realistic_mean_cycle_pnl']:>+12,.0f}")
            print(f"      Win rate:            {result['realistic_win_rate']*100:>5.1f}%")
            print(f"      Best/worst cycle:    ${result['realistic_best_cycle']:>+12,.0f} / ${result['realistic_worst_cycle']:>+12,.0f}")
        # Per-cycle detail (top 3 most extreme)
        cycles = sorted(result["cycle_details"], key=lambda c: c["pnl"], reverse=True)
        if len(cycles) >= 3:
            print(f"    Best/worst cycle examples:")
            for c in [cycles[0], cycles[-1]]:
                d = c["details"][0] if c["details"] else None
                if d:
                    sample = c["details"][0]
                    pct_ret = (sample["terminal_spot"] / sample["spot_at_entry"] - 1) * 100
                    print(f"      {c['date'].strftime('%Y-%m-%d')}  pnl ${c['pnl']:>+10,.0f}  "
                          f"sample: {sample['ticker']} {pct_ret:+.1f}% "
                          f"({sample['spot_at_entry']:.2f}→{sample['terminal_spot']:.2f}, K={sample['strike']:.2f})")
        print()

    if not overall_results:
        print("No regimes had completable cycles.")
        return 1

    # Aggregate
    all_cycles = []
    for r in overall_results:
        for c in r["cycle_details"]:
            all_cycles.append(c["pnl"])

    print()
    print("=" * 80)
    print("AGGREGATE")
    print("=" * 80)
    total_pnl_all = sum(all_cycles)
    mean_cycle = statistics.mean(all_cycles)
    median_cycle = statistics.median(all_cycles)
    overall_win_rate = sum(1 for c in all_cycles if c > 0) / len(all_cycles)
    n_cycles = len(all_cycles)
    capital_per_cycle = EQUITY * ALLOCATION
    pct_return_per_cycle = mean_cycle / capital_per_cycle * 100
    annualized_pct = pct_return_per_cycle * (365 / DTE_TARGET)

    print(f"  Total cycles:        {n_cycles}")
    print(f"  Mean cycle PnL (optimistic):  ${mean_cycle:>+12,.0f}  ({pct_return_per_cycle:+.1f}% on capital)")
    print(f"  Median cycle PnL (optimistic): ${median_cycle:>+12,.0f}")
    print(f"  Win rate (optimistic): {overall_win_rate*100:>5.1f}%")
    print(f"  Annualized (optimistic): {annualized_pct:+.1f}% on barbell capital")

    # Realistic aggregate
    realistic_cycles = []
    for r in overall_results:
        if "cycle_details" in r:
            for c in r["cycle_details"]:
                if "pnl_realistic" in c:
                    realistic_cycles.append(c["pnl_realistic"])
    if realistic_cycles:
        r_mean = statistics.mean(realistic_cycles)
        r_median = statistics.median(realistic_cycles)
        r_win = sum(1 for p in realistic_cycles if p > 0) / len(realistic_cycles)
        r_pct_per_cycle = r_mean / capital_per_cycle * 100
        r_annualized = r_pct_per_cycle * (365 / DTE_TARGET)
        print()
        print(f"  REALISTIC (with vol risk premium + spread):")
        print(f"    Mean cycle PnL:      ${r_mean:>+12,.0f}  ({r_pct_per_cycle:+.1f}% on capital)")
        print(f"    Median cycle PnL:    ${r_median:>+12,.0f}")
        print(f"    Win rate:            {r_win*100:>5.1f}%")
        print(f"    Annualized:          {r_annualized:+.1f}%")
        print()

        # Verdict based on REALISTIC numbers
        if r_annualized > 30:
            print("✓ STRONG: barbell survives realistic IV markup + spreads. SHIP.")
        elif r_annualized > 10:
            print(f"~ MODERATE: {r_annualized:.1f}% annualized. Marginal but capped downside makes 5% allocation rational.")
        elif r_annualized > 0:
            print(f"~ WEAK: {r_annualized:.1f}% annualized. Likely not worth operational complexity.")
        else:
            print(f"✗ FAILS realistic test: {r_annualized:.1f}% — vol risk premium eats the asymmetric edge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

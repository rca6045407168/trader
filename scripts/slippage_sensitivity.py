"""Slippage sensitivity analysis.

The backtest's headline numbers assume zero costs. v3.9 added 5bp/trade and
showed minimal drag (0.4pp/yr) on LIVE. But what's the BREAK-EVEN slippage —
the cost level above which the strategy stops working?

This matters for:
  1. Real-world execution: live slippage can be 5-20bp depending on liquidity,
     order type (market vs limit), time of day, and market volatility
  2. Capacity planning: as account grows, market impact grows; at some size
     the strategy stops being profitable
  3. Variant selection: high-turnover variants (e.g., 3mo lookback) are more
     sensitive to costs than low-turnover (12mo lookback)

This script sweeps cost_bps ∈ {0, 5, 10, 20, 50, 100} for LIVE and the top
shadow candidates, and reports the cost level that erodes the edge over SPY.
"""
from __future__ import annotations

import os
import sys
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd

# Importing regime_stress_test brings in the variants
from regime_stress_test import (
    REGIMES, replay_window,
    variant_top3_eq_80,
    variant_top3_crowding_penalty,
    variant_top3_residual_momentum,
    variant_top3_residual_vol_targeted,
    variant_top3_eq_80_pit,
    variant_top5_eq_40,
)


VARIANTS_TO_TEST = {
    "top3_eq_80 (LIVE)":       variant_top3_eq_80,
    "top5_eq_40 (old LIVE)":   variant_top5_eq_40,
    "top3_crowding (v3.21)":    variant_top3_crowding_penalty,
    "top3_residual (v3.15)":    variant_top3_residual_momentum,
    "top3_residual_vol (v3.16)": variant_top3_residual_vol_targeted,
    "top3_eq_80_PIT (honest)":  variant_top3_eq_80_pit,
}

COST_LEVELS_BPS = [0, 5, 10, 20, 50, 100]


def main():
    print("=" * 100)
    print("SLIPPAGE SENSITIVITY — find the cost level that breaks each strategy")
    print("=" * 100)
    print()
    print(f"Cost levels (bps per trade, applied to |delta_weight|): {COST_LEVELS_BPS}")
    print(f"Variants: {len(VARIANTS_TO_TEST)}")
    print(f"Regimes: {len(REGIMES)}")
    print()

    # results[variant_name][cost_bps] = mean_sharpe across 5 regimes
    results = {v: {} for v in VARIANTS_TO_TEST}

    for cost_bps in COST_LEVELS_BPS:
        print(f"\n=== cost = {cost_bps} bps/trade ===")
        for v_name, fn in VARIANTS_TO_TEST.items():
            sharpes = []
            cagrs = []
            spy_excess = []
            for regime_name, start, end in REGIMES:
                try:
                    r = replay_window(v_name, fn, start, end, cost_bps=cost_bps)
                    if r:
                        sharpes.append(r["sharpe"])
                        cagrs.append(r["cagr"])
                        spy_excess.append(r["cagr"] - r["spy_cagr"])
                except Exception:
                    continue
            if sharpes:
                m_sharpe = statistics.mean(sharpes)
                m_cagr = statistics.mean(cagrs)
                m_excess = statistics.mean(spy_excess)
                results[v_name][cost_bps] = {
                    "mean_sharpe": m_sharpe,
                    "mean_cagr": m_cagr,
                    "mean_spy_excess": m_excess,
                }
                print(f"  {v_name:28s}  mean Sharpe {m_sharpe:>+5.2f}  mean CAGR {m_cagr*100:>+6.1f}%  mean excess vs SPY {m_excess*100:>+6.1f}pp")

    # Final summary table
    print()
    print("=" * 100)
    print("MEAN SHARPE vs cost level (per variant)")
    print("=" * 100)
    header = f"{'Variant':<32s} " + " ".join([f"{c:>4d}bp" for c in COST_LEVELS_BPS])
    print(header)
    print("-" * len(header))
    for v_name in VARIANTS_TO_TEST:
        row = f"{v_name:<32s} " + " ".join([
            f"{results[v_name].get(c, {}).get('mean_sharpe', 0):>+5.2f} "
            for c in COST_LEVELS_BPS
        ])
        print(row)

    print()
    print("MEAN EXCESS RETURN vs SPY (pp/yr) — what we actually care about")
    print("=" * 100)
    print(header)
    print("-" * len(header))
    for v_name in VARIANTS_TO_TEST:
        row = f"{v_name:<32s} " + " ".join([
            f"{results[v_name].get(c, {}).get('mean_spy_excess', 0)*100:>+5.1f}pp"
            for c in COST_LEVELS_BPS
        ])
        print(row)

    print()
    print("INTERPRETATION:")
    print("  - 0 bp:    pure backtest, no friction")
    print("  - 5 bp:    realistic for liquid US equity via Alpaca free commissions")
    print("  - 10 bp:   conservative real-world (some slippage on aggressive rebalance)")
    print("  - 20 bp:   stressed liquidity / mid-cap names / volatile markets")
    print("  - 50 bp:   pessimistic ceiling — wide spreads, urgent rebalance")
    print("  - 100 bp:  break-the-strategy stress test")
    print()
    print("BREAK-EVEN: variant 'breaks' when mean Sharpe drops below 0 OR mean")
    print("excess vs SPY drops below 0 pp. Use this to pick a variant whose edge")
    print("is robust across the cost levels you might actually face.")


if __name__ == "__main__":
    sys.exit(main())

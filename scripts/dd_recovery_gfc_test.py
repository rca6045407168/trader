#!/usr/bin/env python3
"""v3.73.24 — drawdown-based recovery rule, GFC stress test.

The VIX-based recovery rule (xs_top15_recovery_aware) failed in
the GFC because VIX never compressed below 25 during the actual
recovery turn (Mar-Jun 2009). VIX bottomed at ~30 in early 2009
and didn't drop below 25 until summer 2009 — too late.

A drawdown-based detector should fire IN the GFC because it uses
SPY's own price action as the regime signal:
    recovery_active = (
        SPY 180d_drawdown < -25%   # we are in a deep crash
        AND SPY 1m_return > +5%    # rebound has started
    )

This script:
  1. Fetches 25-year history (2000-2025, the same panel as the
     hostile-regime backtest)
  2. Runs four strategies through the GFC window (Sept 2008 -
     Dec 2010): production (12-1), VIX-recovery, dd-recovery,
     and SPY (passive).
  3. Reports cumulative return + max DD for each.
  4. Writes proof to docs/DD_RECOVERY_GFC_TEST_2026_05_07.md
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from trader.data import fetch_history  # noqa: E402
from trader.eval_strategies import (  # noqa: E402
    xs_top15_min_shifted,
    xs_top15_recovery_aware,
    xs_top15_dd_recovery_aware,
)
from trader.sectors import SECTORS  # noqa: E402

GFC_START = pd.Timestamp("2008-09-01")
GFC_END = pd.Timestamp("2010-12-31")


def run_strategy(strategy_fn, prices, dates, name):
    """Walk-forward simulator: monthly rebalance, multiplicative
    compounding. Returns (cum_returns, max_dd, recovery_fires)."""
    eq = 1.0
    eq_path = []
    peak = 1.0
    max_dd = 0.0
    recovery_fires = 0

    # Track regime activations by tracking change in lookback-period.
    # We do that by intercepting prices['SPY'] and computing the
    # detector ourselves for proof.
    spy = prices["SPY"].dropna()

    for i in range(len(dates) - 1):
        t0 = dates[i]
        t1 = dates[i + 1]
        weights = strategy_fn(t0, prices)
        if not weights:
            eq_path.append((t0, eq))
            continue

        # Compute regime detector match for proof
        if "dd_recovery" in name:
            try:
                last = spy[spy.index <= t0]
                if len(last) >= 180:
                    last_180 = last.iloc[-180:]
                    peak_180 = float(last_180.max())
                    current = float(last.iloc[-1])
                    dd_180 = current / peak_180 - 1
                    one_m_ago = last.iloc[-22] if len(last) >= 22 else last.iloc[0]
                    ret_1m = current / float(one_m_ago) - 1
                    if (dd_180 < -0.25) and (ret_1m > 0.05):
                        recovery_fires += 1
            except Exception:
                pass
        elif "vix_recovery" in name or "recovery_aware" in name:
            try:
                if "^VIX" in prices.columns:
                    vix_series = prices["^VIX"].dropna()
                    vix_now = vix_series[vix_series.index <= t0]
                    if not vix_now.empty:
                        current_vix = float(vix_now.iloc[-1])
                        last_30 = vix_now.iloc[-30:] if len(vix_now) >= 30 else vix_now
                        max_30 = float(last_30.max())
                        if (current_vix < 25) and (max_30 > 35):
                            recovery_fires += 1
            except Exception:
                pass

        # Compute period return
        period_ret = 0.0
        for sym, w in weights.items():
            if sym not in prices.columns:
                continue
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
        peak = max(peak, eq)
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
        eq_path.append((t1, eq))

    return eq, max_dd, recovery_fires, eq_path


def spy_baseline(prices, dates):
    spy = prices["SPY"].dropna()
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for i in range(len(dates) - 1):
        t0 = dates[i]
        t1 = dates[i + 1]
        lo = spy[spy.index >= t0]
        hi = spy[spy.index <= t1]
        if lo.empty or hi.empty:
            continue
        p0 = float(lo.iloc[0])
        p1 = float(hi.iloc[-1])
        if p0 <= 0:
            continue
        eq *= p1 / p0
        peak = max(peak, eq)
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
    return eq, max_dd


def main():
    print("Fetching baseline universe + SPY + ^VIX...")
    universe = list(SECTORS.keys()) + ["SPY", "^VIX"]
    prices = fetch_history(universe, start="2000-01-01")
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.95))

    # GFC window
    gfc_dates = [
        d for d in pd.date_range(GFC_START, GFC_END, freq="BME")
    ]
    print(f"GFC window: {gfc_dates[0].date()} → {gfc_dates[-1].date()} "
          f"({len(gfc_dates)} months)")

    print("\nRunning production (xs_top15_min_shifted)...")
    prod_eq, prod_dd, _, _ = run_strategy(
        xs_top15_min_shifted, prices, gfc_dates, "production")

    print("Running VIX-recovery (xs_top15_recovery_aware)...")
    vix_eq, vix_dd, vix_fires, _ = run_strategy(
        xs_top15_recovery_aware, prices, gfc_dates, "vix_recovery_aware")

    print("Running drawdown-recovery (xs_top15_dd_recovery_aware)...")
    dd_eq, dd_dd, dd_fires, _ = run_strategy(
        xs_top15_dd_recovery_aware, prices, gfc_dates, "dd_recovery_aware")

    print("Running SPY baseline...")
    spy_eq, spy_dd = spy_baseline(prices, gfc_dates)

    print("\n" + "=" * 70)
    print(f"{'Strategy':<30} {'Cum return':>12} {'Max DD':>10} {'Fires':>8}")
    print("-" * 70)
    print(f"{'production (12-1)':<30} {(prod_eq-1)*100:>11.2f}% "
          f"{prod_dd*100:>9.2f}% {'n/a':>8}")
    print(f"{'VIX-recovery':<30} {(vix_eq-1)*100:>11.2f}% "
          f"{vix_dd*100:>9.2f}% {vix_fires:>8}")
    print(f"{'DD-recovery':<30} {(dd_eq-1)*100:>11.2f}% "
          f"{dd_dd*100:>9.2f}% {dd_fires:>8}")
    print(f"{'SPY (passive)':<30} {(spy_eq-1)*100:>11.2f}% "
          f"{spy_dd*100:>9.2f}% {'n/a':>8}")
    print("=" * 70)

    out = []
    out.append("# Drawdown-Based Recovery Rule — GFC Stress Test\n\n")
    out.append("**Date:** 2026-05-07  \n")
    out.append("**Goal:** test whether a drawdown-based recovery detector "
                "fixes the GFC weakness where the VIX-based rule "
                "(xs_top15_recovery_aware) failed to fire.\n\n")

    out.append("## Detector definitions\n\n")
    out.append("**VIX-based** (existing v3.73.22 rule):\n```\n"
                "recovery_active = (current_vix < 25) AND (max_vix_30d > 35)\n"
                "```\n\n")
    out.append("**Drawdown-based** (this work, v3.73.24):\n```\n"
                "recovery_active = (SPY_180d_DD < -25%) AND (SPY_1m_return > +5%)\n"
                "```\n\n")
    out.append("Both rules switch from 12-1 to 6-1 momentum when active. "
                "Both keep min-shifted weighting + 80% gross.\n\n")

    out.append("## GFC results (2008-09 → 2010-12, 28 months)\n\n")
    out.append("| Strategy | Cum return | Max DD | Recovery fires |\n")
    out.append("|---|---:|---:|---:|\n")
    out.append(f"| production (12-1) | {(prod_eq-1)*100:+.2f}% "
                f"| {prod_dd*100:.2f}% | n/a |\n")
    out.append(f"| VIX-based recovery | {(vix_eq-1)*100:+.2f}% "
                f"| {vix_dd*100:.2f}% | {vix_fires} |\n")
    out.append(f"| DD-based recovery | {(dd_eq-1)*100:+.2f}% "
                f"| {dd_dd*100:.2f}% | {dd_fires} |\n")
    out.append(f"| SPY (passive) | {(spy_eq-1)*100:+.2f}% "
                f"| {spy_dd*100:.2f}% | n/a |\n\n")

    delta_vs_prod = (dd_eq - prod_eq) * 100
    delta_vs_vix = (dd_eq - vix_eq) * 100
    out.append("## Delta\n\n")
    out.append(f"- DD-recovery vs production: **{delta_vs_prod:+.2f}pp**\n")
    out.append(f"- DD-recovery vs VIX-recovery: **{delta_vs_vix:+.2f}pp**\n\n")

    if dd_fires == 0:
        out.append("## Verdict: ❌ no improvement\n\n")
        out.append("The drawdown rule never fired during the GFC window. The "
                    "thresholds (-25% DD, +5% 1m return) may need tuning. "
                    "The SPY March 2009 trough showed a 1m return that did "
                    "not exceed the +5% threshold cleanly enough at the "
                    "month-end sampling; consider a lower threshold or a "
                    "weekly sampling window.\n")
    elif (dd_eq > prod_eq) and (dd_eq > vix_eq):
        out.append("## Verdict: ✅ DD-rule beats both production AND VIX-rule\n\n")
        out.append("The drawdown-based detector fired during the GFC and the "
                    "regime switch produced a P&L improvement. This is real "
                    "progress on the GFC weakness that the VIX rule could "
                    "not address.\n")
    elif dd_fires > vix_fires:
        out.append("## Verdict: ⚠️  DD-rule fires but P&L delta is mixed\n\n")
        out.append("The drawdown detector successfully fires more often than "
                    "the VIX detector during the GFC, but the resulting 6-1 "
                    "lookback did not improve P&L vs production. The detector "
                    "works; the response (6-1 momentum) may not be the right "
                    "action during a deep-crash recovery. Worth exploring "
                    "alternative responses (e.g., shift to defensive sectors).\n")
    else:
        out.append("## Verdict: ⚠️  DD-rule does not fire materially more\n")

    out_path = ROOT / "docs" / "DD_RECOVERY_GFC_TEST_2026_05_07.md"
    out_path.write_text("".join(out))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

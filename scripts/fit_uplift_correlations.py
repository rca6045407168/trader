#!/usr/bin/env python3
"""Fit Monte Carlo correlations from historical data.

The uplift Monte Carlo in src/trader/uplift_monte_carlo.py has 7
hand-set correlation values that I admitted were "fiction" in the
round-2 self-critique. This script replaces them with values fit on
real data wherever possible.

What gets fit (data available):
  - TLH harvest vs equity-stress: backtest harvest activity month-
    by-month over 5 years, correlate with SPY drawdown-from-peak.
  - Quality factor vs equity-stress: monthly returns of high-quality
    basket minus low-quality basket, correlate with SPY drawdown.
  - Universe expansion vs TLH (it's an amplifier on TLH harvest, so
    inherits the fitted TLH correlation × 0.7 dampening).

What stays literature-based (no historical data available):
  - Insider buying (yfinance + EDGAR): published CMP-2012 results +
    McLean-Pontiff decay
  - PEAD: Bernard-Thomas 1989 + Chordia-Shivakumar 2006 decay
  - Calendar effects: anomaly literature + the empirical recalibration
    already in src/trader/anomalies.py (turn-of-month +2.5bps vs the
    +30bps claim, etc.)
  - Securities lending: economic logic (corr~0 with equity stress)

Output: prints fitted values + a recommended update for
uplift_monte_carlo.py's COMPONENTS list. Operator decides whether to
apply.

Run: python scripts/fit_uplift_correlations.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trader.direct_index_tlh import (  # noqa: E402
    REPLACEMENT_MAP, QUALITY_SCORES, cap_weighted_targets,
    quality_tilted_targets,
)


# ============================================================
# Data fetch
# ============================================================
def fetch_universe_history(start: str = "2021-01-01",
                            end: str = "2026-05-01") -> pd.DataFrame:
    """Adjusted-close history for the 50-name universe + SPY."""
    syms = sorted(set(REPLACEMENT_MAP.keys()) | {"SPY"})
    print(f"Fetching {len(syms)} symbols, {start} → {end}...")
    df = yf.download(syms, start=start, end=end,
                      progress=False, auto_adjust=True, threads=True)
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            df = df["Close"]
        else:
            df = df.xs(df.columns.get_level_values(0)[0], axis=1, level=0)
    print(f"  → {len(df)} days, {len(df.columns)} columns")
    return df


# ============================================================
# Equity stress series
# ============================================================
def equity_stress_series(spy: pd.Series, window_days: int = 252) -> pd.Series:
    """Drawdown from trailing 12-month peak. Positive values mean
    stress (we're below the recent high). Used as the single
    'regime' factor in the Monte Carlo.

    Resampled to monthly so it matches our edge-return series."""
    peak = spy.rolling(window_days, min_periods=1).max()
    dd = (spy - peak) / peak  # negative number; -0.20 = 20% drawdown
    # Convert to "stress" = -dd, so positive means more stress
    stress = -dd
    return stress.resample("ME").last()


# ============================================================
# Fit: TLH harvest correlation
# ============================================================
def fit_tlh_correlation(prices: pd.DataFrame,
                         monthly_contribution: float = 1000.0) -> tuple[float, dict]:
    """Run the TLH simulator month-by-month, get realized losses,
    correlate with equity-stress.

    Returns (rho, debug_info).
    """
    from tlh_backtest import simulate
    universe = [t for t in REPLACEMENT_MAP if t in prices.columns]
    px = prices[universe + ["SPY"]].dropna(how="all")

    # Simulate at monthly checkpoints
    start_date = px.index[0]
    end_date = px.index[-1]
    print(f"  simulating TLH {start_date.date()} → {end_date.date()}...")
    result = simulate(
        prices=px,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        starting_capital=100_000,
        monthly_contribution=monthly_contribution,
    )

    # Bucket swap losses into monthly bins
    swaps = pd.DataFrame(result["swaps"])
    if swaps.empty:
        return 0.0, {"error": "no swaps in simulation"}
    swaps["date"] = pd.to_datetime(swaps["date"])
    monthly_loss = (
        swaps.set_index("date")["loss"]
        .resample("ME").sum()
        .fillna(0)
    )

    # Equity stress on the same monthly axis
    stress = equity_stress_series(px["SPY"])
    aligned = pd.concat(
        [monthly_loss.rename("loss"), stress.rename("stress")], axis=1
    ).dropna()

    # Correlate. Note: stress is positive in drawdowns, loss is
    # negative when harvests fire. Negative correlation means MORE
    # stress → MORE LOSS HARVESTED (more negative loss).
    rho = float(aligned["loss"].corr(aligned["stress"]))
    return rho, {
        "n_obs": int(len(aligned)),
        "total_loss": float(monthly_loss.sum()),
        "n_swaps": result["n_swaps"],
    }


# ============================================================
# Fit: Quality factor correlation
# ============================================================
def fit_quality_correlation(prices: pd.DataFrame) -> tuple[float, dict]:
    """Monthly excess return of top-quality 1/3 minus bottom-quality
    1/3 of our universe. Correlate with equity stress.

    The expected sign: quality is mildly defensive in bear markets
    (negative correlation with stress). My hand-set value was +0.10
    (mild positive); the literature suggests negative.
    """
    universe = [t for t in REPLACEMENT_MAP if t in prices.columns
                  and t in QUALITY_SCORES]
    qs = [(t, QUALITY_SCORES[t]) for t in universe]
    qs.sort(key=lambda x: -x[1])
    third = len(qs) // 3
    top_q = [t for t, _ in qs[:third]]
    bot_q = [t for t, _ in qs[-third:]]

    px = prices[universe + ["SPY"]].dropna(how="all").ffill()
    # Monthly returns
    monthly_px = px.resample("ME").last()
    monthly_ret = monthly_px.pct_change()
    top_ret = monthly_ret[top_q].mean(axis=1)
    bot_ret = monthly_ret[bot_q].mean(axis=1)
    qmj = top_ret - bot_ret  # quality minus junk

    stress = equity_stress_series(prices["SPY"])
    aligned = pd.concat(
        [qmj.rename("qmj"), stress.rename("stress")], axis=1,
    ).dropna()
    rho = float(aligned["qmj"].corr(aligned["stress"]))
    return rho, {
        "n_obs": int(len(aligned)),
        "qmj_mean_monthly": float(qmj.mean()),
        "qmj_annualized": float(qmj.mean() * 12),
        "qmj_std_monthly": float(qmj.std()),
        "n_top": len(top_q),
        "n_bot": len(bot_q),
    }


# ============================================================
# Compare to hand-set values
# ============================================================
HAND_SET = {
    "TLH tax shelter":                   -0.60,
    "Quality factor (Novy-Marx)":         0.10,
    "Insider buying (EDGAR 30d)":         0.20,
    "PEAD (post-earnings drift)":         0.40,
    "Calendar-effect overlay":            0.00,
    "Universe expansion (TLH scope)":    -0.40,
    "Stock lending + cash interest":      0.05,
}


def main():
    print("=" * 72)
    print("UPLIFT MONTE CARLO — CORRELATION CALIBRATION")
    print("=" * 72)
    print("Fitting correlations from historical data where possible.")
    print()

    prices = fetch_universe_history()

    print("\n--- TLH HARVEST CORRELATION ---")
    print("Method: simulate harvest activity month-by-month with $1k/mo DCA,")
    print("        bucket realized losses into monthly bins, correlate with")
    print("        equity-stress (SPY drawdown from 12-mo peak).")
    tlh_rho, tlh_info = fit_tlh_correlation(prices)
    print(f"  fitted ρ: {tlh_rho:+.3f}")
    print(f"  hand-set ρ: {HAND_SET['TLH tax shelter']:+.3f}")
    print(f"  delta: {tlh_rho - HAND_SET['TLH tax shelter']:+.3f}")
    print(f"  diagnostics: {tlh_info}")

    print("\n--- QUALITY FACTOR CORRELATION ---")
    print("Method: top-1/3 quality basket minus bottom-1/3 (by QUALITY_SCORES),")
    print("        monthly returns, correlate with equity stress.")
    q_rho, q_info = fit_quality_correlation(prices)
    print(f"  fitted ρ: {q_rho:+.3f}")
    print(f"  hand-set ρ: {HAND_SET['Quality factor (Novy-Marx)']:+.3f}")
    print(f"  delta: {q_rho - HAND_SET['Quality factor (Novy-Marx)']:+.3f}")
    print(f"  diagnostics: {q_info}")

    # Universe expansion inherits from TLH (it's an amplifier).
    # Damp by 0.7 because the expansion's marginal benefit is smaller
    # than TLH's primary effect.
    ue_rho = tlh_rho * 0.7
    print(f"\n--- UNIVERSE EXPANSION (derived from TLH × 0.7) ---")
    print(f"  fitted ρ: {ue_rho:+.3f}")
    print(f"  hand-set ρ: {HAND_SET['Universe expansion (TLH scope)']:+.3f}")
    print(f"  delta: {ue_rho - HAND_SET['Universe expansion (TLH scope)']:+.3f}")

    print("\n--- LITERATURE-BASED (not fit) ---")
    print("  Insider buying:     +0.20  (CMP 2012, decayed McLean-Pontiff)")
    print("  PEAD:               +0.40  (Bernard-Thomas 1989, Chordia-Shivakumar)")
    print("  Calendar overlay:    0.00  (negligible)")
    print("  Stock lending:      +0.05  (economic logic)")

    print()
    print("=" * 72)
    print("RECOMMENDED UPDATES to src/trader/uplift_monte_carlo.py")
    print("=" * 72)
    print(f"  TLH tax shelter:                  {tlh_rho:+.2f}  (was {HAND_SET['TLH tax shelter']:+.2f})")
    print(f"  Quality factor (Novy-Marx):       {q_rho:+.2f}  (was {HAND_SET['Quality factor (Novy-Marx)']:+.2f})")
    print(f"  Universe expansion (TLH scope):   {ue_rho:+.2f}  (was {HAND_SET['Universe expansion (TLH scope)']:+.2f})")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

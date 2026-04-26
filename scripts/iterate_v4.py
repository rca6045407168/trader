"""v0.4 — add a SPY-drawdown filter to bottom-catch + test 3-sleeve ensemble.

H5: Skip bottom-catch trades when SPY is >X% below its 252-day high.
    Hypothesis: this avoids the 2020-style falling-knife trades.
H6: 3-sleeve ensemble (momentum 60% + bottom-catch 30% + breakout 10%)
    vs 2-sleeve baseline.
H7: Risk-parity weighting (inverse-vol) vs equal-weight.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_history
from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def _spy_drawdown_series():
    spy = fetch_history(["SPY"], start="2014-01-01", end="2025-04-30")["SPY"]
    rolling_max = spy.rolling(252, min_periods=20).max()
    dd = spy / rolling_max - 1
    return dd


def _strategy_returns_from_csv(csv_name: str, score_col: str, score_min: float, ret_col: str = "ret_20d"):
    df = pd.read_csv(REPORTS / csv_name, parse_dates=["date"])
    df = df[df[score_col] >= score_min]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp("M")
    return df.groupby("month")[ret_col].mean(), df


def h5_dd_filter():
    print("\n" + "=" * 78)
    print("H5 — BOTTOM-CATCH WITH SPY-DRAWDOWN FILTER")
    print("=" * 78)
    df = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    df = df[df["score"] >= 0.65]
    dd = _spy_drawdown_series()
    dd_lookup = {d.date(): float(v) for d, v in dd.items() if not pd.isna(v)}
    df["spy_dd"] = df["date"].apply(lambda d: dd_lookup.get(d.date(), 0.0))

    print(f"\n{'SPY DD threshold':>20s}  {'kept':>6s}  {'mean ret_20d':>14s}  {'win':>6s}")
    for thresh in (-0.05, -0.08, -0.10, -0.15, -0.20):
        kept = df[df["spy_dd"] >= thresh]
        skipped = df[df["spy_dd"] < thresh]
        print(
            f"  {thresh:+.0%}             {len(kept):>6d}  {kept['ret_20d'].mean():>+13.2%}  "
            f"{(kept['ret_20d'] > 0).mean():>5.1%}  (skipped {len(skipped)} "
            f"with mean {skipped['ret_20d'].mean() if len(skipped) else 0:+.2%})"
        )

    print("\n  No filter (baseline):  mean +2.29%, win 62.5%")
    print("  VERDICT: pick the threshold that maximises kept-trade mean while preserving most trades.")


def h6_three_sleeve_ensemble():
    print("\n" + "=" * 78)
    print("H6 — 3-SLEEVE ENSEMBLE: MOMENTUM + BOTTOM-CATCH + BREAKOUT")
    print("=" * 78)

    mom = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                            lookback_months=12, top_n=5)
    mom_m = mom.monthly_returns

    bot_m, _ = _strategy_returns_from_csv("bottom_catch_triggers.csv", "score", 0.65)
    brk_m, _ = _strategy_returns_from_csv("breakout_52w_triggers.csv", "score", 0.7)

    bot_m = bot_m.reindex(mom_m.index).fillna(0)
    brk_m = brk_m.reindex(mom_m.index).fillna(0)

    weight_grids = [
        ("100/0/0 — mom only", 1.00, 0.00, 0.00),
        ("80/20/0 — mom + bot", 0.80, 0.20, 0.00),
        ("70/20/10 — 3-sleeve", 0.70, 0.20, 0.10),
        ("60/30/10 — more bot", 0.60, 0.30, 0.10),
        ("60/20/20 — more brk", 0.60, 0.20, 0.20),
        ("50/30/20 — balanced", 0.50, 0.30, 0.20),
        ("33/33/33 — equal", 1/3, 1/3, 1/3),
    ]

    print(f"\n{'config':30s}  {'CAGR':>7s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    rows = []
    for name, wm, wb, wk in weight_grids:
        combined = wm * mom_m + wb * bot_m + wk * brk_m
        eq = (1 + combined.fillna(0)).cumprod() * 100_000
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        ann_vol = combined.std() * np.sqrt(12)
        sharpe = (combined.mean() * 12) / ann_vol if ann_vol > 0 else 0
        dd = (eq / eq.cummax() - 1).min()
        rows.append((name, cagr, sharpe, dd))
        print(f"  {name:30s}  {cagr:>7.2%}  {sharpe:>7.2f}  {dd:>8.2%}")

    best = max(rows, key=lambda r: r[2])
    print(f"\n  WINNER: {best[0]}  (Sharpe {best[2]:.2f})")


def h7_risk_parity():
    print("\n" + "=" * 78)
    print("H7 — RISK-PARITY WEIGHTING (inverse-vol) vs FIXED 80/20")
    print("=" * 78)
    mom = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                            lookback_months=12, top_n=5)
    mom_m = mom.monthly_returns
    bot_m, _ = _strategy_returns_from_csv("bottom_catch_triggers.csv", "score", 0.65)
    bot_m = bot_m.reindex(mom_m.index).fillna(0)

    # Fixed 80/20
    fixed = 0.8 * mom_m + 0.2 * bot_m

    # Risk-parity: weight inversely to trailing 12-month vol
    mom_vol = mom_m.rolling(12).std()
    bot_vol = bot_m.rolling(12).std().replace(0, np.nan)
    inv_mom = 1 / mom_vol
    inv_bot = 1 / bot_vol
    sum_inv = inv_mom + inv_bot
    w_mom = (inv_mom / sum_inv).fillna(0.8)
    w_bot = (inv_bot / sum_inv).fillna(0.2)
    rp = w_mom.shift(1) * mom_m + w_bot.shift(1) * bot_m

    for name, series in [("Fixed 80/20", fixed), ("Risk-parity", rp)]:
        eq = (1 + series.fillna(0)).cumprod() * 100_000
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        sharpe = series.mean() * 12 / (series.std() * np.sqrt(12)) if series.std() > 0 else 0
        dd = (eq / eq.cummax() - 1).min()
        print(f"  {name:14s}  CAGR {cagr:>7.2%}  Sharpe {sharpe:>5.2f}  MaxDD {dd:>7.2%}")


def main():
    h5_dd_filter()
    h6_three_sleeve_ensemble()
    h7_risk_parity()


if __name__ == "__main__":
    main()

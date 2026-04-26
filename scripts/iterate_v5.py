"""v0.5 — walk-forward the v0.4 winners. Don't deploy without out-of-sample proof.

Train: 2015-01 to 2020-12  |  Test: 2021-01 to 2025-04

For each candidate config, compute the same risk-parity / fixed weighting on
the TEST period only. If Sharpe stays above 1.0 with <40% decay, deploy.
Otherwise the in-sample result was a fit.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def _stats(returns_series, label=""):
    eq = (1 + returns_series.fillna(0)).cumprod() * 100_000
    if len(eq) < 12:
        return None
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = returns_series.mean() * 12 / (returns_series.std() * np.sqrt(12)) if returns_series.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": float(cagr), "sharpe": float(sharpe), "maxdd": float(dd)}


def _bot_monthly_for_period(start, end, score_min=0.65):
    df = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    df = df[(df["date"] >= start) & (df["date"] < end) & (df["score"] >= score_min)]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp("M")
    return df.groupby("month")["ret_20d"].mean()


def _brk_monthly_for_period(start, end, score_min=0.7):
    df = pd.read_csv(REPORTS / "breakout_52w_triggers.csv", parse_dates=["date"])
    df = df[(df["date"] >= start) & (df["date"] < end) & (df["score"] >= score_min)]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp("M")
    return df.groupby("month")["ret_20d"].mean()


def run(period_name, start, end):
    print("\n" + "=" * 78)
    print(f"{period_name}: {start} to {end}")
    print("=" * 78)

    mom = backtest_momentum(DEFAULT_LIQUID_50, start=start, end=end,
                            lookback_months=12, top_n=5)
    mom_m = mom.monthly_returns
    bot_m = _bot_monthly_for_period(start, end).reindex(mom_m.index).fillna(0)
    brk_m = _brk_monthly_for_period(start, end).reindex(mom_m.index).fillna(0)

    rows = []
    rows.append(_stats(mom_m, "momentum-only (100/0/0)"))
    rows.append(_stats(0.8 * mom_m + 0.2 * bot_m, "fixed 80/20"))
    rows.append(_stats(0.7 * mom_m + 0.2 * bot_m + 0.1 * brk_m, "3-sleeve 70/20/10"))
    rows.append(_stats(0.6 * mom_m + 0.3 * bot_m + 0.1 * brk_m, "3-sleeve 60/30/10"))
    rows.append(_stats((mom_m + bot_m + brk_m) / 3, "3-sleeve equal 33/33/33"))

    # Risk-parity 2-sleeve
    mom_vol = mom_m.rolling(12).std()
    bot_vol = bot_m.rolling(12).std().replace(0, np.nan)
    inv_m, inv_b = 1 / mom_vol, 1 / bot_vol
    s = inv_m + inv_b
    wm = (inv_m / s).fillna(0.8)
    wb = (inv_b / s).fillna(0.2)
    rp2 = wm.shift(1) * mom_m + wb.shift(1) * bot_m
    rows.append(_stats(rp2, "risk-parity 2-sleeve"))

    # Risk-parity 3-sleeve
    brk_vol = brk_m.rolling(12).std().replace(0, np.nan)
    inv_k = 1 / brk_vol
    s3 = inv_m + inv_b + inv_k
    wm3 = (inv_m / s3).fillna(1/3)
    wb3 = (inv_b / s3).fillna(1/3)
    wk3 = (inv_k / s3).fillna(1/3)
    rp3 = wm3.shift(1) * mom_m + wb3.shift(1) * bot_m + wk3.shift(1) * brk_m
    rows.append(_stats(rp3, "risk-parity 3-sleeve"))

    print(f"\n{'config':28s}  {'CAGR':>7s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    for r in rows:
        if r is None:
            continue
        print(f"  {r['label']:28s}  {r['cagr']:>7.2%}  {r['sharpe']:>7.2f}  {r['maxdd']:>8.2%}")
    return rows


def main():
    train = run("TRAIN (in-sample)", "2015-01-01", "2020-12-31")
    test = run("TEST  (out-of-sample)", "2021-01-01", "2025-04-30")

    print("\n" + "=" * 78)
    print("DECAY ANALYSIS  (Sharpe in - Sharpe out)")
    print("=" * 78)
    print(f"\n{'config':28s}  {'in_sharpe':>9s}  {'out_sharpe':>10s}  {'decay':>7s}  {'verdict':>20s}")
    for ti, te in zip(train, test):
        if ti is None or te is None:
            continue
        decay = (ti["sharpe"] - te["sharpe"]) / ti["sharpe"] if ti["sharpe"] != 0 else float("nan")
        if te["sharpe"] > 1.0 and decay < 0.4:
            verdict = "DEPLOY"
        elif te["sharpe"] > 0.5 and decay < 0.6:
            verdict = "caution"
        else:
            verdict = "REJECT (overfit)"
        print(f"  {ti['label']:28s}  {ti['sharpe']:>9.2f}  {te['sharpe']:>10.2f}  {decay:>+6.1%}  {verdict:>20s}")


if __name__ == "__main__":
    main()

"""v0.3 strategy iteration. Tests four hypotheses in one script.

H1: Does the bottom-catch signal show time decay (post-2020)?
H2: Does a 52-week breakout signal carry edge?
H3: How correlated is bottom-catch P&L with SPY (diversification check)?
H4: How does momentum + bottom-catch combined compare to momentum alone?
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_ohlcv, fetch_history
from trader.signals import bottom_catch_score, breakout_52w_score
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def h1_bottom_catch_decay():
    """Reuse the trigger CSV; segment forward returns by year."""
    print("\n" + "=" * 78)
    print("H1 — BOTTOM-CATCH SIGNAL TIME DECAY")
    print("=" * 78)
    csv = REPORTS / "bottom_catch_triggers.csv"
    if not csv.exists():
        print("  bottom_catch_triggers.csv not found — run run_bottom_catch_backtest.py first")
        return
    df = pd.read_csv(csv, parse_dates=["date"])
    df["year"] = df["date"].dt.year
    by_year = df.groupby("year")["ret_20d"].agg(["mean", "count"])
    by_year["win_rate"] = df.groupby("year")["ret_20d"].apply(lambda s: (s > 0).mean())
    print("\n20-day forward return by year:")
    print(by_year.to_string(formatters={"mean": "{:+.2%}".format, "win_rate": "{:.1%}".format}))

    early = df[df["year"] < 2021]["ret_20d"]
    late = df[df["year"] >= 2021]["ret_20d"]
    print(f"\n2015-2020: mean {early.mean():+.2%}, win {(early > 0).mean():.1%}, n={len(early)}")
    print(f"2021-2025: mean {late.mean():+.2%}, win {(late > 0).mean():.1%}, n={len(late)}")
    print(f"Decay (late - early): {late.mean() - early.mean():+.2%}")
    if late.mean() > 0.005:
        print("  VERDICT: signal HOLDS in recent period.")
    elif late.mean() > 0:
        print("  VERDICT: signal WEAKENED but still positive.")
    else:
        print("  VERDICT: signal DECAYED — needs rework or retirement.")


def h2_breakout_signal():
    """Forward-return test of 52-week breakout signal across the universe."""
    print("\n" + "=" * 78)
    print("H2 — 52-WEEK BREAKOUT SIGNAL EDGE TEST")
    print("=" * 78)

    all_recs = []
    for t in DEFAULT_LIQUID_50:
        try:
            df = fetch_ohlcv(t, start="2014-01-01", end="2025-04-30")
        except Exception:
            continue
        closes = df["Close"]
        if len(closes) < 260:
            continue
        for i in range(252, len(closes) - 60):
            window = closes.iloc[: i + 1]
            score, comp = breakout_52w_score(window)
            if score < 0.7:  # require near-high + positive day
                continue
            entry = float(closes.iloc[i])
            recs = {"ticker": t, "date": df.index[i], "score": score, **comp, "entry": entry}
            for h in (5, 10, 20, 60):
                recs[f"ret_{h}d"] = float(closes.iloc[i + h]) / entry - 1
            all_recs.append(recs)
    if not all_recs:
        print("  no triggers")
        return
    df = pd.DataFrame(all_recs)
    print(f"  triggers: {len(df)} across {df['ticker'].nunique()} tickers")
    print(f"\n{'horizon':>8s}  {'mean':>8s}  {'win':>6s}")
    for h in (5, 10, 20, 60):
        m = df[f"ret_{h}d"].mean()
        w = (df[f"ret_{h}d"] > 0).mean()
        print(f"  {h:>3d}d   {m:>+7.2%}  {w:>5.1%}")
    df.to_csv(REPORTS / "breakout_52w_triggers.csv", index=False)
    if df["ret_20d"].mean() > 0.005 and (df["ret_20d"] > 0).mean() > 0.55:
        print("  VERDICT: signal carries edge — worth adding as 3rd sleeve.")
    else:
        print("  VERDICT: edge too weak to add as a sleeve.")


def h3_bottom_catch_correlation():
    """Bottom-catch trade SPY-correlation: build a daily P&L series, correlate with SPY."""
    print("\n" + "=" * 78)
    print("H3 — BOTTOM-CATCH DIVERSIFICATION CHECK (SPY correlation)")
    print("=" * 78)
    csv = REPORTS / "bottom_catch_triggers.csv"
    if not csv.exists():
        print("  bottom_catch_triggers.csv not found")
        return
    df = pd.read_csv(csv, parse_dates=["date"])

    # Build a daily P&L proxy: every trigger contributes (5d return / 5) to each
    # of the 5 days following it. Aggregate across all triggers → daily series.
    daily_pnl = {}
    for _, row in df.iterrows():
        per_day = row["ret_5d"] / 5
        for d in range(1, 6):
            day = (row["date"] + pd.Timedelta(days=d))
            daily_pnl[day] = daily_pnl.get(day, 0) + per_day
    pnl_series = pd.Series(daily_pnl).sort_index()
    pnl_series.index = pd.to_datetime(pnl_series.index)

    spy = fetch_history(["SPY"], start="2015-01-01", end="2025-04-30")["SPY"]
    spy_ret = spy.pct_change().dropna()

    aligned = pd.concat([pnl_series.rename("bottom"), spy_ret.rename("spy")], axis=1).dropna()
    if len(aligned) < 100:
        print("  too few overlapping days for correlation")
        return
    corr = aligned["bottom"].corr(aligned["spy"])
    print(f"  N overlapping days: {len(aligned)}")
    print(f"  Correlation(bottom-catch P&L, SPY return): {corr:+.3f}")
    if abs(corr) < 0.3:
        print("  VERDICT: low correlation — bottom-catch adds real diversification.")
    elif corr > 0.5:
        print("  VERDICT: bottom-catch is a beta-clone of SPY — limited diversification benefit.")
    else:
        print("  VERDICT: moderate correlation — some diversification.")


def h4_combined_ensemble():
    """Combined backtest: momentum sleeve + bottom-catch trade returns aggregated."""
    print("\n" + "=" * 78)
    print("H4 — COMBINED ENSEMBLE: MOMENTUM 80% + BOTTOM-CATCH 20%")
    print("=" * 78)
    from trader.backtest import backtest_momentum
    csv = REPORTS / "bottom_catch_triggers.csv"
    if not csv.exists():
        print("  no bottom-catch CSV")
        return

    mom = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                            lookback_months=12, top_n=5)
    mom_monthly = mom.monthly_returns

    df = pd.read_csv(csv, parse_dates=["date"])
    df = df[df["score"] >= 0.65]  # apply our new threshold
    # Each trigger → attribute its 20d return to the month of entry
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp("M")
    bot_monthly = df.groupby("month")["ret_20d"].mean().reindex(mom_monthly.index).fillna(0)
    # Scale: each month, bottom-catch sleeve uses 20% allocation; if no triggers, contribute 0
    bot_monthly_contrib = bot_monthly * 0.20
    mom_monthly_contrib = mom_monthly * 0.80

    combined = mom_monthly_contrib + bot_monthly_contrib
    combined_eq = (1 + combined.fillna(0)).cumprod() * 100_000

    cagr = (combined_eq.iloc[-1] / combined_eq.iloc[0]) ** (1 / 10.25) - 1
    sharpe = combined.mean() * 12 / (combined.std() * (12 ** 0.5)) if combined.std() > 0 else 0
    dd = (combined_eq / combined_eq.cummax() - 1).min()

    print(f"  Momentum-only:    CAGR {mom.stats()['cagr']:.2%}  Sharpe {mom.stats()['sharpe']:.2f}  MaxDD {mom.stats()['max_drawdown']:.2%}")
    print(f"  Combined 80/20:   CAGR {cagr:.2%}  Sharpe {sharpe:.2f}  MaxDD {dd:.2%}")
    delta_sharpe = sharpe - mom.stats()["sharpe"]
    print(f"  Delta Sharpe:     {delta_sharpe:+.2f}")
    if delta_sharpe > 0.05:
        print("  VERDICT: ensemble IMPROVES Sharpe — keep both sleeves.")
    else:
        print("  VERDICT: ensemble does not improve risk-adjusted returns — consider momentum-only.")


def main():
    h1_bottom_catch_decay()
    h2_breakout_signal()
    h3_bottom_catch_correlation()
    h4_combined_ensemble()


if __name__ == "__main__":
    main()

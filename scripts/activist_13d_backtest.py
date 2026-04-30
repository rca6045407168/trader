"""Backtest activist 13D following strategy.

Strategy: when a known activist files a 13D, buy the target on the next
trading day. Hold for N days. Compute return vs SPY same period.

Aggregate stats:
  - Mean / median forward return at 30, 90, 180, 365 days
  - Mean / median excess return vs SPY same period
  - Hit rate (% positive)
  - Sharpe of equal-weight portfolio holding each new filing for N days
  - Per-activist breakdown: which activists actually generate alpha?

Uses src/trader/activist_signals.py for SEC EDGAR data and yfinance for prices.
"""
from __future__ import annotations

import sys
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from trader.activist_signals import fetch_all_activist_filings, ActivistFiling
from trader.data import fetch_history


HOLD_PERIODS = [30, 90, 180, 365]


def _ticker_for_yf(ticker: str) -> str:
    """SEC uses dot tickers (BRK.B); yfinance uses dash (BRK-B)."""
    return ticker.replace(".", "-")


def compute_filing_returns(filing: ActivistFiling,
                           price_data: dict[str, pd.Series],
                           spy: pd.Series) -> dict:
    """Compute returns at HOLD_PERIODS days from filing date.
    Returns dict of {hold_days: {'ret', 'spy_ret', 'excess'}} or empty if missing data."""
    ticker = _ticker_for_yf(filing.target_ticker)
    if ticker not in price_data or price_data[ticker].empty:
        return {}
    s = price_data[ticker]
    file_ts = pd.Timestamp(filing.file_date.date())
    # Find next trading day at or after file_date
    try:
        idx_buy = s.index.searchsorted(file_ts, side="right")
        if idx_buy >= len(s):
            return {}
        buy_price = float(s.iloc[idx_buy])
        buy_date = s.index[idx_buy]
    except Exception:
        return {}
    if buy_price <= 0:
        return {}
    # SPY at buy_date
    try:
        spy_idx_buy = spy.index.searchsorted(buy_date, side="left")
        if spy_idx_buy >= len(spy):
            return {}
        spy_buy = float(spy.iloc[spy_idx_buy])
    except Exception:
        return {}
    out = {}
    for hold_days in HOLD_PERIODS:
        sell_date = buy_date + pd.Timedelta(days=hold_days)
        try:
            sell_idx = s.index.searchsorted(sell_date, side="right") - 1
            if sell_idx < 0 or sell_idx >= len(s):
                continue
            sell_price = float(s.iloc[sell_idx])
            ret = sell_price / buy_price - 1
        except Exception:
            continue
        try:
            spy_sell_idx = spy.index.searchsorted(sell_date, side="right") - 1
            if spy_sell_idx < 0 or spy_sell_idx >= len(spy):
                continue
            spy_sell = float(spy.iloc[spy_sell_idx])
            spy_ret = spy_sell / spy_buy - 1
        except Exception:
            continue
        out[hold_days] = {"ret": ret, "spy_ret": spy_ret, "excess": ret - spy_ret}
    return out


def main():
    print("=" * 80)
    print("ACTIVIST 13D STRATEGY BACKTEST")
    print("=" * 80)
    print()
    print("Pulling all 13D filings from known activists 2018-2026...")
    filings = fetch_all_activist_filings("2018-01-01", "2026-04-29")
    print(f"  Total: {len(filings)} initial 13D filings\n")

    # Get unique tickers + earliest/latest dates
    tickers = sorted(set(_ticker_for_yf(f.target_ticker) for f in filings))
    earliest = min(f.file_date for f in filings)
    latest = max(f.file_date for f in filings)
    # Pad: need 365 days after latest filing for full holding period
    end_str = (latest + timedelta(days=400)).strftime("%Y-%m-%d")
    end_str = min(end_str, datetime.now().strftime("%Y-%m-%d"))
    start_str = (earliest - timedelta(days=10)).strftime("%Y-%m-%d")

    print(f"Fetching prices for {len(tickers)} tickers from {start_str} to {end_str}...")
    try:
        prices = fetch_history(tickers + ["SPY"], start=start_str, end=end_str)
    except Exception as e:
        print(f"Price fetch failed: {e}")
        return 1
    if prices.empty:
        print("No price data.")
        return 1

    # Convert to per-ticker dict, drop NaN
    price_data = {}
    for t in tickers:
        if t in prices.columns:
            s = prices[t].dropna()
            if len(s) > 30:
                price_data[t] = s
    spy = prices["SPY"].dropna() if "SPY" in prices.columns else pd.Series(dtype=float)
    if spy.empty:
        print("No SPY data.")
        return 1

    print(f"  {len(price_data)} tickers with data; {len(filings) - len(price_data)} missing/delisted\n")

    # Compute returns for each filing
    results = []
    for f in filings:
        r = compute_filing_returns(f, price_data, spy)
        if r:
            results.append({"filing": f, "returns": r})

    print(f"Computable filings: {len(results)} of {len(filings)}\n")

    # Aggregate stats per holding period
    print("-" * 80)
    print(f"{'Hold (days)':<14s} {'mean ret':>10s} {'median ret':>10s} {'mean spy':>10s} {'mean excess':>12s} {'hit rate':>10s} {'Sharpe (ann)':>12s}")
    print("-" * 80)
    for hold in HOLD_PERIODS:
        filings_with = [r for r in results if hold in r["returns"]]
        if not filings_with:
            continue
        rets = [r["returns"][hold]["ret"] for r in filings_with]
        spy_rets = [r["returns"][hold]["spy_ret"] for r in filings_with]
        excess = [r["returns"][hold]["excess"] for r in filings_with]
        hit_rate = sum(1 for x in excess if x > 0) / len(excess)
        # Annualized Sharpe (assuming each event is independent)
        if len(rets) >= 5 and statistics.stdev(rets) > 0:
            mean_ret = statistics.mean(rets)
            std_ret = statistics.stdev(rets)
            ann_factor = (252 / hold) ** 0.5
            sharpe = (mean_ret / std_ret) * ann_factor * (252 / hold) ** 0.5
            # Above formula is wrong; correct is: Sharpe_ann = (mean_ret / hold) * 252 / (std_ret / hold * sqrt(252))
            # Simpler: convert to daily, then annualize
            daily_mean = mean_ret / hold
            daily_std = std_ret / (hold ** 0.5)  # std of N-day returns ≈ daily_std * sqrt(N)
            sharpe = (daily_mean * 252) / (daily_std * (252 ** 0.5)) if daily_std > 0 else 0
        else:
            sharpe = 0
        print(f"{hold:>10d}     {statistics.mean(rets)*100:>+8.2f}%  {statistics.median(rets)*100:>+8.2f}%  {statistics.mean(spy_rets)*100:>+8.2f}%  {statistics.mean(excess)*100:>+10.2f}pp  {hit_rate*100:>7.1f}%  {sharpe:>+10.2f}")
    print()

    # Per-activist breakdown at 180 days
    print("-" * 80)
    print(f"{'Activist':<40s} {'N':>4s} {'mean 180d':>10s} {'mean excess':>12s} {'hit rate':>10s}")
    print("-" * 80)
    by_activist = defaultdict(list)
    for r in results:
        if 180 in r["returns"]:
            by_activist[r["filing"].activist].append(r["returns"][180])
    rows = []
    for act, rs in by_activist.items():
        if len(rs) < 2:
            continue
        mr = statistics.mean(x["ret"] for x in rs)
        me = statistics.mean(x["excess"] for x in rs)
        hr = sum(1 for x in rs if x["excess"] > 0) / len(rs)
        rows.append((act, len(rs), mr, me, hr))
    rows.sort(key=lambda r: -r[3])  # by mean excess descending
    for act, n, mr, me, hr in rows:
        print(f"{act:<40s} {n:>4d}  {mr*100:>+8.2f}%  {me*100:>+10.2f}pp  {hr*100:>7.1f}%")

    print()
    # Best vs worst per-activist
    if rows:
        best = rows[0]
        worst = rows[-1]
        print(f"BEST:  {best[0]} → +{best[3]*100:.1f}pp excess vs SPY (180d)")
        print(f"WORST: {worst[0]} → {worst[3]*100:+.1f}pp excess vs SPY (180d)")

    # Final verdict
    print()
    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    if 180 in HOLD_PERIODS:
        all_180 = [r["returns"][180]["excess"] for r in results if 180 in r["returns"]]
        if all_180:
            mean_excess = statistics.mean(all_180)
            mean_excess_ann = mean_excess * (365 / 180)
            print(f"Mean 180-day excess return: {mean_excess*100:+.2f}pp")
            print(f"Annualized excess return: {mean_excess_ann*100:+.2f}pp")
            print(f"Sample size: {len(all_180)} filings")
            if mean_excess > 0.03:  # 3pp threshold
                print("✓ Edge appears real. Worth deploying as a sleeve.")
            elif mean_excess > 0:
                print("~ Edge marginal. Worth tracking, may not be worth complexity cost.")
            else:
                print("✗ No edge measurable. Strategy fails.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

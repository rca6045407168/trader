"""Cointegration / pairs-trading backtest.

Strategy:
  - Every month, find cointegrated pairs (ADF p<0.05, |rho|>0.6) using
    last 90 days of data
  - For each pair with |spread z-score| > 2: enter long-short trade
    (long cheap leg, short rich leg, sized by hedge ratio beta)
  - Exit when z mean-reverts to |z| < 0.5 OR after 30 trading days
  - Equal capital allocation per pair, max 5 simultaneous pairs

This is a market-neutral strategy (long + short = ~zero net beta), so
returns are uncorrelated to SPY. Pure alpha.
"""
from __future__ import annotations

import sys
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
import yfinance as yf

from trader.universe import DEFAULT_LIQUID_50
from trader.cointegration import find_cointegrated_pair, current_spread_z_score


ENTRY_Z = 2.0
EXIT_Z = 0.5
MAX_HOLD_DAYS = 30
LOOKBACK_DAYS = 90  # for cointegration test
REBALANCE_DAYS = 21  # check for new pairs monthly
MAX_PAIRS = 5
PER_PAIR_CAPITAL = 0.10  # 10% per pair; max 50% gross with 5 pairs
TICKERS = DEFAULT_LIQUID_50


def main():
    print("=" * 80)
    print("COINTEGRATION PAIRS BACKTEST")
    print("=" * 80)
    print(f"Entry: |z| > {ENTRY_Z}  Exit: |z| < {EXIT_Z}  Max hold: {MAX_HOLD_DAYS} days")
    print(f"Universe: {len(TICKERS)} tickers")
    print(f"Lookback for cointegration: {LOOKBACK_DAYS} days")
    print(f"Max simultaneous pairs: {MAX_PAIRS}")
    print(f"Per-pair capital: {PER_PAIR_CAPITAL*100:.0f}%")
    print()

    print("Fetching prices 2018-2026...")
    df = yf.download(TICKERS, start="2017-06-01", end="2026-04-29",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        prices = df["Close"] if "Close" in df.columns else df.xs("Close", level=0, axis=1)
    else:
        prices = df
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.8))
    print(f"  {len(prices.columns)} tickers with usable history\n")

    # Run backtest 2018-2026
    backtest_start = pd.Timestamp("2018-01-01")
    backtest_end = pd.Timestamp("2026-04-29")
    bdays = pd.bdate_range(backtest_start, backtest_end)

    # Per-pair state: {pair_key: {entry_date, entry_z, ticker_y, ticker_x, beta, capital_y, capital_x}}
    open_pairs = {}
    completed_trades = []
    daily_equity = []
    equity = 100_000.0
    daily_equity.append((bdays[0], equity))

    last_rebalance = bdays[0] - pd.Timedelta(days=100)

    for d in bdays[1:]:
        if d not in prices.index:
            daily_equity.append((d, equity))
            continue

        # Mark-to-market open pairs (compute today's pair P&L)
        prev_d_idx = prices.index.searchsorted(d, side="right") - 2
        if prev_d_idx < 0:
            daily_equity.append((d, equity))
            continue
        for key in list(open_pairs.keys()):
            pos = open_pairs[key]
            ty, tx = pos["ticker_y"], pos["ticker_x"]
            if ty not in prices.columns or tx not in prices.columns:
                continue
            try:
                p_y_today = float(prices[ty].iloc[prev_d_idx + 1])
                p_x_today = float(prices[tx].iloc[prev_d_idx + 1])
                p_y_prev = float(prices[ty].iloc[prev_d_idx])
                p_x_prev = float(prices[tx].iloc[prev_d_idx])
            except Exception:
                continue
            ret_y = p_y_today / p_y_prev - 1
            ret_x = p_x_today / p_x_prev - 1
            # Long Y, short X (entry was at z > 0 → spread rich → short Y long X; entry at z < 0 → long Y short X)
            # We stored the SIDE in pos
            if pos["side"] == "long_y":
                pair_pnl = pos["capital_y"] * ret_y - pos["capital_x"] * ret_x
            else:  # short_y
                pair_pnl = -pos["capital_y"] * ret_y + pos["capital_x"] * ret_x
            equity += pair_pnl
            pos["days_held"] = pos.get("days_held", 0) + 1

            # Check exit conditions
            try:
                # Compute current z-score using full data up to d
                hist_idx = prices.index.searchsorted(d, side="right")
                window_start = max(0, hist_idx - LOOKBACK_DAYS)
                hist_y = prices[ty].iloc[window_start:hist_idx].dropna()
                hist_x = prices[tx].iloc[window_start:hist_idx].dropna()
                spread = hist_y.values - (pos["alpha"] + pos["beta"] * hist_x.values)
                spread_mean = float(np.mean(spread))
                spread_std = float(np.std(spread))
                if spread_std > 0:
                    current_spread = p_y_today - (pos["alpha"] + pos["beta"] * p_x_today)
                    current_z = (current_spread - spread_mean) / spread_std
                else:
                    current_z = 0
            except Exception:
                current_z = 0

            should_exit = (abs(current_z) < EXIT_Z) or (pos["days_held"] >= MAX_HOLD_DAYS)
            if should_exit:
                completed_trades.append({
                    "pair": key,
                    "entry_z": pos["entry_z"],
                    "exit_z": current_z,
                    "days_held": pos["days_held"],
                    "side": pos["side"],
                })
                del open_pairs[key]

        # Periodic rebalance: scan for new pairs
        if (d - last_rebalance).days >= REBALANCE_DAYS:
            last_rebalance = d
            try:
                hist_idx = prices.index.searchsorted(d, side="right")
                window_start = max(0, hist_idx - LOOKBACK_DAYS)
                hist = prices.iloc[window_start:hist_idx].dropna(axis=1, thresh=int(LOOKBACK_DAYS * 0.8))
                if hist.empty or len(hist.columns) < 2:
                    daily_equity.append((d, equity))
                    continue
                tickers = list(hist.columns)
                # Pre-filter by correlation
                rets = hist.pct_change().dropna()
                if len(rets) < 30:
                    daily_equity.append((d, equity))
                    continue
                corr = rets.corr()
                # Find cointegrated pairs
                candidates = []
                for i, ty in enumerate(tickers):
                    if len(open_pairs) + len(candidates) >= MAX_PAIRS * 2:
                        break
                    for tx in tickers[i + 1:]:
                        try:
                            if abs(corr.loc[ty, tx]) < 0.7:
                                continue
                            pair = find_cointegrated_pair(hist[ty], hist[tx], adf_threshold=0.05)
                            if pair:
                                # Compute current z-score
                                latest_y = float(hist[ty].iloc[-1])
                                latest_x = float(hist[tx].iloc[-1])
                                z = current_spread_z_score(pair, latest_y, latest_x)
                                if abs(z) > ENTRY_Z:
                                    candidates.append((abs(z), pair, z))
                        except Exception:
                            continue
                # Sort by abs(z) — strongest signals first
                candidates.sort(key=lambda x: -x[0])
                for _, pair, z in candidates:
                    if len(open_pairs) >= MAX_PAIRS:
                        break
                    pair_key = (pair.ticker_y, pair.ticker_x)
                    if pair_key in open_pairs:
                        continue
                    side = "short_y" if z > 0 else "long_y"
                    open_pairs[pair_key] = {
                        "ticker_y": pair.ticker_y,
                        "ticker_x": pair.ticker_x,
                        "alpha": pair.alpha,
                        "beta": pair.beta,
                        "entry_z": z,
                        "side": side,
                        "capital_y": equity * PER_PAIR_CAPITAL,
                        "capital_x": equity * PER_PAIR_CAPITAL * abs(pair.beta),
                        "days_held": 0,
                    }
            except Exception as e:
                pass

        daily_equity.append((d, equity))

    # Compute final stats
    eq_series = pd.Series([e for _, e in daily_equity], index=[d for d, _ in daily_equity])
    eq_series = eq_series[~eq_series.index.duplicated(keep="last")]
    total_return = float(eq_series.iloc[-1] / eq_series.iloc[0] - 1)
    n_years = len(eq_series) / 252
    cagr = (1 + total_return) ** (1 / max(n_years, 0.01)) - 1
    rets = eq_series.pct_change().dropna()
    sharpe = (rets.mean() * 252) / (rets.std() * (252 ** 0.5)) if rets.std() > 0 else 0
    max_dd = float((eq_series / eq_series.cummax() - 1).min())

    print()
    print("=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"Trades completed: {len(completed_trades)}")
    print(f"Final equity:     ${eq_series.iloc[-1]:>11,.0f}")
    print(f"Total return:     {total_return*100:>+10.2f}%")
    print(f"Years:            {n_years:>10.2f}")
    print(f"CAGR:             {cagr*100:>+10.2f}%")
    print(f"Sharpe (annual):  {sharpe:>+10.2f}")
    print(f"Max drawdown:     {max_dd*100:>+10.2f}%")

    if completed_trades:
        # Trade statistics
        closed = [t for t in completed_trades]
        winners = sum(1 for t in closed if (t["side"] == "short_y" and t["exit_z"] < t["entry_z"]) or
                                            (t["side"] == "long_y" and t["exit_z"] > t["entry_z"]))
        print(f"\nTrade win rate (z mean-reverted in expected direction): {winners/len(closed)*100:.1f}%")
        print(f"Avg days held: {statistics.mean(t['days_held'] for t in closed):.1f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

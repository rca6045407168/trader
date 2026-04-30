"""Small-cap PEAD (Post-Earnings Announcement Drift) backtest.

Hypothesis (Bernard-Thomas 1989, Foster-Olsen-Shevlin 1984): stocks that
beat earnings expectations drift up for 60+ days post-announcement. Effect
is STRONGER on small-caps with low analyst coverage (less efficient pricing).

Methodology (proxy approach since true earnings-surprise data requires
paid feeds):
  1. Universe: a sample of small-cap names (using S&P 600 small-cap proxy
     via DEFAULT_LIQUID_50 for v1 — small-cap v2 in next iteration)
  2. Trigger: a single-day price move >+5% with >2x avg volume = strong
     earnings-surprise proxy
  3. Hold: 60 trading days post-trigger (the documented PEAD window)
  4. Compare to SPY same period

If edge is real:
  - Mean 60-day excess return > +5pp
  - Hit rate > 55%
  - Sharpe > 1.0 annualized

If edge fails this test, we kill the sleeve.
"""
from __future__ import annotations

import sys
import statistics
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import yfinance as yf

from trader.universe import DEFAULT_LIQUID_50

# Use a broader universe to find small-caps. Russell 2000 ETF proxies aren't
# individually tradable; we approximate with a curated small-cap list.
# These were S&P 600 / Russell 2000 names in 2018-2024 with reasonable
# liquidity. Mix of sectors to avoid concentration.
SMALLCAP_UNIVERSE = [
    "AMC", "PLBY", "GME", "BBBY", "TLRY", "SAVA", "OCGN", "MARA", "RIOT", "NCLH",
    "FUBO", "CLOV", "WISH", "SOFI", "DKNG", "PLTR", "SPCE", "OPEN", "ROOT",
    "JOBY", "LCID", "RIVN", "NIO", "XPEV", "LI", "GOTU", "TAL", "EDU",
    "BABA", "BIDU", "JD", "PDD", "TME", "VIPS", "YMM", "DIDI",
    "BYND", "OAT", "PTON", "TWST", "CRSP", "EDIT", "NTLA", "BEAM", "VERV",
    "RPRX", "LEGN", "CYTK", "MRTX", "ARWR",
    "U", "CRWD", "ZS", "SNOW", "NET", "OKTA", "DDOG", "ZM", "DOCU", "CFLT",
    "MQ", "FIGS", "AFRM", "UPST", "HOOD", "COIN",
    "MNST", "EXPE", "TXG", "DXCM", "ALGN",
]

START = "2018-01-01"
END = "2026-04-29"
TRIGGER_RET_THRESHOLD = 0.05  # +5% single-day move
TRIGGER_VOL_MULT = 2.0  # >2x avg daily volume
FWD_DAYS = 60
VOL_LOOKBACK = 20
COOLDOWN_DAYS = 30  # don't trigger again for same name within 30 days


def fetch_universe(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV per ticker. Returns dict of ticker -> DataFrame with Close + Volume."""
    out = {}
    for t in tickers:
        try:
            df = yf.download(t, start=START, end=END, auto_adjust=True, progress=False)
            if not df.empty and "Close" in df.columns and "Volume" in df.columns:
                # Flatten if multi-index
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                out[t] = df[["Close", "Volume"]].dropna()
        except Exception:
            continue
    return out


def find_triggers(prices: dict[str, pd.DataFrame]) -> list[dict]:
    """Find earnings-surprise-proxy triggers across the universe.

    Trigger: ret > +5% AND volume > 2x 20-day rolling avg.
    """
    triggers = []
    for ticker, df in prices.items():
        if len(df) < VOL_LOOKBACK + FWD_DAYS + 5:
            continue
        close = df["Close"]
        vol = df["Volume"]
        ret = close.pct_change()
        avg_vol = vol.rolling(VOL_LOOKBACK).mean().shift(1)
        rel_vol = vol / avg_vol
        # Identify trigger days
        trigger_mask = (ret > TRIGGER_RET_THRESHOLD) & (rel_vol > TRIGGER_VOL_MULT)
        trigger_dates = ret.index[trigger_mask]

        # Apply cooldown
        last_trigger = None
        for d in trigger_dates:
            if last_trigger is not None and (d - last_trigger).days < COOLDOWN_DAYS:
                continue
            last_trigger = d
            try:
                trigger_idx = close.index.get_loc(d)
            except Exception:
                continue
            if trigger_idx + FWD_DAYS >= len(close):
                continue
            buy_price = float(close.iloc[trigger_idx])
            sell_price = float(close.iloc[trigger_idx + FWD_DAYS])
            ret_60d = sell_price / buy_price - 1
            triggers.append({
                "ticker": ticker,
                "trigger_date": d,
                "trigger_ret": float(ret.iloc[trigger_idx]),
                "trigger_relvol": float(rel_vol.iloc[trigger_idx]),
                "ret_60d": ret_60d,
                "buy_idx": trigger_idx,
            })
    return triggers


def main():
    print("=" * 80)
    print("SMALL-CAP PEAD BACKTEST")
    print("=" * 80)
    print(f"Universe: {len(SMALLCAP_UNIVERSE)} small-cap names")
    print(f"Window: {START} to {END}")
    print(f"Trigger: >+{TRIGGER_RET_THRESHOLD:.0%} single-day with >{TRIGGER_VOL_MULT}x avg volume")
    print(f"Hold: {FWD_DAYS} trading days post-trigger")
    print()

    print("Fetching prices...")
    prices = fetch_universe(SMALLCAP_UNIVERSE)
    print(f"  {len(prices)} tickers with usable data\n")

    print("Finding triggers...")
    triggers = find_triggers(prices)
    print(f"  {len(triggers)} triggers found across universe\n")

    # Get SPY for benchmark
    try:
        spy_df = yf.download("SPY", start=START, end=END, auto_adjust=True, progress=False)
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df.columns = [c[0] for c in spy_df.columns]
        spy = spy_df["Close"].dropna()
    except Exception:
        spy = pd.Series(dtype=float)

    if spy.empty:
        print("No SPY data. Aborting.")
        return 1

    # Compute SPY return for each trigger's 60-day window
    for t in triggers:
        try:
            spy_buy_idx = spy.index.searchsorted(t["trigger_date"], side="right") - 1
            if spy_buy_idx < 0:
                continue
            spy_sell_idx = spy.index.searchsorted(
                t["trigger_date"] + pd.Timedelta(days=int(FWD_DAYS * 1.4)),
                side="right"
            ) - 1
            if spy_sell_idx < 0 or spy_sell_idx >= len(spy):
                continue
            spy_buy = float(spy.iloc[spy_buy_idx])
            spy_sell = float(spy.iloc[spy_sell_idx])
            t["spy_ret_60d"] = spy_sell / spy_buy - 1
            t["excess_60d"] = t["ret_60d"] - t["spy_ret_60d"]
        except Exception:
            continue

    valid = [t for t in triggers if "excess_60d" in t]
    print(f"Triggers with full 60d window: {len(valid)}\n")

    if len(valid) < 5:
        print("Insufficient triggers for meaningful stats.")
        return 1

    rets = [t["ret_60d"] for t in valid]
    spy_rets = [t["spy_ret_60d"] for t in valid]
    excess = [t["excess_60d"] for t in valid]

    mean_ret = statistics.mean(rets)
    median_ret = statistics.median(rets)
    mean_spy = statistics.mean(spy_rets)
    mean_excess = statistics.mean(excess)
    median_excess = statistics.median(excess)
    hit_rate = sum(1 for x in excess if x > 0) / len(excess)

    # Annualized stats
    daily_mean = mean_ret / FWD_DAYS
    daily_std = statistics.stdev(rets) / (FWD_DAYS ** 0.5) if len(rets) > 1 else 0
    sharpe_ann = (daily_mean * 252) / (daily_std * (252 ** 0.5)) if daily_std > 0 else 0

    print("-" * 80)
    print("AGGREGATE RESULTS (60-day hold post-trigger)")
    print("-" * 80)
    print(f"  N triggers:          {len(valid)}")
    print(f"  Mean trigger return: {mean_ret*100:>+6.2f}%")
    print(f"  Median return:       {median_ret*100:>+6.2f}%")
    print(f"  Mean SPY return:     {mean_spy*100:>+6.2f}%")
    print(f"  Mean excess vs SPY:  {mean_excess*100:>+6.2f}pp")
    print(f"  Median excess:       {median_excess*100:>+6.2f}pp")
    print(f"  Hit rate (vs SPY):   {hit_rate*100:>5.1f}%")
    print(f"  Annualized Sharpe:   {sharpe_ann:>+6.2f}")
    print()

    # Best and worst triggers
    sorted_excess = sorted(valid, key=lambda x: -x["excess_60d"])
    print("TOP 5 by excess return:")
    for t in sorted_excess[:5]:
        print(f"  {t['trigger_date'].strftime('%Y-%m-%d')}  {t['ticker']:6s}  "
              f"trigger {t['trigger_ret']*100:>+5.1f}%  60d {t['ret_60d']*100:>+6.1f}%  "
              f"excess {t['excess_60d']*100:>+6.1f}pp")
    print("\nBOTTOM 5 by excess return:")
    for t in sorted_excess[-5:]:
        print(f"  {t['trigger_date'].strftime('%Y-%m-%d')}  {t['ticker']:6s}  "
              f"trigger {t['trigger_ret']*100:>+5.1f}%  60d {t['ret_60d']*100:>+6.1f}%  "
              f"excess {t['excess_60d']*100:>+6.1f}pp")

    # Verdict
    print()
    print("=" * 80)
    if mean_excess > 0.05 and hit_rate > 0.55:
        print(f"✓ EDGE LIKELY REAL: +{mean_excess*100:.1f}pp 60-day excess, {hit_rate*100:.0f}% hit rate.")
        print(f"  Annualized excess (assuming 6 holds/yr non-overlapping): "
              f"+{mean_excess*100*(252/FWD_DAYS):.1f}pp/yr theoretical max.")
        print(f"  Realistic deployable Sharpe: ~{sharpe_ann:.2f}")
    elif mean_excess > 0:
        print(f"~ EDGE MARGINAL: +{mean_excess*100:.1f}pp excess. Below the 5pp deployment threshold.")
    else:
        print(f"✗ NO EDGE: {mean_excess*100:.1f}pp excess. Strategy fails in this universe.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

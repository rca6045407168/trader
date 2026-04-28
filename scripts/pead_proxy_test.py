"""PEAD proxy test.

Identify "earnings-like jump days" (>3% move on >1.5x avg volume) for
DEFAULT_LIQUID_50 over 2020-2025, then compute 30-trading-day forward returns.
Compare against SPY random-30d baseline and non-trigger-day baseline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from trader.universe import DEFAULT_LIQUID_50  # noqa: E402

START = "2019-06-01"   # extra lookback so 20-day vol avg is ready by 2020-01-01
END = "2025-12-31"
TRIGGER_START = "2020-01-01"
TRIGGER_END = "2025-09-30"   # need 30d forward room
FWD = 30
VOL_LOOKBACK = 20
RET_THRESH = 0.03
VOL_MULT = 1.5


def fetch(tickers: list[str]) -> pd.DataFrame:
    print(f"[fetch] {len(tickers)} tickers {START} -> {END}")
    df = yf.download(
        tickers, start=START, end=END,
        auto_adjust=True, progress=False, threads=True, group_by="ticker",
    )
    return df


def per_ticker_triggers(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    try:
        sub = df[ticker].dropna(subset=["Close", "Volume"])
    except Exception:
        return pd.DataFrame()
    if sub.empty:
        return pd.DataFrame()
    close = sub["Close"]
    vol = sub["Volume"]
    ret = close.pct_change()
    avg_vol = vol.rolling(VOL_LOOKBACK).mean().shift(1)
    rel_vol = vol / avg_vol

    fwd_close = close.shift(-FWD)
    fwd_ret = (fwd_close / close) - 1.0

    mask_window = (sub.index >= TRIGGER_START) & (sub.index <= TRIGGER_END)
    trigger = mask_window & (ret > RET_THRESH) & (rel_vol > VOL_MULT) & fwd_ret.notna()
    non_trigger = mask_window & (~((ret > RET_THRESH) & (rel_vol > VOL_MULT))) & fwd_ret.notna()

    out_t = pd.DataFrame({
        "ticker": ticker, "date": sub.index[trigger],
        "day_ret": ret[trigger].values,
        "rel_vol": rel_vol[trigger].values,
        "fwd30": fwd_ret[trigger].values,
        "kind": "trigger",
    })
    # Sample non-trigger days (otherwise the array is huge)
    nt_idx = sub.index[non_trigger]
    if len(nt_idx) > 200:
        rng = np.random.default_rng(42 + hash(ticker) % 10_000)
        nt_idx = rng.choice(nt_idx, 200, replace=False)
    out_n = pd.DataFrame({
        "ticker": ticker, "date": nt_idx,
        "day_ret": ret.loc[nt_idx].values,
        "rel_vol": rel_vol.loc[nt_idx].values,
        "fwd30": fwd_ret.loc[nt_idx].values,
        "kind": "non_trigger",
    })
    return pd.concat([out_t, out_n], ignore_index=True)


def stats(label: str, returns: np.ndarray) -> dict:
    r = pd.Series(returns).dropna().values
    if len(r) == 0:
        return {"label": label, "n": 0}
    mean = float(np.mean(r))
    median = float(np.median(r))
    std = float(np.std(r, ddof=1)) if len(r) > 1 else float("nan")
    win = float(np.mean(r > 0))
    sharpe = mean / std if std and std > 0 else float("nan")
    return {"label": label, "n": len(r), "mean": mean, "median": median,
            "win": win, "std": std, "sharpe_per_trade": sharpe}


def fmt(s: dict) -> str:
    if s["n"] == 0:
        return f"{s['label']}: n=0"
    return (f"{s['label']:32s} n={s['n']:5d}  mean={s['mean']*100:+6.2f}%  "
            f"median={s['median']*100:+6.2f}%  win={s['win']*100:5.1f}%  "
            f"std={s['std']*100:5.2f}%  sharpe(per-trade)={s['sharpe_per_trade']:+.3f}")


def main() -> None:
    tickers = DEFAULT_LIQUID_50
    raw = fetch(tickers)

    rows = []
    missing = []
    for t in tickers:
        try:
            sub = raw[t]
        except Exception:
            missing.append(t)
            continue
        if sub.dropna(how="all").empty:
            missing.append(t)
            continue
        rows.append(per_ticker_triggers(raw, t))
    if missing:
        print(f"[warn] missing data: {missing}")

    all_df = pd.concat([r for r in rows if not r.empty], ignore_index=True)
    triggers = all_df[all_df["kind"] == "trigger"]
    non_trig = all_df[all_df["kind"] == "non_trigger"]

    print()
    print(f"Triggers found: {len(triggers)}  across {triggers['ticker'].nunique()} tickers")
    print(f"Avg triggers / ticker / yr: "
          f"{len(triggers) / max(triggers['ticker'].nunique(),1) / 5.75:.2f}")
    print(f"Trigger day_ret mean: {triggers['day_ret'].mean()*100:+.2f}%   "
          f"rel_vol mean: {triggers['rel_vol'].mean():.2f}x")

    # SPY random baseline
    spy = yf.download("SPY", start=START, end=END, auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy_close = spy["Close"].dropna()
    spy_fwd = (spy_close.shift(-FWD) / spy_close) - 1.0
    spy_window = spy_fwd.loc[TRIGGER_START:TRIGGER_END].dropna()
    rng = np.random.default_rng(7)
    sample_idx = rng.choice(spy_window.index, size=min(2000, len(spy_window)), replace=False)
    spy_sample = spy_window.loc[sample_idx].values

    print()
    print("=" * 110)
    print(fmt(stats("PEAD-PROXY (trigger fwd30)", triggers["fwd30"].values)))
    print(fmt(stats("Non-trigger sampled fwd30", non_trig["fwd30"].values)))
    print(fmt(stats("SPY random fwd30 baseline", spy_sample)))
    print("=" * 110)

    # Yearly breakdown
    triggers = triggers.copy()
    triggers["year"] = pd.to_datetime(triggers["date"]).dt.year
    yearly = triggers.groupby("year")["fwd30"].agg(["count", "mean", "median"])
    yearly[["mean", "median"]] *= 100
    print("\nBy year (PEAD-proxy fwd30):")
    print(yearly.to_string(float_format=lambda v: f"{v:+.2f}"))

    # CSV out for reproducibility
    out_dir = Path(__file__).resolve().parent.parent / "reports"
    out_dir.mkdir(exist_ok=True)
    triggers.to_csv(out_dir / "pead_proxy_triggers.csv", index=False)
    print(f"\nSaved {out_dir / 'pead_proxy_triggers.csv'}")


if __name__ == "__main__":
    main()

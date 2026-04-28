"""Test all shadow variants across multiple historical regimes.

The 3-month backfill (Jan-Apr 2026) was a mom-friendly bull market — aggressive
variants crushed it. But we want strategies that work across REGIMES, not just
the recent one. This script runs each variant through 5 known windows:

  - 2018-Q4 selloff (Powell pivot — momentum got hit)
  - 2020-Q1 COVID crash (everything sold off; momentum recovered fast)
  - 2022 bear (FAANG implosion, value rotation)
  - 2023 AI-rally (mega-cap tech concentration paid)
  - Recent 3 months (current regime)

For each variant + window: total return, Sharpe, MaxDD, alpha vs SPY.
End: ranking by Sharpe across regimes. Best variant is one that consistently
wins or ties, not one that dominates in one regime and crashes in others.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math
import statistics
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50
from trader.sectors import get_sector
from trader.anomalies import scan_anomalies


REGIMES = [
    ("2018-Q4 selloff",     pd.Timestamp("2018-09-01"), pd.Timestamp("2019-03-31")),
    ("2020-Q1 COVID",       pd.Timestamp("2020-01-15"), pd.Timestamp("2020-06-30")),
    ("2022 bear",           pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    ("2023 AI-rally",       pd.Timestamp("2023-04-01"), pd.Timestamp("2023-10-31")),
    ("recent 3 months",     pd.Timestamp.today() - pd.Timedelta(days=95), pd.Timestamp.today()),
]


def _momentum_picks_as_of(as_of, top_n=5, lookback_months=12, skip_months=1):
    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50, start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return []
    if prices.empty or len(prices) < L + S:
        return []
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    return rets.nlargest(top_n).index.tolist()


def variant_top5_eq_40(as_of):  # current LIVE: top-5, 40% allocation
    p = _momentum_picks_as_of(as_of, 5)
    return {x: 0.40 / len(p) for x in p} if p else {}


def variant_top5_eq_80(as_of):  # v0.5 fixed: top-5, 80%
    p = _momentum_picks_as_of(as_of, 5)
    return {x: 0.80 / len(p) for x in p} if p else {}


def variant_top3_eq_40(as_of):  # top-3, 40%
    p = _momentum_picks_as_of(as_of, 3)
    return {x: 0.40 / len(p) for x in p} if p else {}


def variant_top3_eq_80(as_of):  # top-3, 80% (most aggressive)
    p = _momentum_picks_as_of(as_of, 3)
    return {x: 0.80 / len(p) for x in p} if p else {}


def variant_top10_eq_80(as_of):  # top-10, 80%
    p = _momentum_picks_as_of(as_of, 10)
    return {x: 0.80 / len(p) for x in p} if p else {}


def variant_sector_cap_5_80(as_of):  # 1-per-sector, 5 names, 80%
    cands = _momentum_picks_as_of(as_of, 20)
    sel = []
    used = set()
    for t in cands:
        s = get_sector(t)
        if s in used:
            continue
        used.add(s)
        sel.append(t)
        if len(sel) >= 5:
            break
    return {x: 0.80 / len(sel) for x in sel} if sel else {}


def variant_top2_eq_80(as_of):
    p = _momentum_picks_as_of(as_of, 2)
    return {x: 0.80 / len(p) for x in p} if p else {}


def variant_top1_eq_80(as_of):
    p = _momentum_picks_as_of(as_of, 1)
    return {x: 0.80 for x in p} if p else {}


def variant_top3_eq_100(as_of):  # 100% all in (no bottom-catch reservation)
    p = _momentum_picks_as_of(as_of, 3)
    return {x: 1.00 / len(p) for x in p} if p else {}


VARIANTS = {
    "top5_eq_40 (curr LIVE pre-v3)": variant_top5_eq_40,
    "top5_eq_80 (v3.0)": variant_top5_eq_80,
    "top3_eq_40": variant_top3_eq_40,
    "top3_eq_80 (v3.1 LIVE)": variant_top3_eq_80,
    "top3_eq_100 (no cash)": variant_top3_eq_100,
    "top2_eq_80": variant_top2_eq_80,
    "top1_eq_80 (max concentration)": variant_top1_eq_80,
    "top10_eq_80": variant_top10_eq_80,
    "sector_cap_5_80": variant_sector_cap_5_80,
}


def replay_window(variant_name, fn, start, end):
    bdays = pd.bdate_range(start, end)
    decisions = []
    for d in bdays:
        next_d = d + pd.Timedelta(days=1)
        if next_d.month != d.month:
            t = fn(d)
            if t:
                decisions.append((d, t))
    if not decisions:
        return None

    all_t = {"SPY"}
    for _, t in decisions:
        all_t.update(t.keys())
    try:
        prices = fetch_history(sorted(all_t),
                              start=(start - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                              end=(end + pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    except Exception:
        return None
    daily_rets = prices.pct_change().fillna(0)
    daily_idx = prices.index
    weights = pd.DataFrame(0.0, index=daily_idx, columns=prices.columns)
    for i, (d, t) in enumerate(decisions):
        try:
            ei = daily_idx.searchsorted(d) + 1
            if ei >= len(daily_idx):
                continue
        except Exception:
            continue
        if i + 1 < len(decisions):
            xi = min(daily_idx.searchsorted(decisions[i+1][0]) + 1, len(daily_idx))
        else:
            xi = len(daily_idx)
        for sym, w in t.items():
            if sym in weights.columns:
                weights.iloc[ei:xi, weights.columns.get_loc(sym)] = w
    pr = (weights.shift(1) * daily_rets).sum(axis=1).fillna(0)
    pr = pr[pr.index >= start]
    if len(pr) < 5:
        return None
    eq = (1 + pr).cumprod() * 100_000
    bench = daily_rets["SPY"][daily_rets["SPY"].index >= start].fillna(0)
    bench_eq = (1 + bench).cumprod() * 100_000
    sd = float(pr.std())
    sharpe = (float(pr.mean()) * 252) / (sd * math.sqrt(252)) if sd > 0 else 0
    n = len(pr)
    cagr = (float(eq.iloc[-1]) / float(eq.iloc[0])) ** (252 / n) - 1
    bench_cagr = (float(bench_eq.iloc[-1]) / float(bench_eq.iloc[0])) ** (252 / n) - 1
    max_dd = float((eq / eq.cummax() - 1).min())
    return {"total_pct": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "cagr": cagr, "sharpe": sharpe, "max_dd": max_dd,
            "spy_total": float(bench_eq.iloc[-1] / bench_eq.iloc[0] - 1),
            "spy_cagr": bench_cagr, "n_days": n}


def main():
    print("=" * 110)
    print("REGIME STRESS TEST — variants across 5 historical windows")
    print("=" * 110)

    # results[variant_name][regime_name] = stats
    results = {v: {} for v in VARIANTS}

    for regime_name, start, end in REGIMES:
        print(f"\n>>> {regime_name}: {start.date()} to {end.date()}")
        for v_name, fn in VARIANTS.items():
            try:
                r = replay_window(v_name, fn, start, end)
                if r:
                    results[v_name][regime_name] = r
                    print(f"  {v_name:35s}  total {r['total_pct']*100:>+7.2f}%  CAGR {r['cagr']*100:>+7.1f}%  "
                          f"Sharpe {r['sharpe']:>+5.2f}  MaxDD {r['max_dd']*100:>+6.2f}%  "
                          f"SPY {r['spy_total']*100:>+7.2f}%")
            except Exception as e:
                print(f"  {v_name:35s}  FAILED: {type(e).__name__}: {e}")

    # Cross-regime ranking by mean Sharpe
    print("\n" + "=" * 110)
    print("CROSS-REGIME SUMMARY (mean Sharpe, mean CAGR, worst MaxDD)")
    print("=" * 110)
    summary = []
    for v_name, by_regime in results.items():
        if not by_regime:
            continue
        sharpes = [r["sharpe"] for r in by_regime.values()]
        cagrs = [r["cagr"] for r in by_regime.values()]
        max_dds = [r["max_dd"] for r in by_regime.values()]
        n_regimes = len(by_regime)
        summary.append({
            "name": v_name,
            "mean_sharpe": statistics.mean(sharpes) if sharpes else 0,
            "median_sharpe": statistics.median(sharpes) if sharpes else 0,
            "mean_cagr": statistics.mean(cagrs) if cagrs else 0,
            "worst_dd": min(max_dds) if max_dds else 0,
            "n": n_regimes,
        })
    summary.sort(key=lambda x: -x["mean_sharpe"])
    print(f"\n{'Variant':40s}  {'Mean Sharpe':>11s}  {'Median Sharpe':>13s}  {'Mean CAGR':>10s}  {'Worst MaxDD':>12s}  {'N regimes':>10s}")
    for s in summary:
        print(f"  {s['name']:40s}  {s['mean_sharpe']:>+10.2f}  {s['median_sharpe']:>+12.2f}  "
              f"{s['mean_cagr']*100:>+9.1f}%  {s['worst_dd']*100:>+11.2f}%  {s['n']:>10d}")

    print("\nKey questions this answers:")
    print("  - Does top3_eq_80 still win across regimes, or only in mom-friendly bulls?")
    print("  - Is there a regime where top-5 / 80% beats top-3 / 80%?")
    print("  - Worst-MaxDD column: which variant takes the deepest pain in 2018-Q4 / 2020-Q1 / 2022?")
    print("\nIf no variant dominates all 5 regimes, consider a REGIME-AWARE meta-allocator.")


if __name__ == "__main__":
    main()

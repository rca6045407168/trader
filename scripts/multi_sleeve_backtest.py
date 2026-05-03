"""[v3.60.0] Multi-sleeve backtest: momentum vs momentum+LowVol blend.

Owns the P&L thesis: does adding LowVol as a second sleeve actually
improve risk-adjusted return on real walk-forward data?

Tests four allocations side-by-side:
  • 100% momentum (current LIVE)
  • 80% momentum / 20% LowVol
  • 70% momentum / 30% LowVol  (V5 proposal endorsement)
  • 50% momentum / 50% LowVol

For each allocation, runs anchored walk-forward across N quarterly
windows. Aggregates: mean Sharpe, % positive windows, worst-window DD,
correlation between sleeve daily returns.

Output: side-by-side table + verdict + JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def fetch_close(ticker: str, start: str, end: str):
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end,
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        out = {}
        for idx in df.index:
            v = df["Close"].loc[idx]
            try:
                out[idx.date()] = float(v.iloc[0] if hasattr(v, "iloc") else v)
            except Exception:
                continue
        return out
    except Exception:
        return {}


def fetch_panel(symbols: list[str], start: str, end: str) -> dict:
    out = {}
    for sym in symbols:
        d = fetch_close(sym, start, end)
        if d:
            out[sym] = d
    return out


def equal_weight_daily(picks: list[str], panel: dict,
                         start: str, end: str) -> list[float]:
    from datetime import date as _date
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    all_dates = sorted(set(
        d for sym in picks if sym in panel
        for d in panel[sym] if s <= d <= e
    ))
    daily = []
    for i in range(1, len(all_dates)):
        prev_d, cur_d = all_dates[i - 1], all_dates[i]
        rs = []
        for sym in picks:
            cd = panel.get(sym, {})
            if prev_d in cd and cur_d in cd and cd[prev_d] > 0:
                rs.append((cd[cur_d] / cd[prev_d]) - 1)
        if rs:
            daily.append(sum(rs) / len(rs))
    return daily


def stats_from_daily(daily: list[float]) -> dict:
    if len(daily) < 2:
        return {"sharpe": None, "max_dd": None, "return_pct": None,
                 "annual_vol_pct": None, "n": len(daily)}
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum)
        mx = min(mx, cum / peak - 1)
    mean = statistics.mean(daily)
    sd = statistics.stdev(daily)
    sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else 0
    return {"sharpe": sharpe, "max_dd": mx * 100,
             "return_pct": (cum - 1) * 100,
             "annual_vol_pct": sd * math.sqrt(252) * 100,
             "n": len(daily)}


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 5:
        return None
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = sum((x - ma) ** 2 for x in a)
    db = sum((x - mb) ** 2 for x in b)
    if da <= 0 or db <= 0:
        return 0
    return num / math.sqrt(da * db)


def run_walk_forward(strategy_picks_fn, asof_dates: list[str],
                       window_days: int, panel: dict) -> dict:
    """For each as-of date, get picks, then compute equal-weight daily
    returns over the next window_days."""
    all_daily: list[float] = []
    per_window: list[dict] = []
    for asof in asof_dates:
        try:
            picks = strategy_picks_fn(asof)
        except Exception as e:
            per_window.append({"asof": asof, "error": str(e)})
            continue
        if not picks:
            per_window.append({"asof": asof, "n_picks": 0})
            continue
        win_end = (datetime.fromisoformat(asof) + timedelta(days=window_days)).date().isoformat()
        daily = equal_weight_daily(picks, panel, asof, win_end)
        if daily:
            all_daily.extend(daily)
            stats = stats_from_daily(daily)
            stats["asof"] = asof
            stats["picks"] = picks[:5]
            stats["n_picks"] = len(picks)
            per_window.append(stats)
    aggregate = stats_from_daily(all_daily)
    pos_count = sum(1 for w in per_window
                     if w.get("return_pct") is not None and w["return_pct"] > 0)
    valid = sum(1 for w in per_window if w.get("return_pct") is not None)
    aggregate["pct_windows_positive"] = pos_count / valid if valid > 0 else None
    return {"aggregate": aggregate, "per_window": per_window,
            "all_daily": all_daily}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-06-01")  # ample history
    ap.add_argument("--first-test", default="2022-01-01")
    ap.add_argument("--end", default="2026-04-01")
    ap.add_argument("--n-windows", type=int, default=16)
    ap.add_argument("--window-days", type=int, default=63)
    args = ap.parse_args()

    print("=" * 78)
    print(f"Multi-sleeve backtest · {args.first_test} → {args.end}")
    print(f"  windows={args.n_windows}  window_days={args.window_days}")
    print("=" * 78)

    from trader.universe import DEFAULT_LIQUID_50
    from trader.strategy import rank_momentum
    from trader.v358_world_class import LowVolSleeve

    universe = DEFAULT_LIQUID_50
    panel = fetch_panel(universe, args.start, args.end)
    if not panel:
        print("ERROR: no price panel")
        return 1
    print(f"  Panel: {len(panel)}/{len(universe)} symbols with data")

    # Generate as-of dates
    s_dt = datetime.fromisoformat(args.first_test)
    e_dt = datetime.fromisoformat(args.end) - timedelta(days=args.window_days)
    span = (e_dt - s_dt).days
    asof_dates = [
        (s_dt + timedelta(days=int(span * i / max(args.n_windows - 1, 1)))).date().isoformat()
        for i in range(args.n_windows)
    ]
    print(f"  as-of dates: {asof_dates[0]} ... {asof_dates[-1]} ({len(asof_dates)} total)")

    # ---- Momentum strategy fn ----
    def mom_picks(asof):
        cands = rank_momentum(universe, lookback_months=12, skip_months=1,
                                top_n=15, end_date=asof)
        return [c.ticker for c in cands]

    # ---- LowVol strategy fn ----
    lv_sleeve = LowVolSleeve(n_holdings=15, lookback_days=60)
    def lv_picks(asof):
        # Need return history per symbol UP TO asof (exclusive)
        end_d = datetime.fromisoformat(asof).date()
        rets = {}
        for sym, cd in panel.items():
            sorted_dates = sorted(d for d in cd if d < end_d)
            prices = [cd[d] for d in sorted_dates[-90:]]
            r = []
            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    r.append((prices[i] / prices[i - 1]) - 1)
            if r:
                rets[sym] = r
        return lv_sleeve.select(rets)

    print("\n--- Running momentum walk-forward ---")
    mom = run_walk_forward(mom_picks, asof_dates, args.window_days, panel)
    print("--- Running LowVol walk-forward ---")
    lv = run_walk_forward(lv_picks, asof_dates, args.window_days, panel)

    # Correlation between sleeve daily returns over the full panel
    corr = correlation(mom["all_daily"], lv["all_daily"])

    # ---- Blend daily-return streams at different allocations ----
    def blend(w_mom: float):
        n = min(len(mom["all_daily"]), len(lv["all_daily"]))
        return [w_mom * mom["all_daily"][i] + (1 - w_mom) * lv["all_daily"][i]
                for i in range(n)]

    blends = {
        "100% momentum (current LIVE)": stats_from_daily(mom["all_daily"]),
        "80/20 momentum/LowVol": stats_from_daily(blend(0.80)),
        "70/30 momentum/LowVol (V5 endorsement)": stats_from_daily(blend(0.70)),
        "50/50 momentum/LowVol": stats_from_daily(blend(0.50)),
        "100% LowVol": stats_from_daily(lv["all_daily"]),
    }

    # Print comparison
    print("\n" + "=" * 78)
    print("ALLOCATION COMPARISON")
    print("=" * 78)
    print(f"  Sleeve daily-return correlation: {corr:+.3f}" if corr is not None else "  (corr n/a)")
    print(f"\n  {'Allocation':<46} {'Sharpe':>8} {'Vol%':>7} {'maxDD%':>8} {'Ret%':>8}")
    print("  " + "-" * 76)
    for label, s in blends.items():
        sharpe = s.get("sharpe")
        vol = s.get("annual_vol_pct")
        dd = s.get("max_dd")
        ret = s.get("return_pct")
        print(f"  {label:<46} "
              f"{sharpe:>+7.2f} {vol:>7.1f} {dd:>+7.1f} {ret:>+7.1f}"
              if sharpe is not None else f"  {label:<46} n/a")

    # Verdict
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    base = blends["100% momentum (current LIVE)"]["sharpe"] or 0
    best_blend_label = None
    best_blend_sharpe = base
    for label, s in blends.items():
        if "current" in label or "100% LowVol" in label:
            continue
        sh = s.get("sharpe")
        if sh and sh > best_blend_sharpe:
            best_blend_sharpe = sh
            best_blend_label = label

    if best_blend_label and best_blend_sharpe > base * 1.05:
        lift = (best_blend_sharpe - base) / abs(base) * 100 if base else 0
        print(f"  ✅ BLEND BEATS BASELINE")
        print(f"     Best blend: {best_blend_label}")
        print(f"     Sharpe lift: {best_blend_sharpe:+.2f} vs {base:+.2f} ({lift:+.1f}%)")
        print(f"     RECOMMENDATION: promote LowVolSleeve from SHADOW → LIVE at the blend weight.")
    elif best_blend_sharpe > base:
        print(f"  🟡 MARGINAL: blend slightly beats baseline ({best_blend_sharpe:.2f} vs {base:.2f}) "
              f"but not enough lift to justify the operational complexity")
    else:
        print(f"  ❌ NO LIFT: best blend {best_blend_sharpe:.2f} ≤ baseline {base:.2f}")
        print(f"     RECOMMENDATION: stay 100% momentum.")

    # Diversification benefit (max-DD reduction)
    base_dd = blends["100% momentum (current LIVE)"]["max_dd"] or 0
    print(f"\n  Drawdown reduction (lower = less negative = better):")
    for label, s in blends.items():
        dd = s.get("max_dd")
        if dd is not None:
            delta = dd - base_dd
            arrow = "✅" if delta > 0 else ("→" if delta == 0 else "❌")
            print(f"    {arrow} {label:<46} {dd:>+6.1f}%  Δ {delta:+.1f}pp")

    # Save
    out_path = ROOT / "data" / "multi_sleeve_backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "args": vars(args),
            "asof_dates": asof_dates,
            "correlation": corr,
            "blends": blends,
            "momentum_per_window": mom["per_window"],
            "lowvol_per_window": lv["per_window"],
        }, f, indent=2, default=str)
    print(f"\nWritten: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""[v3.60.1] Backtest residual momentum vs vanilla momentum.

Tests the claim from sleeve_shadows.py: "residual momentum (Blitz-
Hanauer factor-neutral) lifts Sharpe +0.3 to +0.5 per published
literature → ~70bp/yr expected."

Method: walk-forward both strategies on the same universe + same
windows + same equal-weight portfolio construction. Difference in
realized portfolio Sharpe IS the lift.

Output: side-by-side stats + per-window comparison + verdict.
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


def fetch_close(ticker: str, start: str, end: str) -> dict:
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
    from datetime import datetime as _dt
    s = _dt.fromisoformat(start).date()
    e = _dt.fromisoformat(end).date()
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


def stats(daily: list[float]) -> dict:
    if len(daily) < 2:
        return {"sharpe": None, "cagr_pct": None, "max_dd_pct": None,
                 "return_pct": None}
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum)
        mx = min(mx, cum / peak - 1)
    mean = statistics.mean(daily)
    sd = statistics.stdev(daily)
    sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else 0
    n = len(daily)
    cagr = (cum ** (252 / n)) - 1 if cum > 0 else 0
    return {"sharpe": sharpe, "cagr_pct": cagr * 100,
             "max_dd_pct": mx * 100,
             "return_pct": (cum - 1) * 100,
             "annual_vol_pct": sd * math.sqrt(252) * 100,
             "n_days": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--first-test", default="2022-01-01")
    ap.add_argument("--end", default="2026-04-01")
    ap.add_argument("--n-windows", type=int, default=8)
    ap.add_argument("--window-days", type=int, default=63)
    args = ap.parse_args()

    print("=" * 78)
    print(f"Residual momentum vs vanilla · {args.first_test} → {args.end}")
    print("=" * 78)

    from trader.universe import DEFAULT_LIQUID_50
    from trader.sleeve_shadows import (
        vanilla_momentum_picks, residual_momentum_picks,
    )

    # Pull price panel for all universe symbols across full window
    panel = fetch_panel(DEFAULT_LIQUID_50, args.start, args.end)
    if not panel:
        print("ERROR: empty panel")
        return 1
    print(f"  Panel: {len(panel)}/{len(DEFAULT_LIQUID_50)} symbols")

    # Generate as-of dates
    s_dt = datetime.fromisoformat(args.first_test)
    e_dt = datetime.fromisoformat(args.end) - timedelta(days=args.window_days)
    span = (e_dt - s_dt).days
    asof_dates = [
        (s_dt + timedelta(days=int(span * i / max(args.n_windows - 1, 1)))).date().isoformat()
        for i in range(args.n_windows)
    ]
    print(f"  As-of dates: {len(asof_dates)}: {asof_dates[0]} ... {asof_dates[-1]}")

    vanilla_daily = []
    residual_daily = []
    per_window = []
    for asof in asof_dates:
        win_end = (datetime.fromisoformat(asof)
                    + timedelta(days=args.window_days)).date().isoformat()
        van = vanilla_momentum_picks(DEFAULT_LIQUID_50, asof=asof, top_n=15)
        res = residual_momentum_picks(DEFAULT_LIQUID_50, asof=asof, top_n=15)
        van_daily = equal_weight_daily(van, panel, asof, win_end) if van else []
        res_daily = equal_weight_daily(res, panel, asof, win_end) if res else []
        vanilla_daily.extend(van_daily)
        residual_daily.extend(res_daily)
        van_st = stats(van_daily)
        res_st = stats(res_daily)
        overlap = len(set(van) & set(res)) / max(len(van), 1) * 100 if van else 0
        per_window.append({
            "asof": asof,
            "vanilla_picks": van[:5],
            "residual_picks": res[:5],
            "overlap_pct": overlap,
            "vanilla_return": van_st.get("return_pct"),
            "residual_return": res_st.get("return_pct"),
            "vanilla_sharpe": van_st.get("sharpe"),
            "residual_sharpe": res_st.get("sharpe"),
        })
        print(f"  [{asof}] vanilla {van_st.get('return_pct', 0):+.2f}% / "
              f"residual {res_st.get('return_pct', 0):+.2f}% / overlap {overlap:.0f}%")

    van_total = stats(vanilla_daily)
    res_total = stats(residual_daily)

    print("\n" + "=" * 78)
    print("AGGREGATE — full walk-forward window")
    print("=" * 78)
    print(f"\n  {'Strategy':<25} {'Return%':>9} {'CAGR%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Vol%':>7}")
    print("  " + "-" * 71)
    for label, s in [("Vanilla momentum (LIVE)", van_total),
                      ("Residual momentum", res_total)]:
        print(f"  {label:<25} "
              f"{s.get('return_pct', 0):>+8.2f} "
              f"{s.get('cagr_pct', 0):>+7.2f} "
              f"{s.get('sharpe', 0):>+7.2f} "
              f"{s.get('max_dd_pct', 0):>+7.1f} "
              f"{s.get('annual_vol_pct', 0):>6.1f}")

    sharpe_lift = (res_total.get("sharpe") or 0) - (van_total.get("sharpe") or 0)
    cagr_lift = (res_total.get("cagr_pct") or 0) - (van_total.get("cagr_pct") or 0)
    print(f"\n  Sharpe lift: {sharpe_lift:+.2f}")
    print(f"  CAGR lift: {cagr_lift:+.2f}pp ({cagr_lift * 100:+.0f} bps/yr)")

    avg_overlap = sum(w["overlap_pct"] for w in per_window) / len(per_window)
    print(f"  Average pick-set overlap: {avg_overlap:.0f}%")

    print("\n" + "=" * 78)
    if sharpe_lift >= 0.15 and cagr_lift > 0:
        print(f"  ✅ VERIFIED: residual momentum lifts Sharpe by {sharpe_lift:+.2f}, "
              f"CAGR by {cagr_lift*100:+.0f}bp/yr")
        print("     RECOMMENDATION: 30-day SHADOW validation, then promote")
    elif sharpe_lift > 0 and cagr_lift > 0:
        print(f"  🟡 MARGINAL: lift exists but smaller than published "
              f"({sharpe_lift:+.2f} Sharpe, {cagr_lift*100:+.0f}bp/yr)")
    elif sharpe_lift >= 0:
        print(f"  🟡 NEUTRAL: Sharpe {sharpe_lift:+.2f}, return {cagr_lift*100:+.0f}bp/yr — "
              f"residual is roughly tied with vanilla on this universe/period")
    else:
        print(f"  ❌ UNVERIFIED: residual momentum UNDERPERFORMS vanilla")
        print(f"     Sharpe lift {sharpe_lift:+.2f}, return {cagr_lift*100:+.0f}bp/yr")

    out = ROOT / "data" / "residual_momentum_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "args": vars(args),
            "vanilla": van_total,
            "residual": res_total,
            "sharpe_lift": sharpe_lift,
            "cagr_lift_pct": cagr_lift,
            "avg_overlap_pct": avg_overlap,
            "per_window": per_window,
        }, f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

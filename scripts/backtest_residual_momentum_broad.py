"""[v3.63.0] Re-test residual momentum on a BROADER universe.

Per WHY_REFUTED.md: the v3.60.0 backtest tested residual momentum
against vanilla on liquid_50 (~50 mega-caps) over 2022-2026 (a Mag-7
era). Found -564bp/yr WORSE — but the universe is too narrow for FF5
factor regression to have meaningful cross-sectional variation, and
the period was Mag-7 dominated.

This re-test uses ~150 names (broader than liquid_50, smaller than
SP500 to keep yfinance time bounded) over 2018-2026 (longer window
covering both pre-Mag-7 and Mag-7 regimes).

Run:
  python scripts/backtest_residual_momentum_broad.py
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


# Broader universe: top ~150 by liquidity, hand-picked from SP500.
# Skip ETFs and special situations. Bias toward names with clean
# price history 2018-onward.
BROAD_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "META", "NVDA", "AVGO", "ORCL",
    "ADBE", "CRM", "AMD", "INTC", "CSCO", "QCOM", "TXN", "AMAT", "MU",
    "INTU", "NOW", "IBM",
    # Consumer disc + comms
    "AMZN", "TSLA", "HD", "MCD", "NKE", "DIS", "NFLX", "BKNG", "SBUX",
    "TGT", "LOW", "TJX", "ROST", "CMCSA",
    # Financials
    "JPM", "BAC", "WFC", "MS", "GS", "C", "BLK", "AXP", "V", "MA",
    "BRK-B", "SCHW", "USB", "PNC", "TFC", "COF", "MET",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "ABT", "TMO", "DHR",
    "BMY", "AMGN", "GILD", "MDT", "CVS", "CI", "HUM", "ELV", "ISRG",
    "VRTX", "REGN",
    # Staples
    "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL", "GIS",
    "K",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "VLO", "MPC", "OXY",
    # Industrials
    "LIN", "HON", "CAT", "DE", "GE", "BA", "RTX", "LMT", "NOC", "MMM",
    "UPS", "FDX", "UNP", "CSX", "NSC", "WM", "ETN", "EMR",
    # Comms / Media
    "T", "VZ", "TMUS",
    # Materials
    "FCX", "NEM", "ECL", "DOW", "DD",
    # Utilities + Real Estate
    "NEE", "DUK", "SO", "AEP", "AMT", "PLD", "CCI", "EQIX",
    # Auto / etc
    "F", "GM",
]


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


def stats(daily):
    if len(daily) < 2:
        return {"sharpe": None, "cagr_pct": None}
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum); mx = min(mx, cum/peak - 1)
    mean, sd = statistics.mean(daily), statistics.stdev(daily)
    n = len(daily)
    return {
        "sharpe": (mean/sd)*math.sqrt(252) if sd > 0 else 0,
        "cagr_pct": ((cum**(252/n))-1)*100 if cum > 0 else 0,
        "return_pct": (cum-1)*100,
        "max_dd_pct": mx*100,
        "annual_vol_pct": sd*math.sqrt(252)*100, "n": n,
    }


def equal_weight_daily(picks: list[str], panel: dict, start: str, end: str):
    from datetime import datetime as _dt
    s = _dt.fromisoformat(start).date()
    e = _dt.fromisoformat(end).date()
    all_dates = sorted(set(
        d for sym in picks if sym in panel
        for d in panel[sym] if s <= d <= e
    ))
    daily = []
    for i in range(1, len(all_dates)):
        prev_d, cur_d = all_dates[i-1], all_dates[i]
        rs = []
        for sym in picks:
            cd = panel.get(sym, {})
            if prev_d in cd and cur_d in cd and cd[prev_d] > 0:
                rs.append((cd[cur_d]/cd[prev_d]) - 1)
        if rs:
            daily.append(sum(rs)/len(rs))
    return daily


def main():
    ap = argparse.ArgumentParser()
    # 2018-2026 window covers pre-Mag-7 era + Mag-7 era
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--first-test", default="2020-01-01")
    ap.add_argument("--end", default="2026-04-01")
    ap.add_argument("--n-windows", type=int, default=12)
    ap.add_argument("--window-days", type=int, default=63)
    args = ap.parse_args()

    print("=" * 78)
    print(f"Residual momentum re-test on BROADER universe · "
          f"{args.first_test} → {args.end}")
    print(f"  Universe: {len(BROAD_UNIVERSE)} names (vs liquid_50 = 50)")
    print(f"  Window: {args.start} → {args.end} ({len(BROAD_UNIVERSE)} symbols)")
    print("=" * 78)

    print(f"  Fetching panel...")
    panel = {}
    for sym in BROAD_UNIVERSE:
        d = fetch_close(sym, args.start, args.end)
        if d:
            panel[sym] = d
    print(f"  Got {len(panel)}/{len(BROAD_UNIVERSE)} symbols")

    if len(panel) < 50:
        print(f"  ERROR: only {len(panel)} symbols had data; need ≥50")
        return 1

    from trader.sleeve_shadows import (
        vanilla_momentum_picks, residual_momentum_picks,
    )

    s_dt = datetime.fromisoformat(args.first_test)
    e_dt = datetime.fromisoformat(args.end) - timedelta(days=args.window_days)
    span = (e_dt - s_dt).days
    asof_dates = [
        (s_dt + timedelta(days=int(span * i / max(args.n_windows - 1, 1)))).date().isoformat()
        for i in range(args.n_windows)
    ]
    print(f"  As-of dates: {len(asof_dates)} from {asof_dates[0]} to {asof_dates[-1]}")

    universe = list(panel.keys())  # only symbols with data
    vanilla_daily, residual_daily = [], []
    per_window = []

    for asof in asof_dates:
        win_end = (datetime.fromisoformat(asof) +
                   timedelta(days=args.window_days)).date().isoformat()
        print(f"  [{asof}] computing picks...")
        try:
            van = vanilla_momentum_picks(universe, asof=asof, top_n=15)
        except Exception as e:
            print(f"    vanilla failed: {e}")
            van = []
        try:
            res = residual_momentum_picks(universe, asof=asof, top_n=15)
        except Exception as e:
            print(f"    residual failed: {e}")
            res = []
        if not van or not res:
            continue
        v_d = equal_weight_daily(van, panel, asof, win_end)
        r_d = equal_weight_daily(res, panel, asof, win_end)
        vanilla_daily.extend(v_d)
        residual_daily.extend(r_d)
        per_window.append({
            "asof": asof,
            "vanilla_picks": van[:5],
            "residual_picks": res[:5],
            "overlap_pct": len(set(van) & set(res)) / max(len(van), 1) * 100,
        })

    van_total = stats(vanilla_daily)
    res_total = stats(residual_daily)

    print(f"\n  {'Strategy':<25} {'Return%':>9} {'CAGR%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("  " + "-" * 65)
    for label, s in [("Vanilla momentum", van_total),
                      ("Residual momentum", res_total)]:
        print(f"  {label:<25} "
              f"{s.get('return_pct', 0):>+8.2f} "
              f"{s.get('cagr_pct', 0):>+7.2f} "
              f"{s.get('sharpe', 0):>+7.2f} "
              f"{s.get('max_dd_pct', 0):>+7.1f}")

    sharpe_lift = (res_total.get("sharpe") or 0) - (van_total.get("sharpe") or 0)
    cagr_lift = (res_total.get("cagr_pct") or 0) - (van_total.get("cagr_pct") or 0)
    avg_overlap = sum(w["overlap_pct"] for w in per_window) / max(len(per_window), 1)
    print(f"\n  Sharpe lift: {sharpe_lift:+.2f}")
    print(f"  CAGR lift: {cagr_lift:+.2f}pp ({cagr_lift*100:+.0f}bp/yr)")
    print(f"  Avg pick overlap: {avg_overlap:.0f}%")

    print("\n" + "=" * 78)
    if sharpe_lift >= 0.15 and cagr_lift > 0:
        print(f"  ✅ VERIFIED on broader universe: residual lifts Sharpe by "
              f"{sharpe_lift:+.2f}, CAGR {cagr_lift*100:+.0f}bp/yr")
    elif sharpe_lift > 0:
        print(f"  🟡 MARGINAL: lift exists ({sharpe_lift:+.2f}, "
              f"{cagr_lift*100:+.0f}bp/yr) but smaller than published")
    else:
        print(f"  ❌ STILL REFUTED on broader universe: lift {sharpe_lift:+.2f}, "
              f"{cagr_lift*100:+.0f}bp/yr")
        print(f"     The Blitz-Hanauer claim may not hold on US large/mid-cap "
              f"in our 2018-2026 window. May need full 3000+ name universe + "
              f"longer history.")

    out = ROOT / "data" / "residual_momentum_broad_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "args": vars(args),
            "universe_size_intended": len(BROAD_UNIVERSE),
            "universe_size_actual": len(panel),
            "vanilla": van_total,
            "residual": res_total,
            "sharpe_lift": sharpe_lift,
            "cagr_lift_pct": cagr_lift,
            "avg_overlap_pct": avg_overlap,
            "n_windows": len(per_window),
        }, f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

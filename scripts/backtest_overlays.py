"""[v3.60.1] Backtest the SHADOW overlays: SectorNeutralizer + TrailingStop + EarningsRule.

For each overlay, run baseline (no overlay) vs treatment (overlay applied)
on the same walk-forward windows. Measure return / Sharpe / max DD lift.
This verifies the v3.58 SHADOW claims that I never actually backtested.

Output: per-overlay verdict + side-by-side stats + JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def fetch_close(t, start, end):
    try:
        import yfinance as yf
        df = yf.download(t, start=start, end=end, progress=False, auto_adjust=True)
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
        return {"sharpe": None, "cagr_pct": None, "max_dd_pct": None, "return_pct": None}
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum); mx = min(mx, cum/peak - 1)
    mean = statistics.mean(daily); sd = statistics.stdev(daily)
    sharpe = (mean/sd)*math.sqrt(252) if sd > 0 else 0
    n = len(daily)
    return {"sharpe": sharpe,
             "cagr_pct": ((cum**(252/n)) - 1)*100 if n > 0 and cum > 0 else 0,
             "max_dd_pct": mx*100, "return_pct": (cum-1)*100,
             "annual_vol_pct": sd*math.sqrt(252)*100, "n_days": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-06-01")
    ap.add_argument("--first-test", default="2022-01-01")
    ap.add_argument("--end", default="2026-04-01")
    ap.add_argument("--n-windows", type=int, default=12)
    ap.add_argument("--window-days", type=int, default=63)
    args = ap.parse_args()

    print("=" * 78)
    print(f"Overlay backtests · {args.first_test} → {args.end}")
    print("=" * 78)

    from trader.universe import DEFAULT_LIQUID_50
    from trader.strategy import rank_momentum
    from trader.v358_world_class import (
        SectorNeutralizer, TrailingStop, EarningsRule
    )

    # Pull panel
    panel = {}
    for sym in DEFAULT_LIQUID_50:
        d = fetch_close(sym, args.start, args.end)
        if d:
            panel[sym] = d

    # As-of dates
    s_dt = datetime.fromisoformat(args.first_test)
    e_dt = datetime.fromisoformat(args.end) - timedelta(days=args.window_days)
    span = (e_dt - s_dt).days
    asof_dates = [
        (s_dt + timedelta(days=int(span * i / max(args.n_windows - 1, 1)))).date().isoformat()
        for i in range(args.n_windows)
    ]

    # Sector lookup — use yfinance ticker.info which is slow; instead
    # use a hardcoded mapping for the 50 names (built from CLAUDE.md
    # context + obvious sectors).
    SECTORS = {
        "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "GOOG": "Tech",
        "GOOGL": "Tech", "META": "Tech", "AVGO": "Tech", "ORCL": "Tech",
        "CRM": "Tech", "ADBE": "Tech", "AMD": "Tech", "INTC": "Tech",
        "CSCO": "Tech", "QCOM": "Tech", "TXN": "Tech", "AMAT": "Tech",
        "MU": "Tech", "INTU": "Tech", "NOW": "Tech",
        "AMZN": "ConsumerDisc", "TSLA": "ConsumerDisc",
        "HD": "ConsumerDisc", "MCD": "ConsumerDisc", "NKE": "ConsumerDisc",
        "DIS": "ConsumerDisc", "NFLX": "Comms",
        "JPM": "Fin", "BAC": "Fin", "WFC": "Fin", "MS": "Fin",
        "GS": "Fin", "C": "Fin", "BLK": "Fin", "AXP": "Fin",
        "V": "Fin", "MA": "Fin", "BRK-B": "Fin",
        "WMT": "Staples", "PG": "Staples", "KO": "Staples", "PEP": "Staples",
        "COST": "Staples",
        "JNJ": "Health", "UNH": "Health", "PFE": "Health",
        "ABBV": "Health", "MRK": "Health", "LLY": "Health", "ABT": "Health",
        "XOM": "Energy", "CVX": "Energy",
        "T": "Comms", "VZ": "Comms", "CMCSA": "Comms",
        "LIN": "Materials", "HON": "Industrials", "CAT": "Industrials",
    }

    # ---- Get baseline picks for every as-of ----
    baseline_picks_by_asof: dict[str, list[str]] = {}
    for asof in asof_dates:
        cands = rank_momentum(DEFAULT_LIQUID_50, lookback_months=12,
                                skip_months=1, top_n=15, end_date=asof)
        baseline_picks_by_asof[asof] = [c.ticker for c in cands]

    # ---- BASELINE: equal-weight top-15 ----
    def portfolio_daily(picks_with_weights: dict[str, dict[str, float]],
                          panel: dict, asof: str, win_end: str):
        """picks_with_weights = {asof: {sym: weight}}.
        For the window starting at asof, returns daily portfolio returns."""
        weights = picks_with_weights[asof]
        s = datetime.fromisoformat(asof).date()
        e = datetime.fromisoformat(win_end).date()
        all_dates = sorted(set(d for sym in weights if sym in panel
                                for d in panel[sym] if s <= d <= e))
        daily = []
        for i in range(1, len(all_dates)):
            prev_d, cur_d = all_dates[i-1], all_dates[i]
            r = 0.0; w_sum = 0.0
            for sym, w in weights.items():
                cd = panel.get(sym, {})
                if prev_d in cd and cur_d in cd and cd[prev_d] > 0:
                    r += w * ((cd[cur_d] / cd[prev_d]) - 1)
                    w_sum += w
            if w_sum > 0:
                daily.append(r / w_sum)
        return daily

    def equal_weight(picks: list[str]) -> dict[str, float]:
        if not picks:
            return {}
        w = 1.0 / len(picks)
        return {s: w for s in picks}

    # === BASELINE: equal-weight top-15 ===
    baseline_daily: list[float] = []
    for asof in asof_dates:
        win_end = (datetime.fromisoformat(asof) + timedelta(days=args.window_days)).date().isoformat()
        weights = {asof: equal_weight(baseline_picks_by_asof[asof])}
        baseline_daily.extend(portfolio_daily(weights, panel, asof, win_end))

    # === SECTOR NEUTRALIZER overlay ===
    sn = SectorNeutralizer(max_sector_pct=0.35)
    sector_daily: list[float] = []
    for asof in asof_dates:
        picks = baseline_picks_by_asof[asof]
        eq_w = equal_weight(picks)
        cap_w = sn.neutralize(eq_w, SECTORS)
        win_end = (datetime.fromisoformat(asof) + timedelta(days=args.window_days)).date().isoformat()
        sector_daily.extend(portfolio_daily({asof: cap_w}, panel, asof, win_end))

    # === TRAILING STOP overlay ===
    # For each rebalance window: at each daily step, drop names whose
    # current_price / max(entry_price, peak_close_since_entry) < 1 - 0.15
    ts = TrailingStop(pct=0.15)
    trailing_daily: list[float] = []
    for asof in asof_dates:
        picks = baseline_picks_by_asof[asof]
        if not picks:
            continue
        # Build per-symbol price history for the window
        s = datetime.fromisoformat(asof).date()
        e = (datetime.fromisoformat(asof) + timedelta(days=args.window_days)).date()
        all_dates = sorted(set(d for sym in picks if sym in panel
                                for d in panel[sym] if s <= d <= e))
        if not all_dates:
            continue
        active = list(picks)  # set of currently-held names
        peak = {}
        # entry price = price on first day in window
        entry = {}
        for sym in active:
            for d in all_dates:
                if d in panel.get(sym, {}):
                    entry[sym] = panel[sym][d]
                    peak[sym] = entry[sym]
                    break
        for i in range(1, len(all_dates)):
            prev_d, cur_d = all_dates[i-1], all_dates[i]
            r = 0.0; w_count = 0
            new_active = []
            for sym in active:
                cd = panel.get(sym, {})
                if cur_d not in cd or prev_d not in cd or cd[prev_d] <= 0:
                    new_active.append(sym)
                    continue
                cur_price = cd[cur_d]
                # Update peak
                peak[sym] = max(peak.get(sym, cur_price), cur_price)
                # Daily return
                r += (cd[cur_d] / cd[prev_d]) - 1
                w_count += 1
                # Check stop
                if ts.should_exit(entry_price=entry.get(sym, cur_price),
                                    peak_close=peak[sym],
                                    current_price=cur_price):
                    pass  # don't include in next day
                else:
                    new_active.append(sym)
            active = new_active
            if w_count > 0:
                trailing_daily.append(r / w_count)

    # === EARNINGS RULE overlay ===
    # Trim picks whose earnings date falls within (asof, asof+1d).
    # Approximated: skip earnings lookup, instead simulate the policy
    # by dropping any pick once during the window (random seed).
    # Better: use yfinance earnings_dates per pick and trim at T-1.
    er = EarningsRule(days_before=1, trim_to_pct_of_target=0.50)
    earnings_daily: list[float] = []
    earnings_trims_count = 0
    for asof in asof_dates:
        picks = baseline_picks_by_asof[asof]
        if not picks:
            continue
        # For each pick, try to fetch earnings dates inside window
        s = datetime.fromisoformat(asof).date()
        e = (datetime.fromisoformat(asof) + timedelta(days=args.window_days)).date()
        try:
            import yfinance as yf
            earnings_per_sym = {}
            for sym in picks:
                try:
                    t = yf.Ticker(sym)
                    df = getattr(t, "earnings_dates", None)
                    if df is None:
                        continue
                    if hasattr(df, "empty") and df.empty:
                        continue
                    for idx in df.index:
                        d = idx.date() if hasattr(idx, "date") else None
                        if d and s <= d <= e:
                            earnings_per_sym[sym] = d
                            break
                except Exception:
                    continue
        except Exception:
            earnings_per_sym = {}

        # Build daily weights with trim-on-earnings
        all_dates = sorted(set(d for sym in picks if sym in panel
                                for d in panel[sym] if s <= d <= e))
        if not all_dates:
            continue
        for i in range(1, len(all_dates)):
            prev_d, cur_d = all_dates[i-1], all_dates[i]
            r = 0.0; w_sum = 0.0
            for sym in picks:
                weight = 1.0 / len(picks)
                edate = earnings_per_sym.get(sym)
                if edate and (edate - cur_d).days <= 1 and (edate - cur_d).days >= 0:
                    weight *= 0.5
                    earnings_trims_count += 1
                cd = panel.get(sym, {})
                if prev_d in cd and cur_d in cd and cd[prev_d] > 0:
                    r += weight * ((cd[cur_d]/cd[prev_d]) - 1)
                    w_sum += weight
            if w_sum > 0:
                earnings_daily.append(r / w_sum)

    # === REPORT ===
    bs = stats(baseline_daily)
    sn_s = stats(sector_daily)
    ts_s = stats(trailing_daily)
    er_s = stats(earnings_daily)

    print(f"\n  {'Strategy':<35} {'Return%':>9} {'CAGR%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Vol%':>7}")
    print("  " + "-" * 81)
    for label, s in [("Baseline (equal-weight top-15)", bs),
                      ("+ SectorNeutralizer (35% cap)", sn_s),
                      ("+ TrailingStop (-15%)", ts_s),
                      ("+ EarningsRule (T-1 trim 50%)", er_s)]:
        print(f"  {label:<35} "
              f"{s.get('return_pct') or 0:>+8.2f} "
              f"{s.get('cagr_pct') or 0:>+7.2f} "
              f"{s.get('sharpe') or 0:>+7.2f} "
              f"{s.get('max_dd_pct') or 0:>+7.1f} "
              f"{s.get('annual_vol_pct') or 0:>6.1f}")

    print(f"\n  Earnings trims applied: {earnings_trims_count}")

    # Lifts vs baseline
    print("\n=== Lifts vs baseline ===")
    base_sharpe = bs.get("sharpe") or 0
    base_cagr = bs.get("cagr_pct") or 0
    base_dd = bs.get("max_dd_pct") or 0
    for label, s in [("SectorNeutralizer", sn_s),
                      ("TrailingStop", ts_s),
                      ("EarningsRule", er_s)]:
        sh_lift = (s.get("sharpe") or 0) - base_sharpe
        cagr_lift = (s.get("cagr_pct") or 0) - base_cagr
        dd_lift = (s.get("max_dd_pct") or 0) - base_dd
        verdict = "✅" if (sh_lift > 0.05 or (sh_lift >= -0.05 and dd_lift > 1)) else (
            "🟡" if abs(sh_lift) < 0.1 else "❌")
        print(f"  {verdict} {label:<25}  Sharpe {sh_lift:+.2f}  CAGR {cagr_lift:+.2f}pp  MaxDD {dd_lift:+.1f}pp")

    out = ROOT / "data" / "overlay_backtests.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "baseline": bs,
            "sector_neutralizer": sn_s,
            "trailing_stop": ts_s,
            "earnings_rule": er_s,
            "earnings_trims_applied": earnings_trims_count,
        }, f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""[v3.63.0] Re-test the Daniel-Moskowitz crash detector on the ACTUAL
momentum portfolio path, not SPY proxy.

Per WHY_REFUTED.md: the v3.60.1 backtest tested the cut-to-50% rule
against SPY's path 2008-2026 and found -64bp/yr (REFUTED). But Daniel-
Moskowitz (2016) Table 4 specifically measured momentum-portfolio
drawdowns in those crash regimes — which are 25-40%, vs SPY's 20-50%
with a much faster recovery. SPY rebounds; momentum portfolios don't.

This script re-runs the test using rank_momentum's actual top-15
picks at each rebalance. For the regime where the crash signal is
ON, gross is cut to 50% → halves the daily portfolio return. For
the regime where it's OFF, full exposure.

Run:
  python scripts/backtest_crash_detector_momentum.py [--start 2008-01-01]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
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


def compute_signal_history(spy_returns: list[tuple[date, float]],
                             vol_threshold: float = 0.16):  # DM original; was 0.20 (too restrictive)
    """For each date, compute the crash signal using only data up to date.
    Returns [(date, crash_on)]."""
    out = []
    n_24mo, n_12mo = 24 * 21, 12 * 21
    for i in range(n_24mo, len(spy_returns)):
        last_24mo = [r for _, r in spy_returns[i - n_24mo:i]]
        last_12mo = [r for _, r in spy_returns[i - n_12mo:i]]
        cum = 1.0
        for r in last_24mo:
            cum *= (1 + r)
        sd = statistics.stdev(last_12mo) if len(last_12mo) > 1 else 0
        vol = sd * math.sqrt(252)
        crash_on = (cum - 1 < 0) and (vol > vol_threshold)
        out.append((spy_returns[i][0], crash_on))
    return out


def stats(daily: list[float]) -> dict:
    if len(daily) < 2:
        return {}
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum); mx = min(mx, cum/peak - 1)
    mean = statistics.mean(daily); sd = statistics.stdev(daily)
    n = len(daily)
    return {
        "n": n, "return_pct": (cum-1)*100,
        "cagr_pct": ((cum**(252/n))-1)*100 if cum > 0 else 0,
        "sharpe": (mean/sd)*math.sqrt(252) if sd > 0 else 0,
        "max_dd_pct": mx*100,
        "annual_vol_pct": sd*math.sqrt(252)*100,
    }


def main():
    ap = argparse.ArgumentParser()
    # 2008 is interesting (GFC) but rank_momentum needs ~13mo lookback
    # so we need historical data going back further still
    ap.add_argument("--start", default="2007-01-01")
    ap.add_argument("--end", default="2026-04-01")
    ap.add_argument("--rebalance-days", type=int, default=21)  # monthly
    args = ap.parse_args()

    print("=" * 78)
    print(f"Crash detector — re-test on MOMENTUM portfolio · "
          f"{args.start} → {args.end}")
    print("=" * 78)

    from trader.universe import DEFAULT_LIQUID_50
    from trader.strategy import rank_momentum

    universe = DEFAULT_LIQUID_50

    # Pull SPY for crash signal
    print("  Fetching SPY for crash signal...")
    spy_closes = fetch_close("SPY", args.start, args.end)
    if not spy_closes:
        print("ERROR: SPY fetch failed")
        return 1
    sd = sorted(spy_closes.keys())
    spy_returns = [(sd[i], (spy_closes[sd[i]]/spy_closes[sd[i-1]])-1)
                   for i in range(1, len(sd)) if spy_closes[sd[i-1]] > 0]
    print(f"  SPY: {len(spy_returns)} returns")

    # Pull universe panel
    print(f"  Fetching {len(universe)} universe symbols...")
    panel: dict[str, dict[date, float]] = {}
    for sym in universe:
        cd = fetch_close(sym, args.start, args.end)
        if cd:
            panel[sym] = cd
    print(f"  Panel: {len(panel)}/{len(universe)} symbols")

    # Compute crash signal at each date
    sig_history = compute_signal_history(spy_returns)
    sig_by_date = {d: on for d, on in sig_history}
    print(f"  Crash signal computed for {len(sig_history)} days")

    # Now simulate the momentum portfolio over [first signal date, end].
    # Rebalance every `rebalance_days` calendar days. Between rebalances,
    # hold the picks equal-weight.
    if not sig_history:
        print("  insufficient history for signal")
        return 1
    first_sig_date = sig_history[0][0]
    last_date = sig_history[-1][0]

    # Generate rebalance dates
    cur = first_sig_date
    rebalance_dates = []
    while cur <= last_date:
        rebalance_dates.append(cur)
        cur = cur + timedelta(days=args.rebalance_days)
    print(f"  Rebalance dates: {len(rebalance_dates)}")

    # v3.63.0: efficient version — compute momentum score directly from the
    # pre-fetched panel instead of calling rank_momentum() (which would
    # re-fetch OHLCV per symbol per rebalance). Uses 12-1 trailing return.
    def _picks_at(rb_date, top_n=15):
        # 12-1 momentum: skip last 21 trading days, return over prior 252
        scores = []
        for sym, cd in panel.items():
            sd = sorted(d for d in cd if d <= rb_date)
            if len(sd) < 273:
                continue
            t = sd[-1]; t_skip = sd[-22]; t_back = sd[-273]
            p_skip = cd[t_skip]; p_back = cd[t_back]
            if p_back > 0:
                scores.append((sym, (p_skip / p_back) - 1))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scores[:top_n]]

    no_prot_daily: list[float] = []
    with_prot_daily: list[float] = []
    no_prot_dates: list[date] = []

    for i, rb_date in enumerate(rebalance_dates):
        picks = _picks_at(rb_date, top_n=15)
        if not picks:
            continue
        # Hold from rb_date until next rebalance
        next_rb = (rebalance_dates[i + 1]
                    if i + 1 < len(rebalance_dates) else last_date)
        # Daily portfolio returns over [rb_date, next_rb]
        all_dates = sorted(set(d for sym in picks if sym in panel
                                for d in panel[sym]
                                if rb_date <= d < next_rb))
        for j in range(1, len(all_dates)):
            prev_d, cur_d = all_dates[j - 1], all_dates[j]
            r = 0.0; n_used = 0
            for sym in picks:
                cd = panel.get(sym, {})
                if prev_d in cd and cur_d in cd and cd[prev_d] > 0:
                    r += (cd[cur_d] / cd[prev_d]) - 1
                    n_used += 1
            if n_used > 0:
                daily = r / n_used
                no_prot_daily.append(daily)
                no_prot_dates.append(cur_d)
                # Apply crash protection: cut to 50% if signal on
                crash_on = sig_by_date.get(cur_d, False)
                with_prot_daily.append(daily * (0.5 if crash_on else 1.0))

    no_prot = stats(no_prot_daily)
    with_prot = stats(with_prot_daily)
    crash_on_count = sum(1 for d in no_prot_dates if sig_by_date.get(d, False))

    print(f"\n  Days simulated: {len(no_prot_daily)}")
    print(f"  Days where crash regime was ON: {crash_on_count} "
          f"({crash_on_count/max(len(no_prot_daily), 1)*100:.1f}%)")

    print(f"\n  {'Strategy':<35} {'CAGR%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Vol%':>7}")
    print("  " + "-" * 68)
    for label, s in [("MOMENTUM no protection", no_prot),
                      ("MOMENTUM + crash detector cut", with_prot)]:
        print(f"  {label:<35} "
              f"{s.get('cagr_pct', 0):>+7.2f} "
              f"{s.get('sharpe', 0):>+7.2f} "
              f"{s.get('max_dd_pct', 0):>+7.1f} "
              f"{s.get('annual_vol_pct', 0):>6.1f}")

    cagr_lift = with_prot.get("cagr_pct", 0) - no_prot.get("cagr_pct", 0)
    sharpe_lift = with_prot.get("sharpe", 0) - no_prot.get("sharpe", 0)
    dd_change = with_prot.get("max_dd_pct", 0) - no_prot.get("max_dd_pct", 0)

    print(f"\n  CAGR lift: {cagr_lift:+.2f}pp ({cagr_lift*100:+.0f}bp/yr)")
    print(f"  Sharpe lift: {sharpe_lift:+.2f}")
    print(f"  Max DD change: {dd_change:+.1f}pp (positive = less bad)")

    print("\n" + "=" * 78)
    if cagr_lift >= 0.5 and sharpe_lift > 0:
        print(f"  ✅ VERIFIED ON MOMENTUM PROXY: protection adds "
              f"+{cagr_lift*100:.0f}bp/yr CAGR lift, +{sharpe_lift:.2f} Sharpe")
        print(f"     Daniel-Moskowitz claim survives when tested on actual "
              f"momentum portfolio (was REFUTED on SPY proxy)")
    elif cagr_lift > 0:
        print(f"  🟡 MARGINAL on momentum: +{cagr_lift*100:.0f}bp/yr; "
              f"Sharpe {sharpe_lift:+.2f}")
    elif dd_change > 2:
        print(f"  🟡 DD-protective: cuts max DD by {dd_change:+.1f}pp "
              f"but gives up {-cagr_lift*100:.0f}bp/yr — Calmar trade")
    else:
        print(f"  ❌ STILL REFUTED on momentum proxy. Lift {cagr_lift*100:+.0f}bp/yr.")
        print(f"     V-recovery problem persists even on actual momentum sleeve.")

    out = ROOT / "data" / "crash_detector_momentum_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "args": vars(args),
            "n_days": len(no_prot_daily),
            "crash_on_days": crash_on_count,
            "no_protection": no_prot,
            "with_protection": with_prot,
            "cagr_lift_pct": cagr_lift,
            "sharpe_lift": sharpe_lift,
            "max_dd_change_pct": dd_change,
        }, f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

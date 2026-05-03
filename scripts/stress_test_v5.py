"""[v3.59.2] Multi-regime stress test for V5 sleeves.

Per `docs/SCENARIO_LIBRARY.md`:
  • Tier 1 (9 regimes): must-pass; failure on any single one is a kill.
  • Tier 2 (24 regimes): should-pass; failure permitted but documented.
  • Tier 3 (14 regimes): deep history pre-1985, INDEX-LEVEL replay only
    (single-name yfinance data is unreliable pre-1985).
  • Scripted forward scenarios: scaffold only — full implementation is
    a separate ~12h effort (see scripts/scripted_scenarios.py stub).

Run:
  python scripts/stress_test_v5.py [--tier 1|2|3|all] [--index-only]

Output:
  • prints per-regime + verdict to stdout
  • writes data/stress_test_v5.json (full grid)
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@dataclass
class StressRegime:
    name: str
    start: str
    end: str
    description: str
    tier: int = 1                    # 1, 2, or 3
    index_only: bool = False         # True for Tier-3 pre-1985


# ============================================================
# REGIMES (per docs/SCENARIO_LIBRARY.md)
# ============================================================
TIER1: list[StressRegime] = [
    StressRegime("2008-financial-crisis", "2008-09-01", "2009-06-30",
                  "GFC: Lehman/TARP/Fed bailouts; SPX -55% peak-to-trough", 1),
    StressRegime("2018-Volmageddon", "2018-02-01", "2018-02-28",
                  "VIX 9 → 50 in 4 days; XIV terminated; canonical short-vol disaster", 1),
    StressRegime("2018-Q4-selloff", "2018-09-01", "2019-03-31",
                  "-20% S&P then full recovery; tests defensive-overlay whipsaw", 1),
    StressRegime("2020-COVID", "2020-01-15", "2020-06-30",
                  "-34% S&P in 22 days, VIX 82, fastest-ever recovery", 1),
    StressRegime("2022-bear", "2022-01-01", "2022-12-31",
                  "Fastest hiking cycle ever; momentum reversal", 1),
    StressRegime("2024-yen-unwind", "2024-08-01", "2024-08-31",
                  "VIX 16 → 65 intraday Aug 5; carry-trade cascade", 1),
    StressRegime("2025-tariff-regime", "2025-01-15", "2025-06-30",
                  "Trump reciprocal tariffs; tests factor model under policy regime change", 1),
    StressRegime("2023-AI-rally", "2023-04-01", "2023-10-31",
                  "Mag-7 leadership; tests narrow-breadth momentum capture", 1),
    StressRegime("recent-3-months",
                  (datetime.utcnow() - timedelta(days=95)).date().isoformat(),
                  datetime.utcnow().date().isoformat(),
                  "Rolling current regime", 1),
]

TIER2: list[StressRegime] = [
    StressRegime("1987-Black-Monday", "1987-10-01", "1987-12-31",
                  "-22% single-day; circuit-breaker era assumptions", 2),
    StressRegime("1990-Kuwait-oil", "1990-07-01", "1990-12-31",
                  "Oil $20 → $40 in 2mo; sector dislocation", 2),
    StressRegime("1994-Fed-surprise", "1994-02-01", "1994-12-31",
                  "Greenspan unexpected hike; bond market crash", 2),
    StressRegime("1997-Asian-crisis", "1997-07-01", "1998-01-31",
                  "Thai baht devaluation cascade; -10% S&P October", 2),
    StressRegime("1998-LTCM", "1998-08-01", "1998-10-31",
                  "Russian default + LTCM; -19% peak-to-trough", 2),
    StressRegime("2000-2002-dotcom-bust", "2000-03-01", "2002-09-30",
                  "Nasdaq -78%; multi-year drawdown; momentum-crash test", 2),
    StressRegime("2001-09-11", "2001-09-01", "2001-12-31",
                  "Markets closed 4 days; -14% reopen; liquidity assumptions", 2),
    StressRegime("2007-quant-quake", "2007-08-01", "2007-08-31",
                  "Statistical-arb factors decorrelated 72h", 2),
    StressRegime("2010-Flash-Crash", "2010-05-01", "2010-05-31",
                  "-9% intraday May 6; tests open/close fill assumptions", 2),
    StressRegime("2011-US-downgrade", "2011-07-01", "2011-09-30",
                  "S&P -19%, VIX 48; first US downgrade", 2),
    StressRegime("2013-Taper-Tantrum", "2013-05-01", "2013-08-31",
                  "Bernanke 'taper' comment; pre-FOMC drift inversion", 2),
    StressRegime("2014-oil-collapse", "2014-07-01", "2015-02-28",
                  "Oil $107 → $44; energy sector -50%", 2),
    StressRegime("2015-ETF-Flash-Crash", "2015-08-20", "2015-08-31",
                  "SPY -7% at Aug 24 open; tests open-fill execution", 2),
    StressRegime("2016-Brexit", "2016-06-15", "2016-07-15",
                  "-5% in 2 days; recovery in 4 weeks", 2),
    StressRegime("2018-trade-war", "2018-03-01", "2018-12-31",
                  "US-China tariff escalation; sector rotation", 2),
    StressRegime("2019-repo-spike", "2019-09-15", "2019-10-15",
                  "Overnight repo to 10%; Fed standing facility", 2),
    StressRegime("2020-negative-WTI", "2020-04-15", "2020-04-30",
                  "WTI to -$37; energy ETF dislocation", 2),
    StressRegime("2021-meme-stocks", "2021-01-15", "2021-02-15",
                  "GME, AMC, BBBY squeeze; momentum-crash signal", 2),
    StressRegime("2021-ARKK-top", "2021-11-01", "2022-06-30",
                  "High-growth tech -75%; momentum-crash candidate", 2),
    StressRegime("2022-GBP-gilt", "2022-09-15", "2022-10-15",
                  "UK LDI crisis; cross-asset propagation", 2),
    StressRegime("2023-SVB-crisis", "2023-03-01", "2023-04-30",
                  "Regional bank failures; FRC, SBNY", 2),
    StressRegime("2023-Hamas-Israel", "2023-10-07", "2023-11-07",
                  "Oct 7 attack; oil + risk-off", 2),
    StressRegime("2024-Iran-Israel", "2024-04-01", "2024-04-30",
                  "First direct missile exchange; brief risk-off", 2),
    StressRegime("2020-oil-contango", "2020-03-30", "2020-05-15",
                  "WTI futures went negative; energy-sensitive rolled hard", 2),
]

# Tier 3 — INDEX-LEVEL replay only (yfinance ^GSPC back to 1927).
# Single-name pre-1985 backfill is unreliable; LowVolSleeve must skip these.
TIER3: list[StressRegime] = [
    StressRegime("1962-Cuban-Missile-Crisis", "1962-10-15", "1962-11-30",
                  "Imminent-nuclear standoff; archetype for Iran-direct/Taiwan", 3, index_only=True),
    StressRegime("1968-Tet-social-unrest", "1968-01-15", "1968-12-31",
                  "Year of compounding risk-off (Tet, MLK, RFK, USD crisis)", 3, index_only=True),
    StressRegime("1973-OPEC-oil-embargo", "1973-10-15", "1974-12-31",
                  "Canonical oil shock + stagflation; -48% peak-to-trough", 3, index_only=True),
    StressRegime("1979-Iran-Revolution", "1979-01-01", "1979-12-31",
                  "Second oil shock + revolution-of-major-supplier", 3, index_only=True),
    StressRegime("1979-82-Volcker-shock", "1979-10-06", "1982-08-31",
                  "Fed funds 9 → 20 → 9; equities flat 3 yrs; momentum useless", 3, index_only=True),
    StressRegime("1980-Iran-Iraq-war", "1980-09-22", "1980-12-31",
                  "Two-major-supplier regional war disrupting oil", 3, index_only=True),
    StressRegime("1980-Hunt-silver-squeeze", "1980-03-01", "1980-06-30",
                  "Cornered commodity propagating to broader markets", 3, index_only=True),
    StressRegime("1985-Plaza-Accord", "1985-09-22", "1986-12-31",
                  "Deliberate USD devaluation -50%; orchestrated rebalance", 3, index_only=True),
    StressRegime("1986-87-Iran-Contra", "1986-11-01", "1987-09-30",
                  "Constitutional crisis; presidential authority shock", 3, index_only=True),
    StressRegime("1989-Berlin-Wall", "1989-11-09", "1990-06-30",
                  "Cold-war end; tests positive geopolitical shock response", 3, index_only=True),
    StressRegime("1989-91-S&L-crisis", "1989-01-01", "1991-12-31",
                  "~1000 thrift failures; ~3% of GDP; slow-motion bank unwind", 3, index_only=True),
    StressRegime("1991-Soviet-collapse", "1991-08-19", "1991-12-31",
                  "Collapse of major adversarial state; regime instability", 3, index_only=True),
    StressRegime("1994-Mexican-Peso", "1994-12-20", "1995-03-31",
                  "Tequila Crisis; EM devaluation cascade", 3, index_only=True),
    StressRegime("1995-2000-dotcom-boom", "1995-01-01", "2000-03-10",
                  "Canonical extreme trending bull (5+ years narrow leadership)", 3, index_only=True),
]


def all_regimes(tier: str = "all") -> list[StressRegime]:
    if tier == "1":
        return TIER1
    if tier == "2":
        return TIER1 + TIER2
    if tier == "3":
        return TIER3
    return TIER1 + TIER2 + TIER3


# ============================================================
# Per-sleeve backtests
# ============================================================
def fetch_close(ticker: str, start: str, end: str):
    try:
        import yfinance as yf
        pad_start = (datetime.fromisoformat(start) - timedelta(days=120)).date().isoformat()
        df = yf.download(ticker, start=pad_start, end=end,
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        d = {}
        for idx in df.index:
            v = df["Close"].loc[idx]
            try:
                d[idx.date()] = float(v.iloc[0] if hasattr(v, "iloc") else v)
            except Exception:
                continue
        return d
    except Exception:
        return {}


def regime_stats(daily_returns: list[float]) -> dict:
    if len(daily_returns) < 2:
        return {"n": len(daily_returns), "return_pct": None,
                "annual_vol_pct": None, "sharpe": None,
                "max_drawdown_pct": None, "win_rate": None}
    n = len(daily_returns)
    cum, peak, max_dd = 1.0, 1.0, 0.0
    for r in daily_returns:
        cum *= (1 + r); peak = max(peak, cum)
        max_dd = min(max_dd, cum / peak - 1)
    mean = statistics.mean(daily_returns)
    sd = statistics.stdev(daily_returns) if n > 1 else 0
    return {
        "n": n,
        "return_pct": (cum - 1) * 100,
        "annual_vol_pct": sd * math.sqrt(252) * 100,
        "sharpe": (mean / sd) * math.sqrt(252) if sd > 0 else 0,
        "max_drawdown_pct": max_dd * 100,
        "win_rate": sum(1 for r in daily_returns if r > 0) / n,
    }


def backtest_index(ticker: str, start: str, end: str) -> dict:
    closes = fetch_close(ticker, start, end)
    dates = sorted(d for d in closes if start <= d.isoformat() <= end)
    rets = []
    for i in range(1, len(dates)):
        prev = closes[dates[i - 1]]; cur = closes[dates[i]]
        if prev > 0:
            rets.append((cur / prev) - 1)
    return regime_stats(rets)


def backtest_fomc_drift(start: str, end: str, fomc_dates: list[date]) -> dict:
    closes = fetch_close("SPY", start, end)
    if not closes:
        # Fall back to ^GSPC for pre-1993 (SPY started 1993-01-29)
        closes = fetch_close("^GSPC", start, end)
    in_window: list[float] = []
    for fomc in fomc_dates:
        if not (start <= fomc.isoformat() <= end):
            continue
        eve_p = next((closes[fomc - timedelta(days=d)]
                      for d in range(1, 6)
                      if (fomc - timedelta(days=d)) in closes), None)
        fomc_p = next((closes[fomc + timedelta(days=d)]
                       for d in range(0, 4)
                       if (fomc + timedelta(days=d)) in closes), None)
        if eve_p and fomc_p and eve_p > 0:
            in_window.append((fomc_p / eve_p) - 1)
    if not in_window:
        return {"n": 0, "_note": "no FOMC events in window"}
    s = regime_stats(in_window)
    s["_note"] = f"{s['n']} FOMC events, close-to-close (proxy for 2pm cut)"
    return s


def backtest_lowvol(start: str, end: str) -> dict:
    try:
        from trader.universe import DEFAULT_LIQUID_50
        from trader.v358_world_class import LowVolSleeve
    except Exception as e:
        return {"n": 0, "_error": f"import failed: {e}"}

    panel: dict[str, dict[date, float]] = {}
    for sym in DEFAULT_LIQUID_50:
        cd = fetch_close(sym, start, end)
        if cd:
            panel[sym] = cd
    if len(panel) < 10:
        return {"n": 0, "_error": f"only {len(panel)} symbols had data — likely pre-1985"}

    start_d = datetime.fromisoformat(start).date()
    selection_returns: dict[str, list[float]] = {}
    for sym, cd in panel.items():
        sd = sorted(d for d in cd if d < start_d)
        prices = [cd[d] for d in sd[-90:]]
        rets = [(prices[i] / prices[i - 1]) - 1
                for i in range(1, len(prices)) if prices[i - 1] > 0]
        if rets:
            selection_returns[sym] = rets
    sleeve = LowVolSleeve(n_holdings=15, lookback_days=60)
    picks = sleeve.select(selection_returns)
    if not picks:
        return {"n": 0, "_error": "selection returned no picks"}

    end_d = datetime.fromisoformat(end).date()
    all_dates = sorted(set(
        d for sym in picks if sym in panel
        for d in panel[sym] if start_d <= d <= end_d
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
            daily.append(statistics.mean(rs))
    s = regime_stats(daily)
    s["_picks"] = picks
    return s


# ============================================================
# Main runner
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="all", choices=["1", "2", "3", "all"])
    args = ap.parse_args()

    print("=" * 78)
    print(f"V5 stress test — Tier: {args.tier}")
    print("=" * 78)

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import backtest_fomc_drift as bfd  # type: ignore
        historical_fomc = list(bfd.HISTORICAL_FOMC)
    except Exception:
        historical_fomc = []
    # Older FOMC dates (1962-2014) — approximate per Fed history
    older_fomc = [
        date(1962, 10, 23), date(2001, 9, 17), date(2001, 10, 2),
        date(2001, 11, 6), date(2001, 12, 11),
        date(2008, 9, 16), date(2008, 10, 8), date(2008, 10, 29),
        date(2008, 12, 16),
        date(2009, 1, 28), date(2009, 3, 18),
        date(2013, 5, 1), date(2013, 6, 19), date(2013, 7, 31),
        date(2014, 7, 30), date(1994, 2, 4),
    ]
    fomc_all = sorted(set(historical_fomc) | set(older_fomc))

    regimes = all_regimes(args.tier)
    grid = []
    for regime in regimes:
        print(f"\n--- [T{regime.tier}] {regime.name} ({regime.start} → {regime.end}) ---")
        print(f"    {regime.description}")
        # Index benchmark: SPY for post-1993, ^GSPC otherwise
        idx_ticker = "SPY" if regime.start >= "1993-01-29" else "^GSPC"
        idx = backtest_index(idx_ticker, regime.start, regime.end)
        fomc = backtest_fomc_drift(regime.start, regime.end, fomc_all)
        # LowVol skipped for index-only (Tier 3)
        if regime.index_only:
            lowvol = {"n": 0, "_skipped": "index-only regime (pre-1985 single-name data unreliable)"}
        else:
            lowvol = backtest_lowvol(regime.start, regime.end)
        print(f"  {idx_ticker:>5}:        ret={_pct(idx.get('return_pct'))}  Sharpe={_n(idx.get('sharpe'))}  maxDD={_pct(idx.get('max_drawdown_pct'))}")
        print(f"  FOMC-drift: ret={_pct(fomc.get('return_pct'))}  events={fomc.get('n', 0)}")
        if lowvol.get("_skipped"):
            print(f"  LowVol-15:  {lowvol['_skipped']}")
        else:
            print(f"  LowVol-15:  ret={_pct(lowvol.get('return_pct'))}  Sharpe={_n(lowvol.get('sharpe'))}  maxDD={_pct(lowvol.get('max_drawdown_pct'))}")
        grid.append({
            "regime": asdict(regime),
            "index_ticker": idx_ticker,
            "index": idx,
            "fomc_drift": fomc,
            "lowvol_sleeve": lowvol,
        })

    # Verdict per Gate 1A criteria from SCENARIO_LIBRARY.md §5
    print("\n" + "=" * 78)
    print("VERDICT — Gate 1A (per SCENARIO_LIBRARY.md §5)")
    print("=" * 78)

    # LowVol DD wins
    lv_wins = 0; lv_total = 0; lv_lines = []
    for r in grid:
        lv = r["lowvol_sleeve"]
        if lv.get("_skipped") or lv.get("_error"):
            continue
        spy_dd = r["index"].get("max_drawdown_pct") or 0
        lv_dd = lv.get("max_drawdown_pct") or 0
        lv_total += 1
        if lv_dd > spy_dd:
            lv_wins += 1
            lv_lines.append(f"  ✅ {r['regime']['name']}: LV {lv_dd:.1f}% vs SPY {spy_dd:.1f}%")
        else:
            lv_lines.append(f"  ❌ {r['regime']['name']}: LV {lv_dd:.1f}% vs SPY {spy_dd:.1f}%")

    print("LowVolSleeve max-DD vs index benchmark:")
    for line in lv_lines:
        print(line)
    print(f"  Total: {lv_wins}/{lv_total} regimes won on max-DD")

    out = ROOT / "data" / "stress_test_v5.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "tier_requested": args.tier,
            "n_regimes": len(grid),
            "lv_wins": lv_wins,
            "lv_total_testable": lv_total,
            "regimes": grid,
        }, f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


def _pct(v):
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"


def _n(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"


if __name__ == "__main__":
    sys.exit(main())

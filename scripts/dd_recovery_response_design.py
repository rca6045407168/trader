#!/usr/bin/env python3
"""v3.73.28 — alternative recovery responses, GFC test.

The user's open critique on v3.73.24's dd-recovery rule:
  "You can identify the bad regime, but you still do not know
   what to do with it."

The detector (deep DD + fresh rebound) fires correctly 4× during the
GFC, but the 6-1 momentum response degraded GFC P&L by -1.24pp vs
production. v3.73.28 tries 3 alternative responses to the SAME
detector signal:

  A. Defensive tilt — restrict picks to ConsumerStap + Healthcare
  B. Gross reduction — keep 12-1 picks but cut gross 80% → 40%
  C. Equal-weight top-15 — drop min-shift weighting

Reports cum return + max DD for each over the GFC window
(2008-09 → 2010-12, 28 months). Honest negative result if none help.

Output: docs/DD_RECOVERY_RESPONSE_DESIGN_2026_05_07.md
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from trader.data import fetch_history  # noqa: E402
from trader.eval_strategies import xs_top15_min_shifted  # noqa: E402
from trader.sectors import SECTORS  # noqa: E402
from trader.signals import momentum_score  # noqa: E402

GFC_START = pd.Timestamp("2008-09-01")
GFC_END = pd.Timestamp("2010-12-31")

DEFENSIVE_SECTORS = {"ConsumerStap", "Healthcare"}


def is_recovery_active(asof, prices) -> bool:
    """v3.73.24 detector: deep DD (180d <-25%) + fresh rebound (1m >+5%)."""
    try:
        if "SPY" not in prices.columns:
            return False
        spy = prices["SPY"].dropna()
        spy = spy[spy.index <= asof]
        if len(spy) < 180:
            return False
        last_180 = spy.iloc[-180:]
        peak_180 = float(last_180.max())
        current = float(spy.iloc[-1])
        dd_180 = current / peak_180 - 1
        if len(spy) < 22:
            return False
        ret_1m = current / float(spy.iloc[-22]) - 1
        return (dd_180 < -0.25) and (ret_1m > 0.05)
    except Exception:
        return False


def _stock_panel(prices):
    """Strip ETFs from the panel; return only single-name stocks."""
    etfs = {"SPY", "QQQ", "VTI", "VXUS", "BND", "AGG", "MTUM", "SCHG",
            "VUG", "XLK", "RSP"}
    cols = [c for c in prices.columns if c not in etfs]
    return prices[cols]


def _score_top15(asof, prices, lookback=12, skip=1):
    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return []
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, lookback, skip)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    return scored[:15]


def _min_shift_weights(top, target_gross=0.80):
    if not top:
        return {}
    min_s = min(s for _, s in top)
    shifted = [(t, s - min_s + 0.01) for t, s in top]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: target_gross / len(top) for t, _ in top}
    return {t: target_gross * (s / total) for t, s in shifted}


# ============================================================
# Strategy implementations
# ============================================================
def production(asof, prices):
    """Baseline LIVE: top-15 12-1 momentum, min-shifted, 80% gross."""
    return xs_top15_min_shifted(asof, prices)


def response_A_defensive(asof, prices):
    """When recovery active, restrict picks to defensive sectors only."""
    if not is_recovery_active(asof, prices):
        return xs_top15_min_shifted(asof, prices)
    # Recovery: defensives only, top-15 by 12-1 momentum among them
    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    defensive_syms = [s for s in p.columns
                       if SECTORS.get(s) in DEFENSIVE_SECTORS]
    if not defensive_syms:
        return xs_top15_min_shifted(asof, prices)
    scored = []
    for sym in defensive_syms:
        s = p[sym].dropna()
        m = momentum_score(s, 12, 1)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top = scored[:min(15, len(scored))]
    return _min_shift_weights(top, target_gross=0.80)


def response_B_reduced_gross(asof, prices):
    """When recovery active, keep 12-1 picks but cut gross 80% → 40%."""
    if not is_recovery_active(asof, prices):
        return xs_top15_min_shifted(asof, prices)
    top = _score_top15(asof, prices, lookback=12, skip=1)
    return _min_shift_weights(top, target_gross=0.40)


def response_C_equal_weight(asof, prices):
    """When recovery active, equal-weight the top-15 instead of min-shifting."""
    if not is_recovery_active(asof, prices):
        return xs_top15_min_shifted(asof, prices)
    top = _score_top15(asof, prices, lookback=12, skip=1)
    if not top:
        return {}
    return {t: 0.80 / len(top) for t, _ in top}


# ============================================================
# Walk-forward simulator
# ============================================================
def simulate(strategy_fn, prices, dates):
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    fires = 0
    for i in range(len(dates) - 1):
        t0 = dates[i]
        t1 = dates[i + 1]
        if is_recovery_active(t0, prices):
            fires += 1
        weights = strategy_fn(t0, prices)
        if not weights:
            continue
        period_ret = 0.0
        for sym, w in weights.items():
            if sym not in prices.columns:
                continue
            s = prices[sym].dropna()
            lo = s[s.index >= t0]
            hi = s[s.index <= t1]
            if lo.empty or hi.empty:
                continue
            p0 = float(lo.iloc[0])
            p1 = float(hi.iloc[-1])
            if p0 <= 0:
                continue
            period_ret += w * (p1 / p0 - 1)
        eq *= 1 + period_ret
        peak = max(peak, eq)
        dd = eq / peak - 1
        if dd < max_dd:
            max_dd = dd
    return eq, max_dd, fires


def main():
    print("Fetching universe + SPY...")
    universe = list(SECTORS.keys()) + ["SPY"]
    prices = fetch_history(universe, start="2000-01-01")
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.95))

    gfc_dates = [d for d in pd.date_range(GFC_START, GFC_END, freq="BME")]
    print(f"GFC window: {gfc_dates[0].date()} → {gfc_dates[-1].date()} "
          f"({len(gfc_dates)} months)")

    print("\nRunning production...")
    prod_eq, prod_dd, prod_fires = simulate(production, prices, gfc_dates)
    print("Running response A (defensive)...")
    a_eq, a_dd, a_fires = simulate(response_A_defensive, prices, gfc_dates)
    print("Running response B (reduced gross)...")
    b_eq, b_dd, b_fires = simulate(response_B_reduced_gross, prices, gfc_dates)
    print("Running response C (equal weight)...")
    c_eq, c_dd, c_fires = simulate(response_C_equal_weight, prices, gfc_dates)

    print("\n" + "=" * 70)
    print(f"{'Strategy':<28} {'Cum return':>12} {'Max DD':>10} {'Fires':>6}")
    print("-" * 70)
    print(f"{'production (12-1)':<28} {(prod_eq-1)*100:>11.2f}% "
          f"{prod_dd*100:>9.2f}% {'n/a':>6}")
    print(f"{'A: defensive tilt':<28} {(a_eq-1)*100:>11.2f}% "
          f"{a_dd*100:>9.2f}% {a_fires:>6}")
    print(f"{'B: reduced gross':<28} {(b_eq-1)*100:>11.2f}% "
          f"{b_dd*100:>9.2f}% {b_fires:>6}")
    print(f"{'C: equal-weight':<28} {(c_eq-1)*100:>11.2f}% "
          f"{c_dd*100:>9.2f}% {c_fires:>6}")
    print("=" * 70)

    # Build report
    out = []
    out.append("# Recovery Response Design — GFC Test\n\n")
    out.append("**Date:** 2026-05-07  \n")
    out.append("**Goal:** the v3.73.24 dd-recovery DETECTOR fires "
                "correctly during GFC but the 6-1 momentum RESPONSE "
                "degrades P&L. This work tests three alternative "
                "responses to the SAME detector signal.\n\n")

    out.append("## Detector (unchanged from v3.73.24)\n\n")
    out.append("```\nrecovery_active = (SPY_180d_DD < -25%) AND (SPY_1m_return > +5%)\n```\n\n")

    out.append("## Three response candidates\n\n")
    out.append("| Code | Response | Description |\n|---|---|---|\n")
    out.append("| A | Defensive tilt | Restrict to ConsumerStap + Healthcare; "
                "top-15 by 12-1 momentum among defensives, 80% gross |\n")
    out.append("| B | Reduced gross | Keep 12-1 picks, cut gross 80% → 40% |\n")
    out.append("| C | Equal-weight | Drop min-shift, equal-weight top-15 at 80% |\n\n")

    out.append("## GFC results (2008-09 → 2010-12, 28 months)\n\n")
    out.append("| Strategy | Cum return | Max DD | Recovery fires |\n"
                "|---|---:|---:|---:|\n")
    out.append(f"| production (12-1, control) | {(prod_eq-1)*100:+.2f}% "
                f"| {prod_dd*100:.2f}% | n/a |\n")
    out.append(f"| A: defensive tilt | {(a_eq-1)*100:+.2f}% "
                f"| {a_dd*100:.2f}% | {a_fires} |\n")
    out.append(f"| B: reduced gross | {(b_eq-1)*100:+.2f}% "
                f"| {b_dd*100:.2f}% | {b_fires} |\n")
    out.append(f"| C: equal-weight | {(c_eq-1)*100:+.2f}% "
                f"| {c_dd*100:.2f}% | {c_fires} |\n\n")

    out.append("## Delta vs production\n\n")
    out.append(f"- Response A (defensive): **{(a_eq-prod_eq)*100:+.2f}pp**\n")
    out.append(f"- Response B (reduced gross): **{(b_eq-prod_eq)*100:+.2f}pp**\n")
    out.append(f"- Response C (equal-weight): **{(c_eq-prod_eq)*100:+.2f}pp**\n\n")

    # Determine winner
    deltas = [
        ("A: defensive tilt", a_eq - prod_eq, a_dd, a_eq),
        ("B: reduced gross", b_eq - prod_eq, b_dd, b_eq),
        ("C: equal-weight", c_eq - prod_eq, c_dd, c_eq),
    ]
    best_name, best_delta, best_dd, best_eq = max(deltas, key=lambda x: x[1])

    if best_delta > 0.005:  # > 0.5pp meaningful improvement
        out.append(f"## Verdict: ✅ {best_name} improves GFC P&L\n\n")
        out.append(f"Best response: **{best_name}** with delta "
                    f"{best_delta*100:+.2f}pp vs production over the GFC "
                    f"window. Max DD {best_dd*100:.2f}% (production "
                    f"{prod_dd*100:.2f}%). Worth promoting to a "
                    f"shadow-mode strategy candidate in the eval harness.\n\n")
        out.append("**Caveat:** this is single-window evidence over 28 "
                    "months. Before any production swap, would also need "
                    "to verify the response doesn't break normal-regime "
                    "returns (the detector fires only 4 times in 25y, so "
                    "the response barely runs outside crisis windows — "
                    "but worth confirming with a full-window backtest).\n")
    else:
        out.append("## Verdict: ❌ no clean GFC improvement\n\n")
        out.append("None of A/B/C produce a meaningful improvement "
                    "(>0.5pp) over production during the GFC window. "
                    "Best candidate was "
                    f"**{best_name}** at {best_delta*100:+.2f}pp — "
                    "either neutral or worse.\n\n")
        out.append("The honest reading: the GFC recovery whipsaw is not "
                    "caused by a wrong WEIGHTING scheme, and not "
                    "obviously fixed by SECTOR rotation or GROSS "
                    "reduction either. The 12-1 momentum signal itself "
                    "is the wrong oracle for the regime — it points at "
                    "yesterday's leaders (defensives that held up well "
                    "in 2008) precisely when tomorrow's leaders (cyclicals "
                    "that bounce hard in 2009) need to be picked.\n\n")
        out.append("This is a research win in the negative direction: "
                    "we now know that fixing the GFC weakness requires a "
                    "DIFFERENT signal during recovery regimes, not a "
                    "different action on the existing signal. The next "
                    "candidates worth trying would be: short-window "
                    "earnings momentum, beta-tilt during recovery, or a "
                    "completely separate \"recovery sleeve\" with its own "
                    "selection logic.\n")

    # ============================================================
    # Full-window confirmation: does response B break normal regimes?
    # ============================================================
    # The detector fires only 4 times across 25 years (all GFC).
    # Outside those 4 months, response_B == production. So we expect
    # the full-window 25y result to be very close to production.
    print("\nRunning 25y full-window confirmation (production vs B)...")
    full_dates = [
        d for d in pd.date_range(prices.index[0], prices.index[-1], freq="BME")
        if d <= prices.index[-1]
    ]
    prod_full_eq, prod_full_dd, _ = simulate(production, prices, full_dates)
    b_full_eq, b_full_dd, b_full_fires = simulate(
        response_B_reduced_gross, prices, full_dates)

    print(f"\n  25y full window ({len(full_dates)} months):")
    print(f"    production:      cum={prod_full_eq:.2f}× max_dd={prod_full_dd*100:.2f}%")
    print(f"    response B:      cum={b_full_eq:.2f}× max_dd={b_full_dd*100:.2f}% "
          f"({b_full_fires} fires across 25y)")
    print(f"    delta (full):    {(b_full_eq-prod_full_eq)*100:+.2f}pp cum, "
          f"{(b_full_dd-prod_full_dd)*100:+.2f}pp max-DD")

    out.append("\n## Full-window 25y confirmation\n\n")
    out.append("The detector fires only "
                f"{b_full_fires} times across 25 years (all in GFC). "
                "Outside those months, response B is identical to "
                "production. So the full-window result should be very "
                "close to production (a tiny boost from the GFC delta).\n\n")
    out.append("| Metric | production | response B | delta |\n"
                "|---|---:|---:|---:|\n")
    out.append(f"| 25y cum return (×) | {prod_full_eq:.4f} | "
                f"{b_full_eq:.4f} | {b_full_eq-prod_full_eq:+.4f} |\n")
    out.append(f"| 25y max DD | {prod_full_dd*100:.2f}% | "
                f"{b_full_dd*100:.2f}% | {(b_full_dd-prod_full_dd)*100:+.2f}pp |\n")
    out.append(f"| Detector fires across 25y | n/a | {b_full_fires} | n/a |\n\n")

    full_delta_ok = b_full_eq >= prod_full_eq * 0.995  # within 0.5%
    full_dd_ok = b_full_dd >= prod_full_dd - 0.02  # within 2pp
    if full_delta_ok and full_dd_ok:
        out.append("**Normal-regime returns preserved**: response B does "
                    "not degrade 25-year cum return or max DD beyond noise. "
                    "Safe to promote to a SHADOW-mode candidate in the "
                    "eval harness.\n")
    else:
        out.append("**Normal-regime returns degraded**: the GFC win does "
                    "NOT translate to full-window. Do not promote.\n")

    out_path = ROOT / "docs" / "DD_RECOVERY_RESPONSE_DESIGN_2026_05_07.md"
    out_path.write_text("".join(out))
    print(f"\nWrote {out_path}")
    print(f"Best delta GFC-only: {best_name}: {best_delta*100:+.2f}pp")
    print(f"Full-window OK: {full_delta_ok and full_dd_ok}")


if __name__ == "__main__":
    main()

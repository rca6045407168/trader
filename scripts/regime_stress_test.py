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

============================================================================
v3.3 RESULTS (2026-04-27 run) — top3_eq_80 confirmed robust; new variants killed
============================================================================
Top-3 family ties for best mean Sharpe (+1.48) across all 5 regimes.
Allocation (40/80/100) only scales return + DD linearly — Sharpe unchanged.

KILLED candidates (do NOT promote):
  - dual_momentum_gem (Antonacci): -0.36 Sharpe — bad regime timer (long AGG into
    rate-cut '18, long SPY into '22 bear).
  - combined_top3_dual (50/50): +1.01 Sharpe — dual half drags.
  - top3_80 + anomaly_overlay (pre-FOMC + pre-holiday SPY tilt): +0.83 Sharpe —
    overlay COST -14pp in 2022 bear (longing SPY into anomaly windows during a
    selloff is a worst-case pattern).
  - anomaly_only_spy: -1.01 Sharpe — calendar anomalies have no standalone alpha.

============================================================================
v3.5 RESULTS (2026-04-28 run) — regime-aware meta-allocator KILLED
============================================================================
Tested: SPY 200d MA + VIX > 25 + 3mo/12mo top-pick overlap → route to
TREND (12mo at 80%) / ROTATION (3/6/12 blend at 80%) / STRESS (50% SPY).
Result: ranks #13 of 18 variants. Loses to LIVE on every aggregate metric
AND only wins 1.5 of 5 regimes (gate required ≥3).

Per-regime: -14pp 2018-Q4, -34pp 2020-Q1 (V-shape recovery; defensive cut
caught us at the bottom), +2.6pp 2022, +5pp 2023, -33pp recent (tripped
ROTATION when LIVE's pure 12mo signal was working).

System-design takeaway: detection signals are real but reactive switching
costs more than it saves. Momentum strategies already have built-in regime
adaptation via monthly rebalance — adding an explicit regime layer creates
double-counting + whipsaw. Don't try this again without ENTIRELY different
actions per regime (e.g. position-sizing tweaks, NOT asset-class swaps).

LIVE remains: momentum_top3_aggressive_v1 (top-3 at 80%). v3.2 shadow
(top3_full_deploy at 100%) keeps running to gather live evidence on the
upside-vs-DD tradeoff. v3.4 added top3_blend_3_6_12 + top3_lookback_6mo as
shadows for live A/B evidence on lookback-horizon question.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math
import statistics
from functools import lru_cache
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50
from trader.sectors import get_sector
from trader.anomalies import scan_anomalies, KNOWN_FOMC_DATES_2026, US_HOLIDAYS_2026, _third_friday_of_month
from trader.regime import classify_regime, Regime


REGIMES = [
    ("2018-Q4 selloff",     pd.Timestamp("2018-09-01"), pd.Timestamp("2019-03-31")),
    ("2020-Q1 COVID",       pd.Timestamp("2020-01-15"), pd.Timestamp("2020-06-30")),
    ("2022 bear",           pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
    ("2023 AI-rally",       pd.Timestamp("2023-04-01"), pd.Timestamp("2023-10-31")),
    ("recent 3 months",     pd.Timestamp.today() - pd.Timedelta(days=95), pd.Timestamp.today()),
]


@lru_cache(maxsize=4096)
def _cached_picks_for_month(year, month, top_n, lookback_months, skip_months):
    """Compute momentum picks once per (year, month, params) — picks are stable
    intra-month since the 12-month lookback barely changes day-to-day."""
    # Use last business day of the prior month as as_of (proxy for month-end rebalance)
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:  # back up to Friday if Sat/Sun
        as_of -= pd.Timedelta(days=1)
    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50, start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return tuple()
    if prices.empty or len(prices) < L + S:
        return tuple()
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    return tuple(rets.nlargest(top_n).index.tolist())


def _momentum_picks_as_of(as_of, top_n=5, lookback_months=12, skip_months=1):
    return list(_cached_picks_for_month(as_of.year, as_of.month, top_n, lookback_months, skip_months))


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


# ---------------------------------------------------------------------------
# Lookback-horizon variants — addresses 2023 AI rally underperformance
# ---------------------------------------------------------------------------
# v3.3 finding: LIVE (12mo lookback) underperformed SPY by -3.4pp in 2023 because
# 12mo momentum was too slow to catch the NVDA/META rotation. Faster lookbacks
# (3mo, 6mo) react quicker but historically had worse Sharpe due to noise.
# Hypothesis: a horizon BLEND (e.g. equal weight across 3/6/12mo lookbacks) gets
# better worst-regime behavior at modest cost to mean.

def variant_top3_lookback_3mo(as_of):
    """Fast momentum: 3-month lookback, top-3, 80% allocation."""
    p = _momentum_picks_as_of(as_of, 3, lookback_months=3)
    return {x: 0.80 / 3 for x in p} if p else {}


def variant_top3_lookback_6mo(as_of):
    """Medium momentum: 6-month lookback, top-3, 80% allocation."""
    p = _momentum_picks_as_of(as_of, 3, lookback_months=6)
    return {x: 0.80 / 3 for x in p} if p else {}


def variant_top3_blend_3_6_12(as_of):
    """Multi-horizon blend: each of 3/6/12mo gets a top-3 sleeve at 26.7%
    (80% gross / 3 sleeves). When all three horizons agree, those names get
    overweighted naturally. When they disagree, we get diversification."""
    p3 = _momentum_picks_as_of(as_of, 3, lookback_months=3)
    p6 = _momentum_picks_as_of(as_of, 3, lookback_months=6)
    p12 = _momentum_picks_as_of(as_of, 3, lookback_months=12)
    if not (p3 or p6 or p12):
        return {}
    targets = {}
    sleeve_w = 0.80 / 3  # 26.7% per sleeve
    for picks in (p3, p6, p12):
        if not picks:
            continue
        per_pick = sleeve_w / len(picks)
        for sym in picks:
            targets[sym] = targets.get(sym, 0) + per_pick
    return targets


def variant_top3_blend_6_12(as_of):
    """Two-horizon blend: 6mo + 12mo only. Drops the noisy 3mo signal."""
    p6 = _momentum_picks_as_of(as_of, 3, lookback_months=6)
    p12 = _momentum_picks_as_of(as_of, 3, lookback_months=12)
    if not (p6 or p12):
        return {}
    targets = {}
    sleeve_w = 0.80 / 2  # 40% per sleeve
    for picks in (p6, p12):
        if not picks:
            continue
        per_pick = sleeve_w / len(picks)
        for sym in picks:
            targets[sym] = targets.get(sym, 0) + per_pick
    return targets


# ---------------------------------------------------------------------------
# Regime-aware meta-allocator (v3.5)
# ---------------------------------------------------------------------------
# Picks the strategy based on detected regime instead of always using 12mo:
#   TREND    → top-3 12mo at 80% (= current LIVE)
#   ROTATION → top-3 multi-horizon blend at 80%
#   STRESS   → 50% SPY (defensive cut)
#
# Test gate: must dominate LIVE in >= 3 of 5 regimes AND not have worse
# worst-MaxDD. If it fails the gate, do not promote. This is the most
# overfittable concept in the backlog — strict discipline required.

_VIX_CACHE: dict = {}
_SPY_CACHE: dict = {}


def _get_vix_history(start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.Series:
    """Fetch + cache ^VIX series for the given window."""
    key = (start_date.date(), end_date.date())
    if key in _VIX_CACHE:
        return _VIX_CACHE[key]
    try:
        df = fetch_history(["^VIX"],
                           start=start_date.strftime("%Y-%m-%d"),
                           end=end_date.strftime("%Y-%m-%d"))
        s = df["^VIX"].dropna() if "^VIX" in df.columns else pd.Series(dtype=float)
    except Exception:
        s = pd.Series(dtype=float)
    _VIX_CACHE[key] = s
    return s


def _get_spy_history(start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.Series:
    """Fetch + cache SPY series for the given window."""
    key = (start_date.date(), end_date.date())
    if key in _SPY_CACHE:
        return _SPY_CACHE[key]
    try:
        df = fetch_history(["SPY"],
                           start=start_date.strftime("%Y-%m-%d"),
                           end=end_date.strftime("%Y-%m-%d"))
        s = df["SPY"].dropna() if "SPY" in df.columns else pd.Series(dtype=float)
    except Exception:
        s = pd.Series(dtype=float)
    _SPY_CACHE[key] = s
    return s


def variant_regime_aware(as_of):
    """Meta-allocator: route to TREND / ROTATION / STRESS strategy by regime."""
    # Fetch regime inputs — broad window so 200d MA always has enough history
    spy_hist_start = as_of - pd.Timedelta(days=400)
    spy = _get_spy_history(spy_hist_start, as_of)
    vix = _get_vix_history(as_of - pd.Timedelta(days=10), as_of)
    vix_now = float(vix.iloc[-1]) if len(vix) else None

    picks_3 = _momentum_picks_as_of(as_of, 3, lookback_months=3)
    picks_12 = _momentum_picks_as_of(as_of, 3, lookback_months=12)

    if len(spy) == 0:
        # No data — default to LIVE behavior
        return {x: 0.80 / 3 for x in picks_12} if picks_12 else {}

    sig = classify_regime(
        spy_prices=spy,
        asof=as_of,
        vix=vix_now,
        picks_3mo=picks_3,
        picks_12mo=picks_12,
    )

    if sig.regime == Regime.STRESS:
        # 50% SPY only; no individual-name exposure during stress
        return {"SPY": 0.50}

    if sig.regime == Regime.ROTATION:
        # Multi-horizon blend at 80% gross
        picks_6 = _momentum_picks_as_of(as_of, 3, lookback_months=6)
        targets = {}
        sleeve_w = 0.80 / 3
        for picks in (picks_3, picks_6, picks_12):
            if not picks:
                continue
            per_pick = sleeve_w / len(picks)
            for sym in picks:
                targets[sym] = targets.get(sym, 0) + per_pick
        return targets

    # TREND: 12mo at 80% (matches LIVE)
    return {x: 0.80 / 3 for x in picks_12} if picks_12 else {}


def variant_dual_momentum_gem(as_of):
    """Antonacci-style Global Equities Momentum:
    - Compute trailing 12m return on SPY, ACWX (intl), AGG (bonds)
    - If SPY 12m > T-bill (proxy: 4%), allocate to whichever of SPY / ACWX has higher 12m
    - Else allocate to AGG (defensive)
    - 100% allocation to one ETF
    """
    end = as_of
    start = end - pd.Timedelta(days=400)  # ~13 months
    try:
        prices = fetch_history(["SPY", "ACWX", "AGG"], start=start.strftime("%Y-%m-%d"),
                              end=end.strftime("%Y-%m-%d"))
    except Exception:
        return {}
    if prices.empty or len(prices) < 252:
        return {}
    rets_12m = (prices.iloc[-1] / prices.iloc[-252] - 1).dropna()
    spy_12m = rets_12m.get("SPY", float("nan"))
    if pd.isna(spy_12m):
        return {}
    t_bill_threshold = 0.04
    if spy_12m > t_bill_threshold:
        # Pick higher of SPY vs ACWX
        equities = rets_12m[rets_12m.index.isin(["SPY", "ACWX"])]
        if equities.empty:
            return {"SPY": 1.00}
        winner = equities.idxmax()
        return {winner: 1.00}
    return {"AGG": 1.00}


def variant_combined_momentum_dual(as_of):
    """Combination: 50% top-3 momentum + 50% dual-momentum GEM.
    Half the portfolio gets aggressive concentration; half gets regime-conditional defense.
    """
    mom_picks = _momentum_picks_as_of(as_of, 3)
    gem = variant_dual_momentum_gem(as_of)
    targets = {p: 0.50 / len(mom_picks) for p in mom_picks} if mom_picks else {}
    for sym, w in gem.items():
        targets[sym] = targets.get(sym, 0) + 0.50 * w
    return targets


# ---------------------------------------------------------------------------
# Scheduled-routine signal incorporation
# ---------------------------------------------------------------------------
# The trader-anomaly-scan scheduled task surfaces calendar anomalies. The two
# with strongest empirical edge in OUR 2015-2025 backtest were:
#   - Pre-FOMC drift (+22bps avg, Sharpe 2.35) — high confidence
#   - Pre-holiday drift (+12bps avg, 64.8% win) — medium confidence
# Both are 1-day SPY-long tilts. To replay across regimes we need historical
# FOMC + holiday schedules going back to 2018 (anomalies module only has 2026).

# FOMC meeting dates 2018-2026 (announcement days, approximate from Fed records)
HISTORICAL_FOMC_DATES = [
    # 2018
    pd.Timestamp("2018-01-31"), pd.Timestamp("2018-03-21"), pd.Timestamp("2018-05-02"),
    pd.Timestamp("2018-06-13"), pd.Timestamp("2018-08-01"), pd.Timestamp("2018-09-26"),
    pd.Timestamp("2018-11-08"), pd.Timestamp("2018-12-19"),
    # 2019
    pd.Timestamp("2019-01-30"), pd.Timestamp("2019-03-20"), pd.Timestamp("2019-05-01"),
    pd.Timestamp("2019-06-19"), pd.Timestamp("2019-07-31"), pd.Timestamp("2019-09-18"),
    pd.Timestamp("2019-10-30"), pd.Timestamp("2019-12-11"),
    # 2020
    pd.Timestamp("2020-01-29"), pd.Timestamp("2020-03-15"),  # emergency cut
    pd.Timestamp("2020-04-29"), pd.Timestamp("2020-06-10"), pd.Timestamp("2020-07-29"),
    pd.Timestamp("2020-09-16"), pd.Timestamp("2020-11-05"), pd.Timestamp("2020-12-16"),
    # 2021
    pd.Timestamp("2021-01-27"), pd.Timestamp("2021-03-17"), pd.Timestamp("2021-04-28"),
    pd.Timestamp("2021-06-16"), pd.Timestamp("2021-07-28"), pd.Timestamp("2021-09-22"),
    pd.Timestamp("2021-11-03"), pd.Timestamp("2021-12-15"),
    # 2022
    pd.Timestamp("2022-01-26"), pd.Timestamp("2022-03-16"), pd.Timestamp("2022-05-04"),
    pd.Timestamp("2022-06-15"), pd.Timestamp("2022-07-27"), pd.Timestamp("2022-09-21"),
    pd.Timestamp("2022-11-02"), pd.Timestamp("2022-12-14"),
    # 2023
    pd.Timestamp("2023-02-01"), pd.Timestamp("2023-03-22"), pd.Timestamp("2023-05-03"),
    pd.Timestamp("2023-06-14"), pd.Timestamp("2023-07-26"), pd.Timestamp("2023-09-20"),
    pd.Timestamp("2023-11-01"), pd.Timestamp("2023-12-13"),
    # 2024
    pd.Timestamp("2024-01-31"), pd.Timestamp("2024-03-20"), pd.Timestamp("2024-05-01"),
    pd.Timestamp("2024-06-12"), pd.Timestamp("2024-07-31"), pd.Timestamp("2024-09-18"),
    pd.Timestamp("2024-11-07"), pd.Timestamp("2024-12-18"),
    # 2025
    pd.Timestamp("2025-01-29"), pd.Timestamp("2025-03-19"), pd.Timestamp("2025-05-07"),
    pd.Timestamp("2025-06-18"), pd.Timestamp("2025-07-30"), pd.Timestamp("2025-09-17"),
    pd.Timestamp("2025-10-29"), pd.Timestamp("2025-12-10"),
    # 2026
    pd.Timestamp("2026-01-28"), pd.Timestamp("2026-03-18"), pd.Timestamp("2026-04-29"),
    pd.Timestamp("2026-06-17"), pd.Timestamp("2026-07-29"), pd.Timestamp("2026-09-16"),
    pd.Timestamp("2026-10-28"), pd.Timestamp("2026-12-09"),
]


def _us_holidays_for_year(year):
    """Approximate US market holidays per year."""
    return [
        pd.Timestamp(f"{year}-01-01"),                          # New Year's
        pd.Timestamp(f"{year}-01-15") + pd.tseries.offsets.Week(weekday=0),  # MLK 3rd Mon ~Jan 18-21
        pd.Timestamp(f"{year}-02-15") + pd.tseries.offsets.Week(weekday=0),  # Presidents 3rd Mon
        pd.Timestamp(f"{year}-04-03"),                          # Good Friday (approximate; varies)
        pd.Timestamp(f"{year}-05-25") + pd.tseries.offsets.Week(weekday=0),  # Memorial last Mon
        pd.Timestamp(f"{year}-07-04"),                          # July 4
        pd.Timestamp(f"{year}-09-01") + pd.tseries.offsets.Week(weekday=0),  # Labor 1st Mon
        pd.Timestamp(f"{year}-11-22") + pd.tseries.offsets.Week(weekday=3),  # Thanksgiving 4th Thu
        pd.Timestamp(f"{year}-12-25"),                          # Christmas
    ]


def _is_pre_fomc(asof):
    """True if asof is the day before an FOMC announcement."""
    asof_d = pd.Timestamp(asof.date()) if hasattr(asof, "date") else pd.Timestamp(asof)
    for f in HISTORICAL_FOMC_DATES:
        if (f - asof_d).days == 1:
            return True
    return False


def _is_pre_holiday(asof):
    """True if asof is the day before a US market holiday."""
    asof_d = pd.Timestamp(asof.date()) if hasattr(asof, "date") else pd.Timestamp(asof)
    for h in _us_holidays_for_year(asof_d.year):
        if (h - asof_d).days == 1:
            return True
    return False


def variant_top3_80_anomaly_overlay(as_of):
    """v3.1 LIVE (top-3 at 80%) + tactical SPY tilt during pre-FOMC and pre-holiday days.

    On rebalance days that fall on/right before pre-FOMC or pre-holiday, ADD 10% SPY
    on top of the momentum book (using cash buffer). On normal days, just top-3 at 80%.
    Replay engine must call this daily so it can react to anomaly windows mid-month.
    """
    p = _momentum_picks_as_of(as_of, 3)
    if not p:
        return {}
    targets = {x: 0.80 / 3 for x in p}
    if _is_pre_fomc(as_of) or _is_pre_holiday(as_of):
        # Tactical 10% SPY add — uses cash buffer; doesn't replace momentum
        targets["SPY"] = targets.get("SPY", 0) + 0.10
    return targets


def variant_anomaly_only_spy(as_of):
    """Pure anomaly sleeve: 100% SPY only on pre-FOMC + pre-holiday days, else 100% cash.

    Tests whether the anomaly signal alone has standalone alpha. If this loses to cash,
    the overlay variant's tilt isn't accretive — just noise.
    """
    if _is_pre_fomc(as_of) or _is_pre_holiday(as_of):
        return {"SPY": 1.00}
    return {}  # all cash


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
    "dual_momentum_gem (Antonacci)": variant_dual_momentum_gem,
    "combined_top3_dual (50/50)": variant_combined_momentum_dual,
    "top3_80 + anomaly overlay": variant_top3_80_anomaly_overlay,
    "anomaly_only_spy (sleeve test)": variant_anomaly_only_spy,
    "top3_lookback_3mo": variant_top3_lookback_3mo,
    "top3_lookback_6mo": variant_top3_lookback_6mo,
    "top3_blend_3_6_12 (multi-horizon)": variant_top3_blend_3_6_12,
    "top3_blend_6_12 (med + slow)": variant_top3_blend_6_12,
    "regime_aware_meta (v3.5)": variant_regime_aware,
}

# Daily-decision variants — see DAILY_DECISION_VARIANTS list above

# Variants that need daily decision evaluation (their weights change inside a month
# due to calendar anomalies, regime detection, etc). Default = monthly only.
DAILY_DECISION_VARIANTS = {
    "top3_80 + anomaly overlay",
    "anomaly_only_spy (sleeve test)",
}


def replay_window(variant_name, fn, start, end):
    bdays = pd.bdate_range(start, end)
    decisions = []
    daily_decisions = variant_name in DAILY_DECISION_VARIANTS
    for d in bdays:
        if daily_decisions:
            # Call the variant every business day; only record when weights change
            t = fn(d)
            t_dict = dict(t) if t else {}
            if not decisions:
                if t_dict:
                    decisions.append((d, t_dict))
            else:
                last_t = decisions[-1][1]
                if t_dict != last_t:
                    decisions.append((d, t_dict))
        else:
            # Standard monthly rebalance at month-end
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

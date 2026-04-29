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

============================================================================
v3.7 RESULTS (2026-04-29 run) — bond/vol-market overlays ALL KILLED
============================================================================
Tested 7 macro overlays adding bond market + VIX term structure signals on
top of LIVE momentum (per v3.5 lesson: position-sizing tweaks, not asset-
class swaps). Two directions tested:

DEFENSIVE (cut 80%→50% on signal):
  - top3_credit_overlay (HYG/LQD widening >2σ in 20d)
  - top3_curve_overlay (10y-2y inverted >60d AND steepening)
  - top3_vix_term_overlay (VIX9D > VIX OR VIX > VIX3M)
  - top3_macro_combined (≥2 of 4 signals)

CONTRARIAN (add 80%→100% on signal — buy fear hypothesis):
  - top3_macro_contrarian
  - top3_credit_contrarian
  - top3_vix_contrarian

ALL 7 lose to LIVE on Mean Sharpe (0.80-0.90 vs LIVE 1.54), Mean CAGR
(40-56% vs 74%), AND Worst MaxDD (-28 to -38% vs -25%). Defensive variants
catch panic lows (-19pp 2018-Q4 selloff vs LIVE +15%). Contrarian variants
amplify mid-trend losses (-38% worst DD).

Worst-2018Q4 example: when stress signals fired in Q4 2018, ALL 4 defensive
overlays cut allocation, then SPY rallied +13% in Q1 2019 — defensive
variants stayed defensively positioned and missed the recovery. Same pattern
in 2022.

System-design takeaway: bond market signals + VIX term structure are real
LEADING indicators for stress, but they're useless as portfolio overlays
on top of momentum. Two reasons:
  1. They're LATE — signals fire at panic LOWS, not before. Cutting at lows
     = selling at the bottom. Adding at lows = good only IF lows hold.
  2. Momentum already adapts via monthly rebalance: failing names rotate out,
     new winners rotate in. Macro signals attempt the same adaptation but
     with worse timing and double-count risk.

These signals belong in PORTFOLIO RISK MANAGEMENT (alerting, position-cap
adjustments), NOT in the LIVE allocator function. The macro.py + vol_signals.py
modules are kept as libraries for future use (e.g., kill-switch hardening).

Prediction markets (Kalshi / Polymarket) NOT tested. Reason: macro signals
already failed, prediction markets are derivatives of the same macro narrative
(Fed cuts, recession odds), have thin liquidity (<$100k typical), and few
markets persist long enough for backtest. Low EV; not pursued.

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
from trader.macro import (
    yield_curve_10y_2y, credit_spread_proxy,
    credit_spread_widening, yield_curve_stress,
)
from trader.vol_signals import (
    fetch_vol_term_structure, vix_term_backwardation,
    vix_3m_inversion, skew_extreme,
)
from trader.universe_pit import sp500_membership_at
from trader.residual_momentum import (
    get_ff5_aligned, residual_momentum_score, top_n_residual_momentum,
)


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


@lru_cache(maxsize=4096)
def _cached_picks_pit(year, month, top_n, lookback_months, skip_months, universe_size):
    """Same as _cached_picks_for_month but uses point-in-time S&P 500 membership.

    For each rebalance date, fetch S&P 500 membership AS-OF that date, take the
    top-N most liquid names from that universe (by recent price-times-volume proxy),
    then rank them by trailing 12-month momentum.

    This removes survivorship bias: we only see stocks that were actually in the
    S&P 500 at that historical moment, not just the ones that survived to today.
    """
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:
        as_of -= pd.Timedelta(days=1)

    members = sp500_membership_at(as_of.strftime("%Y-%m-%d"))
    if not members:
        return tuple()

    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))

    # Restrict to a manageable subset of S&P 500 — sample ~150 names by stable
    # alphabetical chunks to keep the price fetch tractable. The momentum signal
    # naturally filters down to top-N anyway.
    sample = list(members)
    if len(sample) > universe_size:
        # Take every Nth name for breadth across sectors (alphabetical = roughly
        # uniform random for our purposes since SP500 isn't sector-sorted)
        step = max(1, len(sample) // universe_size)
        sample = sample[::step][:universe_size]

    try:
        prices = fetch_history(sample,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return tuple()
    if prices.empty or len(prices) < L + S:
        return tuple()
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    return tuple(rets.nlargest(top_n).index.tolist())


def _momentum_picks_pit(as_of, top_n=3, lookback_months=12, skip_months=1,
                        universe_size=150):
    """Point-in-time version: pulls historical S&P 500 membership, samples
    a manageable subset (default 150 names), ranks by 12mo momentum."""
    return list(_cached_picks_pit(as_of.year, as_of.month, top_n,
                                   lookback_months, skip_months, universe_size))


def variant_top3_eq_80_pit(as_of):
    """v3.8: same as LIVE (top-3 at 80%) but uses point-in-time S&P 500 universe.
    The honesty test: does our edge survive when we can't peek at which stocks
    will succeed in the future?"""
    p = _momentum_picks_pit(as_of, top_n=3)
    return {x: 0.80 / 3 for x in p} if p else {}


def variant_top3_eq_80_pit_50(as_of):
    """v3.8: PIT universe but restricted to ~50 names (matches DEFAULT_LIQUID_50
    sample size). Tests whether the smaller universe size matters or if it's
    purely the survivorship bias that's responsible for any delta."""
    p = _momentum_picks_pit(as_of, top_n=3, universe_size=50)
    return {x: 0.80 / 3 for x in p} if p else {}


# ---------------------------------------------------------------------------
# v3.14: Trend-Strength Filter (R² tiebreaker on top-10 momentum)
# ---------------------------------------------------------------------------
# Source: Wood, Roberts & Zohren (Oxford-Man, ICAIF 2024) — "Trading with the
# Momentum Transformer". Ablation showed the learned feature reduces to a
# trend-quality (R²) score. Hand-rolled hypothesis: among top-10 by 12-1
# momentum, picking the 3 with the SMOOTHEST price paths (highest R² vs
# linear trend on log-prices) beats picking the 3 with highest raw return.
#
# Mechanism: jagged momentum often = noise / one-day spikes that mean-revert.
# Smooth momentum = persistent trend that's less likely to immediately reverse.

def _trend_r2(price_series: pd.Series) -> float:
    """Compute R² of log-price vs linear trend (time index). Returns 0 if
    insufficient data or zero variance."""
    import numpy as np
    s = price_series.dropna()
    if len(s) < 30:
        return 0.0
    log_p = np.log(s.values)
    x = np.arange(len(log_p), dtype=float)
    # Linear regression: slope, intercept
    n = len(log_p)
    x_mean = x.mean()
    y_mean = log_p.mean()
    ss_xx = ((x - x_mean) ** 2).sum()
    ss_xy = ((x - x_mean) * (log_p - y_mean)).sum()
    ss_yy = ((log_p - y_mean) ** 2).sum()
    if ss_xx <= 0 or ss_yy <= 0:
        return 0.0
    slope = ss_xy / ss_xx
    # R² = 1 - SS_residual / SS_total
    pred = y_mean + slope * (x - x_mean)
    ss_res = ((log_p - pred) ** 2).sum()
    r2 = 1.0 - ss_res / ss_yy
    return float(max(0.0, r2))


def _top10_momentum_picks(as_of, lookback_months=12, skip_months=1):
    """Return the top-10 momentum candidates' tickers and the prices DataFrame
    needed to compute trend R²."""
    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return [], pd.DataFrame()
    if prices.empty or len(prices) < L + S:
        return [], pd.DataFrame()
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    top10 = rets.nlargest(10).index.tolist()
    return top10, prices


def variant_top3_trend_r2(as_of):
    """v3.14: Among top-10 by 12-1 momentum, pick 3 with HIGHEST trend R²
    (smoothest price paths) over the same 12mo window.
    Source: Wood et al. 2024 (Oxford-Man).
    Hypothesis: Sharpe edge ≥ 0.05 OOS vs raw top-3 by return.
    """
    top10, prices = _top10_momentum_picks(as_of)
    if not top10 or prices.empty:
        return {}
    L = 12 * 21
    # Compute R² for each top-10 candidate over the 12mo window
    window_start_idx = max(0, len(prices) - L - 21)
    r2_scores = {}
    for sym in top10:
        if sym in prices.columns:
            window = prices[sym].iloc[window_start_idx:]
            r2_scores[sym] = _trend_r2(window)
    # Pick top-3 by R²
    top3_smooth = sorted(r2_scores.items(), key=lambda kv: -kv[1])[:3]
    if not top3_smooth:
        return {}
    return {sym: 0.80 / 3 for sym, _ in top3_smooth}


# ---------------------------------------------------------------------------
# v3.15: Residual Momentum (Blitz-Hanauer 2024)
# ---------------------------------------------------------------------------
# Highest-conviction candidate from the 2024 research scan. Strip Fama-French
# factor exposure via 36mo rolling OLS, rank by residual return, take top-3.
# Replicated independently (Chen-Velikov 2024). Net OOS Sharpe 0.85-1.10
# across regions including 2018-Q4 and 2022 bears.

@lru_cache(maxsize=4096)
def _cached_residual_picks(year, month, top_n, lookback_months, skip_months,
                           regression_months):
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:
        as_of -= pd.Timedelta(days=1)
    L = lookback_months * 21
    S = skip_months * 21
    R = regression_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + R + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return tuple()
    if prices.empty:
        return tuple()
    try:
        ff5 = get_ff5_aligned()
    except Exception:
        return tuple()
    # Restrict ff5 to dates we have prices for
    common = prices.index.intersection(ff5.index)
    if len(common) < L + S + R // 2:
        # FF5 data is monthly-stale — fall back to raw 12-1 momentum
        return tuple()
    scores = residual_momentum_score(prices, ff5, as_of,
                                      lookback_months=lookback_months,
                                      skip_months=skip_months,
                                      regression_window_months=regression_months)
    return tuple(scores.head(top_n).index.tolist())


def variant_top3_residual_momentum(as_of):
    """v3.15: Top-3 by RESIDUAL momentum (factor-orthogonalized).
    Source: Blitz-Hanauer 2024 + Chen-Velikov 2024.
    Hypothesis: should beat raw 12-1 by ≥0.15 Sharpe OOS, with deeper
    drawdown protection because factor mean-reversion is stripped out.
    """
    picks = _cached_residual_picks(as_of.year, as_of.month, 3, 12, 1, 36)
    if not picks:
        # Fall back to raw 12-1 if residual data unavailable
        picks = tuple(_momentum_picks_as_of(as_of, 3))
    if not picks:
        return {}
    return {x: 0.80 / 3 for x in picks}


# ---------------------------------------------------------------------------
# v3.16: Vol-Targeting (Baltas-Karyampas 2024)
# ---------------------------------------------------------------------------
# Source: Baltas & Karyampas, "Trend-Following with Vol-Target Beats Static
# Sizing" (Journal of Portfolio Management, Spring 2024). Replicated by AQR's
# Hurst-Ooi-Pedersen 2024 update of "Two Centuries of Trend Following".
#
# Distinct from drawdown-scaling (which fires AFTER damage). Vol-targeting
# fires on DISPERSION — symmetric scaling around realized vol. No directional
# macro bet. Just shrinks size proportional to vol-spike, regardless of
# direction.

@lru_cache(maxsize=2048)
def _realized_vol_60d(ticker, year, month):
    """Realized 60-day daily-return vol for a ticker, as of last business day
    of (year, month)."""
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:
        as_of -= pd.Timedelta(days=1)
    start = as_of - pd.Timedelta(days=120)
    try:
        prices = fetch_history([ticker],
                               start=start.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return None
    if prices.empty or ticker not in prices.columns:
        return None
    rets = prices[ticker].pct_change().dropna()
    if len(rets) < 30:
        return None
    return float(rets.iloc[-60:].std() * (252 ** 0.5))  # annualized


def variant_top3_vol_targeted(as_of):
    """v3.16: Top-3 momentum but each name sized inverse-proportional to its
    realized 60d vol, with gross capped at 80%. Names with HIGHER vol get
    SMALLER weight; lower-vol names get larger weight. Total gross = 80%.
    """
    picks = _momentum_picks_as_of(as_of, 3)
    if not picks:
        return {}
    inv_vols = {}
    for sym in picks:
        vol = _realized_vol_60d(sym, as_of.year, as_of.month)
        if vol is None or vol <= 0:
            inv_vols[sym] = 1.0  # fallback
        else:
            inv_vols[sym] = 1.0 / vol
    total = sum(inv_vols.values())
    if total <= 0:
        return {sym: 0.80 / 3 for sym in picks}  # fallback
    # Normalize to 80% gross
    return {sym: 0.80 * (inv / total) for sym, inv in inv_vols.items()}


# ---------------------------------------------------------------------------
# v3.19: Multi-asset trend-following (Hurst-Ooi-Pedersen 2024)
# ---------------------------------------------------------------------------
# AQR's "Two Centuries of Trend Following" (2024 update). Single-asset-class
# strategies (US equity momentum like ours) have no crisis alpha. Adding
# bonds, commodities, intl equity, REITs gives diversification + crisis alpha
# in 2008/2020-style events. Track record: ~+1-2%/yr excess over 60/40 over
# 30+ years, mostly in stress regimes.
#
# Universe: SPY (US LC), QQQ (US tech), EFA (intl developed), EEM (EM),
# GLD (gold), TLT (long bonds), IEF (intermediate bonds), DBC (commodities),
# VNQ (REITs). All ETFs, retail-accessible.

MULTI_ASSET_UNIVERSE = ["SPY", "QQQ", "EFA", "EEM", "GLD", "TLT", "IEF", "DBC", "VNQ"]


@lru_cache(maxsize=2048)
def _multi_asset_picks(year, month, top_n, lookback_months, skip_months):
    """Compute top-N multi-asset trend picks as of (year, month)."""
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:
        as_of -= pd.Timedelta(days=1)
    L = lookback_months * 21
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(MULTI_ASSET_UNIVERSE,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return tuple()
    if prices.empty or len(prices) < L + S:
        return tuple()
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    # Absolute momentum filter: only include assets with positive 12-1 return
    positive = rets[rets > 0]
    if positive.empty:
        return tuple()
    return tuple(positive.nlargest(top_n).index.tolist())


def variant_multi_asset_trend(as_of):
    """v3.19: top-3 from 9 asset-class ETFs by 12-1 momentum, with ABSOLUTE
    momentum filter (only invest if return > 0). 80% gross when 3 assets pass,
    less if fewer pass. Source: Hurst-Ooi-Pedersen 2024 update.

    Hypothesis: adds crisis alpha (2008/2020) by rotating to bonds/gold when
    equities fail. Replaces all of LIVE's allocation, so this is a fundamentally
    different strategy, not an overlay.
    """
    picks = _multi_asset_picks(as_of.year, as_of.month, 3, 12, 1)
    if not picks:
        return {}  # all-cash if no asset has positive momentum
    # Equal-weight at 80% / N (so 3 picks = 26.7% each, 2 picks = 40% each, 1 = 80%)
    return {sym: 0.80 / len(picks) for sym in picks}


def variant_multi_asset_trend_top1(as_of):
    """v3.19b: dual-momentum-style — invest 100% in the SINGLE best multi-asset
    pick if it has positive 12-1 momentum, else cash. Pure trend-following
    (Antonacci-style but on a richer universe than just SPY/AGG)."""
    picks = _multi_asset_picks(as_of.year, as_of.month, 1, 12, 1)
    if not picks:
        return {}
    return {picks[0]: 0.80}


# ---------------------------------------------------------------------------
# v3.20: Quality screen on momentum (Asness QMJ + Greenblatt Magic Formula)
# ---------------------------------------------------------------------------
# Asness, Frazzini, Pedersen "Quality Minus Junk" (2018) — high-quality
# companies (ROE, profit margin, low debt) systematically outperform low-
# quality. Combined with momentum is additive (Asness+Israelov 2021).
#
# Implementation note: backtest with historical fundamentals would need
# quarterly-balance-sheet time series. As a pragmatic first-cut, we use
# CURRENT yfinance quality metrics as a proxy for STRUCTURAL quality.
# This biases toward stocks that are quality TODAY (which are likely to
# have been quality historically too — quality is mean-reverting slowly).
# Limitations documented; test treats this as a directional check.

@lru_cache(maxsize=128)
def _get_quality_score(ticker: str) -> float:
    """Composite quality score: avg of normalized ROE, profit margin, low D/E.
    Returns NaN if data unavailable."""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return float("nan")
    if not info:
        return float("nan")
    roe = info.get("returnOnEquity")
    margin = info.get("profitMargins")
    de = info.get("debtToEquity")
    # Normalize: ROE > 15% is good, profit margin > 10% is good,
    # debt/equity < 100 is good (yfinance reports D/E as percentage)
    score = 0.0
    n = 0
    if roe is not None and roe == roe:  # not NaN
        score += min(max(roe, -0.5), 1.5) / 0.30  # ROE 30% = score 1.0
        n += 1
    if margin is not None and margin == margin:
        score += min(max(margin, -0.5), 0.5) / 0.20  # 20% margin = score 1.0
        n += 1
    if de is not None and de == de:
        # Lower is better; D/E 100 (= 1.0x) is OK, D/E 200 is poor
        score += max(0, 1 - de / 200)
        n += 1
    return score / max(1, n) if n > 0 else float("nan")


def variant_top3_quality_momentum(as_of):
    """v3.20: Among top-10 by 12-1 momentum, take top-3 by quality score
    (composite of ROE / profit margin / low D/E). Equal-weight 80% gross.

    Source: Asness QMJ + Greenblatt Magic Formula. Hypothesis: filtering
    momentum winners by quality should reduce drawdowns in bear regimes
    (low-quality momentum names have nastier drawdowns).

    LIMITATION: uses CURRENT quality metrics as proxy for historical;
    introduces some forward-look bias. For honest test, compare against
    LIVE on the same set of regimes — the bias affects both.
    """
    top10, prices = _top10_momentum_picks(as_of)
    if not top10:
        return {}
    # Score each top-10 by quality
    scores = {}
    for sym in top10:
        q = _get_quality_score(sym)
        if not (q != q):  # not NaN
            scores[sym] = q
    if not scores:
        # Fall back to LIVE if no quality data
        return {sym: 0.80 / 3 for sym in top10[:3]}
    # Take top-3 by quality
    top3 = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
    return {sym: 0.80 / 3 for sym, _ in top3}


# ---------------------------------------------------------------------------
# v3.21: Crowding penalty (Lou & Polk 2024 NBER WP)
# ---------------------------------------------------------------------------
# Hypothesis: crowded momentum names = elevated reversal risk. Names with
# high short interest are particularly susceptible to short squeezes (which
# create FALSE positive momentum) and unwind reversals.
#
# Lou-Polk "Crowding and Factor Returns" (2024 NBER) shows crowding-penalized
# momentum has +0.18 Sharpe lift in 60-year sample, OOS 2018-2023 includes
# both bears.
#
# Implementation: among top-10 momentum, subtract a normalized short-interest
# z-score from rank. Names with VERY high short interest (likely being shorted
# heavily) get demoted; names with normal short interest preferred.
#
# LIMITATION: yfinance gives current short interest (point-in-time), not
# historical. Same forward-look caveat as v3.20.

@lru_cache(maxsize=128)
def _get_short_interest_pct(ticker: str) -> float:
    """Short interest as percentage of float. Returns NaN if unavailable."""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return float("nan")
    if not info:
        return float("nan")
    si = info.get("shortPercentOfFloat")
    if si is None or si != si:
        return float("nan")
    return float(si)


# ---------------------------------------------------------------------------
# v3.22: Combined residual + vol-targeting + crowding penalty
# ---------------------------------------------------------------------------
# The top-2 individual winners stacked. Each independent edge:
#   v3.16 residual + vol-targeted: +0.07 mean Sharpe vs LIVE
#   v3.21 crowding penalty:        +0.18 mean Sharpe vs LIVE
# If edges are orthogonal, combining could yield +0.20 to +0.25 mean Sharpe.

def variant_top3_combined_winners(as_of):
    """v3.22: top-3 by RESIDUAL momentum (Blitz-Hanauer 2024) but among top-10
    by RAW momentum, with crowding penalty (Lou-Polk 2024) AND inverse-vol
    weighting (Baltas-Karyampas 2024).

    Three layers:
      1. Universe = top-10 by raw 12-1 momentum (DEFAULT_LIQUID_50)
      2. Filter by residual momentum (factor-orthogonalized) — take top 5
      3. Apply crowding penalty (short-interest z-score) — take top 3
      4. Weight by inverse 60d vol, normalize to 80% gross
    """
    import numpy as np
    import yfinance as yf
    # Step 1: top-10 by raw momentum
    top10, prices = _top10_momentum_picks(as_of)
    if not top10 or prices.empty:
        return {}

    # Step 2: among top-10, rank by residual momentum (use raw rank if FF5 stale)
    try:
        ff5 = get_ff5_aligned()
        scores = residual_momentum_score(prices, ff5, as_of,
                                          lookback_months=12, skip_months=1,
                                          regression_window_months=36)
        # Filter scores to top10 only
        scores_top10 = {k: v for k, v in scores.items() if k in top10}
        if len(scores_top10) >= 5:
            top5_residual = sorted(scores_top10.items(),
                                   key=lambda kv: -kv[1])[:5]
            top5_syms = [s for s, _ in top5_residual]
        else:
            top5_syms = top10[:5]
    except Exception:
        top5_syms = top10[:5]

    # Step 3: crowding penalty among the top-5
    si_values = {}
    for sym in top5_syms:
        try:
            info = yf.Ticker(sym).info
            si = info.get("shortPercentOfFloat")
            if si is not None and si == si:
                si_values[sym] = float(si)
        except Exception:
            continue

    if len(si_values) >= 3:
        # Apply crowding penalty: rank by inverse short interest
        si_arr = np.array(list(si_values.values()))
        si_mean = si_arr.mean()
        si_std = si_arr.std()
        if si_std > 0:
            adjusted = {sym: -(si_values[sym] - si_mean) / si_std for sym in si_values}
            top3_syms = [s for s, _ in sorted(adjusted.items(),
                                                key=lambda kv: -kv[1])[:3]]
        else:
            top3_syms = list(si_values.keys())[:3]
    else:
        top3_syms = top5_syms[:3]

    if not top3_syms:
        return {}

    # Step 4: inverse-vol weighting
    inv_vols = {}
    for sym in top3_syms:
        if sym not in prices.columns:
            inv_vols[sym] = 1.0
            continue
        rets = prices[sym].pct_change().dropna().iloc[-60:]
        if len(rets) < 30:
            inv_vols[sym] = 1.0
            continue
        vol = float(rets.std() * (252 ** 0.5))
        inv_vols[sym] = 1.0 / max(vol, 0.01)
    total = sum(inv_vols.values())
    if total <= 0:
        return {sym: 0.80 / 3 for sym in top3_syms}
    return {sym: 0.80 * (inv / total) for sym, inv in inv_vols.items()}


def variant_top3_crowding_penalty_pit(as_of):
    """v3.23: PIT version of crowding penalty — honesty test on the +0.18 edge.
    Uses point-in-time S&P 500 universe (no survivorship bias) to verify the
    crowding signal isn't an artifact of today's-winners universe.
    """
    import numpy as np
    import yfinance as yf
    members = sp500_membership_at(as_of.strftime("%Y-%m-%d"))
    if not members:
        return {}
    sample = list(members)[::max(1, len(members) // 150)][:150]
    L = 12 * 21
    S = 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(sample,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return {}
    if prices.empty or len(prices) < L + S:
        return {}
    end_idx = -1 - S
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    top10 = rets.nlargest(10).index.tolist()
    if not top10:
        return {}
    mom_scores = {sym: float(rets[sym]) for sym in top10}
    si_values = {}
    for sym in top10:
        try:
            info = yf.Ticker(sym).info
            si = info.get("shortPercentOfFloat")
            if si is not None and si == si:
                si_values[sym] = float(si)
        except Exception:
            continue
    if len(si_values) < 5:
        return {sym: 0.80 / 3 for sym in top10[:3]}
    si_arr = np.array(list(si_values.values()))
    si_mean = si_arr.mean()
    si_std = si_arr.std()
    if si_std <= 0:
        return {sym: 0.80 / 3 for sym in top10[:3]}
    mom_arr = np.array([mom_scores[s] for s in si_values])
    mom_mean = mom_arr.mean()
    mom_std = mom_arr.std()
    if mom_std <= 0:
        return {sym: 0.80 / 3 for sym in top10[:3]}
    adjusted = {}
    for sym in si_values:
        mom_z = (mom_scores[sym] - mom_mean) / mom_std
        si_z = (si_values[sym] - si_mean) / si_std
        adjusted[sym] = mom_z - 0.5 * si_z
    top3 = sorted(adjusted.items(), key=lambda kv: -kv[1])[:3]
    return {sym: 0.80 / 3 for sym, _ in top3}


def variant_top3_crowding_penalty(as_of):
    """v3.21: Among top-10 by 12-1 momentum, demote crowded names by short-
    interest z-score. Take top-3 by adjusted score.

    Source: Lou-Polk 2024 NBER. Hypothesis: less-crowded momentum names have
    more sustainable trends (less short-squeeze inflation, less risk of
    unwind reversal).
    """
    import numpy as np
    top10, prices = _top10_momentum_picks(as_of)
    if not top10:
        return {}
    # Get raw 12-1 momentum scores for top-10
    L = 12 * 21
    S = 21
    end_idx = -1 - S
    start_idx = -(L + S) - 1
    mom_scores = {}
    for sym in top10:
        if sym in prices.columns:
            try:
                mom_scores[sym] = float(prices[sym].iloc[end_idx] / prices[sym].iloc[start_idx] - 1)
            except Exception:
                continue
    if not mom_scores:
        return {}
    # Get short interest for each
    si_values = {sym: _get_short_interest_pct(sym) for sym in mom_scores}
    valid_si = {sym: v for sym, v in si_values.items() if v == v}  # not NaN
    if len(valid_si) < 5:
        # Insufficient short data — fall back to plain top-3
        return {sym: 0.80 / 3 for sym in top10[:3]}
    # Compute z-score of short interest within this top-10
    si_array = np.array(list(valid_si.values()))
    si_mean = float(si_array.mean())
    si_std = float(si_array.std())
    if si_std <= 0:
        return {sym: 0.80 / 3 for sym in top10[:3]}
    # Adjusted score = momentum z-score - 0.5 × short-interest z-score
    mom_array = np.array([mom_scores[s] for s in valid_si])
    mom_mean = float(mom_array.mean())
    mom_std = float(mom_array.std())
    if mom_std <= 0:
        return {sym: 0.80 / 3 for sym in top10[:3]}
    adjusted = {}
    for sym in valid_si:
        mom_z = (mom_scores[sym] - mom_mean) / mom_std
        si_z = (valid_si[sym] - si_mean) / si_std
        adjusted[sym] = mom_z - 0.5 * si_z  # penalty weight 0.5
    top3 = sorted(adjusted.items(), key=lambda kv: -kv[1])[:3]
    return {sym: 0.80 / 3 for sym, _ in top3}


# ---------------------------------------------------------------------------
# v3.25: PIT version of residual + vol-targeted (final honesty test)
# ---------------------------------------------------------------------------
# v3.16 (residual + vol-targeted) showed +0.07 mean Sharpe over LIVE on the
# survivor universe. v3.21 crowding showed +0.18 but FAILED PIT validation
# (-0.38 vs PIT baseline). Now testing whether residual+vol survives the
# same PIT honesty test.
#
# If yes → genuine edge worth tracking
# If no → all shadow variants fail PIT validation, future iterations should
#         focus on universe/execution/cost, not signal stacking

@lru_cache(maxsize=2048)
def _cached_residual_picks_pit(year, month, top_n, lookback_months,
                                 skip_months, regression_months,
                                 universe_size):
    """PIT version of _cached_residual_picks. Uses point-in-time S&P 500
    membership instead of DEFAULT_LIQUID_50."""
    as_of = pd.Timestamp(year=year, month=month, day=1) - pd.Timedelta(days=1)
    while as_of.weekday() >= 5:
        as_of -= pd.Timedelta(days=1)
    members = sp500_membership_at(as_of.strftime("%Y-%m-%d"))
    if not members:
        return tuple()
    sample = list(members)[::max(1, len(members) // universe_size)][:universe_size]
    L = lookback_months * 21
    S = skip_months * 21
    R = regression_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + R + 21) * 1.6))
    try:
        prices = fetch_history(sample,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return tuple()
    if prices.empty:
        return tuple()
    try:
        ff5 = get_ff5_aligned()
    except Exception:
        return tuple()
    common = prices.index.intersection(ff5.index)
    if len(common) < L + S + R // 2:
        # FF5 stale — fall back to raw 12-1
        end_idx = -1 - S if S > 0 else -1
        start_idx = -(L + S) - 1
        rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
        return tuple(rets.nlargest(top_n).index.tolist())
    scores = residual_momentum_score(prices, ff5, as_of,
                                      lookback_months=lookback_months,
                                      skip_months=skip_months,
                                      regression_window_months=regression_months)
    return tuple(scores.head(top_n).index.tolist())


def variant_top3_residual_pit(as_of):
    """v3.25: residual momentum on PIT universe."""
    picks = _cached_residual_picks_pit(as_of.year, as_of.month, 3, 12, 1, 36, 150)
    if not picks:
        return {}
    return {sym: 0.80 / 3 for sym in picks}


def variant_top3_residual_voltgt_pit(as_of):
    """v3.25b: residual momentum + vol-targeting on PIT universe."""
    picks = _cached_residual_picks_pit(as_of.year, as_of.month, 3, 12, 1, 36, 150)
    if not picks:
        return {}
    # Need prices for vol calc — fetch fresh
    end = as_of
    start = (end - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    try:
        prices = fetch_history(list(picks),
                               start=start,
                               end=end.strftime("%Y-%m-%d"))
    except Exception:
        return {sym: 0.80 / 3 for sym in picks}
    if prices.empty:
        return {sym: 0.80 / 3 for sym in picks}
    inv_vols = {}
    for sym in picks:
        if sym not in prices.columns:
            inv_vols[sym] = 1.0
            continue
        rets = prices[sym].pct_change().dropna().iloc[-60:]
        if len(rets) < 30:
            inv_vols[sym] = 1.0
            continue
        vol = float(rets.std() * (252 ** 0.5))
        inv_vols[sym] = 1.0 / max(vol, 0.01)
    total = sum(inv_vols.values())
    if total <= 0:
        return {sym: 0.80 / 3 for sym in picks}
    return {sym: 0.80 * (inv / total) for sym, inv in inv_vols.items()}


def variant_top3_residual_vol_targeted(as_of):
    """v3.16 + v3.15: residual momentum picks WITH vol-targeted sizing.
    Combines the two best research-paper candidates.
    """
    picks = _cached_residual_picks(as_of.year, as_of.month, 3, 12, 1, 36)
    if not picks:
        picks = tuple(_momentum_picks_as_of(as_of, 3))
    if not picks:
        return {}
    inv_vols = {}
    for sym in picks:
        vol = _realized_vol_60d(sym, as_of.year, as_of.month)
        if vol is None or vol <= 0:
            inv_vols[sym] = 1.0
        else:
            inv_vols[sym] = 1.0 / vol
    total = sum(inv_vols.values())
    if total <= 0:
        return {sym: 0.80 / 3 for sym in picks}
    return {sym: 0.80 * (inv / total) for sym, inv in inv_vols.items()}


def variant_top3_trend_r2_pit(as_of):
    """v3.14 + v3.8: trend-R² filter on PIT S&P 500 universe (honest test)."""
    members = sp500_membership_at(as_of.strftime("%Y-%m-%d"))
    if not members:
        return {}
    sample = list(members)[::max(1, len(members) // 150)][:150]
    L = 12 * 21
    S = 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(sample,
                               start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return {}
    if prices.empty or len(prices) < L + S:
        return {}
    end_idx = -1 - S
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    top10 = rets.nlargest(10).index.tolist()
    if not top10:
        return {}
    window_start_idx = max(0, len(prices) - L - 21)
    r2_scores = {}
    for sym in top10:
        if sym in prices.columns:
            window = prices[sym].iloc[window_start_idx:]
            r2_scores[sym] = _trend_r2(window)
    top3_smooth = sorted(r2_scores.items(), key=lambda kv: -kv[1])[:3]
    if not top3_smooth:
        return {}
    return {sym: 0.80 / 3 for sym, _ in top3_smooth}


# ---------------------------------------------------------------------------
# v3.10: drawdown-scaled position sizing
# ---------------------------------------------------------------------------
# Hypothesis: cut allocation as drawdown deepens, on a smooth glide path.
# Unlike v3.5 / v3.7 (asset-class swaps based on stress signals), the trigger
# here is unrealized P&L from peak — a SELF-REFERENTIAL signal that cannot be
# late by definition.
#
# Glide path:
#    0 to -5% from peak  → 80% gross (LIVE behavior)
#    -5% to -10% from peak → 60% gross
#    -10% to -15% from peak → 40% gross
#    < -15% from peak → halt (0% gross)
#
# The rationale is behavioral, not probabilistic: if I lose 15%, I'd panic
# and pull capital. Cutting size pre-emptively reduces the chance of hitting
# the panic threshold.

# Track each variant's running peak equity for drawdown calc within a regime
_DD_PEAK: dict = {}


def _equity_curve_to_dd(equity_series: pd.Series) -> pd.Series:
    return equity_series / equity_series.cummax() - 1


def _allocation_for_drawdown(dd: float) -> float:
    """Returns fractional gross allocation for current drawdown."""
    if dd >= -0.05:
        return 0.80
    if dd >= -0.10:
        return 0.60
    if dd >= -0.15:
        return 0.40
    return 0.0  # halt


# We need a way to pass current drawdown into the variant. Track simulated
# equity per-variant during replay; the `as_of` callback computes alloc
# based on the running drawdown for that variant.

_VARIANT_EQUITY_CURVE: dict = {}


def _record_dd_state(variant_name, equity_curve):
    """Called by replay_window after computing the equity curve. Used by
    drawdown-scaled variants to read their own dd-from-peak."""
    _VARIANT_EQUITY_CURVE[variant_name] = equity_curve


def _current_dd_for(variant_name, as_of) -> float:
    """Returns drawdown-from-peak for this variant up to as_of, or 0 if no data."""
    eq = _VARIANT_EQUITY_CURVE.get(variant_name)
    if eq is None or len(eq) == 0:
        return 0.0
    sliced = eq[eq.index <= as_of]
    if len(sliced) == 0:
        return 0.0
    return float(sliced.iloc[-1] / sliced.cummax().iloc[-1] - 1)


# Drawdown-scaled variants need a custom backtest path because their allocation
# depends on the variant's own running equity curve. Implement with a separate
# replay function that tracks equity day-by-day.

def variant_top3_dd_scaled(as_of):
    """Look up our own dd-from-peak and scale allocation accordingly.

    This requires the replay harness to feed back equity history; see
    replay_window_dd_scaled below for the integrated path.
    """
    # Default behavior — used by harness to fetch the 3 picks
    p = _momentum_picks_as_of(as_of, 3)
    return {x: 0.80 / 3 for x in p} if p else {}


def replay_window_dd_scaled(start, end, cost_bps=0.0):
    """Run drawdown-scaled top-3 momentum: rebalance monthly to top-3 picks,
    but scale allocation by current drawdown. Day-by-day path-dependent
    simulation since allocation depends on running equity."""
    bdays = pd.bdate_range(start, end)

    # Get all monthly picks once
    monthly_picks = []
    for d in bdays:
        next_d = d + pd.Timedelta(days=1)
        if next_d.month != d.month:
            picks = _momentum_picks_as_of(d, 3)
            if picks:
                monthly_picks.append((d, picks))
    if not monthly_picks:
        return None

    all_t = {"SPY"}
    for _, picks in monthly_picks:
        all_t.update(picks)
    try:
        prices = fetch_history(sorted(all_t),
                              start=(start - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                              end=(end + pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    except Exception:
        return None
    daily_rets = prices.pct_change().fillna(0)
    daily_idx = prices.index

    # Track equity day-by-day
    equity = 100_000.0
    peak = 100_000.0
    eq_history = []
    dates_history = []
    current_picks = None
    current_alloc = 0.80

    for date in daily_idx:
        if date < start:
            continue
        # On each rebalance day, update picks
        for d, picks in monthly_picks:
            if abs((date - d).days) <= 1 and date >= d:
                current_picks = picks
        # Update allocation DAILY based on current drawdown — not just on
        # rebalance days. This is the key fix: dd can drop -10% mid-month and
        # we react immediately, not 3 weeks later.
        dd = (equity / peak) - 1
        current_alloc = _allocation_for_drawdown(dd)
        # Compute today's return: weighted avg of pick returns at current_alloc
        if current_picks and current_alloc > 0:
            per_pick = current_alloc / len(current_picks)
            day_ret = sum(per_pick * float(daily_rets[p].loc[date])
                          for p in current_picks if p in daily_rets.columns)
            # Apply costs on rebalance days
            if cost_bps > 0:
                # Approximate: cost = current_alloc * cost_bps/10000 only on rebalance
                # (full turnover assumption — conservative)
                for d, _ in monthly_picks:
                    if abs((date - d).days) <= 1 and date >= d:
                        day_ret -= current_alloc * (cost_bps / 10000.0)
                        break
        else:
            day_ret = 0.0
        equity *= (1 + day_ret)
        peak = max(peak, equity)
        eq_history.append(equity)
        dates_history.append(date)

    if len(eq_history) < 5:
        return None
    eq_series = pd.Series(eq_history, index=dates_history)
    pr = eq_series.pct_change().fillna(0)
    sd = float(pr.std())
    sharpe = (float(pr.mean()) * 252) / (sd * math.sqrt(252)) if sd > 0 else 0
    n = len(pr)
    cagr = (float(eq_series.iloc[-1]) / float(eq_series.iloc[0])) ** (252 / n) - 1
    bench = daily_rets["SPY"][daily_rets["SPY"].index >= start].fillna(0)
    bench_eq = (1 + bench).cumprod() * 100_000
    bench_cagr = (float(bench_eq.iloc[-1]) / float(bench_eq.iloc[0])) ** (252 / n) - 1
    max_dd = float((eq_series / eq_series.cummax() - 1).min())
    return {"total_pct": float(eq_series.iloc[-1] / eq_series.iloc[0] - 1),
            "cagr": cagr, "sharpe": sharpe, "max_dd": max_dd,
            "spy_total": float(bench_eq.iloc[-1] / bench_eq.iloc[0] - 1),
            "spy_cagr": bench_cagr, "n_days": n}


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


# ---------------------------------------------------------------------------
# v3.7 Macro + vol-market overlays (POSITION-SIZING tweaks per v3.5 lesson)
# ---------------------------------------------------------------------------
# Each overlay uses LIVE's top-3 momentum picks. When the overlay's signal
# fires, GROSS allocation is cut from 80% to 50% (37.5% reduction). This is
# position-sizing, NOT asset-class swap — momentum picks unchanged, exposure
# scaled. Per v3.5 lesson, asset-class swaps fail at V-shape recoveries.

# Cache the macro/vol series at the regime window level so we don't refetch
# them per-day during the stress test
_MACRO_CACHE: dict = {}


def _get_macro_for_window(start_d: pd.Timestamp, end_d: pd.Timestamp) -> dict:
    key = (start_d.date(), end_d.date())
    if key in _MACRO_CACHE:
        return _MACRO_CACHE[key]
    pad_start = start_d - pd.Timedelta(days=400)
    out = {
        "curve": yield_curve_10y_2y(pad_start, end_d),
        "credit": credit_spread_proxy(pad_start, end_d),
        "term": fetch_vol_term_structure(pad_start, end_d),
    }
    _MACRO_CACHE[key] = out
    return out


def _slice_to_asof(s: pd.Series, asof: pd.Timestamp) -> pd.Series:
    if s is None or s.empty:
        return s
    return s[s.index <= asof]


# We need a shared "current window" pointer so each variant can fetch the right
# slice during stress test. Set by the replay loop.
_CURRENT_WINDOW: dict = {"start": None, "end": None}


def _signals_at(asof: pd.Timestamp) -> dict:
    """Compute (credit, curve, vix_back, vix_inv) signals as of `asof`."""
    if _CURRENT_WINDOW["start"] is None:
        return {"credit": False, "curve": False, "vix_back": False, "vix_inv": False}
    macro = _get_macro_for_window(_CURRENT_WINDOW["start"], _CURRENT_WINDOW["end"])
    credit_sig = credit_spread_widening(_slice_to_asof(macro["credit"], asof))
    curve_sig = yield_curve_stress(_slice_to_asof(macro["curve"], asof))
    vix_back = vix_term_backwardation(macro["term"], asof)
    vix_inv = vix_3m_inversion(macro["term"], asof)
    return {"credit": credit_sig, "curve": curve_sig,
            "vix_back": vix_back, "vix_inv": vix_inv}


def _top3_momentum_targets(as_of, alloc=0.80):
    p = _momentum_picks_as_of(as_of, 3)
    if not p:
        return {}
    return {x: alloc / 3 for x in p}


def variant_top3_credit_overlay(as_of):
    """Top-3 at 80% normally; cut to 50% when HYG/LQD ratio drops >2σ in 20d
    (HY credit spreads widening = risk-off signal)."""
    sig = _signals_at(as_of)
    return _top3_momentum_targets(as_of, alloc=0.50 if sig["credit"] else 0.80)


def variant_top3_curve_overlay(as_of):
    """Top-3 at 80% normally; cut to 50% when 10y-2y curve has been inverted
    >60 days AND is currently steepening (recession-imminent signal)."""
    sig = _signals_at(as_of)
    return _top3_momentum_targets(as_of, alloc=0.50 if sig["curve"] else 0.80)


def variant_top3_vix_term_overlay(as_of):
    """Top-3 at 80% normally; cut to 50% when VIX term structure inverted
    (VIX9D > VIX OR VIX > VIX3M = acute stress)."""
    sig = _signals_at(as_of)
    stressed = sig["vix_back"] or sig["vix_inv"]
    return _top3_momentum_targets(as_of, alloc=0.50 if stressed else 0.80)


def variant_top3_macro_combined(as_of):
    """Top-3 at 80% normally; cut to 50% when ≥2 of 4 signals fire
    (credit, curve, vix_back, vix_inv). Multi-signal majority filter."""
    sig = _signals_at(as_of)
    n_active = sum([sig["credit"], sig["curve"], sig["vix_back"], sig["vix_inv"]])
    return _top3_momentum_targets(as_of, alloc=0.50 if n_active >= 2 else 0.80)


def variant_top3_macro_contrarian(as_of):
    """CONTRARIAN: Top-3 at 80% normally; UP to 100% when ≥2 of 4 stress signals
    fire. Hypothesis: stress signals are LATE — they fire near panic lows. Buying
    fear (max aggression at peak panic) should beat reflexive defensive cuts."""
    sig = _signals_at(as_of)
    n_active = sum([sig["credit"], sig["curve"], sig["vix_back"], sig["vix_inv"]])
    return _top3_momentum_targets(as_of, alloc=1.00 if n_active >= 2 else 0.80)


def variant_top3_credit_contrarian(as_of):
    """CONTRARIAN: Top-3 at 80% normally; UP to 100% when HY spreads widening signal."""
    sig = _signals_at(as_of)
    return _top3_momentum_targets(as_of, alloc=1.00 if sig["credit"] else 0.80)


def variant_top3_vix_contrarian(as_of):
    """CONTRARIAN: Top-3 at 80% normally; UP to 100% when VIX backwardation."""
    sig = _signals_at(as_of)
    stressed = sig["vix_back"] or sig["vix_inv"]
    return _top3_momentum_targets(as_of, alloc=1.00 if stressed else 0.80)


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
    "top3_credit_overlay (v3.7)": variant_top3_credit_overlay,
    "top3_curve_overlay (v3.7)": variant_top3_curve_overlay,
    "top3_vix_term_overlay (v3.7)": variant_top3_vix_term_overlay,
    "top3_macro_combined (v3.7)": variant_top3_macro_combined,
    "top3_macro_contrarian (v3.7)": variant_top3_macro_contrarian,
    "top3_credit_contrarian (v3.7)": variant_top3_credit_contrarian,
    "top3_vix_contrarian (v3.7)": variant_top3_vix_contrarian,
    "top3_eq_80_PIT (v3.8 honesty)": variant_top3_eq_80_pit,
    "top3_eq_80_PIT_50 (v3.8 small)": variant_top3_eq_80_pit_50,
    "top3_dd_scaled (v3.10)": variant_top3_dd_scaled,  # uses replay_window_dd_scaled
    "top3_trend_r2 (v3.14)": variant_top3_trend_r2,
    "top3_trend_r2_PIT (v3.14)": variant_top3_trend_r2_pit,
    "top3_residual (v3.15)": variant_top3_residual_momentum,
    "top3_vol_targeted (v3.16)": variant_top3_vol_targeted,
    "top3_residual_vol (v3.15+16)": variant_top3_residual_vol_targeted,
    "multi_asset_trend (v3.19)": variant_multi_asset_trend,
    "multi_asset_trend_top1 (v3.19b)": variant_multi_asset_trend_top1,
    "top3_quality_momentum (v3.20)": variant_top3_quality_momentum,
    "top3_crowding_penalty (v3.21)": variant_top3_crowding_penalty,
    "top3_combined_winners (v3.22)": variant_top3_combined_winners,
    "top3_crowding_PIT (v3.23)": variant_top3_crowding_penalty_pit,
    "top3_residual_PIT (v3.25)": variant_top3_residual_pit,
    "top3_residual_voltgt_PIT (v3.25b)": variant_top3_residual_voltgt_pit,
}

# Variants that need a custom path-dependent replay harness instead of the
# standard replay_window (their allocation at time t depends on equity at t-1).
DD_DEPENDENT_VARIANTS = {"top3_dd_scaled (v3.10)"}

# Daily-decision variants — see DAILY_DECISION_VARIANTS list above

# Variants that need daily decision evaluation (their weights change inside a month
# due to calendar anomalies, regime detection, etc). Default = monthly only.
DAILY_DECISION_VARIANTS = {
    "top3_80 + anomaly overlay",
    "anomaly_only_spy (sleeve test)",
    "top3_credit_overlay (v3.7)",
    "top3_curve_overlay (v3.7)",
    "top3_vix_term_overlay (v3.7)",
    "top3_macro_combined (v3.7)",
    "top3_macro_contrarian (v3.7)",
    "top3_credit_contrarian (v3.7)",
    "top3_vix_contrarian (v3.7)",
}


def replay_window(variant_name, fn, start, end, cost_bps=0.0):
    """Replay a variant across a window. cost_bps applies per-trade slippage
    + half-spread (one-way). 5bps is realistic for liquid US stocks via Alpaca:
    ~1bp Alpaca commission (free), ~2bp half-spread, ~2bp slippage on aggressive
    rebalance. 0bps gives the optimistic best-case (current default for backwards
    compat with v3.7 and earlier results)."""
    # Set the macro/vol cache window so the v3.7 overlay variants can compute signals
    _CURRENT_WINDOW["start"] = start
    _CURRENT_WINDOW["end"] = end

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

    # v3.9: subtract transaction costs on rebalance days (when weights change).
    # cost = sum(|delta_weight|) * cost_bps / 10000 — applied on the rebalance day.
    if cost_bps > 0:
        weight_diffs = weights.diff().abs().sum(axis=1).fillna(0)
        # First row has 0 → first decision day weight_diffs equals sum of initial weights.
        # That mirrors a real first-buy cost. Convert bps to decimal: 5 bps = 0.0005.
        cost_decimal = cost_bps / 10_000.0
        daily_costs = weight_diffs * cost_decimal
        pr = pr - daily_costs

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
    import os
    cost_bps = float(os.getenv("STRESS_COST_BPS", "0"))
    label = f" (costs={cost_bps}bps/trade)" if cost_bps > 0 else " (NO costs)"
    print("=" * 110)
    print(f"REGIME STRESS TEST — variants across 5 historical windows{label}")
    print("=" * 110)

    # results[variant_name][regime_name] = stats
    results = {v: {} for v in VARIANTS}

    for regime_name, start, end in REGIMES:
        print(f"\n>>> {regime_name}: {start.date()} to {end.date()}")
        for v_name, fn in VARIANTS.items():
            try:
                if v_name in DD_DEPENDENT_VARIANTS:
                    r = replay_window_dd_scaled(start, end, cost_bps=cost_bps)
                else:
                    r = replay_window(v_name, fn, start, end, cost_bps=cost_bps)
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

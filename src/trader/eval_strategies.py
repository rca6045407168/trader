"""v3.73.7 — Candidate strategies for constant evaluation.

Distinct from strategy_registry.py (which catalogs production-routed
strategies). This module holds the pure-function candidates used by
the eval runner to score 10 alternatives in parallel.

Each strategy is:

    fn(asof: pd.Timestamp, prices: pd.DataFrame) -> dict[ticker, weight]

All strategies share the same universe + same momentum signal so the
comparison isolates SELECTION + WEIGHTING differences only.

Today: 10 strategies covering the design space (concentrated vs.
diversified, equal vs. weighted, single-name signal vs. sector-stratified):

  1. xs_top15               XS top-15 equal-weight @ 80% (current baseline)
  2. xs_top15_capped        XS top-15 + 8% name cap + 25% sector cap
  3. vertical_winner        top-1 per sector, abs-momentum floor (≥0)
  4. xs_top8                concentrated (top-8)
  5. xs_top25               diversified (top-25)
  6. score_weighted_xs      XS top-15, weights ∝ score
  7. inv_vol_xs             XS top-15, weights ∝ 1/realized-vol (60d)
  8. dual_momentum          XS top-15 ∩ abs-return-positive
  9. sector_rotation_top3   top-3 sectors by avg score; top-half names
 10. equal_weight_universe  1/N over universe (no signal — sanity check)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import pandas as pd

from .signals import momentum_score
from .sectors import get_sector, SECTORS

# v3.73.14: ETFs are now in the price panel for the passive baselines.
# Active stock-picking strategies must explicitly filter to the
# stock universe, otherwise xs_top15 / equal_weight_universe / etc.
# would treat ETFs as universe members.
_STOCK_UNIVERSE = frozenset(SECTORS.keys())


def _stock_panel(prices):
    """Return prices restricted to the stock universe (excludes ETFs
    like SPY/VTI/VXUS/BND/AGG that may be present for passive
    baselines)."""
    cols = [c for c in prices.columns if c in _STOCK_UNIVERSE]
    return prices[cols]


@dataclass
class StrategySpec:
    name: str
    description: str
    fn: Callable
    target_gross: float = 0.80


_REGISTRY: dict[str, StrategySpec] = {}


def register(name: str, description: str, target_gross: float = 0.80):
    def deco(fn):
        _REGISTRY[name] = StrategySpec(name, description, fn, target_gross)
        return fn
    return deco


def all_strategies() -> list[StrategySpec]:
    return list(_REGISTRY.values())


def get(name: str) -> Optional[StrategySpec]:
    return _REGISTRY.get(name)


# ============================================================
# Shared helpers
# ============================================================
def _score_universe(asof, prices, lookback: int = 12, skip: int = 1):
    # v3.73.14: filter to stock universe so ETFs in the panel (added
    # for passive baselines) don't contaminate momentum-based picks.
    p = _stock_panel(prices)
    p = p[p.index <= asof]
    out: list[tuple[str, float]] = []
    for t in p.columns:
        s = p[t].dropna()
        m = momentum_score(s, lookback, skip)
        if not pd.isna(m):
            out.append((t, float(m)))
    out.sort(key=lambda x: -x[1])
    return out


def _realized_vol(asof, prices, ticker: str, window: int = 60) -> float:
    p = prices[prices.index <= asof][ticker].dropna()
    if len(p) < 5:
        return 0.0
    rets = p.pct_change().dropna().tail(window)
    if len(rets) < 5:
        return 0.0
    return float(rets.std())


# ============================================================
# 1. XS top-15
# ============================================================
@register("xs_top15", "Cross-sectional top-15 equal-weight @ 80% (baseline)")
def xs_top15(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 2. XS top-15 with caps
# ============================================================
@register("xs_top15_capped", "XS top-15 + 8% name cap + 25% sector cap")
def xs_top15_capped(asof, prices):
    from .portfolio_caps import apply_portfolio_caps
    base = xs_top15(asof, prices)
    if not base:
        return {}
    return apply_portfolio_caps(base, get_sector).targets


# ============================================================
# 3. Vertical winner
# ============================================================
@register("vertical_winner",
          "Top-1 per sector with absolute-momentum floor")
def vertical_winner(asof, prices):
    scored = _score_universe(asof, prices)
    best: dict[str, tuple[str, float]] = {}
    for t, m in scored:
        if m < 0:
            continue
        s = get_sector(t)
        if s not in best or m > best[s][1]:
            best[s] = (t, m)
    if not best:
        return {}
    w = 0.80 / len(best)
    return {t: w for t, _ in best.values()}


# ============================================================
# 4. XS top-8 (concentrated)
# ============================================================
@register("xs_top8", "Cross-sectional top-8 equal-weight")
def xs_top8(asof, prices):
    picks = _score_universe(asof, prices)[:8]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 5. XS top-25 (diversified)
# ============================================================
@register("xs_top25", "Cross-sectional top-25 equal-weight")
def xs_top25(asof, prices):
    picks = _score_universe(asof, prices)[:25]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 6. Score-weighted XS top-15 (max-zero scheme)
# ============================================================
@register("score_weighted_xs",
          "XS top-15, weights ∝ max(score, 0); drops negative-momentum names")
def score_weighted_xs(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    raw = {t: max(m, 0.0) for t, m in picks}
    total = sum(raw.values())
    if total <= 0:
        w = 0.80 / len(picks)
        return {t: w for t, _ in picks}
    return {t: 0.80 * (raw[t] / total) for t in raw}


# ============================================================
# 6b. PRODUCTION variant — replicate the LIVE momentum_top15_mom_weighted
#     so the leaderboard compares apples-to-apples against what's
#     actually deployed. Different from score_weighted_xs in negative-
#     score handling: this scheme keeps all 15 names by adding
#     (score - min(score) + 0.01), even when momentum is broadly
#     negative.
# ============================================================
@register("xs_top15_min_shifted",
          "PRODUCTION (LIVE variant momentum_top15_mom_weighted): "
          "weights ∝ (score - min + 0.01); preserves all 15 names")
def xs_top15_min_shifted(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    min_s = min(m for _, m in picks)
    shifted = [(t, m - min_s + 0.01) for t, m in picks]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: 0.80 / len(picks) for t, _ in picks}
    return {t: 0.80 * (s / total) for t, s in shifted}


# ============================================================
# 7. Inverse-vol weighted XS top-15
# ============================================================
@register("inv_vol_xs",
          "XS top-15 with weights ∝ 1/realized-vol (60d)")
def inv_vol_xs(asof, prices):
    picks = _score_universe(asof, prices)[:15]
    if not picks:
        return {}
    inv = {}
    for t, _ in picks:
        v = _realized_vol(asof, prices, t)
        inv[t] = 1.0 / v if v > 1e-6 else 0.0
    total = sum(inv.values())
    if total <= 0:
        w = 0.80 / len(picks)
        return {t: w for t, _ in picks}
    return {t: 0.80 * (inv[t] / total) for t in inv}


# ============================================================
# 8. Dual momentum
# ============================================================
@register("dual_momentum",
          "XS top-15 BUT skip names with absolute 12-1 return < 0")
def dual_momentum(asof, prices):
    picks = [(t, m) for t, m in _score_universe(asof, prices)[:15] if m > 0]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 9. Sector rotation: top-3 sectors, top-half names within
# ============================================================
@register("sector_rotation_top3",
          "Top-3 sectors by avg score; equal-weight top-half names")
def sector_rotation_top3(asof, prices):
    scored = _score_universe(asof, prices)
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for t, m in scored:
        by_sector.setdefault(get_sector(t), []).append((t, m))
    avg = {
        s: sum(m for _, m in members) / len(members)
        for s, members in by_sector.items()
    }
    top3 = sorted(avg.items(), key=lambda x: -x[1])[:3]
    selected: list[str] = []
    for s, _ in top3:
        sector_picks = sorted(by_sector[s], key=lambda x: -x[1])
        keep = max(1, len(sector_picks) // 2)
        selected.extend(t for t, _ in sector_picks[:keep])
    if not selected:
        return {}
    w = 0.80 / len(selected)
    return {t: w for t in selected}


# ============================================================
# 10. Equal-weight universe (no signal — sanity floor)
# ============================================================
@register("equal_weight_universe",
          "Naive 1/N over entire stock universe (no signal)")
def equal_weight_universe(asof, prices):
    # v3.73.14: filter to stock universe (excludes ETFs added for
    # passive baselines) so this is comparable across re-backfills
    # regardless of how many ETFs are in the panel.
    p = _stock_panel(prices)
    p = p[p.index <= asof]
    available = [t for t in p.columns if not p[t].dropna().empty]
    if not available:
        return {}
    w = 0.80 / len(available)
    return {t: w for t in available}


# ============================================================
# 12. Buy-and-hold SPY (passive baseline)
#
#     v3.73.14 — added in response to Reddit research finding the
#     Bogleheads counter-argument. The community's strongest empirical
#     case against active strategies is that ~85% of active funds lose
#     to their benchmark over 10y (SPIVA data). The right way to
#     validate our active strategy is to MEASURE it against the
#     simplest possible passive alternative, not to claim it beats
#     SPY in prose.
# ============================================================
@register("buy_and_hold_spy",
          "100% SPY at the first rebalance, never reset. The simplest "
          "passive baseline. If our active can't beat this, the "
          "Boglehead recommendation wins.")
def buy_and_hold_spy(asof, prices):
    return {"SPY": 1.00}


# ============================================================
# 13. Boglehead 3-fund (VTI / VXUS / BND, rebalanced monthly)
# ============================================================
@register("boglehead_three_fund",
          "60% VTI / 30% VXUS / 10% BND, rebalanced to target every "
          "month. The canonical Boglehead allocation.")
def boglehead_three_fund(asof, prices):
    # If VTI / VXUS / BND aren't in the panel (smaller backfill), fall
    # back to SPY-only to avoid empty picks. This preserves the
    # baseline-test intent: 'the boring portfolio.'
    weights = {}
    if "VTI" in prices.columns:
        weights["VTI"] = 0.60
    elif "SPY" in prices.columns:
        weights["SPY"] = 0.60
    if "VXUS" in prices.columns:
        weights["VXUS"] = 0.30
    if "BND" in prices.columns:
        weights["BND"] = 0.10
    elif "AGG" in prices.columns:
        weights["AGG"] = 0.10
    # Renormalize if some are missing
    total = sum(weights.values())
    if total <= 0:
        return {"SPY": 1.00}
    return {t: w / total for t, w in weights.items()}


# ============================================================
# 14. Classic 60/40 (SPY / AGG)
#
#     The single most-cited 'balanced' allocation in retail. Proxy
#     for the original Markowitz / Bogle balanced portfolio.
# ============================================================
@register("simple_60_40",
          "60% SPY / 40% AGG, rebalanced monthly. The classic "
          "balanced portfolio against which all multi-asset "
          "alternatives are measured.")
def simple_60_40(asof, prices):
    weights = {}
    if "SPY" in prices.columns:
        weights["SPY"] = 0.60
    if "AGG" in prices.columns:
        weights["AGG"] = 0.40
    elif "BND" in prices.columns:
        weights["BND"] = 0.40
    elif "TLT" in prices.columns:
        weights["TLT"] = 0.40
    total = sum(weights.values())
    if total <= 0:
        return {"SPY": 1.00}
    return {t: w / total for t, w in weights.items()}


# ============================================================
# v3.73.18 — HARSHER PASSIVE BASELINES
#
# Per the v3.73.17 critique: "beating a Boglehead 3-fund over a
# US-tech-led period is not informative. The real passive benchmark
# is SPY, QQQ, MTUM, SCHG, VUG, XLK, equal-weight S&P 500."
# These add the appropriate-difficulty passive comparisons.
# ============================================================
@register("buy_and_hold_qqq",
          "100% QQQ (Nasdaq-100). Beats most strategies during US-tech-led "
          "regimes; the test of whether our momentum book actually beats "
          "the simplest mega-cap-tech bet.")
def buy_and_hold_qqq(asof, prices):
    if "QQQ" in prices.columns:
        return {"QQQ": 1.00}
    return {"SPY": 1.00}  # safe fallback


@register("buy_and_hold_mtum",
          "100% MTUM (iShares MSCI USA Momentum Factor ETF). The "
          "honest factor-ETF benchmark — if our active strategy "
          "doesn't beat the canned momentum factor, the active "
          "book isn't earning its complexity.")
def buy_and_hold_mtum(asof, prices):
    if "MTUM" in prices.columns:
        return {"MTUM": 1.00}
    return {"SPY": 1.00}


@register("buy_and_hold_schg",
          "100% SCHG (Schwab US Large-Cap Growth ETF). Growth-tilt "
          "passive baseline.")
def buy_and_hold_schg(asof, prices):
    if "SCHG" in prices.columns:
        return {"SCHG": 1.00}
    return {"SPY": 1.00}


@register("buy_and_hold_vug",
          "100% VUG (Vanguard Growth ETF). Vanguard's large-cap-"
          "growth alternative to SCHG.")
def buy_and_hold_vug(asof, prices):
    if "VUG" in prices.columns:
        return {"VUG": 1.00}
    return {"SPY": 1.00}


@register("buy_and_hold_xlk",
          "100% XLK (SPDR Tech Select Sector). Pure-tech sector ETF — "
          "the 'just buy the sector' test for a momentum book that "
          "happens to be Tech-heavy.")
def buy_and_hold_xlk(asof, prices):
    if "XLK" in prices.columns:
        return {"XLK": 1.00}
    return {"SPY": 1.00}


@register("equal_weight_sp500",
          "100% RSP (Invesco S&P 500 Equal Weight ETF). Removes "
          "the cap-weighting bias of SPY; tests whether our "
          "selection adds value vs. naive 1/N over the index.")
def equal_weight_sp500(asof, prices):
    if "RSP" in prices.columns:
        return {"RSP": 1.00}
    return {"SPY": 1.00}


# ============================================================
# Naive top-15 12-month return (no min-shift, no caps, no skip)
#
# The "what if we just bought last year's winners equal-weight"
# strategy. Tests whether our sophistication (12-1 skip,
# min-shift, caps) adds anything over a college-freshman version.
# ============================================================
@register("naive_top15_12mo_return",
          "Top-15 by trailing 12-month return (NO 1-month skip), "
          "equal-weight at 80% gross. The 'college-freshman momentum' "
          "baseline — tests whether our 12-1 skip + min-shift + caps "
          "actually add anything.")
def naive_top15_12mo_return(asof, prices):
    import pandas as pd
    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    cutoff = asof - pd.DateOffset(months=12)
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        s_then = s[s.index <= cutoff]
        s_now = s[s.index <= asof]
        if s_then.empty or s_now.empty:
            continue
        p0 = float(s_then.iloc[-1]); p1 = float(s_now.iloc[-1])
        if p0 > 0:
            scored.append((sym, p1 / p0 - 1))
    scored.sort(key=lambda x: -x[1])
    picks = scored[:15]
    if not picks:
        return {}
    w = 0.80 / len(picks)
    return {t: w for t, _ in picks}


# ============================================================
# 15. Vol-targeted production (v3.73.17)
#
#     Same picks + min-shift weights as the production LIVE
#     variant, but the GROSS scales down when realized portfolio
#     vol exceeds 18% (the design target). Only ever scales down,
#     never up.
# ============================================================
@register("xs_top15_vol_targeted",
          "PRODUCTION + vol-target overlay: gross scaled down when "
          "trailing portfolio vol > 18% annualized")
def xs_top15_vol_targeted(asof, prices):
    from .sizing import (
        realized_portfolio_vol_daily, vol_target_scalar,
    )
    base = xs_top15_min_shifted(asof, prices)
    if not base:
        return {}
    # Estimate trailing 60-day realized portfolio vol using current
    # weights as proxy. Pull daily returns for each name in base
    # and compute weighted portfolio daily return series.
    p = _stock_panel(prices)
    p = p[p.index <= asof].tail(60)
    if len(p) < 30:
        return base  # not enough history yet
    daily_port_rets = []
    for i in range(1, len(p)):
        r = 0.0
        for sym, w in base.items():
            if sym not in p.columns:
                continue
            p0, p1 = p[sym].iloc[i - 1], p[sym].iloc[i]
            if p0 > 0:
                r += w * (p1 / p0 - 1)
        daily_port_rets.append(r)
    realized = realized_portfolio_vol_daily(daily_port_rets)
    scalar = vol_target_scalar(realized, target_vol=0.18)
    return {t: w * scalar for t, w in base.items()}


# ============================================================
# 16. Score-weighted vol-parity (v3.73.17)
#
#     Per-name vol-parity within score-weighting. High-vol names
#     (NVDA, AMD ~ 40% ann) get less weight than low-vol names
#     (JNJ, WMT ~ 15% ann) at the same score, so each contributes
#     equally to portfolio vol.
# ============================================================
@register("score_weighted_vol_parity",
          "Top-15 with weights ∝ (score - min + 0.01) / vol(name); "
          "per-name vol-parity within min-shifted score-weighting")
def score_weighted_vol_parity(asof, prices):
    from .sizing import inverse_vol_weights, per_name_vol
    scored = _score_universe(asof, prices)[:15]
    if not scored:
        return {}
    p = _stock_panel(prices)
    vols = per_name_vol(p, asof, window_days=60)
    return inverse_vol_weights(scored, vols, target_gross=0.80,
                                min_shift=True)


# ============================================================
# 16c. Recovery-aware momentum (v3.73.22) — addresses GFC whipsaw
#
# The v3.73.21 GFC postmortem identified the failure mode:
# 12-1 momentum lagged the 2009 Q1 recovery rally because the
# signal still pointed at defensives (WMT, NFLX, MCD) that had
# won by losing-less, while high-beta names (AMD, AMZN, BAC)
# were leading the bounce.
#
# This candidate uses a VIX-compression-after-panic signal as a
# regime detector:
#   recovery_active = (current_vix < 25) AND (max_vix_30d > 35)
# When recovery_active, use 6-1 momentum (faster rotation)
# instead of 12-1. Otherwise use the standard 12-1.
#
# 6-1 instead of 3-1 because 3-1 is noisier; 6-1 still gives
# reasonable signal stability while dropping the lagging
# defensive names that 12-1 over-weights at regime turns.
# ============================================================
@register("xs_top15_recovery_aware",
          "PRODUCTION + recovery rule: switches from 12-1 to 6-1 "
          "momentum when VIX compression after panic suggests a "
          "rally is starting (addresses 2009-Q1 whipsaw)")
def xs_top15_recovery_aware(asof, prices):
    """Use 6-1 momentum when recovery_active; else 12-1 (production)."""
    # Detect recovery: VIX compression after panic
    recovery_active = False
    try:
        if "^VIX" in prices.columns:
            vix_series = prices["^VIX"].dropna()
        elif "VIX" in prices.columns:
            vix_series = prices["VIX"].dropna()
        else:
            vix_series = None

        if vix_series is not None:
            vix_now = vix_series[vix_series.index <= asof]
            if not vix_now.empty:
                current_vix = float(vix_now.iloc[-1])
                last_30 = vix_now.iloc[-30:] if len(vix_now) >= 30 else vix_now
                max_30 = float(last_30.max())
                # Recovery: current low + recent panic
                recovery_active = (current_vix < 25) and (max_30 > 35)
    except Exception:
        pass

    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    # Choose lookback: 6 months in recovery, 12 otherwise
    lookback = 6 if recovery_active else 12
    skip = 1
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, lookback, skip)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top15 = scored[:15]
    if not top15:
        return {}
    # Min-shift weighting (same as production)
    min_s = min(s for _, s in top15)
    shifted = [(t, s - min_s + 0.01) for t, s in top15]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: 0.80 / len(top15) for t, _ in top15}
    return {t: 0.80 * (s / total) for t, s in shifted}


# ============================================================
# 17. Production picks + reactor-driven trims (v3.73.17)
#
#     Same picks + min-shift weights as the production LIVE
#     variant, but applies the reactor's trim rule to any name
#     with a recent BEARISH/M3 8-K signal. Lookback window per
#     reactor_rule.lookback_days (default 7). Trim to 50% of
#     original weight per the rule's bounded-trim contract.
#
#     This is the DD's "reactor in execution mode, but only as
#     measurement candidate" — production rule still SHADOW.
# ============================================================
@register("xs_top15_reactor_trimmed",
          "PRODUCTION + reactor-trim overlay: positions with recent "
          "BEARISH/M3 reactor signals are trimmed to 50% of weight")
def xs_top15_reactor_trimmed(asof, prices):
    from .reactor_rule import ReactorSignalRule
    base = xs_top15_min_shifted(asof, prices)
    if not base:
        return {}
    try:
        rule = ReactorSignalRule()
        # Fresh in-memory rule with default trim_to_pct=0.5
        # Reads from journal.earnings_signals via compute_trims().
        # When journal has no signals, returns empty dict → base
        # picks are returned unchanged.
        import pandas as pd
        as_of_dt = (asof.to_pydatetime() if hasattr(asof, "to_pydatetime")
                    else asof)
        trims = rule.compute_trims(base, as_of=as_of_dt)
        if not trims:
            return base
        # Apply trims; keep all other positions unchanged
        out = dict(base)
        for sym, decision in trims.items():
            out[sym] = decision.new_weight
        return out
    except Exception:
        return base


# ============================================================
# 18. Long-short momentum (the structural alpha)
# ============================================================
@register("long_short_momentum",
          "Long top-15 (min-shifted) + short bottom-5 (equal-weighted); "
          "70% long / 30% short / 40% net")
def long_short_momentum(asof, prices):
    """v3.73.12 — the only structural addition that can produce
    benchmark-beating IR in a 2022-style reversal regime, where pure
    long-only momentum drawdowns are -25 to -40%.

    Construction:
      Long  side: 70% gross, top-15 by score, min-shift weighted
                  (replicates the LIVE production scheme on the
                  long side, which was the leader of the long-only
                  comparison).
      Short side: 30% gross, bottom-5 by score, equal-weight.
                  Bottom-5 (not bottom-15) because the conviction
                  on shorts is harder; concentrate on the worst.
      Net:        +40% gross long exposure (lower beta than the
                  pure long book's +80%).

    Returns negative weights for short positions. Caller
    (eval_runner) is intentional that net P&L sums correctly:
      ret_total = sum_t weight_t * (price_t1 / price_t0 - 1)
    A short with weight -0.06 on a name that drops 10% contributes
    -(-0.06) * 0.10 = +0.006 to portfolio return. Already correct.
    """
    scored = _score_universe(asof, prices)
    if len(scored) < 20:
        return {}

    # Long side (top-15, min-shifted)
    longs = scored[:15]
    min_long = min(m for _, m in longs)
    shifted = [(t, m - min_long + 0.01) for t, m in longs]
    total_l = sum(s for _, s in shifted)
    long_weights = (
        {t: 0.70 * (s / total_l) for t, s in shifted}
        if total_l > 0 else {t: 0.70 / len(longs) for t, _ in longs}
    )

    # Short side (bottom-5, equal-weight, negative)
    shorts = scored[-5:]
    short_weights = {t: -0.30 / len(shorts) for t, _ in shorts}

    # Combine
    return {**long_weights, **short_weights}


# ============================================================
# 19. v3.73.24 — drawdown-based recovery rule
#
# The VIX-based recovery rule (xs_top15_recovery_aware) failed
# in the GFC because VIX never crossed back below 25 during
# the actual recovery turn (March-June 2009). VIX bottomed at
# ~30 in early-2009 and only fell below 25 in mid-2009 after
# the rebound was 6 months old.
#
# A drawdown-based detector should fire IN the GFC because it
# uses SPY's own price action as the regime signal:
#   recovery_active = (
#     SPY 180d_drawdown < -25%   # we are in a deep crash
#     AND SPY 1m_return > +5%    # rebound has started
#   )
#
# When recovery_active, switch to 6-1 momentum (faster
# rotation), same as the VIX rule. Rule must NOT activate
# during normal market operation (low DD = no signal).
#
# This is a "compound" detector: needs BOTH a deep crash
# context AND a fresh rebound. Either alone is insufficient.
# ============================================================
@register("xs_top15_dd_recovery_aware",
          "PRODUCTION + drawdown-based recovery rule: switches "
          "12-1 → 6-1 when SPY is in deep DD AND has just bounced "
          "(addresses GFC where VIX-based rule failed to fire)")
def xs_top15_dd_recovery_aware(asof, prices):
    """Use 6-1 momentum when SPY shows deep-DD-with-rebound; else 12-1."""
    recovery_active = False
    try:
        if "SPY" in prices.columns:
            spy = prices["SPY"].dropna()
            spy = spy[spy.index <= asof]
            if len(spy) >= 180:
                last_180 = spy.iloc[-180:]
                peak_180 = float(last_180.max())
                current = float(spy.iloc[-1])
                dd_180 = current / peak_180 - 1
                # 1-month trailing return (~21 trading days)
                one_month_ago = spy.iloc[-22] if len(spy) >= 22 else spy.iloc[0]
                ret_1m = current / float(one_month_ago) - 1
                # Deep crash + fresh rebound
                recovery_active = (dd_180 < -0.25) and (ret_1m > 0.05)
    except Exception:
        pass

    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    lookback = 6 if recovery_active else 12
    skip = 1
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, lookback, skip)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top15 = scored[:15]
    if not top15:
        return {}
    min_s = min(s for _, s in top15)
    shifted = [(t, s - min_s + 0.01) for t, s in top15]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: 0.80 / len(top15) for t, _ in top15}
    return {t: 0.80 * (s / total) for t, s in shifted}


# ============================================================
# 20. v3.73.28 — drawdown-based recovery + GROSS-REDUCTION response
#
# v3.73.24 result: dd-recovery DETECTOR fires correctly during GFC
# but the 6-1 momentum RESPONSE degraded P&L by -1.24pp vs production.
# v3.73.28 swaps the response from "switch to 6-1 lookback" to
# "keep 12-1 picks but cut gross 80% → 40%".
#
# GFC test (2008-09 → 2010-12, 28 months):
#   production:        +2.45% cum, -25.37% max DD
#   v3.73.24 (6-1):    +1.21% cum, -26.44% max DD  (worse)
#   v3.73.28 (40%gr):  +3.61% cum, -22.59% max DD  (BETTER)
#
# 25y full-window (316 months) confirmation:
#   production:        57.25× cum, -38.50% max DD
#   v3.73.28 (40%gr):  57.89× cum, -36.21% max DD
#   delta: +64.51pp cum, +2.29pp max-DD improvement
#
# Insight: when the detector says "you're in a regime where the
# 12-1 signal is unreliable (defensives that held up in '08 are
# the wrong leaders for the '09 rotation)", the right response
# isn't a different signal — it's LESS exposure. Just take less
# risk during the regime and let the dust settle.
#
# Detector fires only 4 times in 25 years (all GFC months), so
# this strategy is ~99% of the time identical to production.
# Status: SHADOW candidate in the eval harness; not promoted to
# LIVE because the 30-run gate has not cleared.
# ============================================================
@register("xs_top15_dd_recovery_reduced_gross",
          "PRODUCTION + dd-recovery rule with REDUCED-GROSS response: "
          "when deep-DD-with-rebound detected, keep 12-1 picks but "
          "cut gross 80% → 40% (closes v3.73.24 negative result)")
def xs_top15_dd_recovery_reduced_gross(asof, prices):
    """v3.73.28 — when recovery active, keep 12-1 but cut gross to 40%."""
    recovery_active = False
    try:
        if "SPY" in prices.columns:
            spy = prices["SPY"].dropna()
            spy = spy[spy.index <= asof]
            if len(spy) >= 180:
                last_180 = spy.iloc[-180:]
                peak_180 = float(last_180.max())
                current = float(spy.iloc[-1])
                dd_180 = current / peak_180 - 1
                one_month_ago = spy.iloc[-22] if len(spy) >= 22 else spy.iloc[0]
                ret_1m = current / float(one_month_ago) - 1
                recovery_active = (dd_180 < -0.25) and (ret_1m > 0.05)
    except Exception:
        pass

    target_gross = 0.40 if recovery_active else 0.80

    p = _stock_panel(prices)
    p = p[p.index <= asof]
    if len(p) < 252:
        return {}
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, 12, 1)
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top15 = scored[:15]
    if not top15:
        return {}
    min_s = min(s for _, s in top15)
    shifted = [(t, s - min_s + 0.01) for t, s in top15]
    total = sum(s for _, s in shifted)
    if total <= 0:
        return {t: target_gross / len(top15) for t, _ in top15}
    return {t: target_gross * (s / total) for t, s in shifted}

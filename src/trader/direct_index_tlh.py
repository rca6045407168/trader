"""Direct indexing + tax-loss harvesting.

v6 architectural addition. Implements the highest-confidence structural
edge available to a retail taxable account: hold individual S&P-500
names instead of the SPY ETF, mechanically realize losses as they
appear, swap to sector-matched replacements (avoiding wash sales),
swap back after 31 days. Edge: ~0.5-2% per year of after-tax
outperformance vs SPY-the-ETF, depending on tax bracket and
cross-sectional volatility regime.

Edge source: tax arbitrage, not alpha. The IRS treats realized losses
preferentially (offsets gains + up to $3k/yr ordinary income). The
edge does NOT decay because it's not a prediction.

Why this works structurally:
  - At any given month, some fraction of the 50 names will be down
    on a cost-basis basis (even when SPY is flat or up). Selling
    those crystallizes losses.
  - Wash-sale rule: 30-day window. Replacement security in the same
    sector keeps the portfolio's β intact while avoiding disallowed
    losses.
  - After the 30-day window expires, swap back to the original if
    desired (or stay in the replacement; the index exposure is
    near-identical).

Constraints:
  - Only delivers value in a TAXABLE account. On paper (Alpaca) or in
    a retirement account (401k/IRA), this module produces no
    realizable edge. Code runs in either case; harvest events are
    journaled regardless so the operator can verify the logic.

  - The wash-sale rule operates on substantially-identical securities.
    Sector-matched substitutes (e.g. JPM ↔ BAC, AAPL ↔ MSFT) are
    not substantially identical for IRS purposes. Stay clear of
    SPY ↔ IVV ↔ VOO swaps; those ARE substantially identical.

  - The trader's existing 50-name universe is hand-curated, not the
    full S&P 500. This module uses that universe as the basket.
    Tracking error vs SPY is real (~50-100bps annualized depending
    on sector weighting); the user accepts this as the cost of being
    able to TLH on individual names. To minimize tracking error,
    weights are roughly cap-weighted within sectors.

Operational integration:
  - Lives as a separate sleeve from the auto-router. The trader
    becomes a two-book system: Book A (direct-index core, default
    70% of capital) and Book B (auto-router alpha sleeve, default
    30%). Allocation split is env-configurable via
    DIRECT_INDEX_CORE_PCT.
  - On each daily run, this module:
      1. Computes target weights for the direct-index basket.
      2. Reads current positions + cost basis from journal.
      3. Identifies harvest candidates (positions in loss).
      4. Finds wash-sale-safe replacements.
      5. Emits a list of (sell, buy) swap orders.
  - The orchestrator merges these with the auto-router's alpha
    sleeve targets and routes through the existing cap/risk/
    execution pipeline.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from .config import DB_PATH
from .sectors import SECTORS

# Wash-sale window per IRS: 30 days before AND after the loss-realizing
# sale. We track the SELL side; replacement-purchase must avoid the
# same ticker (or its substantially-identical class) for 31 days.
WASH_SALE_DAYS = 31

# Default capital split. The direct-index core gets DIRECT_INDEX_CORE_PCT;
# the auto-router alpha sleeve gets the rest.
DEFAULT_CORE_PCT = float(os.getenv("DIRECT_INDEX_CORE_PCT", "0.70"))

# Replacement table per ticker. Each ticker maps to an ordered list of
# sector-matched substitutes. When TLH-selling X, the planner picks the
# first substitute that's NOT currently in a wash-sale window.
#
# Tickers within a sector but with distinct businesses (e.g. JPM vs BAC,
# both Financials but different revenue mixes) are considered NOT
# substantially identical. This is the standard interpretation; consult
# a tax professional for edge cases.
REPLACEMENT_MAP: dict[str, list[str]] = {
    # Tech — big enough sector to rotate within
    "AAPL":  ["MSFT", "GOOGL", "META"],
    "MSFT":  ["AAPL", "ORCL", "ADBE"],
    "NVDA":  ["AMD", "AVGO", "QCOM"],
    "AVGO":  ["NVDA", "QCOM", "TXN"],
    "AMD":   ["NVDA", "QCOM", "INTC"],
    "INTC":  ["AMD", "TXN", "QCOM"],
    "ORCL":  ["MSFT", "ADBE", "CRM"],
    "CSCO":  ["ORCL", "ACN", "QCOM"],
    "ADBE":  ["ORCL", "CRM", "MSFT"],
    "CRM":   ["ADBE", "ORCL", "ACN"],
    "ACN":   ["CSCO", "CRM", "ORCL"],
    "QCOM":  ["TXN", "AVGO", "AMD"],
    "TXN":   ["QCOM", "AVGO", "ORCL"],
    # Communication
    "GOOGL": ["META", "AAPL", "DIS"],
    "META":  ["GOOGL", "NFLX", "AAPL"],
    "NFLX":  ["DIS", "META", "GOOGL"],
    "DIS":   ["NFLX", "META", "VZ"],
    "T":     ["VZ", "DIS", "NFLX"],
    "VZ":    ["T", "DIS", "NFLX"],
    # Consumer Discretionary
    "AMZN":  ["TSLA", "HD", "NKE"],
    "TSLA":  ["AMZN", "HD", "MCD"],
    "HD":    ["AMZN", "NKE", "MCD"],
    "MCD":   ["NKE", "HD", "TSLA"],
    "NKE":   ["HD", "MCD", "AMZN"],
    # Consumer Staples
    "WMT":   ["COST", "PG", "KO"],
    "PG":    ["KO", "PEP", "WMT"],
    "KO":    ["PEP", "PG", "WMT"],
    "PEP":   ["KO", "PG", "COST"],
    "COST":  ["WMT", "PG", "PEP"],
    # Healthcare
    "JNJ":   ["PFE", "MRK", "ABT"],
    "UNH":   ["JNJ", "ABT", "TMO"],
    "PFE":   ["MRK", "JNJ", "ABT"],
    "MRK":   ["PFE", "JNJ", "ABT"],
    "ABT":   ["TMO", "DHR", "JNJ"],
    "TMO":   ["DHR", "ABT", "UNH"],
    "DHR":   ["TMO", "ABT", "UNH"],
    # Financials
    "JPM":   ["BAC", "WFC", "GS"],
    "V":     ["MA", "JPM", "BAC"],
    "MA":    ["V", "JPM", "BAC"],
    "BAC":   ["JPM", "WFC", "MS"],
    "WFC":   ["BAC", "JPM", "MS"],
    "MS":    ["GS", "BAC", "JPM"],
    "GS":    ["MS", "JPM", "BAC"],
    "BLK":   ["JPM", "GS", "MS"],
    "BRK-B": ["JPM", "BAC", "BLK"],
    # Energy / Industrials / Materials — small sectors; same-sector
    # replacement may not always work, so we cross to closest-cousin
    "XOM":   ["CAT", "BA", "HON"],  # Energy → Industrials cross-sector fallback
    "CAT":   ["HON", "BA", "XOM"],
    "BA":    ["HON", "CAT", "XOM"],
    "HON":   ["CAT", "BA", "XOM"],
    "LIN":   ["HON", "CAT", "XOM"],
}


def _autocomplete_replacement_map() -> None:
    """v6.0.x: auto-fill REPLACEMENT_MAP for the expanded 138-name
    universe. For any ticker in SECTORS that's missing from the
    hand-curated map, pick 3 same-sector siblings as replacements.
    Selection is deterministic (sorted by ticker) so the same
    replacements are chosen on every load — important for
    wash-sale logic to be stable across daily runs.

    Hand-curated entries are NEVER overwritten; this only fills gaps.
    """
    from .sectors import SECTORS
    # Build sector → tickers map
    by_sector: dict[str, list[str]] = {}
    for t, s in SECTORS.items():
        by_sector.setdefault(s, []).append(t)
    for s in by_sector:
        by_sector[s].sort()
    # Fill gaps
    for ticker, sector in SECTORS.items():
        if ticker in REPLACEMENT_MAP:
            continue
        siblings = [t for t in by_sector[sector] if t != ticker]
        if not siblings:
            # Singleton sector — cross to nearest cousin
            siblings = [t for t in REPLACEMENT_MAP.keys() if t != ticker][:3]
        REPLACEMENT_MAP[ticker] = siblings[:3]


# Run autocomplete at import time so the expanded universe has full
# REPLACEMENT_MAP coverage from the first orchestrator tick.
_autocomplete_replacement_map()


def _autocomplete_quality_and_caps() -> None:
    """v6.0.x: same idea for QUALITY_SCORES + APPROX_CAP_B. Default
    quality 1.0 (neutral) and cap 50B (small) for any ticker not
    hand-curated. The strategy operator can hand-tune later if a
    specific name deserves better."""
    from .sectors import SECTORS
    for ticker in SECTORS:
        if ticker not in QUALITY_SCORES:
            QUALITY_SCORES[ticker] = 1.0
        if ticker not in APPROX_CAP_B:
            APPROX_CAP_B[ticker] = 50.0


@dataclass
class HarvestSwap:
    sell_ticker: str
    buy_ticker: str
    weight: float
    unrealized_loss_pct: float
    reason: str


@dataclass
class TLHPlan:
    target_weights: dict[str, float]   # what the direct-index core wants to hold
    swaps: list[HarvestSwap]            # this run's harvest events
    skipped: list[tuple[str, str]]      # (ticker, reason) for would-have-harvested but blocked
    cumulative_realized_loss: float     # all-time realized loss from prior swaps
    notes: list[str]


def get_wash_sale_blocked(db_path=DB_PATH, today: Optional[date] = None) -> set[str]:
    """Tickers sold at a loss in the last WASH_SALE_DAYS — these are
    blocked from being bought back as a replacement (would trigger
    wash sale).
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=WASH_SALE_DAYS)
    blocked = set()
    try:
        con = sqlite3.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM position_lots "
                "WHERE closed_at IS NOT NULL "
                "AND closed_at >= ? "
                "AND realized_pnl IS NOT NULL AND realized_pnl < 0",
                (cutoff.isoformat(),),
            ).fetchall()
        finally:
            con.close()
        blocked = {r[0] for r in rows}
    except Exception:
        pass
    return blocked


def get_current_unrealized_pnl(db_path=DB_PATH) -> dict[str, dict]:
    """Returns {ticker: {qty, avg_cost, ...}} from open position_lots.

    Aggregates across multiple open lots per ticker (FIFO accounting
    happens on close, so multiple lots can be open simultaneously for
    averaged-cost reporting).
    """
    out = {}
    try:
        con = sqlite3.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT symbol, sleeve, qty, open_price "
                "FROM position_lots WHERE closed_at IS NULL"
            ).fetchall()
        finally:
            con.close()
        agg = {}
        for sym, sleeve, qty, open_price in rows:
            if open_price is None or qty is None:
                continue
            d = agg.setdefault(sym, {"qty": 0.0, "cost_total": 0.0,
                                       "sleeves": set()})
            d["qty"] += float(qty)
            d["cost_total"] += float(qty) * float(open_price)
            d["sleeves"].add(sleeve)
        for sym, d in agg.items():
            if d["qty"] > 0:
                out[sym] = {
                    "qty": d["qty"],
                    "avg_cost": d["cost_total"] / d["qty"],
                    "sleeves": list(d["sleeves"]),
                }
    except Exception:
        pass
    return out


def get_cumulative_realized_loss(db_path=DB_PATH) -> float:
    """Total realized loss across all-time TLH and other sells.

    Sums realized_pnl for closed lots where realized_pnl < 0.
    Returns a NEGATIVE number representing the cumulative loss
    available for tax offset (subject to wash-sale recapture if any
    wash sales occurred).
    """
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM position_lots "
                "WHERE closed_at IS NOT NULL "
                "AND realized_pnl IS NOT NULL AND realized_pnl < 0"
            ).fetchone()
        finally:
            con.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


# Approximate market caps (USD billions, mid-2026 snapshot). Hand-
# curated from public data; not live. The exactness doesn't matter
# much for TLH (the edge is structural to having individual holdings,
# not to exact cap-weight tracking) but using realistic weights keeps
# tracking error to SPY ≈ 100-200bps instead of the ~500bps a naive
# sector-equal-weight would produce.
APPROX_CAP_B: dict[str, float] = {
    "AAPL": 3500, "MSFT": 3300, "NVDA": 3000, "GOOGL": 2200, "AMZN": 2000,
    "META": 1500, "TSLA": 900, "AVGO": 900, "JPM": 700, "BRK-B": 1000,
    "V": 600, "MA": 500, "WMT": 700, "JNJ": 400, "UNH": 500,
    "XOM": 500, "ORCL": 400, "PG": 400, "COST": 400, "HD": 400,
    "BAC": 350, "AMD": 350, "ABT": 250, "MRK": 300, "PFE": 200,
    "CSCO": 200, "ADBE": 250, "CRM": 250, "TMO": 220, "ACN": 200,
    "DHR": 200, "QCOM": 200, "DIS": 180, "VZ": 180, "T": 160,
    "INTC": 200, "TXN": 180, "WFC": 200, "GS": 150, "MS": 180,
    "BLK": 150, "CAT": 150, "HON": 150, "MCD": 200, "NKE": 130,
    "KO": 270, "PEP": 230, "NFLX": 250, "BA": 120, "LIN": 200,
}


# v6.0.x: Novy-Marx quality scores (hand-curated approximation of
# gross-profitability-to-assets, ROIC, and balance-sheet strength).
# Score range ~0.5–1.5 with mean ≈ 1.0. Hand-calibrated from
# 2025 10-K data (Yahoo Finance, Macrotrends snapshot).
#
# The pattern: hyperscaler tech + asset-light franchises (V, MA,
# Visa, Costco, Microsoft) score highest; cyclicals + capital-
# intensive industrials score lowest. This matches the
# academic-quality factor and the trailing 5-yr ROE rankings
# from the public datasets.
#
# Quality is one of the most-replicated post-Fama-French anomalies
# (Novy-Marx 2013; Asness-Frazzini-Pedersen 2019 follow-up). The
# 0.5x → 1.5x range is intentionally conservative — the production
# tilt knob lets the operator dial it down further via
# QUALITY_TILT_STRENGTH (default 0.5, range 0..1).
QUALITY_SCORES: dict[str, float] = {
    # Top tier (asset-light, high-moat, capital-return machines)
    "AAPL": 1.45, "MSFT": 1.40, "V": 1.50, "MA": 1.48, "GOOGL": 1.35,
    "META": 1.35, "COST": 1.30, "NVDA": 1.30, "ADBE": 1.30, "ORCL": 1.20,
    "AVGO": 1.25, "CRM": 1.20, "TXN": 1.20, "QCOM": 1.15, "ACN": 1.20,
    # Strong mid (defensive consumer + healthcare)
    "JNJ":  1.20, "UNH": 1.20, "ABT": 1.15, "TMO": 1.15, "DHR": 1.15,
    "PG":   1.20, "KO":  1.15, "PEP": 1.15, "MRK": 1.10, "WMT": 1.10,
    "MCD":  1.20, "HD":  1.15, "NKE": 1.05, "LIN": 1.15, "HON": 1.10,
    # Average (banks + media + transports)
    "JPM":  1.10, "BLK": 1.10, "GS":  1.05, "MS":  1.00, "BRK-B":1.15,
    "BAC":  0.95, "WFC": 0.95, "PFE": 1.00, "AMZN":1.10, "TSLA":1.00,
    "DIS":  0.90, "NFLX":1.05, "T":   0.90, "VZ":  0.90, "CSCO":1.05,
    # Below average (cyclical, levered, structural-headwind)
    "AMD":  1.00, "INTC":0.75, "CAT": 0.95, "BA":  0.70, "XOM": 0.90,
}


def quality_tilted_targets(universe: list[str],
                            gross: float = 1.00,
                            tilt_strength: float = 0.5) -> dict[str, float]:
    """Cap-weighted basket with a quality overlay.

    The pure cap-weight is multiplied by `quality_score^tilt_strength`
    and then re-normalized to the target gross. With tilt_strength=0,
    this degenerates to pure cap-weight. With tilt_strength=1, weights
    skew strongly toward high-quality names. The default 0.5 is a
    middle-of-the-road tilt that the literature suggests adds
    0.3–0.7%/yr long-run alpha without crushing diversification.

    Edge source: Novy-Marx 2013 + Asness-Frazzini-Pedersen 2019
    show the quality premium has not decayed (unlike value, which
    decayed post-publication). The factor is uncorrelated with
    momentum, so it stacks with the auto-router alpha sleeve.

    Missing-quality-score tickers default to 1.0 (no tilt)."""
    if tilt_strength < 0:
        tilt_strength = 0
    if tilt_strength > 1:
        tilt_strength = 1
    caps = {}
    for sym in universe:
        cap = APPROX_CAP_B.get(sym, 50.0)
        q = QUALITY_SCORES.get(sym, 1.0)
        caps[sym] = cap * (q ** tilt_strength)
    total = sum(caps.values())
    if total <= 0:
        return {}
    return {s: gross * c / total for s, c in caps.items()}


def drawdown_gross_scalar(current_dd: float,
                           high_water_dd: float = 0.0,
                           reduce_band: tuple[float, float] = (-0.05, -0.10),
                           floor: float = 0.70) -> float:
    """Conservative drawdown-aware gross-sizing primitive.

    Used as an overlay on the alpha sleeve's gross when DRAWDOWN_AWARE
    _ENABLED=true. The behavior is intentionally one-sided: it ONLY
    reduces gross during drawdowns. The academic literature
    (Asness 2014, Garleanu-Pedersen 2013) supports BOTH directions
    (lever up during DDs for recovery), but levering up after a DD
    is fundamentally risky for retail — a path-dependent strategy
    that can compound losses. So we ship the safe direction only.

    Args:
        current_dd: current drawdown from high-water mark, NEGATIVE.
                    -0.05 means 5% drawdown.
        high_water_dd: max-DD-ever (for hysteresis; not yet used).
        reduce_band: (start, end) drawdown thresholds. Between these
                     two values, gross linearly tapers from 1.0 to
                     `floor`.
        floor: minimum scalar (default 0.70 = 30% de-grossing cap).

    Returns: scalar in [floor, 1.0]
    """
    if current_dd >= reduce_band[0]:
        return 1.0
    if current_dd <= reduce_band[1]:
        return floor
    # Linear taper between the two band edges
    span = reduce_band[0] - reduce_band[1]
    if span <= 0:
        return floor
    progress = (reduce_band[0] - current_dd) / span  # 0..1
    return 1.0 - progress * (1.0 - floor)


def cap_weighted_targets(universe: list[str],
                          gross: float = 1.00) -> dict[str, float]:
    """Cap-weighted direct-index basket using approximate market caps.

    For each ticker in the universe, weight = (its cap) / (sum of caps).
    Tickers missing from APPROX_CAP_B get a placeholder cap of 50B
    (small but not zero).

    The weights are normalized to `gross` total. Tracking error to SPY
    is ~100-200bps annualized given the 50-name universe vs SPY's 500
    names; this is the structural cost of being able to TLH on
    individual names.
    """
    caps = {}
    for sym in universe:
        caps[sym] = APPROX_CAP_B.get(sym, 50.0)
    total = sum(caps.values())
    if total <= 0:
        return {}
    return {s: gross * c / total for s, c in caps.items()}


def plan_tlh(universe: list[str],
              current_prices: Optional[dict[str, float]] = None,
              core_pct: float = DEFAULT_CORE_PCT,
              db_path=DB_PATH,
              today: Optional[date] = None,
              min_loss_pct: float = 0.05,
              core_sleeve_tag: str = "direct_index_core",
              quality_tilt: float = 0.0) -> TLHPlan:
    """The main planner. Returns target_weights for the direct-index
    core plus a list of harvest swaps to execute this run.

    Args:
        universe: tickers eligible for the direct-index basket.
        current_prices: {ticker: current_price}. Required to compute
            unrealized P&L. If None, the planner can still produce
            target_weights but will skip swap detection.
        core_pct: fraction of total capital allocated to the direct-
            index core. Default 0.70.
        min_loss_pct: minimum unrealized-loss threshold to trigger a
            harvest. Default 5%; below this, the after-tax benefit is
            small enough that transaction costs probably eat it.
        core_sleeve_tag: sleeve identifier for journaling.

    Returns:
        TLHPlan with target_weights (for the core sleeve only) and the
        list of swaps to execute. The orchestrator combines these with
        the alpha sleeve's targets to form the full book.
    """
    if today is None:
        today = date.today()

    notes = []
    notes.append(f"core_pct={core_pct:.2f}, today={today.isoformat()}, "
                  f"min_loss_pct={min_loss_pct:.2%}")

    # 1. Target weights for the direct-index core. When quality_tilt > 0
    #    we use the Novy-Marx quality-tilted basket (still cap-anchored,
    #    but quality-weighted on the margin). Otherwise pure cap-weight.
    if quality_tilt > 0:
        target_weights = quality_tilted_targets(
            universe, gross=core_pct, tilt_strength=quality_tilt,
        )
        notes.append(f"quality tilt: strength={quality_tilt:.2f}")
    else:
        target_weights = cap_weighted_targets(universe, gross=core_pct)

    # 2. Current open positions + cost basis.
    open_positions = get_current_unrealized_pnl(db_path=db_path)
    # Restrict to positions in the core sleeve (don't TLH alpha-sleeve
    # holdings — those rotate too often for TLH to make sense)
    core_positions = {
        sym: d for sym, d in open_positions.items()
        if core_sleeve_tag in d.get("sleeves", [])
    }

    # 3. Wash-sale blocked set
    blocked = get_wash_sale_blocked(db_path=db_path, today=today)
    notes.append(f"wash-sale blocked: {len(blocked)} tickers")

    # 4. Detect harvest candidates
    swaps: list[HarvestSwap] = []
    skipped: list[tuple[str, str]] = []
    if current_prices:
        for sym, d in core_positions.items():
            avg_cost = d["avg_cost"]
            cur_px = current_prices.get(sym)
            if cur_px is None or avg_cost <= 0:
                continue
            unrealized_pct = (cur_px - avg_cost) / avg_cost
            if unrealized_pct >= -min_loss_pct:
                continue  # not enough loss to harvest
            # Find replacement
            candidates = REPLACEMENT_MAP.get(sym, [])
            replacement = None
            for c in candidates:
                if c in blocked:
                    continue
                if c in core_positions:
                    # already holding it — skip; would just net-out, not harvest
                    continue
                if c not in universe:
                    continue
                replacement = c
                break
            if replacement is None:
                skipped.append((sym, f"no wash-sale-safe replacement among "
                                        f"{candidates}"))
                continue
            weight = target_weights.get(sym, 0.0)
            swaps.append(HarvestSwap(
                sell_ticker=sym,
                buy_ticker=replacement,
                weight=weight,
                unrealized_loss_pct=unrealized_pct,
                reason=(f"harvest: {sym} at {unrealized_pct*100:+.1f}% "
                         f"→ {replacement} (sector-matched, not wash-sale)"),
            ))

    # 5. Apply swaps to target_weights
    for swap in swaps:
        w = target_weights.pop(swap.sell_ticker, 0.0)
        target_weights[swap.buy_ticker] = target_weights.get(
            swap.buy_ticker, 0.0) + w

    # 6. Cumulative realized loss (for reporting)
    cum_loss = get_cumulative_realized_loss(db_path=db_path)
    notes.append(f"cumulative realized loss (all-time): ${cum_loss:,.2f}")
    notes.append(f"harvest swaps this run: {len(swaps)}")

    return TLHPlan(
        target_weights=target_weights,
        swaps=swaps,
        skipped=skipped,
        cumulative_realized_loss=cum_loss,
        notes=notes,
    )


def format_plan_summary(plan: TLHPlan) -> str:
    """Operator-readable summary of a TLH plan. Used in orchestrator log."""
    lines = []
    lines.append(f"TLH plan: {len(plan.target_weights)} core targets, "
                  f"{len(plan.swaps)} harvests, {len(plan.skipped)} skipped, "
                  f"cum realized loss ${plan.cumulative_realized_loss:,.2f}")
    for s in plan.swaps:
        lines.append(f"  HARVEST: {s.sell_ticker} → {s.buy_ticker} "
                      f"(loss {s.unrealized_loss_pct*100:+.1f}%, w={s.weight*100:.1f}%)")
    for sym, reason in plan.skipped:
        lines.append(f"  SKIPPED: {sym} — {reason}")
    return "\n".join(lines)


# Run autocomplete at import time — must be at the bottom so
# QUALITY_SCORES and APPROX_CAP_B are already defined.
_autocomplete_quality_and_caps()

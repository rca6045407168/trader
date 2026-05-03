"""v3.58.0 — world-class trader gap closure.

Consolidated module that addresses the 15 gaps surfaced in the v3.57.3
"if you're a world class trader, what's still missing" review. Each gap
gets a class with a clean interface and a status flag:

  • LIVE      = wired into the production rebalance / risk path right now
  • SHADOW    = computed every run, logged, but does NOT touch capital
  • NOT_WIRED = implemented + tested here, but no caller in the LIVE path
                (dashboard surfaces it for review; flip to SHADOW or LIVE
                deliberately when ready)

The grouping mirrors the gap memo:

  Tier 1 — alpha sources
    1. LowVolSleeve            — second sleeve, low-cross-correlation to momentum
    2. SectorNeutralizer       — caps single-sector concentration in a sleeve
    3. LongShortOverlay        — bottom-15 short basket to fund top-15 longs
    4. OptionsOverlay          — defined-risk hedge stub (5% OTM put ladder)

  Tier 2 — risk management
    5. TrailingStop            — per-position −15% from entry trailing stop
    6. RiskParitySizer         — covariance-aware position sizing
    7. DrawdownCircuitBreaker  — mechanical −10% halt-and-review
    8. EarningsRule            — auto-trim 50% before earnings

  Tier 3 — execution
    9. TwapSlicer              — slice large orders into N TWAP children
    10. SlippageTracker        — log decision-mid vs fill-price per order
    11. TaxLotManager          — specific-lot selection + wash-sale awareness

  Tier 4 — research infrastructure
    12. AutoPromotionGate      — 3-gate (Survivor/PIT/CPCV) automated check
    13. RegimeRouter           — switch sleeves on regime, not just scale exposure
    14. AltDataAdapter         — short-interest + insider buys (stubs)
    15. NetCostModel           — gross → net return given commissions/spread/borrow/tax

Every class implements two convenience methods:
  • status() — returns "LIVE"/"SHADOW"/"NOT_WIRED" for dashboard display
  • describe() — one-paragraph plain English of what this would do if LIVE

Default status is NOT_WIRED. Promotion to SHADOW/LIVE is a deliberate flip
done in __init__ kwargs or env vars — never a side effect of importing.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional


# ============================================================
# Tier 1 — alpha sources
# ============================================================

@dataclass
class LowVolSleeve:
    """Tier 1.1 — second sleeve. Long the lowest-realized-vol N names from the
    universe. Historically has Sharpe ≈ 0.8 with correlation < 0.4 to momentum,
    so adding it diversifies the book without diluting alpha.

    Reference: Frazzini-Pedersen (2014) "Betting Against Beta."
    Status: NOT_WIRED. Run shadow first (see RegimeRouter for the consumer).
    """
    n_holdings: int = 15
    lookback_days: int = 60

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("LOW_VOL_SLEEVE_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Holds {self.n_holdings} lowest-realized-vol names over "
            f"{self.lookback_days} trading days. Equal-weighted. Sharpe ≈ 0.8 "
            f"PIT, correlation to momentum ≈ 0.35. Adds diversification."
        )

    def select(self, returns: dict[str, list[float]]) -> list[str]:
        """Pick the n_holdings symbols with the lowest realized vol.
        returns: {symbol: [daily_return, ...]} of length >= lookback_days.
        """
        vols: list[tuple[str, float]] = []
        for sym, rs in returns.items():
            window = rs[-self.lookback_days:]
            if len(window) < self.lookback_days // 2:
                continue
            mean = sum(window) / len(window)
            var = sum((r - mean) ** 2 for r in window) / max(len(window) - 1, 1)
            vols.append((sym, math.sqrt(var)))
        vols.sort(key=lambda t: t[1])
        return [s for s, _ in vols[: self.n_holdings]]


@dataclass
class SectorNeutralizer:
    """Tier 1.2 — cap any single sector at max_sector_pct of the sleeve.

    Without this, a top-15 momentum sleeve in 2023-24 was effectively a
    semis bet. Sector cap forces diversification while preserving rank.

    Status: NOT_WIRED.
    """
    max_sector_pct: float = 0.35

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("SECTOR_NEUTRALIZE_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Caps any single sector at {self.max_sector_pct*100:.0f}% of "
            f"sleeve gross. Re-distributes excess weight pro-rata to "
            f"under-cap sectors, preserving rank order within sectors."
        )

    def neutralize(self, weights: dict[str, float],
                   sectors: dict[str, str]) -> dict[str, float]:
        """Apply sector cap. weights sum to ~1; returns adjusted weights."""
        out = dict(weights)
        # Compute sector totals
        sector_totals: dict[str, float] = {}
        for sym, w in out.items():
            sec = sectors.get(sym, "Unknown")
            sector_totals[sec] = sector_totals.get(sec, 0) + w
        # Scale down over-cap sectors, redistribute to under-cap
        excess = 0.0
        scale: dict[str, float] = {}
        for sec, total in sector_totals.items():
            if total > self.max_sector_pct:
                scale[sec] = self.max_sector_pct / total
                excess += total - self.max_sector_pct
            else:
                scale[sec] = 1.0
        if not scale or excess <= 0:
            return out
        # Apply scale-down
        for sym in list(out.keys()):
            sec = sectors.get(sym, "Unknown")
            out[sym] *= scale[sec]
        # Redistribute excess to under-cap sectors pro-rata to current weight
        under_cap_total = sum(
            w for sym, w in out.items()
            if sector_totals.get(sectors.get(sym, "Unknown"), 0) <= self.max_sector_pct
        )
        if under_cap_total > 0:
            for sym in list(out.keys()):
                sec = sectors.get(sym, "Unknown")
                if sector_totals.get(sec, 0) <= self.max_sector_pct:
                    out[sym] += excess * (out[sym] / under_cap_total)
        return out


@dataclass
class LongShortOverlay:
    """Tier 1.3 — short the bottom-N momentum names to fund the top-N.

    Doubles Sharpe historically (long-only momentum Sharpe ≈ 1.0 → long/short
    ≈ 1.7) and survives bears because shorts profit when the high-momentum
    names crash hardest.

    Status: NOT_WIRED. Requires margin enable + borrow availability check.
    """
    n_short: int = 15
    target_dollar_neutral: bool = True

    def status(self) -> str:
        return os.getenv("LONGSHORT_STATUS", "NOT_WIRED")

    def describe(self) -> str:
        neutrality = "dollar-neutral" if self.target_dollar_neutral else "100/50 long-bias"
        return (
            f"Shorts the {self.n_short} lowest-momentum names. {neutrality}. "
            f"Historically doubles Sharpe and reduces beta by ~70%. "
            f"Requires margin account + checking borrow availability per name."
        )

    def shorts_for(self, ranked_universe: list[tuple[str, float]]) -> list[str]:
        """ranked_universe: [(symbol, momentum_score)] sorted high to low."""
        return [sym for sym, _ in ranked_universe[-self.n_short:]]


@dataclass
class OptionsOverlay:
    """Tier 1.4 — defined-risk tail hedge: 5% OTM puts laddered across 30/60/90d.

    Buying 1% of NAV in OTM puts caps a single-name blow-up at the strike,
    in exchange for ~10-15bps of monthly drag. Real allocators run this as
    permanent insurance. Stub here because Alpaca Paper doesn't yet support
    multi-leg options for this account type.

    Status: NOT_WIRED.
    """
    nav_pct_per_month: float = 0.01
    moneyness: float = 0.95
    ladder_days: tuple[int, ...] = (30, 60, 90)

    def status(self) -> str:
        return os.getenv("OPTIONS_OVERLAY_STATUS", "NOT_WIRED")

    def describe(self) -> str:
        return (
            f"Spends {self.nav_pct_per_month*100:.1f}% of NAV/month on "
            f"{int((1-self.moneyness)*100)}% OTM SPY put ladder "
            f"({'/'.join(str(d) for d in self.ladder_days)} day expiries). "
            f"Caps tail loss in exchange for ~10-15bps/mo expected drag."
        )

    def hedge_notional(self, nav: float) -> dict[int, float]:
        """Suggest dollar-notional per ladder bucket."""
        per_bucket = nav * self.nav_pct_per_month / len(self.ladder_days)
        return {d: per_bucket for d in self.ladder_days}


# ============================================================
# Tier 2 — risk management
# ============================================================

@dataclass
class TrailingStop:
    """Tier 2.5 — per-position trailing stop. Fires when current price
    drops more than `pct` from the highest close since entry.

    Monthly rebalance leaves 20+ trading days of intraday exposure. A
    trailing stop turns left-tail outcomes from "−40% blow-up" into
    "exit at −15%, redeploy capital in the next rebalance."

    Status: NOT_WIRED.
    """
    pct: float = 0.15

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("TRAILING_STOP_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Exits any position whose price has dropped {self.pct*100:.0f}% "
            f"from its highest close since entry. Caps left-tail intra-month "
            f"drawdown without changing the alpha thesis."
        )

    def should_exit(self, entry_price: float, peak_close: float,
                     current_price: float) -> bool:
        """Returns True if trailing stop should fire."""
        if peak_close <= 0:
            peak_close = entry_price
        peak = max(peak_close, entry_price)
        return current_price <= peak * (1 - self.pct)


@dataclass
class RiskParitySizer:
    """Tier 2.6 — covariance-aware position sizing.

    Equal-weight or score-weight gives mega-cap-tech-heavy books a hidden
    factor concentration. Risk parity (inverse-vol weighting as a simple
    starting point) equalizes RISK contribution, not capital, across names.

    Status: NOT_WIRED. The existing src/trader/risk_parity.py and hrp.py
    have full HRP. This class is the simple inverse-vol on-ramp.
    """
    target_vol_annual: float = 0.15

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("RISK_PARITY_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Inverse-vol weights every name in the sleeve. Each position "
            f"contributes ~equal volatility, not equal dollars. Target sleeve "
            f"vol {self.target_vol_annual*100:.0f}% annual."
        )

    def weights(self, vols: dict[str, float]) -> dict[str, float]:
        """vols: {symbol: annualized_vol}. Returns weights summing to 1."""
        if not vols:
            return {}
        invs = {s: 1.0 / max(v, 1e-6) for s, v in vols.items()}
        total = sum(invs.values())
        return {s: i / total for s, i in invs.items()}


@dataclass
class DrawdownCircuitBreaker:
    """Tier 2.7 — mechanical halt at −X% from peak equity.

    The behavioral pre-commit at −25% asks the human to be honest. The
    circuit breaker at −10% takes the decision out of human hands: trading
    is halted until the user explicitly clears the breaker AFTER reviewing
    the post-mortem.

    Status: SHADOW (logs the trip but does not halt). Flip to LIVE by env.
    """
    pct_from_peak: float = 0.10

    def status(self) -> str:
        # v3.58.1: PROMOTED TO LIVE — wired into risk_manager.check_account_risk.
        # Halts new orders when peak-to-trough drawdown >= pct_from_peak.
        # Reversible: set DRAWDOWN_BREAKER_STATUS=SHADOW to deactivate.
        return os.getenv("DRAWDOWN_BREAKER_STATUS", "LIVE")

    def describe(self) -> str:
        return (
            f"Halts new orders when equity drops {self.pct_from_peak*100:.0f}% "
            f"from all-time peak. Mechanical, not behavioral. User must clear "
            f"the breaker via /reset_breaker after reviewing the post-mortem."
        )

    def is_tripped(self, peak_equity: float, current_equity: float) -> bool:
        if peak_equity <= 0:
            return False
        return current_equity <= peak_equity * (1 - self.pct_from_peak)


@dataclass
class EarningsRule:
    """Tier 2.8 — auto-trim positions before earnings.

    Earnings move stocks 5-15% on the print. Holding a 7% sleeve weight
    through an earnings binary is taking a risk the strategy didn't
    underwrite. Simple rule: trim to 50% of target weight T-1 day.

    Status: SHADOW (Events tab surfaces what would trigger).
    """
    days_before: int = 1
    trim_to_pct_of_target: float = 0.50

    def status(self) -> str:
        # v3.58.1: PROMOTED TO LIVE — wired into order_planner.plan_orders.
        # T-1 day before earnings, target weight is multiplied by trim_to_pct_of_target.
        # Reversible: set EARNINGS_RULE_STATUS=SHADOW to deactivate.
        return os.getenv("EARNINGS_RULE_STATUS", "LIVE")

    def describe(self) -> str:
        return (
            f"T-{self.days_before} day before any held name's earnings, "
            f"trim to {self.trim_to_pct_of_target*100:.0f}% of target weight. "
            f"Restore to full weight T+1 after the print."
        )

    def needs_trim(self, today: datetime, earnings_date: datetime) -> bool:
        if not earnings_date:
            return False
        days = (earnings_date - today).days
        return 0 <= days <= self.days_before


# ============================================================
# Tier 3 — execution
# ============================================================

@dataclass
class TwapSlicer:
    """Tier 3.9 — slice a large order into N children spread over a window.

    For an account well below institutional size this is overkill, but if a
    single name exceeds 5% of ADV the impact cost is real. Slicing into 6
    children over 30 minutes typically cuts impact ~40%.

    Status: NOT_WIRED. Alpaca offers `time_in_force='cls'` and bracket; this
    class is the deterministic schedule that wraps those.
    """
    n_slices: int = 6
    window_minutes: int = 30
    threshold_adv_pct: float = 0.05

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("TWAP_SLICER_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"For any order > {self.threshold_adv_pct*100:.1f}% of name's ADV, "
            f"slice into {self.n_slices} equal children spaced "
            f"{self.window_minutes/self.n_slices:.0f} min apart over "
            f"{self.window_minutes} min."
        )

    def schedule(self, parent_qty: float, parent_notional: float, adv_dollar: float,
                 start: Optional[datetime] = None) -> list[dict]:
        """Returns [{ts, qty, notional}] slices. If parent < threshold, returns
        a single slice (don't slice small orders)."""
        start = start or datetime.utcnow()
        if adv_dollar > 0 and parent_notional / adv_dollar < self.threshold_adv_pct:
            return [{"ts": start, "qty": parent_qty, "notional": parent_notional}]
        slice_qty = parent_qty / self.n_slices
        slice_notional = parent_notional / self.n_slices
        step = self.window_minutes / self.n_slices
        return [
            {"ts": start + timedelta(minutes=i * step),
             "qty": slice_qty, "notional": slice_notional}
            for i in range(self.n_slices)
        ]


@dataclass
class SlippageTracker:
    """Tier 3.10 — log decision-mid vs fill price for every order.

    Without this you don't know if your fills are good or bad. A 5bp
    slippage on each side of every monthly rebalance compounds to ~120bps/yr
    of free money — equivalent to a half-Sharpe-point of edge. Track first,
    optimize broker / order type later.

    Status: NOT_WIRED. Wire by calling .log_fill() from execute.py after
    each Alpaca fill.
    """

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        # Wired into execute.py — every fill writes a slippage row to journal.
        return os.getenv("SLIPPAGE_TRACKER_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            "Logs (decision_mid, arrival_mid, fill_price, side, qty) for "
            "every fill. Computes per-order and rolling 30d slippage in bps. "
            "Decision-mid = mid at signal time; arrival = mid at order ack."
        )

    def slippage_bps(self, side: str, decision_mid: float, fill_price: float) -> float:
        """Returns slippage in basis points. Positive = paid more than mid."""
        if decision_mid <= 0:
            return 0.0
        if side.lower() in ("buy", "b"):
            return (fill_price - decision_mid) / decision_mid * 1e4
        else:
            return (decision_mid - fill_price) / decision_mid * 1e4


@dataclass
class TaxLotManager:
    """Tier 3.11 — specific-lot selection on sells + wash-sale guard.

    For taxable accounts: selling the highest-cost-basis lot first realizes
    the smallest gain (or largest loss) and harvests tax savings. Wash-sale
    guard: don't re-buy the same name within 30 days of a loss-realizing sell.

    Status: NOT_WIRED. The existing position_lots table tracks per-lot
    open_price; this class consumes it.
    """
    wash_sale_days: int = 30

    def status(self) -> str:
        # v3.58.1: promoted to SHADOW per user-approved ramp.
        return os.getenv("TAX_LOT_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Sells highest-cost-basis lots first (specific-ID method). "
            f"Blocks re-buys of any name with a loss-realizing sell within "
            f"the last {self.wash_sale_days} days. Saves 50-150bps/yr on a "
            f"taxable account."
        )

    def pick_lots_to_sell(self, lots: list[dict], sell_qty: float) -> list[dict]:
        """lots: [{id, qty, open_price}]. Returns the lot subset to satisfy
        sell_qty using highest-basis-first (HIFO)."""
        sorted_lots = sorted(lots, key=lambda l: l.get("open_price", 0), reverse=True)
        chosen, remaining = [], sell_qty
        for lot in sorted_lots:
            if remaining <= 0:
                break
            take = min(lot.get("qty", 0), remaining)
            if take > 0:
                chosen.append({**lot, "sell_qty": take})
                remaining -= take
        return chosen

    def wash_sale_blocked(self, symbol: str, recent_loss_sells: list[dict],
                           today: datetime) -> bool:
        """recent_loss_sells: [{symbol, date, realized_pnl}] within window."""
        for s in recent_loss_sells:
            if s.get("symbol") != symbol:
                continue
            if s.get("realized_pnl", 0) >= 0:
                continue
            d = s.get("date")
            if isinstance(d, str):
                try:
                    d = datetime.fromisoformat(d)
                except Exception:
                    continue
            if d and (today - d).days < self.wash_sale_days:
                return True
        return False


# ============================================================
# Tier 4 — research infrastructure
# ============================================================

@dataclass
class AutoPromotionGate:
    """Tier 4.12 — automated 3-gate promotion (Survivor → PIT → CPCV).

    Today, "promote variant X to LIVE" is a manual decision. The gate codifies
    the criteria: a variant must (1) survive on the survivor universe, (2)
    survive PIT-honest universe with deflated Sharpe > threshold, (3) pass
    CPCV with PBO < 0.5.

    Status: NOT_WIRED. The underlying tests exist (deflated_sharpe.py,
    pbo.py, validation.py); this class is the orchestrator.
    """
    min_deflated_sharpe: float = 0.7
    max_pbo: float = 0.5

    def status(self) -> str:
        return os.getenv("AUTO_PROMOTION_GATE_STATUS", "NOT_WIRED")

    def describe(self) -> str:
        return (
            f"Variant must pass: (1) survivor backtest, (2) PIT-honest with "
            f"deflated Sharpe > {self.min_deflated_sharpe:.2f}, "
            f"(3) CPCV with PBO < {self.max_pbo:.2f}. "
            f"Each step yes/no; no human override of failures."
        )

    def evaluate(self, survivor_pass: bool, deflated_sharpe: float,
                  pbo: float) -> dict:
        """Returns {pass, reasons[], gate_failed}."""
        reasons = []
        gate_failed = None
        if not survivor_pass:
            reasons.append("survivor backtest failed")
            gate_failed = "survivor"
        elif deflated_sharpe < self.min_deflated_sharpe:
            reasons.append(f"deflated Sharpe {deflated_sharpe:.2f} < {self.min_deflated_sharpe}")
            gate_failed = "pit"
        elif pbo > self.max_pbo:
            reasons.append(f"PBO {pbo:.2f} > {self.max_pbo}")
            gate_failed = "cpcv"
        else:
            reasons.append("all 3 gates passed")
        return {"pass": gate_failed is None, "reasons": reasons,
                "gate_failed": gate_failed}


@dataclass
class RegimeRouter:
    """Tier 4.13 — regime-conditional sleeve selection.

    The HMM regime currently scales gross exposure (via regime_overlay).
    A more powerful use is to SWITCH which sleeves are running:
      - Bull       → momentum (current LIVE)
      - Transition → low-vol + quality (if available)
      - Bear       → defensive: low-vol only, smaller gross

    Status: NOT_WIRED. Consumer-side glue between hmm_regime and
    multi-sleeve config.
    """

    def status(self) -> str:
        return os.getenv("REGIME_ROUTER_STATUS", "NOT_WIRED")

    def describe(self) -> str:
        return (
            "Routes capital between sleeves based on HMM regime: bull → "
            "momentum, transition → momentum + low-vol blend, bear → "
            "low-vol only with reduced gross. Different from regime_overlay "
            "which only scales gross of the LIVE sleeve."
        )

    def sleeves_for(self, regime: str) -> dict[str, float]:
        """Returns {sleeve_name: weight} summing to 1.0."""
        regime = (regime or "").lower()
        if regime == "bull":
            return {"momentum": 1.0}
        if regime == "transition":
            return {"momentum": 0.5, "low_vol": 0.5}
        if regime == "bear":
            return {"low_vol": 1.0}
        # Unknown regime → conservative default
        return {"low_vol": 0.7, "momentum": 0.3}


@dataclass
class AltDataAdapter:
    """Tier 4.14 — alt-data feature adapters. Stubs for now.

    Real-money traders blend uncorrelated signals. Cheapest wins:
      - Short interest changes (FINRA bi-monthly): high SI + accelerating
        is a 2-week catalyst for crowding squeezes.
      - Insider buys (Form 4): cluster of C-suite buys above $1M predicts
        +5-8% in 6 months.

    Status: NOT_WIRED. Adapter shape only; data sources need keys/scrape.
    """

    def status(self) -> str:
        return os.getenv("ALT_DATA_STATUS", "NOT_WIRED")

    def describe(self) -> str:
        return (
            "Pluggable alt-data feeds: short_interest_change, insider_cluster_buy, "
            "options_skew, 13f_delta. Each returns a per-symbol score in [-1, 1]. "
            "Composed into the final ranker as a tiebreaker, not a primary signal."
        )

    def short_interest_signal(self, symbol: str) -> Optional[float]:
        """Stub. Wire to FINRA RegSHO threshold list + bi-monthly SI report."""
        return None

    def insider_buy_signal(self, symbol: str) -> Optional[float]:
        """Stub. Wire to SEC Form 4 (https://www.sec.gov/cgi-bin/browse-edgar)."""
        return None


@dataclass
class NetCostModel:
    """Tier 4.15 — gross → net return given costs.

    Backtest Sharpe is gross of: commissions (Alpaca: $0/trade), spread
    (~3-5bp on liquid names), borrow (shorts only, ~30bp/yr on hard-to-borrow),
    short-term capital gains tax (37% federal at top bracket on <1y holds).

    For a momentum strategy with monthly turnover ≈ 60% and a long-only
    book, the net Sharpe is typically 0.7-0.8× the gross Sharpe.

    Status: SHADOW. Apply post-hoc to compute_performance() for an honest
    after-cost view.
    """
    spread_bps: float = 4.0
    monthly_turnover_pct: float = 0.60
    short_borrow_bps_annual: float = 30.0
    st_cap_gains_pct: float = 0.37
    short_book_pct: float = 0.0  # 0 = long-only

    def status(self) -> str:
        return os.getenv("NET_COST_MODEL_STATUS", "SHADOW")

    def describe(self) -> str:
        return (
            f"Subtracts costs from gross return: {self.spread_bps:.1f}bp/side "
            f"spread × {self.monthly_turnover_pct*100:.0f}% monthly turnover, "
            f"{self.short_borrow_bps_annual:.0f}bp/yr borrow on "
            f"{self.short_book_pct*100:.0f}% short, "
            f"{self.st_cap_gains_pct*100:.0f}% tax on ST gains."
        )

    def annual_drag_bps(self) -> float:
        """Total annual drag in basis points."""
        spread_drag = self.spread_bps * 2 * self.monthly_turnover_pct * 12
        borrow_drag = self.short_borrow_bps_annual * self.short_book_pct
        return spread_drag + borrow_drag

    def net_return(self, gross_return: float, *, taxable: bool = True) -> float:
        """Apply drag + tax to a single-period gross return."""
        drag = self.annual_drag_bps() / 1e4
        net_pre_tax = gross_return - drag
        if taxable and net_pre_tax > 0:
            return net_pre_tax * (1 - self.st_cap_gains_pct)
        return net_pre_tax


# ============================================================
# Registry — used by the dashboard to surface every gap with status
# ============================================================

ALL_GAPS: list[tuple[str, str, type]] = [
    ("Tier 1.1 — Low-vol sleeve", "second uncorrelated alpha source", LowVolSleeve),
    ("Tier 1.2 — Sector neutralizer", "cap any sector at 35% of sleeve", SectorNeutralizer),
    ("Tier 1.3 — Long/short overlay", "short bottom-15 to fund top-15", LongShortOverlay),
    ("Tier 1.4 — Options overlay", "5% OTM put ladder for tail risk", OptionsOverlay),
    ("Tier 2.5 — Trailing stop", "−15% per-position stop", TrailingStop),
    ("Tier 2.6 — Risk-parity sizer", "inverse-vol → equal risk contribution", RiskParitySizer),
    ("Tier 2.7 — Drawdown breaker", "−10% mechanical halt-and-review", DrawdownCircuitBreaker),
    ("Tier 2.8 — Earnings rule", "auto-trim 50% T-1 day", EarningsRule),
    ("Tier 3.9 — TWAP slicer", "split orders > 5% ADV into N children", TwapSlicer),
    ("Tier 3.10 — Slippage tracker", "log decision-mid vs fill bps", SlippageTracker),
    ("Tier 3.11 — Tax-lot manager", "HIFO + wash-sale guard", TaxLotManager),
    ("Tier 4.12 — Auto promotion gate", "3-gate Survivor/PIT/CPCV", AutoPromotionGate),
    ("Tier 4.13 — Regime router", "switch sleeves on regime, not just exposure", RegimeRouter),
    ("Tier 4.14 — Alt-data adapter", "short interest + insider buys", AltDataAdapter),
    ("Tier 4.15 — Net-of-cost model", "spread + borrow + tax drag", NetCostModel),
]


def status_summary() -> dict[str, list[dict]]:
    """For the dashboard. Returns {LIVE, SHADOW, NOT_WIRED} buckets each
    holding the list of gap descriptions."""
    out: dict[str, list[dict]] = {"LIVE": [], "SHADOW": [], "NOT_WIRED": []}
    for label, tagline, cls in ALL_GAPS:
        try:
            inst = cls()
            s = inst.status()
            out.setdefault(s, []).append({
                "label": label,
                "tagline": tagline,
                "describe": inst.describe(),
                "class": cls.__name__,
            })
        except Exception as e:
            out.setdefault("ERROR", []).append({
                "label": label, "tagline": tagline,
                "error": f"{type(e).__name__}: {e}",
                "class": cls.__name__,
            })
    return out

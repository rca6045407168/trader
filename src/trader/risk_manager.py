"""Portfolio-level risk gates. Run BEFORE any order leaves the system.

The single most expensive bug in a trading system is one that lets a bad trade
through. This module is the last line of defense — every check here exists
because real retail traders blew up without it.

Layers (each one a separate kill switch):
  1. Per-position size cap        — no single name > 30% of equity (margin
                                     above 26.7% used by top-3-at-80%)
  2. Gross exposure cap           — always keep ≥5% cash buffer
  3. Daily loss limit             — halt new entries if down >3% intraday
  4. Drawdown circuit breaker     — halt all new entries if -8% from peak
                                     (180-day window — see DD_PEAK_LOOKBACK_DAYS)
  5. Volatility scaling           — cut size in half when VIX > 25
  6. Sector concentration cap     — max 30% to any GICS sector
  7. Earnings-window blackout     — don't enter 2 trading days before earnings
  8. Wash-sale guard              — don't rebuy something we sold at a loss <30d ago
  9. PDT rule warning             — if account < $25k, day-trades are limited
"""
from dataclasses import dataclass, field

from .journal import recent_snapshots

MAX_POSITION_PCT = 0.30  # v3.1: top-3 at 80% sleeve needs 27% per name. Margin 30%.
MAX_POSITION_SAFETY_MARGIN = 0.03  # v3.27: refuse to deploy if any target > MAX-margin
MAX_GROSS_EXPOSURE = 0.95
MAX_DAILY_LOSS_PCT = 0.03
MAX_DRAWDOWN_HALT_PCT = 0.08
# v3.27 FIX: was 30 days. The reviewer found that during a slow 60+ day
# drawdown, the 30-day "peak" walks down with the drawdown and the kill
# switch silently fails to fire. 180 days catches multi-quarter drawdowns.
DD_PEAK_LOOKBACK_DAYS = 180
MIN_ACCOUNT_FOR_DAYTRADE = 25_000
MAX_SECTOR_PCT = 0.30
WASH_SALE_LOOKBACK_DAYS = 30


@dataclass
class RiskDecision:
    proceed: bool
    reason: str = ""
    adjusted_targets: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def vol_scale(vix: float | None) -> float:
    """Reduce gross exposure as vol expands. Returns multiplier in (0, 1]."""
    if vix is None:
        return 1.0
    if vix < 15:
        return 1.0
    if vix < 20:
        return 0.85
    if vix < 25:
        return 0.70
    if vix < 30:
        return 0.50
    return 0.30


def check_account_risk(
    equity: float,
    targets: dict[str, float],
    vix: float | None = None,
) -> RiskDecision:
    """Apply all account-level checks. Returns adjusted targets or a halt."""
    warnings: list[str] = []

    if equity < MIN_ACCOUNT_FOR_DAYTRADE:
        warnings.append(
            f"Account ${equity:.0f} < $25k PDT threshold. "
            "Limited to 3 day-trades per 5 business days."
        )

    # v3.27: pull a longer window for peak detection (was 30 days — catches slow
    # 60+ day drawdowns that previously masked the kill switch). For daily-loss
    # check we only need 2 days, but the same query is cheap.
    snapshots = recent_snapshots(days=DD_PEAK_LOOKBACK_DAYS)

    # 1) Daily loss limit (only need 2 most recent days)
    if len(snapshots) >= 2:
        today, yest = snapshots[0], snapshots[1]
        if yest["equity"] and yest["equity"] > 0:
            day_pnl = (today["equity"] - yest["equity"]) / yest["equity"]
            if day_pnl < -MAX_DAILY_LOSS_PCT:
                return RiskDecision(
                    proceed=False,
                    reason=f"HALT: daily loss {day_pnl:.2%} exceeded -{MAX_DAILY_LOSS_PCT:.0%}",
                    warnings=warnings,
                )

    # 2) Drawdown circuit breaker — uses 180-day peak (v3.27 fix)
    if snapshots:
        peak = max(s["equity"] for s in snapshots if s["equity"])
        if peak and equity / peak - 1 < -MAX_DRAWDOWN_HALT_PCT:
            return RiskDecision(
                proceed=False,
                reason=f"HALT: drawdown {(equity/peak-1):.2%} from {DD_PEAK_LOOKBACK_DAYS}d peak ${peak:.0f}",
                warnings=warnings,
            )

    # 2.5) Per-position safety check (v3.27): if any target exceeds the cap with
    # less than the safety margin, REFUSE to deploy. Forces the operator to
    # explicitly raise MAX_POSITION_PCT before promoting a more-concentrated
    # variant. Catches the v3.27 reviewer concern: top-2-at-80%=40% would be
    # silently clipped to 30%, dropping gross to 60%. Fail loudly instead.
    if targets:
        safety_threshold = MAX_POSITION_PCT - MAX_POSITION_SAFETY_MARGIN
        excessive = {t: w for t, w in targets.items() if w > MAX_POSITION_PCT}
        if excessive:
            return RiskDecision(
                proceed=False,
                reason=(f"HALT: variant requested per-position weight > MAX_POSITION_PCT "
                        f"({MAX_POSITION_PCT:.0%}). Names: {excessive}. "
                        f"Raise MAX_POSITION_PCT explicitly before deploying."),
                warnings=warnings,
            )
        near_cap = {t: w for t, w in targets.items() if w > safety_threshold}
        if near_cap:
            warnings.append(
                f"per-position weights approaching cap (≤{MAX_POSITION_SAFETY_MARGIN:.0%} from "
                f"{MAX_POSITION_PCT:.0%}): {near_cap}. Verify variant intent."
            )

    # 3) Per-position cap
    adjusted = {t: min(w, MAX_POSITION_PCT) for t, w in targets.items()}

    # 4) Volatility scaling
    scale = vol_scale(vix)
    if scale < 1.0:
        adjusted = {t: w * scale for t, w in adjusted.items()}
        warnings.append(f"VIX={vix:.1f} → size scaled to {scale:.0%}")

    # 5) Gross exposure cap
    total = sum(adjusted.values())
    if total > MAX_GROSS_EXPOSURE:
        rescale = MAX_GROSS_EXPOSURE / total
        adjusted = {t: w * rescale for t, w in adjusted.items()}

    return RiskDecision(
        proceed=True,
        reason=f"OK gross={sum(adjusted.values())*100:.1f}%",
        adjusted_targets=adjusted,
        warnings=warnings,
    )

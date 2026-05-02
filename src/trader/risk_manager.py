"""Portfolio-level risk gates. Run BEFORE any order leaves the system.

The single most expensive bug in a trading system is one that lets a bad trade
through. This module is the last line of defense — every check here exists
because real retail traders blew up without it.

v3.46 ladder (per swarm-debate synthesis — agents 1/2/3):

  Layer 1 — Per-position cap            16% (down from 30%)
  Layer 2 — Gross exposure cap          95%
  Layer 3 — Daily loss circuit breaker  -6% triggers 48h freeze (was -3% halt)
  Layer 4 — Drawdown from 180d peak     -8% halts new entries
  Layer 5 — Drawdown from DEPLOYMENT    -25% triggers 30-day no-new-position
                                         freeze (NEW v3.46 — institutional risk
                                         best practice)
  Layer 6 — Liquidation gate            -33% requires written post-mortem before
                                         resume (NEW v3.46 — at backtest worst-DD,
                                         strategy may be broken vs just losing)
  Layer 7 — Volatility scaling          cut size when VIX > 25
  Layer 8 — Sector concentration cap    max 35% to any GICS sector
  Layer 9 — Position-cap safety margin  refuse if any target > MAX - margin

The deployment-DD layers (5 & 6) reference deployment_anchor.py which records
the equity at first daily-run. Agent-2 (institutional risk) flagged that
"max-loss-since-deployment" is the single biggest gap in retail risk policies.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR
from .journal import recent_snapshots

# Position sizing
MAX_POSITION_PCT = 0.16  # v3.46: tightened from 0.30. Top-15 mom-weighted max ~14%; 16% gives 2pp safety margin
MAX_POSITION_SAFETY_MARGIN = 0.02  # v3.46: tightened from 0.03

# Gross / daily / peak limits
MAX_GROSS_EXPOSURE = 0.95
MAX_DAILY_LOSS_PCT = 0.06  # v3.46: institutional-style -6% (was -3%). Below this triggers 48h freeze.
MAX_DRAWDOWN_HALT_PCT = 0.08  # from 180-day peak (existing)
DD_PEAK_LOOKBACK_DAYS = 180

# v3.46 NEW: deployment-DD gates (referenced from deployment_anchor module)
MAX_DEPLOY_DD_FREEZE_PCT = 0.25       # -25% from deployment → 30-day no-new-position freeze
MAX_DEPLOY_DD_LIQUIDATION_PCT = 0.33  # -33% from deployment → written post-mortem required
DEPLOY_DD_FREEZE_DAYS = 30

# Daily-loss freeze (v3.46): once tripped, no new entries for 48 hours
DAILY_LOSS_FREEZE_HOURS = 48
FREEZE_STATE_PATH = DATA_DIR / "risk_freeze_state.json"

# PDT
MIN_ACCOUNT_FOR_DAYTRADE = 25_000

# Sector
MAX_SECTOR_PCT = 0.35  # v3.46: 35% per agent-2 institutional best practice (was 30%)
WASH_SALE_LOOKBACK_DAYS = 30


@dataclass
class RiskDecision:
    proceed: bool
    reason: str = ""
    adjusted_targets: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def vol_scale(vix: float | None) -> float:
    """Reduce gross exposure as vol expands. Returns multiplier in (0, 1].

    Note: Agent-2 (institutional) recommended VIX-based de-risking, but v3.5
    proved VIX cuts at panic lows. Keeping the GENTLE vol-scaling here (which
    only mildly reduces, doesn't go to zero) but NOT adding the aggressive
    "VIX>40 cut to 50%" rule that Agent 2 suggested — that's already in v3.5
    and known to fail.
    """
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


def _load_freeze_state() -> dict:
    if not FREEZE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(FREEZE_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_freeze_state(state: dict) -> None:
    FREEZE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_STATE_PATH.write_text(json.dumps(state, indent=2))


def _check_freeze_active() -> tuple[bool, str]:
    """Check if a previous trigger has placed the system in a freeze window.
    Returns (is_frozen, reason)."""
    state = _load_freeze_state()
    now = datetime.utcnow()
    if "daily_loss_freeze_until" in state:
        try:
            until = datetime.fromisoformat(state["daily_loss_freeze_until"])
            if now < until:
                return True, f"DAILY-LOSS FREEZE active until {until.isoformat()}"
            else:
                # Expired — clean up
                del state["daily_loss_freeze_until"]
                _save_freeze_state(state)
        except ValueError:
            pass
    if "deploy_dd_freeze_until" in state:
        try:
            until = datetime.fromisoformat(state["deploy_dd_freeze_until"])
            if now < until:
                return True, f"DEPLOYMENT-DD FREEZE active until {until.isoformat()}"
            else:
                del state["deploy_dd_freeze_until"]
                _save_freeze_state(state)
        except ValueError:
            pass
    if state.get("liquidation_gate_tripped", False):
        return True, ("LIQUIDATION GATE TRIPPED — strategy halted pending "
                      "written post-mortem. To resume: write post-mortem at "
                      "docs/POST_MORTEM_<date>.md, then explicitly clear the "
                      "gate via reset_anchor() with a 50+ char reason.")
    return False, ""


def _trigger_daily_loss_freeze() -> None:
    state = _load_freeze_state()
    until = datetime.utcnow() + timedelta(hours=DAILY_LOSS_FREEZE_HOURS)
    state["daily_loss_freeze_until"] = until.isoformat()
    _save_freeze_state(state)


def _trigger_deploy_dd_freeze() -> None:
    state = _load_freeze_state()
    until = datetime.utcnow() + timedelta(days=DEPLOY_DD_FREEZE_DAYS)
    state["deploy_dd_freeze_until"] = until.isoformat()
    _save_freeze_state(state)


def _trigger_liquidation_gate() -> None:
    state = _load_freeze_state()
    state["liquidation_gate_tripped"] = True
    state["liquidation_tripped_at"] = datetime.utcnow().isoformat()
    _save_freeze_state(state)


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

    # 0) Check if a previous trigger has placed the system in a freeze window
    is_frozen, freeze_reason = _check_freeze_active()
    if is_frozen:
        return RiskDecision(
            proceed=False,
            reason=f"FROZEN: {freeze_reason}",
            warnings=warnings,
        )

    # Pull snapshot history for daily/peak checks
    snapshots = recent_snapshots(days=DD_PEAK_LOOKBACK_DAYS)

    # 1) Daily loss limit (v3.46: trigger 48h freeze, not just today's halt)
    if len(snapshots) >= 2:
        today, yest = snapshots[0], snapshots[1]
        if yest["equity"] and yest["equity"] > 0:
            day_pnl = (today["equity"] - yest["equity"]) / yest["equity"]
            if day_pnl < -MAX_DAILY_LOSS_PCT:
                _trigger_daily_loss_freeze()
                return RiskDecision(
                    proceed=False,
                    reason=(f"HALT: daily loss {day_pnl:.2%} exceeded "
                            f"-{MAX_DAILY_LOSS_PCT:.0%}. "
                            f"48-hour freeze triggered."),
                    warnings=warnings,
                )

    # 2) Drawdown circuit breaker — uses 180-day peak
    if snapshots:
        peak = max(s["equity"] for s in snapshots if s["equity"])
        if peak and equity / peak - 1 < -MAX_DRAWDOWN_HALT_PCT:
            return RiskDecision(
                proceed=False,
                reason=(f"HALT: drawdown {(equity/peak-1):.2%} from "
                        f"{DD_PEAK_LOOKBACK_DAYS}d peak ${peak:.0f}"),
                warnings=warnings,
            )

    # 3) Deployment-anchor drawdown gates (v3.46 NEW)
    try:
        from .deployment_anchor import drawdown_from_deployment
        deploy_dd, anchor = drawdown_from_deployment(equity)
        if deploy_dd < -MAX_DEPLOY_DD_LIQUIDATION_PCT:
            _trigger_liquidation_gate()
            return RiskDecision(
                proceed=False,
                reason=(f"LIQUIDATION GATE: drawdown {deploy_dd:.2%} from "
                        f"deployment anchor ${anchor.equity_at_deploy:.0f} "
                        f"(set {anchor.deploy_timestamp}). At backtest worst-DD: "
                        f"strategy may be broken vs just losing. "
                        f"Required: written post-mortem + manual gate reset."),
                warnings=warnings,
            )
        elif deploy_dd < -MAX_DEPLOY_DD_FREEZE_PCT:
            _trigger_deploy_dd_freeze()
            return RiskDecision(
                proceed=False,
                reason=(f"DEPLOYMENT-DD FREEZE: drawdown {deploy_dd:.2%} from "
                        f"deployment anchor ${anchor.equity_at_deploy:.0f}. "
                        f"30-day no-new-position freeze triggered. "
                        f"Hold existing positions; do not add."),
                warnings=warnings,
            )
        elif deploy_dd < -0.15:
            warnings.append(
                f"deployment DD {deploy_dd:.1%} approaching -25% freeze threshold. "
                f"Re-read docs/BEHAVIORAL_PRECOMMIT.md."
            )
    except Exception as e:
        warnings.append(f"deployment_anchor unavailable: {e}")

    # 4) Per-position safety check
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

    # 5) Per-position cap (apply clip after safety check passes)
    adjusted = {t: min(w, MAX_POSITION_PCT) for t, w in targets.items()}

    # 6) Volatility scaling (legacy VIX-based; kept alongside GARCH)
    scale = vol_scale(vix)
    if scale < 1.0:
        adjusted = {t: w * scale for t, w in adjusted.items()}
        warnings.append(f"VIX={vix:.1f} → size scaled to {scale:.0%}")

    # 6b) v3.49: Regime overlay (HMM + macro + GARCH).
    # Always computes (for observability); only APPLIES the cut if the env flag
    # REGIME_OVERLAY_ENABLED=true. Defensive multiplier in [0, 1.2].
    try:
        from .regime_overlay import compute_overlay
        overlay = compute_overlay()
        # Always log so we can compare paper-applied vs live for the decay watch
        warnings.append(f"regime_overlay: {overlay.rationale}")
        if overlay.enabled and overlay.final_mult < 1.0:
            adjusted = {t: w * overlay.final_mult for t, w in adjusted.items()}
        elif overlay.enabled and overlay.final_mult > 1.0:
            # Gentle boost — but never exceed MAX_POSITION_PCT per name
            adjusted = {t: min(w * overlay.final_mult, MAX_POSITION_PCT) for t, w in adjusted.items()}
    except Exception as e:
        warnings.append(f"regime_overlay unavailable (non-fatal): {e}")

    # 7) Gross exposure cap (final clamp; runs after all multipliers)
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


def clear_liquidation_gate(post_mortem_path: str, reason: str) -> None:
    """Manually clear the liquidation gate. Requires written post-mortem.
    See deployment_anchor.reset_anchor() — typical flow is:
      1. Write docs/POST_MORTEM_YYYY-MM-DD.md analyzing the -33% event
      2. Call deployment_anchor.reset_anchor(new_equity, reason, path)
      3. Call this function to clear the gate
    """
    if not post_mortem_path or len(reason) < 50:
        raise ValueError("clear_liquidation_gate requires post_mortem_path and reason ≥50 chars")
    state = _load_freeze_state()
    state.pop("liquidation_gate_tripped", None)
    state.pop("liquidation_tripped_at", None)
    state["liquidation_cleared_at"] = datetime.utcnow().isoformat()
    state["liquidation_cleared_reason"] = reason
    state["liquidation_cleared_post_mortem"] = post_mortem_path
    _save_freeze_state(state)

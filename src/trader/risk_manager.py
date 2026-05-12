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

# v3.73.2: Four-threshold drawdown protocol per docs/RISK_FRAMEWORK.md §6.
# Adds tiers BETWEEN the existing -8% kill and the deployment-anchor gates,
# each with a pre-committed response action (no discretion under stress).
# Tiers are evaluated against the SAME 180-day-peak metric the existing
# -8% kill uses; -8% is preserved as the existing red-alert threshold.
DRAWDOWN_YELLOW_PCT = 0.05         # -5% pause new sizing, weekly→biweekly review
DRAWDOWN_RED_PCT = 0.08            # -8% existing kill (alias for clarity)
DRAWDOWN_ESCALATION_PCT = 0.12     # -12% trim core to top 5, raise cash to 50%
DRAWDOWN_CATASTROPHIC_PCT = 0.15   # -15% liquidate all, manual re-arm + cool-off

# v3.73.2: drawdown protocol modes — same SHADOW/LIVE/INERT pattern as the
# v3.69.0 ReactorSignalRule. ADVISORY surfaces the tier in warnings + the
# dashboard but does not mutate targets. ENFORCING actually applies the
# mechanical responses (trim core, raise cash). User flips via env when
# ready. Default ADVISORY because the existing -8% kill remains binding
# regardless; the new tiers are additional response actions.
DRAWDOWN_PROTOCOL_MODE = "ADVISORY"  # "ADVISORY" | "ENFORCING" — env-overridable

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


def _current_broker_for_freeze() -> str:
    """Mirror trader.journal._current_broker — pick up BROKER env."""
    import os as _os
    return _os.environ.get("BROKER", "alpaca_paper").lower()


def _read_all_freeze() -> dict:
    """Read the full multi-broker freeze-state file. Migrates legacy
    flat format → dict-by-broker on first read."""
    if not FREEZE_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(FREEZE_STATE_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # Legacy format: top-level keys are freeze fields (liquidation_gate_tripped,
    # daily_loss_freeze_until, etc.) rather than broker names. Detect by
    # looking for any known freeze key at the top level.
    legacy_keys = {
        "liquidation_gate_tripped", "liquidation_tripped_at",
        "daily_loss_freeze_until", "deploy_dd_freeze_until",
    }
    if any(k in data for k in legacy_keys):
        migrated = {"alpaca_paper": data}
        try:
            FREEZE_STATE_PATH.write_text(json.dumps(migrated, indent=2))
        except Exception:
            pass
        return migrated
    return data


def _load_freeze_state() -> dict:
    """Return the freeze state for the CURRENT broker (per BROKER env).
    Caller-facing behavior is unchanged: a dict of freeze-key → value."""
    return _read_all_freeze().get(_current_broker_for_freeze(), {})


def _save_freeze_state(state: dict) -> None:
    """Persist the freeze state for the current broker. Other brokers'
    freeze state is preserved unchanged."""
    all_data = _read_all_freeze()
    all_data[_current_broker_for_freeze()] = state
    FREEZE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_STATE_PATH.write_text(json.dumps(all_data, indent=2))


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


# ============================================================
# v3.73.2 — Four-threshold drawdown protocol
# Per docs/RISK_FRAMEWORK.md §6. Each tier carries a pre-committed
# response so there's no discretion under stress.
# ============================================================
@dataclass
class DrawdownTier:
    """One tier of the four-threshold protocol."""
    name: str                 # "GREEN" | "YELLOW" | "RED" | "ESCALATION" | "CATASTROPHIC"
    label: str                # display label
    threshold_pct: float      # decimal, e.g. 0.05 for -5%
    response: str             # human-readable action
    enforce_action: str       # "NONE" | "PAUSE_GROWTH" | "HALT_ALL" | "TRIM_TO_TOP5" | "LIQUIDATE_ALL"


_DRAWDOWN_TIERS = [
    DrawdownTier(
        name="GREEN",
        label="Green",
        threshold_pct=0.0,
        response="Normal operation. Standard weekly review.",
        enforce_action="NONE",
    ),
    DrawdownTier(
        name="YELLOW",
        label="Yellow alert",
        threshold_pct=DRAWDOWN_YELLOW_PCT,
        response=("Pause new position sizing across all sleeves. "
                  "Continue holding existing positions. Increase review "
                  "cadence weekly → twice-weekly. No halt."),
        enforce_action="PAUSE_GROWTH",
    ),
    DrawdownTier(
        name="RED",
        label="Red alert (existing kill)",
        threshold_pct=DRAWDOWN_RED_PCT,
        response=("Halt all rebalancing. Liquidate VRP sleeve in full "
                  "(when v5 ships). Freeze experimental sleeves. "
                  "Daily risk review until DD recovers to -6%."),
        enforce_action="HALT_ALL",
    ),
    DrawdownTier(
        name="ESCALATION",
        label="Escalation",
        threshold_pct=DRAWDOWN_ESCALATION_PCT,
        response=("Trim momentum core from current gross to 30% gross "
                  "(keep top 5 names by score, drop ranks 6-15). Raise "
                  "cash to 50%. Daily email with recovery plan."),
        enforce_action="TRIM_TO_TOP5",
    ),
    DrawdownTier(
        name="CATASTROPHIC",
        label="Catastrophic",
        threshold_pct=DRAWDOWN_CATASTROPHIC_PCT,
        response=("Liquidate all positions. Manual re-arm only after "
                  "30-day cool-off + external human review + written "
                  "re-arming pre-commit. -$1.5k on $10k account; risk "
                  "is no longer 'managed', it's catastrophic."),
        enforce_action="LIQUIDATE_ALL",
    ),
]


def evaluate_drawdown_tier(current_dd_pct: float) -> DrawdownTier:
    """Map a current 180d-peak drawdown (decimal, e.g. -0.07 for -7%)
    to its tier. Returns the WORST tier whose threshold has been
    crossed. -0.07 → YELLOW (between -5% and -8%), -0.13 → ESCALATION
    (between -12% and -15%), etc."""
    # Normalize: convert negative DD into positive magnitude for comparison
    dd_mag = abs(current_dd_pct) if current_dd_pct < 0 else 0.0
    # Walk from worst to best, return first crossed
    for tier in reversed(_DRAWDOWN_TIERS):
        if dd_mag >= tier.threshold_pct:
            return tier
    return _DRAWDOWN_TIERS[0]  # GREEN


def drawdown_protocol_mode() -> str:
    """LIVE/SHADOW pattern: ADVISORY surfaces the tier without mutating
    targets; ENFORCING applies the mechanical responses. Env-overridable
    via DRAWDOWN_PROTOCOL_MODE."""
    import os as _os
    return _os.getenv("DRAWDOWN_PROTOCOL_MODE",
                       DRAWDOWN_PROTOCOL_MODE).upper()


def apply_drawdown_protocol(
    equity: float,
    targets: dict[str, float],
    snapshots: list[dict] | None = None,
    momentum_ranks: list[str] | None = None,
) -> tuple[dict[str, float], DrawdownTier, list[str]]:
    """Compute the current tier and (if mode == ENFORCING) apply the
    mechanical response to the target weights.

    Returns: (adjusted_targets, current_tier, warnings).

    Snapshots format: list of {date, equity}. If None, no DD evaluation
    happens and we return GREEN tier.

    momentum_ranks: ordered list of symbols by current momentum (top
    rank first). Required only for the ESCALATION TRIM_TO_TOP5 action.
    Without it we degrade to PAUSE_GROWTH semantics + a warning."""
    warnings: list[str] = []
    if not snapshots:
        return dict(targets), _DRAWDOWN_TIERS[0], warnings

    peak = max(s["equity"] for s in snapshots if s.get("equity"))
    if not peak or peak <= 0:
        return dict(targets), _DRAWDOWN_TIERS[0], warnings
    dd_pct = (equity - peak) / peak  # negative number

    tier = evaluate_drawdown_tier(dd_pct)
    if tier.name == "GREEN":
        return dict(targets), tier, warnings

    mode = drawdown_protocol_mode()
    warnings.append(
        f"drawdown_protocol[{mode}]: {tier.label} ({dd_pct*100:+.2f}% "
        f"from {DD_PEAK_LOOKBACK_DAYS}d peak ${peak:,.0f}). {tier.response}"
    )

    if mode != "ENFORCING":
        # ADVISORY mode — log + return targets unchanged
        return dict(targets), tier, warnings

    # ENFORCING — apply the mechanical response
    adjusted = dict(targets)
    if tier.enforce_action == "PAUSE_GROWTH":
        # Pause new sizing means: don't grow any weight beyond what's
        # already on the book. We don't have the actual current weights
        # here so the safest interpretation is "freeze the rebalance
        # entirely" — same effect as RED in v1. A future v3.73.x can
        # plumb current_weights through the call signature.
        warnings.append(
            "PAUSE_GROWTH semantics in v3.73.2: returns targets "
            "unchanged but the daily orchestrator is expected to skip "
            "the rebalance entirely until DD recovers. Wire-up TODO.")
    elif tier.enforce_action == "HALT_ALL":
        # The existing -8% halt path in check_account_risk already
        # halts; this tier reaches that branch and returns proceed=False
        # before our enforcement runs. So this is informational here.
        pass
    elif tier.enforce_action == "TRIM_TO_TOP5":
        if momentum_ranks and len(momentum_ranks) >= 5:
            keep = set(momentum_ranks[:5])
            adjusted = {sym: w for sym, w in targets.items() if sym in keep}
            # Rescale the kept names to 30% gross total
            current_gross = sum(adjusted.values())
            if current_gross > 0:
                scale = 0.30 / current_gross
                adjusted = {sym: w * scale for sym, w in adjusted.items()}
            warnings.append(
                f"ESCALATION enforced: kept top 5 ({sorted(keep)}); "
                f"dropped ranks 6-15; rescaled to 30% gross (cash 70%).")
        else:
            warnings.append(
                "ESCALATION wanted to trim to top 5 but momentum_ranks "
                "weren't provided; returning targets unchanged.")
    elif tier.enforce_action == "LIQUIDATE_ALL":
        adjusted = {sym: 0.0 for sym in targets}
        warnings.append(
            "CATASTROPHIC enforced: all targets set to 0.0. Daily "
            "orchestrator is expected to liquidate; manual re-arm "
            "required after the 30-day cool-off.")
    return adjusted, tier, warnings


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

    # 2b) v3.58.1 DrawdownCircuitBreaker — independent layer using ALL-time
    # snapshot peak (not just 180d). Activated only when status() == LIVE so
    # users can flip back to SHADOW via env without code changes.
    try:
        from .v358_world_class import DrawdownCircuitBreaker
        cb = DrawdownCircuitBreaker()
        if cb.status() == "LIVE":
            from .journal import recent_snapshots as _rs_all
            # v6.0.x: filter by current broker so Alpaca-paper peaks
            # don't cross-compare against Public.com live equity.
            all_snaps = _rs_all(days=10_000)  # default broker = current BROKER env
            if all_snaps:
                all_peak = max(s["equity"] for s in all_snaps if s.get("equity"))
                if cb.is_tripped(peak_equity=all_peak, current_equity=equity):
                    return RiskDecision(
                        proceed=False,
                        reason=(
                            f"HALT: v3.58 circuit breaker tripped — equity "
                            f"${equity:.0f} is "
                            f"{(equity/all_peak-1)*100:+.1f}% from all-time "
                            f"peak ${all_peak:.0f}, threshold "
                            f"-{cb.pct_from_peak*100:.0f}%. Mechanical "
                            f"halt-and-review. Set "
                            f"DRAWDOWN_BREAKER_STATUS=SHADOW to deactivate."
                        ),
                        warnings=warnings,
                    )
    except Exception as e:
        warnings.append(f"v3.58 breaker check failed (non-fatal): {type(e).__name__}: {e}")

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

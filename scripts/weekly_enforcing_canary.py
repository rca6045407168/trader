#!/usr/bin/env python3
"""v3.73.26 — weekly ENFORCING canary.

The user's calibrated verdict on v3.73.25:
  "the only thing that matters now is whether the system can go 30
   trading days without lying, drifting, crashing, or needing you."

Concrete failure mode this script defends against: the drawdown
protocol silently breaks during the 30-run window without anyone
noticing, because no real DD ever fires to exercise it. The clock
ticks to 30/30 with an inert brake — a "gate cleared but actually
broken" outcome.

This script runs in-process (no subprocess, no real journal mutation)
against a sandbox SQLite copy:
  1. Inject -13% synthetic DD snapshots into a TEMP DB
  2. Call apply_drawdown_protocol() with the synthetic snapshots and
     verify the response is what we expect under ENFORCING:
       - tier is one of EARLY/ESCALATION/CATASTROPHIC
       - targets MUTATED (sum < 80% gross)
  3. If the response is wrong, FAIL LOUD via Slack + email.
  4. If correct, write a one-line green status to docs/ENFORCING_CANARY_LOG.md
     so we have a paper trail of weekly verifications.

Schedule via cron or launchd to run every Sunday. The canary does
NOT touch the real journal and CANNOT break the 30-run streak.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.notify import notify  # noqa: E402
from trader.risk_manager import (  # noqa: E402
    apply_drawdown_protocol, drawdown_protocol_mode,
)

LOG = ROOT / "docs" / "ENFORCING_CANARY_LOG.md"


def _build_synthetic_snaps(dd_pct: float) -> list[dict]:
    """200 daily snapshots ramping linearly from $120k peak to (1+dd_pct) of peak."""
    peak = 120_000.0
    end_eq = peak * (1 + dd_pct)
    snaps = []
    for i in range(200):
        eq = peak + (end_eq - peak) * (i / 199)
        # 'date' format matches journal: YYYY-MM-DD, oldest first
        snaps.append({"date": f"2026-04-{(i % 28)+1:02d}", "equity": eq})
    # Reverse so newest is first, matching recent_snapshots() order
    return list(reversed(snaps))


# v3.73.27 — tier-by-tier expected behavior.
# Each entry: (dd_pct, expected_tier, expected_gross_min, expected_gross_max,
#              expected_action, description)
#   - GREEN  (DD <= -5%): action=NONE, gross unchanged at 80%
#   - YELLOW (-8% < DD <= -5%): action=PAUSE_GROWTH, gross unchanged
#     (warning only; the orchestrator skips rebalance)
#   - RED (-12% < DD <= -8%): action=HALT_ALL, gross unchanged here
#     (the actual halt fires upstream in check_account_risk)
#   - ESCALATION (-15% < DD <= -12%): action=TRIM_TO_TOP5, gross = 30%
#   - CATASTROPHIC (DD <= -15%): action=LIQUIDATE_ALL, gross = 0%
TIER_TESTS = [
    # (dd_pct, expected_tier, gross_min, gross_max, expected_action)
    (-0.03, "GREEN",        0.7999, 0.8001, "NONE",          "no DD"),
    (-0.06, "YELLOW",       0.7999, 0.8001, "PAUSE_GROWTH",  "early DD"),
    (-0.10, "RED",          0.7999, 0.8001, "HALT_ALL",      "kill threshold"),
    (-0.13, "ESCALATION",   0.2999, 0.3001, "TRIM_TO_TOP5",  "trim to top 5"),
    (-0.17, "CATASTROPHIC", 0.0,    0.0001, "LIQUIDATE_ALL", "liquidate all"),
]


def _build_targets() -> dict[str, float]:
    """Realistic targets at 80% gross (15 names ~5.3% each)."""
    return {
        "AAPL": 0.06, "MSFT": 0.06, "NVDA": 0.06, "JPM": 0.06,
        "GOOGL": 0.06, "META": 0.05, "AMZN": 0.05, "BRK-B": 0.05,
        "JNJ": 0.05, "V": 0.05, "MA": 0.05, "HD": 0.05,
        "WMT": 0.05, "XOM": 0.05, "CAT": 0.05,
    }


def run_one_tier(dd_pct: float, expected_tier: str,
                  expected_gross_min: float, expected_gross_max: float,
                  expected_action: str, description: str) -> dict:
    """Test one tier: given dd_pct, assert tier name, action, and final gross."""
    mode = drawdown_protocol_mode()
    targets = _build_targets()
    initial_gross = sum(targets.values())
    snaps = _build_synthetic_snaps(dd_pct)
    current_eq = snaps[0]["equity"]
    momentum_ranks = list(targets.keys())

    adjusted, tier, warnings = apply_drawdown_protocol(
        equity=current_eq, targets=targets, snapshots=snaps,
        momentum_ranks=momentum_ranks,
    )
    final_gross = sum(adjusted.values())

    tier_match = tier.name == expected_tier
    action_match = tier.enforce_action == expected_action

    if mode == "ENFORCING":
        gross_match = expected_gross_min <= final_gross <= expected_gross_max
    else:
        # ADVISORY: gross must be unchanged regardless of expected_gross
        gross_match = abs(final_gross - initial_gross) < 1e-6

    ok = tier_match and action_match and gross_match
    detail = (
        f"DD={dd_pct*100:+.0f}% [{description}] -> "
        f"tier={tier.name} (expect {expected_tier} {'✓' if tier_match else '✗'}) "
        f"action={tier.enforce_action} (expect {expected_action} {'✓' if action_match else '✗'}) "
        f"gross={final_gross:.2%} "
        f"(expect {expected_gross_min:.0%}..{expected_gross_max:.0%} "
        f"{'✓' if gross_match else '✗'})"
    )
    return {
        "ok": ok,
        "dd_pct": dd_pct,
        "tier": tier.name,
        "expected_tier": expected_tier,
        "action": tier.enforce_action,
        "expected_action": expected_action,
        "initial_gross": initial_gross,
        "final_gross": final_gross,
        "tier_match": tier_match,
        "action_match": action_match,
        "gross_match": gross_match,
        "detail": detail,
    }


def run_canary(dd_pct: float = -0.13) -> dict:
    """Backwards-compatible single-tier canary (used by tests).
    Returns the same dict shape as before for the v3.73.26 callers."""
    mode = drawdown_protocol_mode()
    targets = _build_targets()
    initial_gross = sum(targets.values())
    snaps = _build_synthetic_snaps(dd_pct)
    current_eq = snaps[0]["equity"]
    adjusted, tier, warnings = apply_drawdown_protocol(
        equity=current_eq, targets=targets, snapshots=snaps,
        momentum_ranks=list(targets.keys()),
    )
    final_gross = sum(adjusted.values())
    targets_mutated = (
        abs(final_gross - initial_gross) > 1e-6 or set(adjusted) != set(targets)
    )
    if mode == "ENFORCING":
        ok = tier.name != "GREEN" and targets_mutated
        detail = (
            f"PASS: tier={tier.name} fired and targets MUTATED "
            f"(gross {initial_gross:.2%} -> {final_gross:.2%})"
            if ok else
            f"FAIL: mode=ENFORCING but expected mutation did NOT happen. "
            f"tier={tier.name}, gross {initial_gross:.4f} -> {final_gross:.4f}, "
            f"targets_changed={targets_mutated}, warnings={warnings}"
        )
    else:
        ok = tier.name != "GREEN" and not targets_mutated
        detail = (
            f"PASS (ADVISORY): tier={tier.name} fired and targets unchanged"
            if ok else
            f"FAIL: mode=ADVISORY but tier/mutation state is wrong. "
            f"tier={tier.name}, gross {initial_gross:.4f} -> {final_gross:.4f}"
        )
    return {
        "ok": ok, "tier": tier.name, "targets_mutated": targets_mutated,
        "mode": mode, "initial_gross": initial_gross,
        "final_gross": final_gross, "detail": detail,
    }


def run_full_tier_sweep() -> dict:
    """v3.73.27 — sweep every tier, assert specific expected behavior.
    Catches partial regressions (e.g. ESCALATION works but CATASTROPHIC
    silently broke, or vice versa)."""
    results = []
    for dd_pct, exp_tier, gmin, gmax, exp_action, desc in TIER_TESTS:
        r = run_one_tier(dd_pct, exp_tier, gmin, gmax, exp_action, desc)
        results.append(r)
    all_ok = all(r["ok"] for r in results)
    return {
        "ok": all_ok,
        "mode": drawdown_protocol_mode(),
        "results": results,
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
    }


def main():
    print("=" * 60)
    print("Weekly ENFORCING canary — tier-by-tier sweep")
    print("=" * 60)

    sweep = run_full_tier_sweep()
    timestamp = datetime.now().isoformat(timespec="seconds")

    for r in sweep["results"]:
        flag = "✅" if r["ok"] else "❌"
        print(f"  {flag} {r['detail']}")

    print(f"\n  passed: {sweep['passed']}/{sweep['total']}")
    print(f"  mode:   {sweep['mode']}")

    line = (
        f"| {timestamp} | {sweep['mode']} | "
        f"{sweep['passed']}/{sweep['total']} | "
        f"{'✅' if sweep['ok'] else '❌'} | "
        f"{', '.join(r['tier'] for r in sweep['results'])} |"
    )

    if not LOG.exists():
        header = (
            "# ENFORCING Canary Log\n\n"
            "Weekly verification that the drawdown protocol still mutates "
            "targets correctly across ALL tiers (v3.73.27 tier-sweep). "
            "Asserts specific expected behavior for GREEN, YELLOW, RED, "
            "ESCALATION, CATASTROPHIC. Run via "
            "`scripts/weekly_enforcing_canary.py`.\n\n"
            "| Timestamp | Mode | Passed | OK | Tiers tested |\n"
            "|---|---|---:|---|---|\n"
        )
        LOG.write_text(header + line + "\n")
    else:
        LOG.write_text(LOG.read_text() + line + "\n")

    if not sweep["ok"]:
        failed = [r for r in sweep["results"] if not r["ok"]]
        failed_summary = "; ".join(
            f"DD={r['dd_pct']*100:+.0f}% expected {r['expected_tier']} "
            f"got {r['tier']} (gross {r['final_gross']:.2%})"
            for r in failed
        )
        try:
            notify(
                f"WEEKLY ENFORCING CANARY FAILED — drawdown protocol "
                f"regression detected in {len(failed)}/{sweep['total']} "
                f"tiers. Failures: {failed_summary}. "
                f"30-run clock should be considered SUSPENDED until "
                f"investigated.",
                level="warn",
                subject="[trader v3.73.27] ENFORCING canary FAILED",
            )
        except Exception as e:
            print(f"  notify failed: {e}")
        sys.exit(1)
    print("\nGreen. Logged to docs/ENFORCING_CANARY_LOG.md")


if __name__ == "__main__":
    main()

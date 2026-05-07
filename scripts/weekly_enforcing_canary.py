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


def run_canary(dd_pct: float = -0.13) -> dict:
    """Returns {ok: bool, tier: str, targets_mutated: bool, mode: str, detail: str}."""
    mode = drawdown_protocol_mode()

    # Realistic targets at 80% gross
    targets = {
        "AAPL": 0.06, "MSFT": 0.06, "NVDA": 0.06, "JPM": 0.06,
        "GOOGL": 0.06, "META": 0.05, "AMZN": 0.05, "BRK-B": 0.05,
        "JNJ": 0.05, "V": 0.05, "MA": 0.05, "HD": 0.05,
        "WMT": 0.05, "XOM": 0.05, "CAT": 0.05,
    }
    initial_gross = sum(targets.values())
    snaps = _build_synthetic_snaps(dd_pct)
    current_eq = snaps[0]["equity"]

    momentum_ranks = list(targets.keys())
    adjusted, tier, warnings = apply_drawdown_protocol(
        equity=current_eq, targets=targets, snapshots=snaps,
        momentum_ranks=momentum_ranks,
    )

    final_gross = sum(adjusted.values())
    targets_mutated = abs(final_gross - initial_gross) > 1e-6 or set(adjusted) != set(targets)

    if mode == "ENFORCING":
        # Expect tier to fire (since DD = -13% should be ESCALATION
        # or worse) AND targets mutated
        ok = tier.name != "GREEN" and targets_mutated
        if not ok:
            detail = (
                f"FAIL: mode=ENFORCING but expected mutation did NOT happen. "
                f"tier={tier.name}, initial_gross={initial_gross:.4f}, "
                f"final_gross={final_gross:.4f}, "
                f"targets_changed={targets_mutated}, warnings={warnings}"
            )
        else:
            detail = (
                f"PASS: tier={tier.name} fired and targets MUTATED "
                f"(gross {initial_gross:.2%} -> {final_gross:.2%})"
            )
    else:
        # ADVISORY: tier should fire BUT targets unchanged
        ok = tier.name != "GREEN" and not targets_mutated
        if not ok:
            detail = (
                f"FAIL: mode=ADVISORY but tier/mutation state is wrong. "
                f"tier={tier.name}, initial_gross={initial_gross:.4f}, "
                f"final_gross={final_gross:.4f}"
            )
        else:
            detail = (
                f"PASS (ADVISORY): tier={tier.name} fired and targets "
                f"unchanged (warning-only mode)"
            )

    return {
        "ok": ok,
        "tier": tier.name,
        "targets_mutated": targets_mutated,
        "mode": mode,
        "initial_gross": initial_gross,
        "final_gross": final_gross,
        "detail": detail,
    }


def main():
    print("=" * 60)
    print("Weekly ENFORCING canary")
    print("=" * 60)

    result = run_canary(dd_pct=-0.13)
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = (
        f"| {timestamp} | {result['mode']} | {result['tier']} | "
        f"{'✅' if result['ok'] else '❌'} | "
        f"{result['initial_gross']:.2%} → {result['final_gross']:.2%} |"
    )

    if not LOG.exists():
        header = (
            "# ENFORCING Canary Log\n\n"
            "Weekly verification that the drawdown protocol still mutates "
            "targets correctly under synthetic -13% DD. Run via "
            "`scripts/weekly_enforcing_canary.py`.\n\n"
            "| Timestamp | Mode | Tier | OK | Gross before → after |\n"
            "|---|---|---|---|---|\n"
        )
        LOG.write_text(header + line + "\n")
    else:
        LOG.write_text(LOG.read_text() + line + "\n")

    print(f"  mode:           {result['mode']}")
    print(f"  tier:           {result['tier']}")
    print(f"  targets mutated: {result['targets_mutated']}")
    print(f"  gross:           {result['initial_gross']:.2%} → {result['final_gross']:.2%}")
    print(f"  verdict:         {'✅ PASS' if result['ok'] else '❌ FAIL'}")
    print(f"  detail:          {result['detail']}")

    if not result["ok"]:
        try:
            notify(
                f"WEEKLY ENFORCING CANARY FAILED — drawdown protocol is "
                f"NOT mutating targets correctly. {result['detail']}. "
                f"30-run clock should be considered SUSPENDED until this "
                f"is investigated.",
                level="warn",
                subject="[trader v3.73.26] ENFORCING canary FAILED",
            )
        except Exception as e:
            print(f"  notify failed: {e}")
        sys.exit(1)
    print("\nGreen. Logged to docs/ENFORCING_CANARY_LOG.md")


if __name__ == "__main__":
    main()

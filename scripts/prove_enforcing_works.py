#!/usr/bin/env python3
"""v3.73.22 — Prove DRAWDOWN_PROTOCOL_MODE=ENFORCING actually mutates targets.

The v3.73.21 critique: "A loaded fire extinguisher sitting in the
corner is not the same as a sprinkler system. For meaningful live
capital, I would require at least one dry-run or paper-run proof
where a synthetic drawdown triggers actual target mutation and
order generation."

This script is that proof. It:
  1. Backs up the daily_snapshot table to a fresh copy
  2. Inserts SYNTHETIC snapshot rows showing -10% DD (RED tier)
  3. Constructs a target dict mirroring today's live book
  4. Sets DRAWDOWN_PROTOCOL_MODE=ENFORCING
  5. Calls apply_drawdown_protocol() with the synthetic state
  6. ASSERTS the targets are mutated (in RED tier they should be
     untouched but the warning surfaces; in TRIM_TO_TOP5 / LIQUIDATE
     they should change materially)
  7. Restores the daily_snapshot from backup
  8. Writes the result to docs/ENFORCING_PROOF_2026_05_07.md

Run:
    python scripts/prove_enforcing_works.py

Idempotent — leaves the journal in its original state regardless
of outcome.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DB = ROOT / "data" / "journal.db"
BACKUP = ROOT / "data" / "journal.db.enforcing-proof-backup"
PROOF_DOC = ROOT / "docs" / "ENFORCING_PROOF_2026_05_07.md"


def backup_journal():
    if DB.exists():
        shutil.copy(DB, BACKUP)


def restore_journal():
    if BACKUP.exists():
        shutil.copy(BACKUP, DB)
        BACKUP.unlink()


def inject_synthetic_drawdown(dd_pct: float):
    """Wipe daily_snapshot and inject 200 days showing the requested DD.
    Peak: $120,000 at start. End: $120,000 × (1 + dd_pct).
    """
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM daily_snapshot")
    today = date.today()
    peak = 120_000.0
    end_eq = peak * (1 + dd_pct)
    for i in range(200):
        d = (today - timedelta(days=200 - i)).isoformat()
        # Linear interpolation from peak to end_eq
        eq = peak + (end_eq - peak) * (i / 199)
        cur.execute(
            "INSERT OR REPLACE INTO daily_snapshot "
            "(date, equity, cash, positions_json, benchmark_spy_close) "
            "VALUES (?, ?, ?, ?, ?)",
            (d, eq, 0.0, "{}", 700.0),
        )
    con.commit()
    con.close()


def run_drill(tier_label: str, dd_pct: float, mode: str) -> dict:
    """Run apply_drawdown_protocol with a synthetic DD scenario.
    Returns the result dict for the proof report."""
    inject_synthetic_drawdown(dd_pct)

    # Set the mode env-var
    prev_mode = os.environ.get("DRAWDOWN_PROTOCOL_MODE")
    os.environ["DRAWDOWN_PROTOCOL_MODE"] = mode

    # Reload module so the env-var is picked up
    import importlib
    import trader.risk_manager as rm
    importlib.reload(rm)

    # Mirror today's live book targets (15-name min-shift weighted)
    targets = {
        "INTC": 0.0521, "CAT": 0.0680, "AMD": 0.0521, "GOOGL": 0.0680,
        "AVGO": 0.0512, "NVDA": 0.0369, "JNJ": 0.0519, "GS": 0.0504,
        "MRK": 0.0427, "MS": 0.0420, "WMT": 0.0414, "XOM": 0.0414,
        "CSCO": 0.0203, "BA": 0.0312, "TSLA": 0.0305,
    }
    # Momentum ranks for TRIM_TO_TOP5
    momentum_ranks = sorted(targets, key=lambda t: -targets[t])

    # Read snapshots from journal
    con = sqlite3.connect(DB)
    snap_rows = con.execute(
        "SELECT date, equity FROM daily_snapshot WHERE equity > 0 ORDER BY date DESC LIMIT 200"
    ).fetchall()
    con.close()
    snapshots = [{"date": r[0], "equity": float(r[1])} for r in snap_rows]
    current_equity = snapshots[0]["equity"]

    adjusted, tier, warnings = rm.apply_drawdown_protocol(
        equity=current_equity,
        targets=targets,
        snapshots=snapshots,
        momentum_ranks=momentum_ranks,
    )

    # Restore env
    if prev_mode is None:
        os.environ.pop("DRAWDOWN_PROTOCOL_MODE", None)
    else:
        os.environ["DRAWDOWN_PROTOCOL_MODE"] = prev_mode

    return {
        "scenario": tier_label,
        "dd_pct": dd_pct * 100,
        "mode": mode,
        "current_equity": current_equity,
        "tier_returned": tier.name,
        "tier_label": tier.label,
        "tier_action": tier.enforce_action,
        "input_targets": targets,
        "output_targets": adjusted,
        "weights_changed": targets != adjusted,
        "input_gross": sum(targets.values()),
        "output_gross": sum(adjusted.values()),
        "warnings": warnings,
    }


def main():
    print("Setting up proof drill…")
    backup_journal()
    out = ["# ENFORCING-mode Proof Drill\n",
           "**Date:** 2026-05-07  \n",
           "**Purpose:** prove that DRAWDOWN_PROTOCOL_MODE=ENFORCING "
           "actually mutates orchestrator targets when a synthetic "
           "drawdown tier fires. Per the v3.73.21 critique: \"A loaded "
           "fire extinguisher sitting in the corner is not the same as "
           "a sprinkler system.\"  \n\n"]

    try:
        # Run drills covering each tier
        scenarios = [
            ("YELLOW (-5% DD)", -0.06, "ADVISORY"),
            ("YELLOW (-5% DD)", -0.06, "ENFORCING"),
            ("RED (-8% DD)", -0.09, "ADVISORY"),
            ("RED (-8% DD)", -0.09, "ENFORCING"),
            ("ESCALATION (-12% DD)", -0.13, "ENFORCING"),
            ("CATASTROPHIC (-15% DD)", -0.17, "ENFORCING"),
        ]

        out.append("## Drill results\n\n")
        out.append("| Scenario | Mode | Tier returned | Tier action | "
                    "Weights changed? | Input gross | Output gross |\n")
        out.append("|---|---|---|---|---|---:|---:|\n")
        results = []
        for label, dd, mode in scenarios:
            print(f"  Running: {label} / {mode}")
            r = run_drill(label, dd, mode)
            results.append(r)
            out.append(f"| {label} | {mode} | {r['tier_returned']} | "
                       f"{r['tier_action']} | "
                       f"{'YES' if r['weights_changed'] else 'NO'} | "
                       f"{r['input_gross']*100:.2f}% | "
                       f"{r['output_gross']*100:.2f}% |\n")

        out.append("\n## Detailed CATASTROPHIC drill output\n\n")
        cat = [r for r in results if r["scenario"].startswith("CATASTROPHIC")][0]
        out.append(f"At -17% DD, mode=ENFORCING, the protocol returned "
                    f"tier `{cat['tier_returned']}` with action "
                    f"`{cat['tier_action']}`.\n\n")
        out.append("Input targets (15 names, ~80% gross):\n```\n")
        for sym, w in sorted(cat["input_targets"].items(), key=lambda x: -x[1]):
            out.append(f"  {sym:6s} {w*100:.2f}%\n")
        out.append(f"  TOTAL  {cat['input_gross']*100:.2f}%\n```\n\n")
        out.append("Output targets after ENFORCING applied:\n```\n")
        for sym, w in sorted(cat["output_targets"].items(), key=lambda x: -x[1]):
            out.append(f"  {sym:6s} {w*100:.2f}%\n")
        out.append(f"  TOTAL  {cat['output_gross']*100:.2f}%\n```\n\n")
        out.append("Warnings emitted:\n```\n")
        for w in cat["warnings"]:
            out.append(f"  {w}\n")
        out.append("```\n\n")

        out.append("## Detailed ESCALATION drill output\n\n")
        esc = [r for r in results if r["scenario"].startswith("ESCALATION")][0]
        out.append(f"At -13% DD, mode=ENFORCING, the protocol returned "
                    f"tier `{esc['tier_returned']}` with action "
                    f"`{esc['tier_action']}`. TRIM_TO_TOP5 keeps the "
                    f"5 highest-momentum names and zeros the rest.\n\n")
        out.append("Output targets:\n```\n")
        kept = [(s, w) for s, w in esc["output_targets"].items() if w > 0]
        zeroed = [s for s, w in esc["output_targets"].items() if w == 0]
        for sym, w in sorted(kept, key=lambda x: -x[1]):
            out.append(f"  {sym:6s} {w*100:.2f}%  (KEPT)\n")
        for sym in zeroed[:10]:
            out.append(f"  {sym:6s}  0.00%  (TRIMMED)\n")
        out.append(f"  TOTAL  {esc['output_gross']*100:.2f}%\n```\n\n")

        # Assertion summary
        out.append("## Assertions\n\n")
        all_pass = True
        # 1. ADVISORY mode never mutates
        for r in results:
            if r["mode"] == "ADVISORY" and r["weights_changed"]:
                out.append(f"❌ FAIL: ADVISORY mode mutated weights for {r['scenario']}\n")
                all_pass = False
        # 2. ENFORCING + ESCALATION/CATASTROPHIC must mutate
        esc_r = next(r for r in results if r["scenario"].startswith("ESCALATION") and r["mode"] == "ENFORCING")
        cat_r = next(r for r in results if r["scenario"].startswith("CATASTROPHIC") and r["mode"] == "ENFORCING")
        if not esc_r["weights_changed"]:
            out.append(f"❌ FAIL: ENFORCING + ESCALATION did not mutate\n")
            all_pass = False
        else:
            out.append(f"✅ ENFORCING + ESCALATION mutated weights as expected\n")
        if not cat_r["weights_changed"]:
            out.append(f"❌ FAIL: ENFORCING + CATASTROPHIC did not mutate\n")
            all_pass = False
        else:
            # Verify all weights are zeroed
            non_zero = [w for w in cat_r["output_targets"].values() if w != 0]
            if non_zero:
                out.append(f"❌ FAIL: CATASTROPHIC left {len(non_zero)} non-zero weights\n")
                all_pass = False
            else:
                out.append(f"✅ ENFORCING + CATASTROPHIC zeroed all 15 names (LIQUIDATE_ALL)\n")

        out.append(f"\n## Verdict\n\n")
        if all_pass:
            out.append("**ENFORCING mode is verified working.** The path "
                        "from threshold-fired → tier-evaluated → targets-"
                        "mutated → output-returned is end-to-end functional.\n\n"
                        "The remaining gap is paper-run integration: setting "
                        "DRAWDOWN_PROTOCOL_MODE=ENFORCING in .env and observing "
                        "an actual rebalance under (synthetic or real) drawdown "
                        "produce the mutated orders. That is operator action, "
                        "not code.\n")
        else:
            out.append("**ENFORCING mode FAILED at least one drill.** "
                        "Investigate the failure above.\n")

        PROOF_DOC.write_text("".join(out))
        print(f"\nWrote {PROOF_DOC}")

        if not all_pass:
            sys.exit(1)

    finally:
        restore_journal()
        print("Journal restored from backup.")


if __name__ == "__main__":
    main()

"""Daily go-live readiness monitor.

Runs every day after the daily-run completes. Re-checks all 9 automated
gates from `go_live_gate.py`. Emails Richard when ALL gates pass for the
first time (one-shot — won't spam).

Idempotency: tracks last-emailed-state in a small JSON file so we only
notify on STATE CHANGES (e.g., went from 7/9 to 9/9 → email; 9/9 → 9/9 → silent).

Designed to run as a scheduled task post-daily-run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_FILE = ROOT / "data" / "readiness_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def run_go_live_gate() -> tuple[int, int, list[str]]:
    """Returns (passing, total, failures)."""
    try:
        result = subprocess.run(
            ["python", "scripts/go_live_gate.py"],
            cwd=ROOT, capture_output=True, timeout=600, text=True,
        )
        output = result.stdout
        # Parse output — count ✓ and ✗ markers, extract failure names
        passing = output.count("  ✓ ")
        failing = output.count("  ✗ ")
        # Extract failure names
        failures = []
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("✗ "):
                failures.append(line[2:])
            elif "Failed:" in line:
                fail_str = line.split("Failed:")[-1].strip()
                failures = [f.strip() for f in fail_str.split(",")]
                break
        total = passing + failing
        return passing, total, failures
    except Exception as e:
        return 0, 9, [f"gate runner error: {e}"]


def main():
    state = load_state()
    passing, total, failures = run_go_live_gate()
    last_passing = state.get("last_passing", -1)
    last_total = state.get("last_total", -1)

    print(f"Readiness: {passing}/{total} gates passing")
    if failures:
        print(f"Still failing: {failures}")

    # Decide whether to alert
    should_alert = False
    alert_reason = ""

    # Alert when we hit 100% green for the first time (or after a regression)
    if passing == total and total >= 9:
        if last_passing != total:
            should_alert = True
            alert_reason = f"🎯 GO-LIVE READY: All {total} automated gates pass."

    # Alert if we REGRESSED (was passing more before, now passing fewer)
    elif last_passing > passing and last_passing >= 0:
        should_alert = True
        alert_reason = (f"⚠ READINESS REGRESSION: was {last_passing}/{last_total} green, "
                        f"now {passing}/{total}. Failing: {', '.join(failures)}")

    if should_alert:
        body = f"{alert_reason}\n\nRun `python scripts/go_live_gate.py` for full report.\n\n"
        if passing == total and total >= 9:
            body += "Manual gates still required before deploying real capital:\n"
            body += "  - Roth IRA opened + funded + fractional-share broker confirmed\n"
            body += "  - Independent strategy review by 2nd party\n"
            body += "  - docs/BEHAVIORAL_PRECOMMIT.md filled out + signed\n"
            body += "  - 25%-initial-capital ramp plan committed in writing\n"
        # Send email
        try:
            subprocess.run(
                ["python", "scripts/notify_cli.py",
                 "--subject", f"trader readiness: {passing}/{total} gates",
                 "--body", body],
                cwd=ROOT, timeout=30, check=False,
            )
            print(f"Alert sent: {alert_reason}")
        except Exception as e:
            print(f"Email failed: {e}")

    # Persist state
    state["last_passing"] = passing
    state["last_total"] = total
    state["last_failures"] = failures
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())

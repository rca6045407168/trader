"""Sleeve health monitor entry point.

Run:    python scripts/run_sleeve_health.py
Or in container:  docker run --rm -v $(pwd)/data:/app/data --entrypoint python \
                    trader-test scripts/run_sleeve_health.py

Reads journal, computes correlation/decay/demote-recommendation report,
writes to data/sleeve_health.json. Exit codes:
  0 = green (no alerts)
  1 = yellow (correlation alerts but no demote candidates)
  2 = red (demote candidate found — next daily-run will pre-emptively
          flag this for review; no auto-demote happens silently)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.sleeve_health import compute_health, write_health_report  # noqa: E402


def main() -> int:
    rep = compute_health()
    out = write_health_report(rep)
    print(f"[sleeve_health @ {rep.timestamp}] overall_health={rep.overall_health}")
    print(f"  rationale: {rep.rationale}")
    print(f"  written to: {out}")

    for s in rep.per_sleeve:
        sharpe_str = f"{s.rolling_sharpe:.2f}" if s.rolling_sharpe is not None else "n/a"
        flag = " ⚠️ FLAGGED FOR DEMOTE" if s.flagged_for_demote else ""
        print(f"  sleeve={s.sleeve_id:14s} n={s.n_observations:4d} "
              f"sharpe={sharpe_str}{flag}")
        if s.flag_reason:
            print(f"    reason: {s.flag_reason}")

    for c in rep.correlations:
        flag = " ⚠️ OVER THRESHOLD" if c.over_threshold else ""
        print(f"  corr {c.sleeve_a} ↔ {c.sleeve_b}: {c.correlation:+.3f} "
              f"(n={c.n_observations}){flag}")

    if rep.overall_health == "red":
        return 2
    if rep.overall_health == "yellow":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

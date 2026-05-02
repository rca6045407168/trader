"""Intraday risk watcher — entry point for .github/workflows/intraday-risk-watch.yml.

Runs every 30 min during market hours. Pulls broker equity, checks vs day-open
and vs deployment anchor, fires freeze states as needed.

Exit codes:
  0 — OK or warn (no action needed beyond logging)
  2 — freeze triggered (workflow should alert)
  1 — non-recoverable error (workflow should alert + investigate)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.intraday_risk import check  # noqa: E402


def main() -> int:
    result = check()
    print(f"[intraday-risk-watch @ {result.timestamp}] action={result.action}")
    print(f"  rationale: {result.rationale}")
    if result.equity_now:
        print(f"  equity_now=${result.equity_now:,.0f}")
    if result.day_open_equity:
        print(f"  day_open=${result.day_open_equity:,.0f}")
    if result.intraday_pnl_pct is not None:
        print(f"  intraday={result.intraday_pnl_pct:+.2%}")
    if result.deploy_dd_pct is not None:
        print(f"  deploy_dd={result.deploy_dd_pct:+.2%}")
    if result.error:
        print(f"  ERROR: {result.error}")
        return 1
    if result.action.startswith("freeze") or result.action == "freeze_liquidation":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

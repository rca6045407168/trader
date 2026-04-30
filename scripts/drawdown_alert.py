"""Drawdown alert — emails Richard when paper account hits -5%, -10%, -15%
from peak equity. Each tier alerts ONCE per drawdown cycle (won't spam).

Tier 1 (-5%):  Notice — no action recommended, just acknowledge
Tier 2 (-10%): Warning — re-read docs/BEHAVIORAL_PRECOMMIT.md
Tier 3 (-15%): Pre-halt — system is approaching the kill-switch threshold (-8% from 30d peak per risk_manager + behavioral panic threshold)

Resets when account makes a new peak (drawdown back to 0%).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STATE_FILE = ROOT / "data" / "drawdown_state.json"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"peak": 0, "tiers_alerted": []}
    return {"peak": 0, "tiers_alerted": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    from trader.execute import get_client
    c = get_client()
    acct = c.get_account()
    equity = float(acct.equity)
    state = load_state()

    # Update peak if we made a new high
    if equity > state.get("peak", 0):
        state["peak"] = equity
        state["tiers_alerted"] = []  # reset alerts on new peak
        save_state(state)
        print(f"New peak: ${equity:,.2f}")
        return 0

    peak = state["peak"]
    if peak <= 0:
        save_state({"peak": equity, "tiers_alerted": []})
        return 0

    dd_pct = (equity / peak - 1) * 100
    tiers = state.get("tiers_alerted", [])

    print(f"Equity: ${equity:,.2f}  Peak: ${peak:,.2f}  Drawdown: {dd_pct:+.2f}%")

    # Determine tier
    new_alert = None
    if dd_pct <= -15.0 and "t3" not in tiers:
        new_alert = (
            "t3",
            "🚨 DRAWDOWN -15% — pre-halt zone",
            (f"Account at ${equity:,.0f} (peak ${peak:,.0f}, drawdown {dd_pct:+.2f}%).\n\n"
             f"This is the pre-halt zone. Risk manager kill-switch fires at -8%\n"
             f"from 30-day peak (already triggered) and -3% daily loss.\n\n"
             f"BEHAVIORAL PROTOCOL:\n"
             f"  - DO NOT panic-sell\n"
             f"  - Re-read docs/BEHAVIORAL_PRECOMMIT.md\n"
             f"  - Do nothing for 5 trading days\n"
             f"  - If you want to halt: bash scripts/halt.sh\n"
             f"  - If you want to retire LIVE: see docs/PRE_MORTEM_TEMPLATE.md\n\n"
             f"Backtest worst-DD on PIT-honest universe was -33%. We're 15pp\n"
             f"into that range. Stay calm. The strategy is doing what it's designed to do.")
        )
    elif dd_pct <= -10.0 and "t2" not in tiers:
        new_alert = (
            "t2",
            "⚠ DRAWDOWN -10% — warning zone",
            (f"Account at ${equity:,.0f} (peak ${peak:,.0f}, drawdown {dd_pct:+.2f}%).\n\n"
             f"This is normal for a momentum strategy. Backtest worst-DD is -33%.\n"
             f"You're early in the drawdown range.\n\n"
             f"BEHAVIORAL PROTOCOL:\n"
             f"  - Re-read docs/BEHAVIORAL_PRECOMMIT.md before next cycle\n"
             f"  - Do not check Alpaca more than once per day\n"
             f"  - Do not read financial Twitter\n"
             f"  - Do not add capital ('averaging down') — follow your contribution schedule\n\n"
             f"This alert fires once at -10%. You won't get another until you make\n"
             f"a new peak. If you reach -15%, expect another alert with halt instructions.")
        )
    elif dd_pct <= -5.0 and "t1" not in tiers:
        new_alert = (
            "t1",
            "ℹ Drawdown -5% — notice",
            (f"Account at ${equity:,.0f} (peak ${peak:,.0f}, drawdown {dd_pct:+.2f}%).\n\n"
             f"This is well within normal range. Backtest worst-DD is -33%.\n"
             f"Current drawdown represents normal strategy volatility.\n\n"
             f"NO ACTION REQUIRED. Just acknowledging. You'll get the next alert\n"
             f"only if drawdown reaches -10% (warning) or -15% (pre-halt).")
        )

    if new_alert:
        tier_id, subject, body = new_alert
        try:
            subprocess.run(
                ["python", "scripts/notify_cli.py", "--subject", subject, "--body", body],
                cwd=ROOT, timeout=30, check=False,
            )
            tiers.append(tier_id)
            state["tiers_alerted"] = tiers
            save_state(state)
            print(f"Sent alert: {subject}")
        except Exception as e:
            print(f"Email failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

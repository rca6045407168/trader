"""CLI wrapper for trader.notify so scheduled tasks can email without writing Python.

Usage:
    python3 /Users/richardchen/trader/scripts/email.py \
        --subject "Day 5 perf: +0.3% vs SPY +0.1%" \
        --body "$(cat <<'EOF'
Equity: $100,623
Day P&L: +0.31%
SPY today: +0.12%
Top winner: GOOGL +1.4%
Top loser: AMD -0.6%
EOF
)"
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.notify import notify


def main():
    p = argparse.ArgumentParser(description="Send a trader notification email.")
    p.add_argument("--subject", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--level", default="info", choices=["info", "warn", "error"])
    args = p.parse_args()
    result = notify(args.body, subject=args.subject, level=args.level)
    if result["email"]:
        print(f"✅ email delivered to {result['to']}")
    else:
        print(f"❌ email NOT delivered — check SMTP_USER/SMTP_PASS in .env")


if __name__ == "__main__":
    main()

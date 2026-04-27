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


STUB_PHRASES = {"hello", "hi", "test", "ping", "stub", "placeholder", "tbd", "todo"}


def _looks_like_stub(subject: str, body: str) -> str | None:
    """Return reason string if this is a stub email; None if substantive."""
    s = (subject or "").strip().lower()
    b = (body or "").strip().lower()
    if not b or len(b) < 80:
        return f"body too short ({len(b)} chars; need >=80 of real content)"
    if b in STUB_PHRASES:
        return f"body is a stub phrase ('{b}')"
    if any(t in s for t in ("<task name", "<headline", "<one-line")):
        return "subject contains unfilled template placeholders"
    if any(t in b for t in ("<key finding", "<recommended action", "<task name")):
        return "body contains unfilled template placeholders"
    return None


def main():
    p = argparse.ArgumentParser(description="Send a trader notification email.")
    p.add_argument("--subject", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--level", default="info", choices=["info", "warn", "error"])
    p.add_argument("--allow-stub", action="store_true",
                   help="Bypass stub guard (for legitimate short test messages)")
    args = p.parse_args()

    if not args.allow_stub:
        reason = _looks_like_stub(args.subject, args.body)
        if reason:
            print(f"❌ REFUSED to send stub email: {reason}")
            print(f"   Subject: {args.subject!r}")
            print(f"   Body length: {len(args.body)} chars")
            print("   If intentional, add --allow-stub.")
            sys.exit(2)

    result = notify(args.body, subject=args.subject, level=args.level)
    if result["email"]:
        print(f"✅ email delivered to {result['to']}")
    else:
        print(f"❌ email NOT delivered — check SMTP_USER/SMTP_PASS in .env")


if __name__ == "__main__":
    main()

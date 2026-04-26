"""Manually arm or disarm the kill switch.

Usage:
  python scripts/halt.py on "reason"   # halts new orders
  python scripts/halt.py off            # resumes
  python scripts/halt.py status         # check current state
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.kill_switch import arm_kill_switch, disarm_kill_switch, KILL_FLAG_PATH


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "on":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
        arm_kill_switch(reason)
        print(f"Kill switch ARMED. Reason: {reason}")
        print(f"Flag file: {KILL_FLAG_PATH}")
    elif cmd == "off":
        disarm_kill_switch()
        print("Kill switch DISARMED. Trading will resume next run.")
    elif cmd == "status":
        if KILL_FLAG_PATH.exists():
            print(f"ARMED: {KILL_FLAG_PATH.read_text().strip()}")
        else:
            print("DISARMED.")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

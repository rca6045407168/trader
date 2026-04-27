"""Reconciliation script. Run morning-of, before placing new orders."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.execute import get_client
from trader.reconcile import reconcile


def main():
    print("=" * 60)
    print("DAILY RECONCILIATION")
    print("=" * 60)
    client = get_client()
    rep = reconcile(client)
    print(f"\n{rep['summary']}")
    if rep["missing"]:
        print("\nMISSING (in journal but not in Alpaca — may have stopped out):")
        for m in rep["missing"]:
            print(f"  {m['symbol']}: expected qty {m['expected_qty']}")
    if rep["unexpected"]:
        print("\nUNEXPECTED (in Alpaca but not in journal):")
        for u in rep["unexpected"]:
            print(f"  {u['symbol']}: actual qty {u['actual_qty']}")
    if rep["size_mismatch"]:
        print("\nSIZE MISMATCH (qty):")
        for s in rep["size_mismatch"]:
            print(f"  {s['symbol']}: expected {s['expected']}  actual {s['actual']}  diff {s['diff']:+}")
    print()
    if rep["halt_recommended"]:
        print("⚠️  HALT RECOMMENDED — manual review required before next trading run.")
        sys.exit(2)
    print("✓ Reconciliation passed.")


if __name__ == "__main__":
    main()

"""One-shot script: backfill position_lots from current Alpaca positions.

Use when migrating from a pre-v1.9 account state where momentum positions
existed in Alpaca but were never tracked in the journal's position_lots table.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.execute import backfill_momentum_lots_from_positions


def main():
    print("=== BACKFILL MOMENTUM LOTS FROM ALPACA POSITIONS ===")
    results = backfill_momentum_lots_from_positions()
    for r in results:
        if r.get("action") == "backfilled":
            print(f"  + {r['symbol']:6s}  qty={r['qty']:.4f}  avg=${r['avg_entry']:.2f}  lot_id={r['lot_id']}")
        else:
            print(f"  = {r['symbol']:6s}  {r['action']}")
    print(f"\n{sum(1 for r in results if r.get('action') == 'backfilled')} lots backfilled.")


if __name__ == "__main__":
    main()

"""One-shot backfill: insert current Alpaca positions into the journal as
open `position_lots`, so the next reconcile pass shows zero "unexpected"
drift.

Use case: GH Actions journal artifact got reset (or system was upgraded
without journal continuity), but Alpaca has real positions from prior runs.
Without backfill, reconcile flags all 5 positions as "unexpected" and HALTs.

After running this:
  - position_lots has 5 open lots (one per current Alpaca position)
  - reconcile sees them as expected → matched=5
  - tonight's daily-run proceeds with rebalance under v3.42 LIVE (top-15)

Idempotency: skips any symbol already present in open lots. Safe to re-run.

Sleeve tag: "momentum" (matches the LIVE variant's sleeve so close_lots_fifo
under v3.42 will cleanly rotate them out tonight).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.execute import get_client
from trader.journal import init_db, _conn, open_lot


def main():
    print("=" * 70)
    print("BACKFILL JOURNAL FROM ALPACA POSITIONS")
    print("=" * 70)
    init_db()

    client = get_client()
    positions = client.get_all_positions()
    print(f"\nAlpaca positions: {len(positions)}")
    for p in positions:
        print(f"  {p.symbol:6s} qty={float(p.qty):>10.4f}  "
              f"avg_entry=${float(p.avg_entry_price):>9.2f}  "
              f"market_value=${float(p.market_value):>11,.2f}")

    # Find existing open lots
    with _conn() as c:
        rows = c.execute(
            "SELECT symbol FROM position_lots WHERE closed_at IS NULL"
        ).fetchall()
    existing = {r["symbol"] for r in rows}
    print(f"\nExisting open lots in journal: {len(existing)}")
    if existing:
        print(f"  Symbols: {sorted(existing)}")

    # Insert any missing
    inserted = 0
    skipped = 0
    for p in positions:
        if p.symbol in existing:
            print(f"  [SKIP] {p.symbol} already has an open lot — not duplicating")
            skipped += 1
            continue
        lot_id = open_lot(
            symbol=p.symbol,
            sleeve="momentum",
            qty=float(p.qty),
            open_price=float(p.avg_entry_price),
            open_order_id=f"backfill-{p.symbol}",
        )
        print(f"  [INSERT] {p.symbol} lot_id={lot_id} qty={float(p.qty):.4f} "
              f"@ ${float(p.avg_entry_price):.2f}")
        inserted += 1

    print()
    print(f"Inserted: {inserted}, Skipped: {skipped}")
    print()

    # Verify reconcile would pass now
    from trader.reconcile import reconcile
    rep = reconcile(client)
    print(f"Reconcile check: matched={len(rep['matched'])} "
          f"missing={len(rep['missing'])} "
          f"unexpected={len(rep['unexpected'])} "
          f"size_mismatch={len(rep['size_mismatch'])}")
    print(f"Halt recommended: {rep['halt_recommended']}")

    if rep["halt_recommended"]:
        print("\n⚠ Reconcile still wants to HALT. Investigate before tonight's run.")
        return 1
    print("\n✓ Reconcile clean. Tonight's daily-run should proceed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Import Public.com positions CSV → reconcile to journal.

Public.com doesn't expose a programmatic API for retail accounts, so
position reconciliation goes via CSV export. The operator:
  1. Logs into Public.com → Account → Holdings → Export.
  2. Saves the CSV (typically named `Holdings_<date>.csv`).
  3. Runs this script with the CSV path.

This script:
  - Parses the CSV (Public.com's standard format)
  - Compares to the journal's open `position_lots`
  - Shows the drift per ticker
  - Optionally applies a resync (--apply) — closes journal lots and
    re-opens new ones matching the CSV, similar to
    resync_lots_from_broker.py but driven by CSV instead of Alpaca API

Usage:
  python scripts/import_public_positions.py ~/Downloads/Holdings.csv
  python scripts/import_public_positions.py ~/Downloads/Holdings.csv --apply
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


# Column header detection — Public.com CSV format varies across
# exports. We accept several common names per field.
SYMBOL_COLS = {"symbol", "ticker", "instrument", "stock"}
QTY_COLS = {"quantity", "qty", "shares", "share quantity"}
PRICE_COLS = {"price", "last price", "current price", "mark price",
               "market price"}
COST_COLS = {"cost basis", "average cost", "avg cost", "avg price",
              "average price", "cost per share"}


def parse_public_csv(csv_path: Path) -> list[dict]:
    """Returns list of {symbol, qty, price, cost_basis}. Tolerant to
    column-name variation."""
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        fieldnames = [n.lower().strip() for n in (reader.fieldnames or [])]
        sym_key = _find_col(fieldnames, SYMBOL_COLS)
        qty_key = _find_col(fieldnames, QTY_COLS)
        price_key = _find_col(fieldnames, PRICE_COLS)
        cost_key = _find_col(fieldnames, COST_COLS)
        if not sym_key or not qty_key:
            raise ValueError(
                f"CSV missing required columns. Got: {fieldnames}. "
                f"Need at least a symbol column ({SYMBOL_COLS}) and a "
                f"quantity column ({QTY_COLS})."
            )
        positions = []
        # Re-read since DictReader was advanced past header by fieldnames check
        f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            row_lower = {k.lower().strip(): v for k, v in row.items()}
            sym = (row_lower.get(sym_key, "") or "").strip().upper()
            if not sym:
                continue
            try:
                qty = float((row_lower.get(qty_key, "0") or "0").replace(",", ""))
            except ValueError:
                continue
            if qty <= 0:
                continue
            try:
                price = float((row_lower.get(price_key or "", "0") or "0").replace(",", "").replace("$", ""))
            except (ValueError, KeyError):
                price = 0.0
            try:
                cost = float((row_lower.get(cost_key or "", "0") or "0").replace(",", "").replace("$", ""))
            except (ValueError, KeyError):
                cost = 0.0
            positions.append({
                "symbol": sym,
                "qty": qty,
                "price": price,
                "cost_basis": cost or price,
            })
    return positions


def _find_col(fieldnames: list[str], options: set[str]) -> str | None:
    for f in fieldnames:
        if f.lower().strip() in options:
            return f.lower().strip()
    return None


def fetch_journal_open_lots(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT id, symbol, sleeve, opened_at, qty, open_price "
            "FROM position_lots WHERE closed_at IS NULL"
        ).fetchall()
    finally:
        con.close()
    return [
        {"id": r[0], "symbol": r[1], "sleeve": r[2],
         "opened_at": r[3], "qty": r[4], "open_price": r[5]}
        for r in rows
    ]


def compute_drift(public_csv: list[dict],
                    journal_lots: list[dict]) -> dict:
    """Returns dict with: matched, drift_per_symbol, public_only,
    journal_only."""
    pub = {p["symbol"]: p["qty"] for p in public_csv}
    jrn_by_sym: dict[str, float] = {}
    for lot in journal_lots:
        jrn_by_sym[lot["symbol"]] = (
            jrn_by_sym.get(lot["symbol"], 0) + lot["qty"]
        )

    all_syms = set(pub.keys()) | set(jrn_by_sym.keys())
    drift = {}
    matched = []
    public_only = []
    journal_only = []
    for s in sorted(all_syms):
        p_qty = pub.get(s, 0)
        j_qty = jrn_by_sym.get(s, 0)
        if abs(p_qty - j_qty) < 1e-4:
            matched.append(s)
        elif p_qty > 0 and j_qty == 0:
            public_only.append((s, p_qty))
        elif j_qty > 0 and p_qty == 0:
            journal_only.append((s, j_qty))
        else:
            drift[s] = {
                "public": p_qty, "journal": j_qty,
                "diff": p_qty - j_qty,
            }
    return {
        "matched": matched,
        "drift": drift,
        "public_only": public_only,
        "journal_only": journal_only,
    }


def render_drift(result: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("PUBLIC.COM ↔ JOURNAL RECONCILIATION")
    lines.append("=" * 70)
    lines.append(f"  Matched (within 0.0001 sh): {len(result['matched'])}")
    lines.append(f"  Quantity drift:             {len(result['drift'])}")
    lines.append(f"  Public-only (broker has, journal doesn't): "
                  f"{len(result['public_only'])}")
    lines.append(f"  Journal-only (journal has, broker doesn't): "
                  f"{len(result['journal_only'])}")
    lines.append("")
    if result["drift"]:
        lines.append("QUANTITY DRIFTS (public - journal):")
        for sym, d in result["drift"].items():
            lines.append(
                f"  {sym:<7}  public={d['public']:>10.4f}  "
                f"journal={d['journal']:>10.4f}  "
                f"diff={d['diff']:>+10.4f}"
            )
        lines.append("")
    if result["public_only"]:
        lines.append("BROKER HAS, JOURNAL DOESN'T (likely missed fills):")
        for sym, qty in result["public_only"]:
            lines.append(f"  {sym:<7}  qty={qty:>10.4f}")
        lines.append("")
    if result["journal_only"]:
        lines.append("JOURNAL HAS, BROKER DOESN'T (likely stop-outs):")
        for sym, qty in result["journal_only"]:
            lines.append(f"  {sym:<7}  qty={qty:>10.4f}")
        lines.append("")
    return "\n".join(lines)


def apply_resync(db_path: Path, public_csv: list[dict],
                  current_lots: list[dict]) -> int:
    """Close all open lots; insert one new open lot per Public position.
    Returns total rows written."""
    now = datetime.utcnow().isoformat()
    con = sqlite3.connect(str(db_path))
    n = 0
    try:
        # Close all current open lots with a marker
        for lot in current_lots:
            con.execute(
                "UPDATE position_lots SET closed_at = ?, close_price = ?, "
                "close_order_id = ?, realized_pnl = 0 WHERE id = ?",
                (now, lot["open_price"], "public-csv-resync", lot["id"]),
            )
            n += 1
        # Open new lots from CSV
        for p in public_csv:
            con.execute(
                "INSERT INTO position_lots "
                "(symbol, sleeve, opened_at, qty, open_price, open_order_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p["symbol"], "MOMENTUM", now,
                 p["qty"], p["cost_basis"] or p["price"], "public-csv-resync"),
            )
            n += 1
        con.commit()
    finally:
        con.close()
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", type=Path,
                     help="Public.com Holdings CSV export")
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    ap.add_argument("--apply", action="store_true",
                     help="Apply the resync (default: dry run only)")
    args = ap.parse_args(argv)

    try:
        positions = parse_public_csv(args.csv_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    print(f"Parsed {len(positions)} positions from {args.csv_path}.")

    journal_lots = fetch_journal_open_lots(args.db)
    print(f"Found {len(journal_lots)} open lots in journal.")
    print()

    result = compute_drift(positions, journal_lots)
    print(render_drift(result))

    if args.apply:
        n = apply_resync(args.db, positions, journal_lots)
        print(f"\n✅ Applied resync: {n} rows written.")
    else:
        if (result["drift"] or result["public_only"]
                or result["journal_only"]):
            print("\n(dry run — pass --apply to update the journal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

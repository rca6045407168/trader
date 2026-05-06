#!/usr/bin/env python3
"""v3.73.13 — Reactor signal spot-check against source 8-K text.

Hallucination defense: the reactor's LLM-generated summaries are
trusted across the system. This script validates them against the
archived source by:

  1. Pulling the N most recent earnings_signals with non-empty summaries
  2. For each, locating the archived 8-K text in data/filings/
  3. Extracting numerical claims ($amounts) from Claude's summary
  4. Verifying each claim appears in the source text
  5. Flagging any claim that does NOT appear in source

Run:
    python scripts/spotcheck_reactor.py        # check 5 most recent
    python scripts/spotcheck_reactor.py 20     # check 20 most recent
    python scripts/spotcheck_reactor.py INTC   # check all INTC signals

Output is a structured report. Exit code 0 = all claims verified or
soft-claims-only. Exit code 1 = at least one numerical claim does NOT
appear in source — possible hallucination, manual review needed.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "journal.db"
ARCHIVE = ROOT / "data" / "filings"


def extract_money_claims(text: str) -> list[str]:
    """Find $amount claims in summary text (e.g. '$6.5B', '$500 million')."""
    return re.findall(
        r"\$[\d,]+\.?\d*\s*(?:billion|million|thousand|B|M|K)?\b",
        text,
    )


def extract_dates(text: str) -> list[str]:
    return re.findall(
        r"\b(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d+,?\s*\d{4}\b",
        text,
    )


def claim_in_source(claim: str, source: str) -> bool:
    """Loose match: try exact + with-comma-stripped + with-decimal."""
    if claim in source:
        return True
    stripped = claim.replace(",", "").replace("$", "").strip()
    if stripped and stripped in source:
        return True
    # Try without unit suffix (e.g. "6.5" instead of "$6.5B")
    base = re.sub(r"[a-zA-Z\s]", "", stripped)
    if base and len(base) >= 2 and base in source:
        return True
    return False


def find_source_file(symbol: str, accession: str) -> Path | None:
    candidates = list(ARCHIVE.glob(f"{symbol}/*/{accession}.txt"))
    return candidates[0] if candidates else None


def check_signal(row: tuple) -> dict:
    sym, acc, filed, direction, mat, summary = row
    out = {
        "symbol": sym,
        "accession": acc,
        "filed_at": filed,
        "direction": direction,
        "materiality": mat,
        "summary_excerpt": (summary or "")[:200],
        "source_found": False,
        "money_claims": [],
        "money_verified": [],
        "money_unverified": [],
        "date_claims": [],
        "date_verified": [],
    }

    src_path = find_source_file(sym, acc)
    if src_path is None:
        out["error"] = f"no archived source for {acc}"
        return out
    out["source_found"] = True
    out["source_path"] = str(src_path.relative_to(ROOT))

    source = src_path.read_text(errors="ignore")
    money = extract_money_claims(summary)
    dates = extract_dates(summary)
    out["money_claims"] = money
    out["date_claims"] = dates

    for m in money:
        if claim_in_source(m, source):
            out["money_verified"].append(m)
        else:
            out["money_unverified"].append(m)

    for d in dates:
        if d in source:
            out["date_verified"].append(d)

    return out


def query_signals(n_or_symbol: str | int) -> list[tuple]:
    con = sqlite3.connect(DB)
    if isinstance(n_or_symbol, int):
        rows = con.execute(
            """SELECT symbol, accession, filed_at, direction, materiality, summary
               FROM earnings_signals
               WHERE summary IS NOT NULL AND summary != ''
               ORDER BY filed_at DESC LIMIT ?""",
            (n_or_symbol,),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT symbol, accession, filed_at, direction, materiality, summary
               FROM earnings_signals
               WHERE symbol = ? AND summary IS NOT NULL AND summary != ''
               ORDER BY filed_at DESC""",
            (n_or_symbol,),
        ).fetchall()
    con.close()
    return rows


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "5"
    if arg.isdigit():
        rows = query_signals(int(arg))
    else:
        rows = query_signals(arg)

    if not rows:
        print(f"No signals found for: {arg}")
        return 0

    total_money = 0
    total_unverified = 0
    flagged = []

    for row in rows:
        result = check_signal(row)
        print("=" * 72)
        print(f"{result['symbol']:6s} {result['filed_at']:24s} "
              f"{result['direction']} M{result['materiality']}")
        print(f"  acc: {result['accession']}")
        if not result["source_found"]:
            print(f"  ⚠️  {result.get('error', 'no source')}")
            continue
        print(f"  source: {result['source_path']}")
        print(f"  summary: {result['summary_excerpt']}...")

        if result["money_claims"]:
            print(f"  $-claims: {result['money_claims']}")
            print(f"    verified in source: {result['money_verified']}")
            if result["money_unverified"]:
                print(f"    ⚠️  UNVERIFIED: {result['money_unverified']}")
                flagged.append(result)
        else:
            print(f"  $-claims: (none — wrapper-only filing or non-financial)")

        if result["date_claims"]:
            n_dates = len(result["date_claims"])
            n_verified = len(result["date_verified"])
            print(f"  date-claims: {n_verified}/{n_dates} verified")

        total_money += len(result["money_claims"])
        total_unverified += len(result["money_unverified"])

    print()
    print("=" * 72)
    print(f"SUMMARY: {len(rows)} signals checked, {total_money} $-claims, "
          f"{total_unverified} unverified")
    if flagged:
        print(f"⚠️  {len(flagged)} signals have unverified $-claims:")
        for r in flagged:
            print(f"  - {r['symbol']} {r['filed_at']}: "
                  f"{r['money_unverified']}")
        print("\nManual review required for unverified claims.")
        return 1
    print("✅ All numerical claims verified against source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

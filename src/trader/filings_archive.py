"""Persistent archive of company filings + earnings call transcripts (v3.68.0).

The system that didn't exist before today: a queryable, on-disk archive
of every SEC filing (8-K, 10-Q, 10-K) and earnings call transcript we
encounter for our LIVE positions.

## Why

Without an archive:
- Every Claude call to analyze an earnings doc is a one-shot fetch +
  read + throw-away
- Cross-quarter analysis ("how did NVDA's guidance language change Q1
  vs Q4?") is impossible
- HANK can't ground answers in primary source material

With an archive:
- One-time fetch + permanent on-disk text
- SQLite index for fast lookup by symbol / form type / date
- Cheap full-text search across 1000s of docs
- HANK gains a `read_filings` tool that searches the archive

## Layout

    data/filings/
      index.db                      # SQLite: one row per filing
      {symbol}/
        {form_type}/
          {accession}.txt           # raw text (1-50KB typical 8-K)
          {accession}.json          # metadata (filed_at, url, items)

`accession` is SEC's accession number (e.g. "0001628280-26-001234"),
guaranteed unique across all filers.

## Idempotency

`store()` is keyed on accession number. Re-storing the same filing is
a no-op — safe to run from a cron without dedup logic upstream.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_ARCHIVE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "filings"


@dataclass
class Filing:
    """One archived document. `accession` is the canonical key."""
    symbol: str
    form_type: str          # "8-K" | "10-Q" | "10-K" | "TRANSCRIPT" | "OTHER"
    accession: str          # SEC accession number, or fabricated for non-SEC sources
    filed_at: str           # ISO date
    url: str
    source: str             # "sec_edgar" | "polygon" | "finnhub" | "alpha_vantage" | "manual"
    items: list[str] = field(default_factory=list)  # 8-K Item codes, e.g. ["2.02", "9.01"]
    text_path: Optional[str] = None     # absolute path to .txt file
    n_chars: int = 0
    archived_at: str = ""               # ISO timestamp when we stored it
    title: str = ""                     # human-readable (e.g. "Q3 2026 results")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "form_type": self.form_type,
            "accession": self.accession, "filed_at": self.filed_at,
            "url": self.url, "source": self.source, "items": self.items,
            "text_path": self.text_path, "n_chars": self.n_chars,
            "archived_at": self.archived_at, "title": self.title,
        }


def _index_db_path(root: Optional[Path] = None) -> Path:
    return (root or DEFAULT_ARCHIVE_ROOT) / "index.db"


def init_db(root: Optional[Path] = None) -> None:
    """Create the index table if missing. Idempotent."""
    root = root or DEFAULT_ARCHIVE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_index_db_path(root)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS filings (
                accession   TEXT PRIMARY KEY,
                symbol      TEXT NOT NULL,
                form_type   TEXT NOT NULL,
                filed_at    TEXT NOT NULL,
                url         TEXT,
                source      TEXT,
                items_json  TEXT,
                text_path   TEXT,
                n_chars     INTEGER,
                archived_at TEXT,
                title       TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_filings_symbol_filed "
                   "ON filings (symbol, filed_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_filings_form_filed "
                   "ON filings (form_type, filed_at DESC)")
        c.commit()


def store(symbol: str, form_type: str, accession: str,
           filed_at: str, url: str, text: str,
           items: Optional[list[str]] = None,
           source: str = "sec_edgar",
           title: str = "",
           root: Optional[Path] = None) -> Filing:
    """Store a filing's text + metadata. Returns the Filing.

    Idempotent: re-storing the same accession overwrites the text
    (useful if SEC re-publishes a corrected version) but keeps the
    same accession key + filed_at.
    """
    root = root or DEFAULT_ARCHIVE_ROOT
    init_db(root)

    # Normalize symbol case for path consistency
    sym_norm = symbol.upper().strip()
    form_norm = form_type.upper().strip().replace("/", "_")

    # Write the raw text to disk
    folder = root / sym_norm / form_norm
    folder.mkdir(parents=True, exist_ok=True)
    text_path = folder / f"{accession}.txt"
    text_path.write_text(text, encoding="utf-8")

    # Write metadata sidecar (useful for direct file inspection)
    meta_path = folder / f"{accession}.json"
    meta_path.write_text(json.dumps({
        "symbol": sym_norm, "form_type": form_norm, "accession": accession,
        "filed_at": filed_at, "url": url, "source": source,
        "items": items or [], "title": title,
        "n_chars": len(text),
        "archived_at": datetime.utcnow().isoformat(),
    }, indent=2))

    # Insert/replace the index row
    archived_at = datetime.utcnow().isoformat()
    items_json = json.dumps(items or [])
    n_chars = len(text)
    with sqlite3.connect(_index_db_path(root)) as c:
        c.execute("""
            INSERT OR REPLACE INTO filings
            (accession, symbol, form_type, filed_at, url, source,
             items_json, text_path, n_chars, archived_at, title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (accession, sym_norm, form_norm, filed_at, url, source,
               items_json, str(text_path), n_chars, archived_at, title))
        c.commit()

    return Filing(
        symbol=sym_norm, form_type=form_norm, accession=accession,
        filed_at=filed_at, url=url, source=source,
        items=items or [], text_path=str(text_path),
        n_chars=n_chars, archived_at=archived_at, title=title,
    )


def exists(accession: str, root: Optional[Path] = None) -> bool:
    """O(1) check whether we've already archived this filing."""
    init_db(root)
    with sqlite3.connect(_index_db_path(root)) as c:
        row = c.execute(
            "SELECT 1 FROM filings WHERE accession = ? LIMIT 1",
            (accession,)
        ).fetchone()
    return row is not None


def get(accession: str, root: Optional[Path] = None) -> Optional[Filing]:
    """Fetch one filing by accession. Returns None if not archived."""
    init_db(root)
    with sqlite3.connect(_index_db_path(root)) as c:
        row = c.execute(
            "SELECT symbol, form_type, accession, filed_at, url, source, "
            "items_json, text_path, n_chars, archived_at, title "
            "FROM filings WHERE accession = ?",
            (accession,)
        ).fetchone()
    if row is None:
        return None
    items = json.loads(row[6]) if row[6] else []
    return Filing(
        symbol=row[0], form_type=row[1], accession=row[2],
        filed_at=row[3], url=row[4] or "", source=row[5] or "",
        items=items, text_path=row[7], n_chars=row[8] or 0,
        archived_at=row[9] or "", title=row[10] or "",
    )


def read_text(accession: str, root: Optional[Path] = None) -> Optional[str]:
    """Read the raw text of an archived filing. None if not found."""
    f = get(accession, root)
    if f is None or not f.text_path:
        return None
    p = Path(f.text_path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def list_for_symbol(symbol: str,
                     since: Optional[str] = None,
                     form_types: Optional[list[str]] = None,
                     limit: int = 100,
                     root: Optional[Path] = None) -> list[Filing]:
    """All archived filings for one symbol, newest first."""
    init_db(root)
    sym_norm = symbol.upper().strip()
    sql = ("SELECT symbol, form_type, accession, filed_at, url, source, "
           "items_json, text_path, n_chars, archived_at, title "
           "FROM filings WHERE symbol = ?")
    params: list = [sym_norm]
    if since:
        sql += " AND filed_at >= ?"
        params.append(since)
    if form_types:
        sql += f" AND form_type IN ({','.join('?' * len(form_types))})"
        params.extend(f.upper() for f in form_types)
    sql += " ORDER BY filed_at DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(_index_db_path(root)) as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_to_filing(r) for r in rows]


def list_recent(since: str,
                 form_types: Optional[list[str]] = None,
                 limit: int = 200,
                 root: Optional[Path] = None) -> list[Filing]:
    """All filings filed on or after `since` (ISO date), newest first."""
    init_db(root)
    sql = ("SELECT symbol, form_type, accession, filed_at, url, source, "
           "items_json, text_path, n_chars, archived_at, title "
           "FROM filings WHERE filed_at >= ?")
    params: list = [since]
    if form_types:
        sql += f" AND form_type IN ({','.join('?' * len(form_types))})"
        params.extend(f.upper() for f in form_types)
    sql += " ORDER BY filed_at DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(_index_db_path(root)) as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_to_filing(r) for r in rows]


def search(query: str, symbol: Optional[str] = None,
            limit: int = 20,
            root: Optional[Path] = None) -> list[Filing]:
    """Naive substring search across stored texts. Returns the index
    rows whose .txt body contains the query (case-insensitive).

    Not FTS5 — for personal-scale archives (few hundred filings) a
    sequential scan is plenty fast (well under 1s). If the archive
    grows past ~10K docs, swap for FTS5."""
    candidates = (list_for_symbol(symbol, root=root, limit=500)
                  if symbol else list_recent("1970-01-01", root=root, limit=500))
    q_lower = query.lower()
    matches = []
    for f in candidates:
        text = read_text(f.accession, root)
        if text and q_lower in text.lower():
            matches.append(f)
            if len(matches) >= limit:
                break
    return matches


def _row_to_filing(row) -> Filing:
    items = json.loads(row[6]) if row[6] else []
    return Filing(
        symbol=row[0], form_type=row[1], accession=row[2],
        filed_at=row[3], url=row[4] or "", source=row[5] or "",
        items=items, text_path=row[7], n_chars=row[8] or 0,
        archived_at=row[9] or "", title=row[10] or "",
    )


def stats(root: Optional[Path] = None) -> dict:
    """Summary numbers for the dashboard archive view."""
    init_db(root)
    with sqlite3.connect(_index_db_path(root)) as c:
        n_total = c.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
        n_symbols = c.execute(
            "SELECT COUNT(DISTINCT symbol) FROM filings").fetchone()[0]
        by_form = dict(c.execute(
            "SELECT form_type, COUNT(*) FROM filings "
            "GROUP BY form_type ORDER BY COUNT(*) DESC").fetchall())
        latest = c.execute(
            "SELECT filed_at FROM filings "
            "ORDER BY filed_at DESC LIMIT 1").fetchone()
        total_chars = c.execute(
            "SELECT COALESCE(SUM(n_chars), 0) FROM filings").fetchone()[0]
    return {
        "n_total": n_total,
        "n_symbols": n_symbols,
        "by_form": by_form,
        "latest_filed_at": latest[0] if latest else None,
        "total_chars": total_chars,
    }

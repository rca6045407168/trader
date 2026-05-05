"""Earnings reactor — Claude-powered analysis of newly archived filings (v3.68.0).

Mirrors the Sand Grove Capital pattern from the FT article that
prompted this build (LLMQuant 2026-05-04): a corporate event happens
→ AI reads the long-form doc in seconds → human sees a structured
summary → trades within minutes.

We compress the "human reads 100-page doc" step. The decision layer
remains human (per the article's universal pattern: AI as analysis
layer, human as decision layer; 0% of cited funds let AI directly
trade).

## Flow

1. Take a list of symbols (default: current LIVE positions from broker)
2. For each symbol, fetch recent SEC 8-K filings via sec_filings.py
3. Skip ones already in the archive (idempotent via accession key)
4. Download + strip + archive new ones via filings_archive.store()
5. For "material" 8-Ks (Item 2.02 earnings, Item 7.01 guidance, etc.),
   run Claude with a structured-output schema
6. Persist signals to journal.earnings_signals
7. Log every Claude call to llm_audit (v3.64.0 audit log)

## Output schema (what Claude extracts)

- direction: BULLISH | NEUTRAL | BEARISH | SURPRISE
- materiality: 1-5 (1 = housekeeping; 5 = thesis-altering)
- guidance_change: RAISED | MAINTAINED | LOWERED | NONE
- surprise_direction: BEAT | INLINE | MISSED | NONE
- summary: 2-3 sentence plain English
- bullish_quotes: list of verbatim quotes that support BULLISH read
- bearish_quotes: list of verbatim quotes that support BEARISH read
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import filings_archive
from . import sec_filings

DEFAULT_JOURNAL_DB = (Path(__file__).resolve().parent.parent.parent
                       / "data" / "journal.db")
DEFAULT_MODEL = os.getenv("EARNINGS_REACTOR_MODEL", "claude-sonnet-4-6")


@dataclass
class ReactionResult:
    symbol: str
    accession: str
    filed_at: str
    items: list[str] = field(default_factory=list)
    direction: str = "NEUTRAL"
    materiality: int = 1
    guidance_change: str = "NONE"
    surprise_direction: str = "NONE"
    summary: str = ""
    bullish_quotes: list[str] = field(default_factory=list)
    bearish_quotes: list[str] = field(default_factory=list)
    raw_response: str = ""
    model: str = ""
    cost_usd: Optional[float] = None
    error: Optional[str] = None


def _ensure_signals_table(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS earnings_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                 TEXT NOT NULL,
                symbol             TEXT NOT NULL,
                accession          TEXT NOT NULL,
                filed_at           TEXT NOT NULL,
                items_json         TEXT,
                direction          TEXT,
                materiality        INTEGER,
                guidance_change    TEXT,
                surprise_direction TEXT,
                summary            TEXT,
                bullish_quotes_json TEXT,
                bearish_quotes_json TEXT,
                model              TEXT,
                cost_usd           REAL,
                raw_response       TEXT,
                error              TEXT,
                UNIQUE(symbol, accession)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_earnings_signals_symbol_filed "
                   "ON earnings_signals (symbol, filed_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_earnings_signals_filed "
                   "ON earnings_signals (filed_at DESC)")
        # v3.68.2 migration: add notified_at column for alert idempotency.
        # SQLite ADD COLUMN raises if the column exists — catch + ignore.
        try:
            c.execute("ALTER TABLE earnings_signals "
                      "ADD COLUMN notified_at TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        c.commit()


def _signal_exists(db_path: Path, symbol: str, accession: str) -> bool:
    _ensure_signals_table(db_path)
    with sqlite3.connect(db_path) as c:
        row = c.execute(
            "SELECT 1 FROM earnings_signals "
            "WHERE symbol = ? AND accession = ? LIMIT 1",
            (symbol, accession)
        ).fetchone()
    return row is not None


def _persist_signal(db_path: Path, r: ReactionResult) -> None:
    _ensure_signals_table(db_path)
    with sqlite3.connect(db_path) as c:
        c.execute("""
            INSERT OR REPLACE INTO earnings_signals
            (ts, symbol, accession, filed_at, items_json, direction,
             materiality, guidance_change, surprise_direction, summary,
             bullish_quotes_json, bearish_quotes_json, model, cost_usd,
             raw_response, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            r.symbol, r.accession, r.filed_at,
            json.dumps(r.items), r.direction, r.materiality,
            r.guidance_change, r.surprise_direction, r.summary,
            json.dumps(r.bullish_quotes), json.dumps(r.bearish_quotes),
            r.model, r.cost_usd, r.raw_response, r.error,
        ))
        c.commit()


CLAUDE_SYSTEM_PROMPT = """You are a senior buy-side analyst reviewing a freshly-filed SEC 8-K.

Your job: extract a structured trading-relevant signal in 60 seconds. You are
NOT making the investment decision — you're feeding a portfolio manager who
will. Be specific, conservative, and quote verbatim from the document.

Output ONLY valid JSON matching this schema (no prose before or after):
{
  "direction": "BULLISH" | "NEUTRAL" | "BEARISH" | "SURPRISE",
  "materiality": 1-5,
  "guidance_change": "RAISED" | "MAINTAINED" | "LOWERED" | "NONE",
  "surprise_direction": "BEAT" | "INLINE" | "MISSED" | "NONE",
  "summary": "2-3 sentence plain-English read for a PM",
  "bullish_quotes": ["verbatim quote 1", "verbatim quote 2", ...],
  "bearish_quotes": ["verbatim quote 1", "verbatim quote 2", ...]
}

Materiality scale:
  1 = housekeeping / no thesis impact
  2 = minor color
  3 = worth a PM's attention
  4 = warrants position adjustment
  5 = thesis-altering — call the PM
SURPRISE = either direction; reserved for genuinely unexpected disclosures.
If you see no clear bullish or bearish signal, leave the quote arrays empty.
Never invent a quote — only direct text from the document."""


def _analyze_filing_with_claude(
    symbol: str, meta: sec_filings.FilingMetadata,
    text: str, model: str = DEFAULT_MODEL,
) -> ReactionResult:
    """Run Claude over one filing. Returns a populated ReactionResult.
    Falls back to a NEUTRAL stub on any error so the caller doesn't
    have to special-case."""
    r = ReactionResult(
        symbol=symbol, accession=meta.accession, filed_at=meta.filed_at,
        items=meta.items, model=model,
    )
    if not os.getenv("ANTHROPIC_API_KEY"):
        r.error = "ANTHROPIC_API_KEY not set; reactor stubbed to NEUTRAL"
        r.summary = "(stub — no API key)"
        return r

    try:
        from anthropic import Anthropic
        client = Anthropic()
        # Trim aggressively to keep cost bounded
        max_chars = 80_000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[truncated for token budget]"
        user_msg = (
            f"Symbol: {symbol}\n"
            f"Form: {meta.form_type}\n"
            f"Filed: {meta.filed_at}\n"
            f"Items: {', '.join(meta.items) or '(none)'}\n"
            f"---\n\n{text}"
        )
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
        r.raw_response = raw

        # Cost accounting (best-effort)
        try:
            from .llm_audit import estimate_cost, log_llm_call
            r.cost_usd = estimate_cost(
                model=model,
                input_tokens=getattr(resp.usage, "input_tokens", 0),
                output_tokens=getattr(resp.usage, "output_tokens", 0),
            )
            log_llm_call(
                context="earnings_reactor",
                user_input=f"{symbol} {meta.accession}",
                response_text=raw,
                model=model,
                input_tokens=getattr(resp.usage, "input_tokens", 0),
                output_tokens=getattr(resp.usage, "output_tokens", 0),
            )
        except Exception:
            pass

        # Parse Claude's JSON response
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract a JSON block from anywhere in the response
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = {}
            else:
                parsed = {}

        r.direction = str(parsed.get("direction", "NEUTRAL")).upper()
        r.materiality = int(parsed.get("materiality", 1))
        r.guidance_change = str(parsed.get("guidance_change", "NONE")).upper()
        r.surprise_direction = str(parsed.get("surprise_direction", "NONE")).upper()
        r.summary = str(parsed.get("summary", ""))
        r.bullish_quotes = list(parsed.get("bullish_quotes", []))
        r.bearish_quotes = list(parsed.get("bearish_quotes", []))
    except Exception as e:
        r.error = f"{type(e).__name__}: {e}"
    return r


def _format_alert_body(r: ReactionResult) -> str:
    """Compose a substantive email body for a material reactor signal.
    Anti-stub guard in trader.notify._is_stub requires ≥80 chars; we
    always exceed that with summary + quotes."""
    lines: list[str] = []
    lines.append(
        f"Symbol: {r.symbol}  ·  Materiality: M{r.materiality}/5  "
        f"·  Direction: {r.direction}"
    )
    lines.append(
        f"Filed: {r.filed_at}  ·  Items: "
        f"{', '.join(r.items) if r.items else '—'}"
    )
    if r.guidance_change and r.guidance_change != "NONE":
        lines.append(f"Guidance: {r.guidance_change}")
    if r.surprise_direction and r.surprise_direction != "NONE":
        lines.append(f"Surprise: {r.surprise_direction}")
    lines.append("")
    if r.summary:
        lines.append("SUMMARY")
        lines.append(r.summary)
        lines.append("")
    if r.bearish_quotes:
        lines.append("BEARISH QUOTES")
        for q in r.bearish_quotes[:5]:
            lines.append(f'  > "{q}"')
        lines.append("")
    if r.bullish_quotes:
        lines.append("BULLISH QUOTES")
        for q in r.bullish_quotes[:5]:
            lines.append(f'  > "{q}"')
        lines.append("")
    lines.append(f"Accession: {r.accession}")
    lines.append("")
    lines.append("--")
    lines.append("trader earnings reactor — see 📞 Earnings reactor in dashboard "
                 "for the full picture. Decision is yours; this email is the "
                 "analyst layer, not the order layer.")
    return "\n".join(lines)


def _short_summary_for_subject(s: str, max_chars: int = 50) -> str:
    """Trim summary for email subject so the line stays under ~78 chars total."""
    s = (s or "").strip().replace("\n", " ").replace("  ", " ")
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 1].rstrip() + "…"


def _maybe_alert(r: ReactionResult, db_path: Path,
                  min_materiality: int = 3) -> bool:
    """Send email alert for one signal IF (a) materiality crosses the
    threshold, (b) no error happened, (c) we haven't already notified
    about this (symbol, accession) pair. Returns True iff an alert was
    sent.

    Idempotent across runs: the `notified_at` column gates re-sends.
    Failed sends leave notified_at NULL so the next reactor run retries.
    """
    if r.error:
        return False
    if (r.materiality or 0) < int(min_materiality):
        return False

    _ensure_signals_table(db_path)
    with sqlite3.connect(db_path) as c:
        row = c.execute(
            "SELECT notified_at FROM earnings_signals "
            "WHERE symbol = ? AND accession = ?",
            (r.symbol, r.accession),
        ).fetchone()
    if row is None:
        return False  # signal not yet persisted — caller order error
    if row[0]:
        return False  # already notified — idempotent skip

    short = _short_summary_for_subject(r.summary)
    subject = f"[trader] {r.symbol} M{r.materiality} {r.direction}"
    if short:
        subject = f"{subject} — {short}"
    body = _format_alert_body(r)

    try:
        from .notify import notify
        result = notify(body, level="info", subject=subject)
        # v3.69.1: count either channel as successful delivery so we
        # don't retry every reactor iter when only one channel is
        # configured (e.g. only email + no Slack webhook yet).
        delivered = bool(result.get("email") or result.get("slack"))
        if not delivered:
            return False
    except Exception:
        return False

    # Mark notified_at so we don't re-send next run
    with sqlite3.connect(db_path) as c:
        c.execute(
            "UPDATE earnings_signals SET notified_at = ? "
            "WHERE symbol = ? AND accession = ?",
            (datetime.utcnow().isoformat(), r.symbol, r.accession),
        )
        c.commit()
    return True


def _alert_threshold() -> int:
    """Min materiality for email alerts. Default 3 = 'worth a PM's
    attention'. Configurable via env REACTOR_ALERT_MIN_MATERIALITY."""
    try:
        return int(os.getenv("REACTOR_ALERT_MIN_MATERIALITY", "3"))
    except ValueError:
        return 3


def react_for_symbol(
    symbol: str,
    since_days: int = 14,
    journal_db: Path = DEFAULT_JOURNAL_DB,
    archive_root: Optional[Path] = None,
    only_material: bool = True,
    model: str = DEFAULT_MODEL,
    alert: bool = True,
) -> list[ReactionResult]:
    """Fetch + archive + analyze recent 8-Ks for one symbol.

    Idempotent on accession: if a filing is already archived AND
    already analyzed (signal row present), it's skipped."""
    since_iso = (datetime.utcnow().date() - timedelta(days=since_days)).isoformat()
    metas = sec_filings.fetch_recent_filings(
        symbol, form_types=("8-K",), since=since_iso, limit=20,
    )
    results: list[ReactionResult] = []
    for meta in metas:
        if only_material and not sec_filings.is_material_8k(meta):
            continue
        # Already analyzed?
        if _signal_exists(journal_db, symbol, meta.accession):
            continue
        # Archive if not yet stored
        if not filings_archive.exists(meta.accession, root=archive_root):
            body = sec_filings.download_filing(meta)
            if body is None:
                continue
            if "<html" in body[:500].lower() or "<!doctype" in body[:200].lower():
                body = sec_filings.strip_html(body)
            filings_archive.store(
                symbol=symbol,
                form_type=meta.form_type,
                accession=meta.accession,
                filed_at=meta.filed_at,
                url=meta.archive_url,
                text=body,
                items=meta.items,
                source="sec_edgar",
                title=meta.primary_doc_description,
                root=archive_root,
            )
        # Read text from archive (might've just been stored, or already there)
        text = filings_archive.read_text(meta.accession, root=archive_root) or ""
        if not text:
            continue
        r = _analyze_filing_with_claude(symbol, meta, text, model=model)
        _persist_signal(journal_db, r)
        if alert:
            try:
                _maybe_alert(r, journal_db, min_materiality=_alert_threshold())
            except Exception as e:
                print(f"  ! alert send failed for {symbol} {r.accession}: "
                      f"{type(e).__name__}: {e}")
        results.append(r)
    return results


def alert_unsent_signals(
    journal_db: Path = DEFAULT_JOURNAL_DB,
    min_materiality: Optional[int] = None,
    since_days: int = 30,
    limit: int = 50,
) -> list[tuple[str, str]]:
    """Send alerts for any material signals that haven't been notified
    yet. Useful for backfilling: if the alert layer was added AFTER
    signals already landed in the journal, this catches them up.

    Returns the list of (symbol, accession) pairs we successfully
    notified about (empty list if none qualified or all were already
    sent)."""
    if min_materiality is None:
        min_materiality = _alert_threshold()
    _ensure_signals_table(journal_db)
    since_iso = (datetime.utcnow().date()
                 - timedelta(days=since_days)).isoformat()
    sent: list[tuple[str, str]] = []
    # Pull rows that are eligible (materiality crosses threshold,
    # no error, no prior notification)
    with sqlite3.connect(journal_db) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT symbol, accession, filed_at, items_json, direction, "
            "materiality, guidance_change, surprise_direction, summary, "
            "bullish_quotes_json, bearish_quotes_json, model, error "
            "FROM earnings_signals "
            "WHERE filed_at >= ? AND materiality >= ? "
            "AND (notified_at IS NULL OR notified_at = '') "
            "AND (error IS NULL OR error = '') "
            "ORDER BY filed_at DESC, materiality DESC LIMIT ?",
            (since_iso, min_materiality, limit),
        ).fetchall()
    for row in rows:
        r = ReactionResult(
            symbol=row["symbol"], accession=row["accession"],
            filed_at=row["filed_at"],
            items=json.loads(row["items_json"]) if row["items_json"] else [],
            direction=row["direction"] or "NEUTRAL",
            materiality=int(row["materiality"] or 0),
            guidance_change=row["guidance_change"] or "NONE",
            surprise_direction=row["surprise_direction"] or "NONE",
            summary=row["summary"] or "",
            bullish_quotes=(json.loads(row["bullish_quotes_json"])
                             if row["bullish_quotes_json"] else []),
            bearish_quotes=(json.loads(row["bearish_quotes_json"])
                             if row["bearish_quotes_json"] else []),
            model=row["model"] or "",
            error=row["error"],
        )
        if _maybe_alert(r, journal_db, min_materiality=min_materiality):
            sent.append((r.symbol, r.accession))
    return sent


def react_for_positions(
    symbols: list[str],
    since_days: int = 14,
    journal_db: Path = DEFAULT_JOURNAL_DB,
    archive_root: Optional[Path] = None,
    only_material: bool = True,
    model: str = DEFAULT_MODEL,
    alert: bool = True,
) -> dict[str, list[ReactionResult]]:
    """Run the reactor across many symbols. Returns {symbol: [results]}."""
    out: dict[str, list[ReactionResult]] = {}
    for sym in symbols:
        try:
            out[sym] = react_for_symbol(
                sym, since_days=since_days, journal_db=journal_db,
                archive_root=archive_root, only_material=only_material,
                model=model, alert=alert,
            )
        except Exception as e:
            out[sym] = []
            # We don't raise — one bad symbol shouldn't stop the others
            print(f"  ! {sym} reactor error: {type(e).__name__}: {e}")
    return out


def recent_signals(
    journal_db: Optional[Path] = None,
    since_days: int = 30,
    symbol: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Read recent earnings_signals rows for the dashboard view.

    `journal_db=None` reads the module-level DEFAULT_JOURNAL_DB at
    CALL time (not function-definition time). This matters for tests:
    monkeypatching `trader.earnings_reactor.DEFAULT_JOURNAL_DB` only
    works if the function consults the attribute when called."""
    if journal_db is None:
        journal_db = DEFAULT_JOURNAL_DB
    _ensure_signals_table(journal_db)
    since_iso = (datetime.utcnow().date()
                 - timedelta(days=since_days)).isoformat()
    sql = ("SELECT symbol, accession, filed_at, items_json, direction, "
           "materiality, guidance_change, surprise_direction, summary, "
           "bullish_quotes_json, bearish_quotes_json, model, cost_usd, ts "
           "FROM earnings_signals WHERE filed_at >= ?")
    params: list = [since_iso]
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    sql += " ORDER BY filed_at DESC, ts DESC LIMIT ?"
    params.append(limit)
    out = []
    with sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True) as c:
        for row in c.execute(sql, params).fetchall():
            out.append({
                "symbol": row[0], "accession": row[1], "filed_at": row[2],
                "items": json.loads(row[3]) if row[3] else [],
                "direction": row[4], "materiality": row[5],
                "guidance_change": row[6], "surprise_direction": row[7],
                "summary": row[8],
                "bullish_quotes": json.loads(row[9]) if row[9] else [],
                "bearish_quotes": json.loads(row[10]) if row[10] else [],
                "model": row[11], "cost_usd": row[12], "ts": row[13],
            })
    return out

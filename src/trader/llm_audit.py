"""[v3.64.0] Compliance audit log for every LLM call that influences trading.

Per the productization roadmap: regulators (and serious customers) expect
to see what the LLM said at every decision point. The journal already
tracks human decisions; this module adds the LLM trail.

Schema (SQLite table `llm_audit_log`):
  id              INTEGER PK
  ts              TEXT (UTC ISO)
  context         TEXT  — "copilot_chat" / "postmortem" / "ranker_critique"
  user_input      TEXT  — what the user / system asked
  model           TEXT  — claude-sonnet-4-6 etc
  response_text   TEXT  — full LLM response
  tools_called    TEXT  — JSON array of tool names invoked
  influenced_trade INTEGER  — 1 if this LLM output influenced a real
                              order, 0 if research/conversation only
  cost_estimate   REAL  — rough $ cost of this call
  session_id      TEXT  — chat thread id if applicable

Public API:
  • log_llm_call(...) — insert one row
  • recent(n=50) — pull recent audit rows
  • by_context(ctx) — filter by context
  • cost_summary(window_days) — total $ spent + per-context breakdown
  • exports_csv(path) — for regulator request
"""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


DB = DATA_DIR / "journal.db"

# Rough per-token cost estimates (USD). Update when Anthropic pricing changes.
COST_PER_INPUT_TOKEN = {
    "claude-opus-4-7": 15 / 1_000_000,
    "claude-opus-4-6": 15 / 1_000_000,
    "claude-sonnet-4-6": 3 / 1_000_000,
    "claude-sonnet-4-5": 3 / 1_000_000,
    "claude-haiku-4-5": 0.80 / 1_000_000,
}
COST_PER_OUTPUT_TOKEN = {
    "claude-opus-4-7": 75 / 1_000_000,
    "claude-opus-4-6": 75 / 1_000_000,
    "claude-sonnet-4-6": 15 / 1_000_000,
    "claude-sonnet-4-5": 15 / 1_000_000,
    "claude-haiku-4-5": 4 / 1_000_000,
}


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""
        CREATE TABLE IF NOT EXISTS llm_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            context TEXT NOT NULL,
            user_input TEXT,
            model TEXT,
            response_text TEXT,
            tools_called TEXT,
            influenced_trade INTEGER DEFAULT 0,
            cost_estimate REAL,
            session_id TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_llm_audit_ts ON llm_audit_log(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_llm_audit_context ON llm_audit_log(context)")
    return c


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost of one Claude API call."""
    cin = COST_PER_INPUT_TOKEN.get(model, 3 / 1_000_000)
    cout = COST_PER_OUTPUT_TOKEN.get(model, 15 / 1_000_000)
    return input_tokens * cin + output_tokens * cout


def log_llm_call(
    context: str,
    user_input: str,
    response_text: str,
    model: str = "claude-sonnet-4-6",
    tools_called: Optional[list[str]] = None,
    influenced_trade: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    session_id: Optional[str] = None,
) -> int:
    """Log one LLM call. Returns audit row id.
    Best-effort — never raises; if logging fails (disk full, schema drift),
    swallow + return -1 so the caller's LIVE path isn't affected."""
    try:
        cost = estimate_cost(model, input_tokens, output_tokens)
        with _conn() as c:
            cur = c.execute("""
                INSERT INTO llm_audit_log
                  (ts, context, user_input, model, response_text,
                   tools_called, influenced_trade, cost_estimate, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.utcnow().isoformat(),
                context,
                (user_input or "")[:5000],  # truncate to keep DB small
                model,
                (response_text or "")[:10000],
                json.dumps(tools_called or []),
                1 if influenced_trade else 0,
                cost,
                session_id,
            ))
            return cur.lastrowid or -1
    except Exception:
        return -1


def recent(n: int = 50) -> list[dict]:
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("""
                SELECT * FROM llm_audit_log ORDER BY ts DESC LIMIT ?
            """, (n,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def by_context(context: str, n: int = 100) -> list[dict]:
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("""
                SELECT * FROM llm_audit_log WHERE context = ?
                ORDER BY ts DESC LIMIT ?
            """, (context, n)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def cost_summary(window_days: int = 30) -> dict:
    """Returns {total_cost, n_calls, by_context: {ctx: {n, cost}}}."""
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
    try:
        with _conn() as c:
            total = c.execute("""
                SELECT COUNT(*), COALESCE(SUM(cost_estimate), 0)
                FROM llm_audit_log WHERE ts >= ?
            """, (cutoff,)).fetchone()
            per_context = c.execute("""
                SELECT context, COUNT(*), COALESCE(SUM(cost_estimate), 0)
                FROM llm_audit_log WHERE ts >= ?
                GROUP BY context ORDER BY 3 DESC
            """, (cutoff,)).fetchall()
        return {
            "window_days": window_days,
            "n_calls": total[0],
            "total_cost_usd": float(total[1] or 0),
            "by_context": {
                row[0]: {"n": row[1], "cost_usd": float(row[2] or 0)}
                for row in per_context
            },
        }
    except Exception as e:
        return {"error": str(e)}


def export_csv(path: Path, since: Optional[datetime] = None) -> int:
    """Export audit log to CSV (for regulator request).
    Returns number of rows written."""
    since = since or (datetime.utcnow() - timedelta(days=365))
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("""
                SELECT * FROM llm_audit_log WHERE ts >= ? ORDER BY ts
            """, (since.isoformat(),)).fetchall()
        if not rows:
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows([dict(r) for r in rows])
        return len(rows)
    except Exception:
        return 0

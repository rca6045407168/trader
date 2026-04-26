"""SQLite journal. Single source of truth for every decision, order, and snapshot.

This is what the post-mortem agent reads each night. Schema is intentionally
flat — easier to query, easier to dump to CSV when we want to audit.
"""
import json
import sqlite3
from datetime import datetime
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    style TEXT,
    score REAL,
    rationale_json TEXT,
    bull TEXT,
    bear TEXT,
    risk_decision TEXT,
    final TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT,
    side TEXT,
    notional REAL,
    alpaca_order_id TEXT,
    status TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS daily_snapshot (
    date TEXT PRIMARY KEY,
    equity REAL,
    cash REAL,
    positions_json TEXT,
    benchmark_spy_close REAL
);
CREATE TABLE IF NOT EXISTS postmortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    pnl_pct REAL,
    summary TEXT,
    proposed_tweak TEXT
);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)


def log_decision(
    ticker: str, action: str, style: str, score: float, rationale: dict,
    debate_dict: dict | None = None, final: str = "",
):
    init_db()
    debate_dict = debate_dict or {}
    with _conn() as c:
        c.execute(
            """INSERT INTO decisions
               (ts, ticker, action, style, score, rationale_json, bull, bear, risk_decision, final)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().isoformat(), ticker, action, style, score,
                json.dumps(rationale, default=str),
                debate_dict.get("bull"), debate_dict.get("bear"),
                debate_dict.get("decision_text"), final,
            ),
        )


def log_order(ticker: str, side: str, notional: float, order_id: str | None,
              status: str, error: str | None = None):
    init_db()
    with _conn() as c:
        c.execute(
            """INSERT INTO orders (ts, ticker, side, notional, alpaca_order_id, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), ticker, side, notional, order_id, status, error),
        )


def log_daily_snapshot(equity: float, cash: float, positions: dict, spy_close: float = 0.0):
    init_db()
    today = datetime.utcnow().date().isoformat()
    with _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO daily_snapshot
               (date, equity, cash, positions_json, benchmark_spy_close)
               VALUES (?, ?, ?, ?, ?)""",
            (today, equity, cash, json.dumps(positions), spy_close),
        )


def log_postmortem(summary: str, tweak: str, pnl_pct: float | None = None):
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO postmortems (date, pnl_pct, summary, proposed_tweak) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().date().isoformat(), pnl_pct, summary, tweak),
        )


def recent_decisions(days: int = 1) -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM decisions WHERE ts >= datetime('now', '-{days} days') ORDER BY ts DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def recent_snapshots(days: int = 7) -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM daily_snapshot WHERE date >= date('now', '-{days} days') ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]

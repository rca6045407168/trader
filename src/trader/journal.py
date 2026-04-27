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
-- v1.3: durable run sentinel for idempotency (B5 fix). Written at the START of
-- main(), updated to 'completed' at the end. If a run crashes mid-execution we
-- still see the 'started' row and don't double-trade.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,  -- 'started' | 'completed' | 'failed' | 'halted'
    notes TEXT
);
-- v1.3: position lots for sleeve-level P&L attribution (B7 fix).
-- One row per opening order; close events fill in close_* columns FIFO.
CREATE TABLE IF NOT EXISTS position_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    sleeve TEXT NOT NULL,  -- 'MOMENTUM' | 'BOTTOM_CATCH'
    opened_at TEXT NOT NULL,
    qty REAL NOT NULL,
    open_price REAL,
    open_order_id TEXT,
    closed_at TEXT,
    close_price REAL,
    close_order_id TEXT,
    realized_pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_lots_sleeve_open ON position_lots (sleeve, closed_at);
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


def start_run(run_id: str, notes: str = "") -> bool:
    """v1.3: insert a 'started' run sentinel. Returns False if a run for this date
    is already started/completed (idempotency guard against B5 race condition)."""
    init_db()
    today = datetime.utcnow().date().isoformat()
    with _conn() as c:
        existing = c.execute(
            "SELECT run_id, status FROM runs WHERE run_id LIKE ? AND status IN ('started', 'completed')",
            (f"{today}%",),
        ).fetchone()
        if existing:
            return False
        c.execute(
            "INSERT INTO runs (run_id, started_at, status, notes) VALUES (?, ?, 'started', ?)",
            (run_id, datetime.utcnow().isoformat(), notes),
        )
    return True


def finish_run(run_id: str, status: str = "completed", notes: str | None = None):
    init_db()
    with _conn() as c:
        c.execute(
            "UPDATE runs SET completed_at = ?, status = ?, notes = COALESCE(?, notes) WHERE run_id = ?",
            (datetime.utcnow().isoformat(), status, notes, run_id),
        )


def open_lot(symbol: str, sleeve: str, qty: float, open_price: float | None,
             open_order_id: str | None = None) -> int:
    """v1.3: record a new position lot tagged to a sleeve. Returns lot id."""
    init_db()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO position_lots (symbol, sleeve, opened_at, qty, open_price, open_order_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, sleeve, datetime.utcnow().isoformat(), qty, open_price, open_order_id),
        )
        return cur.lastrowid


def close_lots_fifo(symbol: str, sleeve: str, qty: float, close_price: float,
                    close_order_id: str | None = None) -> list[dict]:
    """v1.3: FIFO close of open lots for (symbol, sleeve). Returns list of lots closed."""
    init_db()
    closed = []
    remaining = qty
    with _conn() as c:
        open_lots = c.execute(
            """SELECT id, qty, open_price FROM position_lots
               WHERE symbol = ? AND sleeve = ? AND closed_at IS NULL
               ORDER BY opened_at ASC""",
            (symbol, sleeve),
        ).fetchall()
        for lot in open_lots:
            if remaining <= 0:
                break
            close_qty = min(remaining, lot["qty"])
            realized = (close_price - (lot["open_price"] or 0)) * close_qty
            if close_qty == lot["qty"]:
                # full close
                c.execute(
                    """UPDATE position_lots
                       SET closed_at = ?, close_price = ?, close_order_id = ?, realized_pnl = ?
                       WHERE id = ?""",
                    (datetime.utcnow().isoformat(), close_price, close_order_id, realized, lot["id"]),
                )
            else:
                # partial close: reduce qty on existing, insert closed sub-lot
                c.execute("UPDATE position_lots SET qty = qty - ? WHERE id = ?", (close_qty, lot["id"]))
                c.execute(
                    """INSERT INTO position_lots
                       (symbol, sleeve, opened_at, qty, open_price, open_order_id,
                        closed_at, close_price, close_order_id, realized_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, sleeve, lot.get("opened_at") or datetime.utcnow().isoformat(),
                     close_qty, lot["open_price"], None,
                     datetime.utcnow().isoformat(), close_price, close_order_id, realized),
                )
            closed.append({"lot_id": lot["id"], "qty": close_qty, "realized_pnl": realized})
            remaining -= close_qty
    return closed


def open_lots_for_sleeve(sleeve: str, max_age_days: int | None = None) -> list[dict]:
    """v1.3: list open lots for a sleeve, optionally filtered by age. Used by
    the time-exit logic in execute.close_aged_bottom_catches() (B1 fix)."""
    init_db()
    with _conn() as c:
        if max_age_days is not None:
            rows = c.execute(
                f"""SELECT * FROM position_lots
                   WHERE sleeve = ? AND closed_at IS NULL
                   AND opened_at < datetime('now', '-{int(max_age_days)} days')
                   ORDER BY opened_at ASC""",
                (sleeve,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM position_lots WHERE sleeve = ? AND closed_at IS NULL ORDER BY opened_at ASC",
                (sleeve,),
            ).fetchall()
        return [dict(r) for r in rows]

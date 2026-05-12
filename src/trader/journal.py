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
-- v6.0.x: daily_snapshot gains a `broker` column with composite PK
-- (date, broker) so cross-broker journal data doesn't mix. New table
-- below; existing single-PK rows get migrated automatically via
-- _migrate_daily_snapshot_broker() called from init_db().
CREATE TABLE IF NOT EXISTS daily_snapshot (
    date TEXT NOT NULL,
    broker TEXT NOT NULL DEFAULT 'alpaca_paper',
    equity REAL,
    cash REAL,
    positions_json TEXT,
    benchmark_spy_close REAL,
    PRIMARY KEY (date, broker)
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
-- v2.9: A/B testing infrastructure for safe strategy iteration
CREATE TABLE IF NOT EXISTS variants (
    variant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,  -- 'live' | 'shadow' | 'paper' | 'retired'
    params_json TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    promoted_at TEXT,
    retired_at TEXT
);
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    rationale TEXT,
    market_context_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_variant_ts ON shadow_decisions (variant_id, ts);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _current_broker() -> str:
    """The current BROKER env value, defaulted to 'alpaca_paper'.

    v6.0.x: daily_snapshot + recent_snapshots use this to scope rows
    by broker so cross-broker journal data doesn't mix.
    """
    import os as _os
    return _os.environ.get("BROKER", "alpaca_paper").lower()


def _migrate_daily_snapshot_broker(con) -> bool:
    """One-shot migration: add `broker` column + composite PK to
    daily_snapshot if the existing table uses the legacy single-PK
    schema. Idempotent — safe to call on already-migrated DBs.

    Returns True if a migration ran, False if the table was already
    current. Existing rows get tagged 'alpaca_paper' (the only broker
    that was in scope before v6.0.x).
    """
    cols = con.execute("PRAGMA table_info(daily_snapshot)").fetchall()
    if not cols:
        return False  # CREATE TABLE will have made the new shape already
    col_names = {row[1] for row in cols}
    if "broker" in col_names:
        return False  # already migrated
    # Legacy schema detected — rebuild
    con.executescript("""
        CREATE TABLE daily_snapshot_v6 (
            date TEXT NOT NULL,
            broker TEXT NOT NULL DEFAULT 'alpaca_paper',
            equity REAL,
            cash REAL,
            positions_json TEXT,
            benchmark_spy_close REAL,
            PRIMARY KEY (date, broker)
        );
        INSERT INTO daily_snapshot_v6
            (date, broker, equity, cash, positions_json, benchmark_spy_close)
        SELECT date, 'alpaca_paper', equity, cash, positions_json,
               benchmark_spy_close
        FROM daily_snapshot;
        DROP TABLE daily_snapshot;
        ALTER TABLE daily_snapshot_v6 RENAME TO daily_snapshot;
    """)
    return True


def init_db():
    with _conn() as c:
        # Run the broker-column migration BEFORE executing the CREATE
        # TABLE in SCHEMA (which is IF NOT EXISTS — so it would be a
        # no-op on an existing legacy table). The migration recreates
        # the table with the new shape.
        try:
            _migrate_daily_snapshot_broker(c)
        except Exception:
            pass  # if migration fails (e.g. table doesn't exist), SCHEMA creates fresh
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


def log_daily_snapshot(equity: float, cash: float, positions: dict,
                        spy_close: float = 0.0, broker: str | None = None):
    """Write today's snapshot for the current (or specified) broker.

    v6.0.x: composite PK (date, broker) — two brokers writing the same
    date are independent rows. Default broker comes from the BROKER env."""
    init_db()
    today = datetime.utcnow().date().isoformat()
    if broker is None:
        broker = _current_broker()
    with _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO daily_snapshot
               (date, broker, equity, cash, positions_json, benchmark_spy_close)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (today, broker, equity, cash, json.dumps(positions), spy_close),
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


def recent_snapshots(days: int = 7, broker: str | None = None) -> list[dict]:
    """Return recent daily snapshots, filtered by broker.

    v6.0.x: `broker` defaults to the current BROKER env. Pass
    broker='all' to get every broker's history (legacy behavior, used
    by some operator tools that span historical runs)."""
    init_db()
    if broker is None:
        broker = _current_broker()
    with _conn() as c:
        if broker == "all":
            rows = c.execute(
                f"SELECT * FROM daily_snapshot "
                f"WHERE date >= date('now', '-{days} days') "
                f"ORDER BY date DESC"
            ).fetchall()
        else:
            rows = c.execute(
                f"SELECT * FROM daily_snapshot "
                f"WHERE date >= date('now', '-{days} days') "
                f"AND broker = ? "
                f"ORDER BY date DESC",
                (broker,),
            ).fetchall()
        return [dict(r) for r in rows]


def start_run(run_id: str, notes: str = "") -> bool:
    """v1.3: insert a 'started' run sentinel. Returns False if a run for
    this date is already started/completed (idempotency guard).

    v3.73.16: run_ids ending in '-FORCE' bypass the idempotency check.
    This lets `python -m trader.main --force` actually journal a row
    when the original same-day run hit a HALT and we want to re-run
    after manual intervention. Without this, the FORCE re-run would
    do all its work but leave no runs-table evidence, making the
    journal inconsistent with the orders/decisions/strategy_eval rows
    it produced.
    """
    init_db()
    today = datetime.utcnow().date().isoformat()
    is_force = run_id.endswith("-FORCE")
    with _conn() as c:
        if not is_force:
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


def close_lots(symbol: str, sleeve: str, qty: float, close_price: float,
                close_order_id: str | None = None,
                selection: str = "FIFO") -> list[dict]:
    """v6.0.x: generalised lot-close with selection-method support.

    selection: "FIFO" (first-in-first-out — IRS default) or
               "HIFO" (highest-cost-first — maximises realized loss
                       on a close at any given price, the standard
                       TLH-optimised lot-selection method).

    HIFO is allowed by the IRS via Form 8949 "specific identification"
    as long as the broker confirms the chosen lots BEFORE settlement.
    Alpaca supports specific-ID closes via the API. Our journal mirrors
    that selection so the post-trade record matches what the broker
    reports on the 1099-B.

    Returns the list of closed-lot dicts ordered as consumed."""
    init_db()
    closed = []
    remaining = qty
    sel = selection.upper()
    if sel == "HIFO":
        order_sql = "open_price DESC, opened_at ASC"
    else:
        order_sql = "opened_at ASC"  # FIFO (legacy default)
    with _conn() as c:
        open_lots = c.execute(
            f"""SELECT id, qty, open_price FROM position_lots
               WHERE symbol = ? AND sleeve = ? AND closed_at IS NULL
               ORDER BY {order_sql}""",
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
                lot_opened_at = c.execute(
                    "SELECT opened_at FROM position_lots WHERE id = ?", (lot["id"],)
                ).fetchone()
                opened_at_value = lot_opened_at["opened_at"] if lot_opened_at else datetime.utcnow().isoformat()
                c.execute(
                    """INSERT INTO position_lots
                       (symbol, sleeve, opened_at, qty, open_price, open_order_id,
                        closed_at, close_price, close_order_id, realized_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, sleeve, opened_at_value,
                     close_qty, lot["open_price"], None,
                     datetime.utcnow().isoformat(), close_price, close_order_id, realized),
                )
            closed.append({"lot_id": lot["id"], "qty": close_qty, "realized_pnl": realized})
            remaining -= close_qty
    return closed


def close_lots_fifo(symbol: str, sleeve: str, qty: float, close_price: float,
                    close_order_id: str | None = None) -> list[dict]:
    """v1.3: FIFO close. v6.0.x preserves this entry point for callers
    that explicitly want FIFO; for env-driven selection, callers should
    use close_lots() directly. Behaviour unchanged."""
    return close_lots(symbol, sleeve, qty, close_price, close_order_id,
                       selection="FIFO")


def close_lots_auto(symbol: str, sleeve: str, qty: float, close_price: float,
                     close_order_id: str | None = None) -> list[dict]:
    """v6.0.x: env-driven selection wrapper.

    Reads TLH_LOT_SELECTION from environment. Defaults to HIFO in
    v6 (was FIFO in v5). HIFO is the standard TLH-optimised choice
    and the multiplier on harvested loss when paired with
    TLH_ENABLED=true. The change is a no-op for single-lot tickers
    (HIFO ≡ FIFO when there's only one lot) and strictly better when
    multiple lots exist. Set TLH_LOT_SELECTION=FIFO to revert."""
    import os as _os
    sel = _os.environ.get("TLH_LOT_SELECTION", "HIFO")
    return close_lots(symbol, sleeve, qty, close_price, close_order_id,
                       selection=sel)


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

"""[v3.59.2 — SCAFFOLD] operator-alpha thesis ledger.

Per BLINDSPOTS.md §1: in your day-job role, you observe public-
carrier signal (ODFL, SAIA, KNX, XPO, JBHT, CHRW, EXPD, ARCB, FDX, UPS)
weeks before it diffuses to public data. This module is the persistence
layer for those observations so a sleeve can later backtest them.

Operational guardrails (from BLINDSPOTS.md):

  1. **Mandatory logging** — every meaningful observation gets a row,
     regardless of trade outcome. Avoids survivorship bias in the
     ledger.
  2. **72-hour minimum lag** between observation and trade. Avoids any
     accidental MNPI risk if a private-co customer says something
     about a public-co partner.
  3. **No automated trading from this module.** It records and surfaces
     for review; trades happen through the regular sleeve interface
     ONLY after the ledger is backtested and a sleeve-level allocation
     is justified.

Schema:
  observation_id (UUID), ts (ISO), ticker, direction (+/-/neutral),
  confidence (1-5), source ("apollo", "wechat", "linkedin", "earnings_call",
  "internal_meeting"), commentary (free text), tradeable_after_ts (ISO).

Storage: SQLite at data/thesis_ledger.db. Separate from journal.db so
the trading system continues to operate even if the ledger schema
changes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


LEDGER_PATH = DATA_DIR / "thesis_ledger.db"
MIN_LAG_HOURS = 72  # BLINDSPOTS guardrail


@dataclass
class Observation:
    observation_id: str
    ts: str                       # when observed
    ticker: str
    direction: str                # "positive" / "negative" / "neutral"
    confidence: int               # 1-5
    source: str                   # "apollo" / "wechat" / etc
    commentary: str
    tradeable_after_ts: str       # ts + MIN_LAG_HOURS
    realized_outcome: Optional[str] = None  # filled in post-hoc


def _conn() -> sqlite3.Connection:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(LEDGER_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            observation_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            source TEXT NOT NULL,
            commentary TEXT,
            tradeable_after_ts TEXT NOT NULL,
            realized_outcome TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON observations(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON observations(ticker)")
    return c


def add_observation(ticker: str, direction: str, confidence: int,
                     source: str, commentary: str = "") -> str:
    """Log a new observation. Returns observation_id."""
    if direction not in ("positive", "negative", "neutral"):
        raise ValueError(f"direction must be positive/negative/neutral, got {direction}")
    if not (1 <= confidence <= 5):
        raise ValueError(f"confidence must be 1-5, got {confidence}")
    obs_id = str(uuid.uuid4())
    now = datetime.utcnow()
    tradeable = (now + timedelta(hours=MIN_LAG_HOURS)).isoformat()
    with _conn() as c:
        c.execute("""
            INSERT INTO observations
                (observation_id, ts, ticker, direction, confidence,
                 source, commentary, tradeable_after_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (obs_id, now.isoformat(), ticker.upper(), direction,
               confidence, source, commentary, tradeable))
    return obs_id


def list_observations(ticker: Optional[str] = None,
                       limit: int = 100) -> list[dict]:
    """Return recent observations, optionally filtered by ticker."""
    with _conn() as c:
        if ticker:
            rows = c.execute("""
                SELECT * FROM observations WHERE ticker = ?
                ORDER BY ts DESC LIMIT ?
            """, (ticker.upper(), limit)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM observations
                ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        cols = [d[0] for d in c.execute("PRAGMA table_info(observations)").fetchall()
                or []]
    # Re-query column names properly
    with _conn() as c:
        cols = [d[1] for d in c.execute("PRAGMA table_info(observations)").fetchall()]
    return [dict(zip(cols, row)) for row in rows]


def is_tradeable(observation_id: str) -> bool:
    """Has the 72h cool-off elapsed for this observation?"""
    with _conn() as c:
        row = c.execute(
            "SELECT tradeable_after_ts FROM observations WHERE observation_id = ?",
            (observation_id,)).fetchone()
    if not row:
        return False
    try:
        target = datetime.fromisoformat(row[0])
    except Exception:
        return False
    return datetime.utcnow() >= target


def update_outcome(observation_id: str, outcome: str) -> bool:
    """Post-hoc: mark whether this observation panned out.
    outcome: free-text or structured ("validated_+5pct", "invalidated", etc)."""
    with _conn() as c:
        cur = c.execute("""
            UPDATE observations SET realized_outcome = ?
            WHERE observation_id = ?
        """, (outcome, observation_id))
        return cur.rowcount > 0


def stats_by_direction() -> dict:
    """Aggregate count + avg-confidence per direction."""
    with _conn() as c:
        rows = c.execute("""
            SELECT direction, COUNT(*) as n, AVG(confidence) as avg_conf,
                   SUM(CASE WHEN realized_outcome IS NOT NULL THEN 1 ELSE 0 END) as n_outcomes
            FROM observations GROUP BY direction
        """).fetchall()
    return {
        r[0]: {"count": r[1], "avg_confidence": float(r[2] or 0),
                "n_outcomes_logged": r[3]}
        for r in rows
    }

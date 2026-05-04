"""Single source of truth for current account equity + day P&L (v3.66.0).

Resolves the v3.65.x bug class where four different cache layers
(daily_snapshot, briefing_cache, _live_portfolio, _cached_snapshots)
returned conflicting equity numbers — leading to user confusion like
"why does my account show $107K here and $106K there?"

Every dashboard view should call `get_equity_state()` and read from
the returned `EquityState` dataclass — never compute equity / day P&L
from raw sources.

## Source priority

1. **live_broker** (Alpaca via positions_live.fetch_live_portfolio) —
   freshest, sub-second-old marks. Preferred when reachable.
2. **journal_snapshot** (data/journal.db daily_snapshot table) — last
   cron-written value. Used when broker fetch fails.
3. **briefing_cache** (data/briefing_cache.json) — disk-cached briefing
   from a recent prewarm. Last-resort fallback.
4. **none** — returned with `error` set when no source is reachable.

## Day-P&L semantics (the v3.65.1 lesson)

The dataclass exposes BOTH `today_pl_*` and `last_session_pl_*`:

- `today_pl_*` is **only set when `session.is_open`** — i.e. during
  regular trading hours on a real trading day. This is "today vs
  yesterday's close" in the conventional sense.
- `last_session_pl_*` is the most recent session's full move; **always
  set if data is available**, regardless of whether the market is
  currently open. On a Sunday, this is Friday's close vs Thursday's
  close — labeled correctly so the consumer doesn't mistake it for
  "today".

Consumers pick one based on what they want to display:
    if state.session.is_open:
        st.metric("Day P&L", state.today_pl_dollar, ...)
    else:
        st.metric(f"Last session ({state.last_session_date})",
                  state.last_session_pl_dollar, ...)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from .market_session import market_session_now, SessionState

SourceLabel = Literal["live_broker", "journal_snapshot",
                       "briefing_cache", "none"]


@dataclass
class EquityState:
    """One canonical view of the account at the moment of compute.

    All numeric fields are Optional — a `none`-source state has them
    all set to None. Consumers should always check `equity_now is not
    None` before formatting."""
    equity_now: Optional[float]
    cash: Optional[float]
    n_positions: int

    # Session-aware day P&L
    today_pl_dollar: Optional[float]      # only set if session.is_open
    today_pl_pct: Optional[float]
    last_session_pl_dollar: Optional[float]
    last_session_pl_pct: Optional[float]
    last_session_date: Optional[str]      # ISO date, e.g. "2026-05-01"

    # Provenance
    source: SourceLabel
    source_age_seconds: float             # how stale is the underlying number
    session: SessionState
    timestamp: str = field(
        default_factory=lambda: datetime.utcnow().isoformat())
    error: Optional[str] = None

    @property
    def is_stale(self) -> bool:
        """True if our source is > 5 minutes old AND market is open.
        During RTH we expect sub-minute freshness from the broker;
        off-hours staleness is fine because nothing is moving."""
        return self.session.is_open and self.source_age_seconds > 300

    def short_provenance(self) -> str:
        """Compact human-readable provenance suitable for a tooltip,
        e.g. 'live_broker · 0s ago · CLOSED_WEEKEND'."""
        return (f"{self.source} · {int(self.source_age_seconds)}s ago "
                f"· {self.session.label}")


def _try_live_broker(session: SessionState) -> Optional[EquityState]:
    """Hit Alpaca via positions_live. Returns None on any failure so the
    caller can fall through to the next source."""
    try:
        from .positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
    except Exception:
        return None
    if pf.error or pf.equity is None:
        return None
    return EquityState(
        equity_now=float(pf.equity),
        cash=float(pf.cash) if pf.cash is not None else None,
        n_positions=len(pf.positions),
        today_pl_dollar=(pf.total_day_pl_dollar
                         if session.is_open else None),
        today_pl_pct=(pf.total_day_pl_pct
                       if session.is_open else None),
        last_session_pl_dollar=pf.total_day_pl_dollar,
        last_session_pl_pct=pf.total_day_pl_pct,
        last_session_date=session.last_trading_day.isoformat(),
        source="live_broker",
        source_age_seconds=0.0,
        session=session,
    )


def _try_journal_snapshot(db_path: Path,
                            session: SessionState) -> Optional[EquityState]:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as c:
            rows = c.execute(
                "SELECT date, equity, cash, positions_json "
                "FROM daily_snapshot ORDER BY date DESC LIMIT 2"
            ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    latest_date, eq, cash, positions_json = rows[0]
    eq = float(eq) if eq is not None else None
    cash = float(cash) if cash is not None else None
    n_positions = 0
    try:
        if positions_json:
            n_positions = len(json.loads(positions_json))
    except Exception:
        pass

    last_session_pl_dollar = None
    last_session_pl_pct = None
    if len(rows) >= 2 and eq is not None:
        prev_eq = float(rows[1][1]) if rows[1][1] is not None else None
        if prev_eq and prev_eq > 0:
            last_session_pl_dollar = eq - prev_eq
            last_session_pl_pct = (eq - prev_eq) / prev_eq

    age = 0.0
    try:
        snap_dt = datetime.fromisoformat(latest_date)
        age = (datetime.utcnow() - snap_dt).total_seconds()
    except Exception:
        # Date stamp is non-ISO; fallback to file mtime
        try:
            age = (datetime.utcnow() -
                    datetime.fromtimestamp(db_path.stat().st_mtime)).total_seconds()
        except Exception:
            age = 0.0

    return EquityState(
        equity_now=eq, cash=cash, n_positions=n_positions,
        today_pl_dollar=(last_session_pl_dollar if session.is_open else None),
        today_pl_pct=(last_session_pl_pct if session.is_open else None),
        last_session_pl_dollar=last_session_pl_dollar,
        last_session_pl_pct=last_session_pl_pct,
        last_session_date=latest_date,
        source="journal_snapshot",
        source_age_seconds=age,
        session=session,
    )


def _try_briefing_cache(cache_path: Path,
                          session: SessionState) -> Optional[EquityState]:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        return None
    br = data.get("briefing", {}) or {}
    eq = br.get("equity_now")
    if eq is None:
        return None
    age = 0.0
    try:
        cached_dt = datetime.fromisoformat(data.get("_cached_at", ""))
        age = (datetime.utcnow() - cached_dt).total_seconds()
    except Exception:
        pass
    day_pl_pct = br.get("day_pl_pct")
    day_pl_dollar = None
    if day_pl_pct is not None and eq:
        day_pl_dollar = eq * day_pl_pct
    return EquityState(
        equity_now=float(eq), cash=None, n_positions=0,
        today_pl_dollar=(day_pl_dollar if session.is_open else None),
        today_pl_pct=(day_pl_pct if session.is_open else None),
        last_session_pl_dollar=day_pl_dollar,
        last_session_pl_pct=day_pl_pct,
        last_session_date=session.last_trading_day.isoformat(),
        source="briefing_cache",
        source_age_seconds=age,
        session=session,
    )


def get_equity_state(
    journal_db: Optional[Path] = None,
    briefing_cache: Optional[Path] = None,
    prefer: Optional[SourceLabel] = None,
) -> EquityState:
    """Return one canonical EquityState. Tries live broker, then journal,
    then briefing cache. `prefer` skips the priority chain to force a
    specific source (useful for tests + admin views)."""
    session = market_session_now()

    if prefer == "live_broker":
        s = _try_live_broker(session)
        if s:
            return s
    elif prefer == "journal_snapshot" and journal_db:
        s = _try_journal_snapshot(journal_db, session)
        if s:
            return s
    elif prefer == "briefing_cache" and briefing_cache:
        s = _try_briefing_cache(briefing_cache, session)
        if s:
            return s

    # Default chain: live → journal → briefing
    if prefer is None:
        s = _try_live_broker(session)
        if s:
            return s
        if journal_db:
            s = _try_journal_snapshot(journal_db, session)
            if s:
                return s
        if briefing_cache:
            s = _try_briefing_cache(briefing_cache, session)
            if s:
                return s

    return EquityState(
        equity_now=None, cash=None, n_positions=0,
        today_pl_dollar=None, today_pl_pct=None,
        last_session_pl_dollar=None, last_session_pl_pct=None,
        last_session_date=session.last_trading_day.isoformat(),
        source="none", source_age_seconds=0.0, session=session,
        error="no equity source reachable (broker, journal, briefing all failed)",
    )

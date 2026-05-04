"""Tests for v3.66.0 — single source of truth for equity / day P&L.

Resolves the v3.65.x bug class where journal_snapshot, briefing_cache,
_live_portfolio, and _cached_snapshots all returned different "equity"
values for the same instant in time.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
ET = ZoneInfo("America/New_York")


# ============================================================
# equity_state module
# ============================================================
def test_equity_state_module_imports():
    from trader.equity_state import (
        EquityState, get_equity_state,
        _try_live_broker, _try_journal_snapshot, _try_briefing_cache,
    )
    assert callable(get_equity_state)


def test_get_equity_state_returns_none_state_when_no_sources(tmp_path):
    """When journal + briefing don't exist and broker fails, get
    a 'none'-source state with error set, not an exception."""
    from trader.equity_state import get_equity_state
    # Pass paths that don't exist
    s = get_equity_state(
        journal_db=tmp_path / "missing.db",
        briefing_cache=tmp_path / "missing.json",
        prefer="journal_snapshot",  # skip the broker attempt
    )
    assert s.source == "none"
    assert s.equity_now is None
    assert s.error is not None


def test_journal_snapshot_source(tmp_path):
    """When journal has rows, get_equity_state with prefer='journal'
    returns them."""
    db = tmp_path / "j.db"
    with sqlite3.connect(db) as c:
        c.execute("""CREATE TABLE daily_snapshot (
            date TEXT PRIMARY KEY, equity REAL, cash REAL,
            positions_json TEXT, benchmark_spy_close REAL)""")
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-05-01", 106503.15, 31853.39,
                   '{"AMD": 1, "AVGO": 2, "CAT": 3}', 0.0))
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-04-30", 105000.00, 31000.00, '{"AMD": 1}', 0.0))
        c.commit()
    from trader.equity_state import get_equity_state
    s = get_equity_state(journal_db=db, prefer="journal_snapshot")
    assert s.source == "journal_snapshot"
    assert s.equity_now == 106503.15
    assert s.cash == 31853.39
    assert s.n_positions == 3
    # Last-session delta computed from row[0] − row[1]
    assert s.last_session_pl_dollar is not None
    assert abs(s.last_session_pl_dollar - 1503.15) < 0.01


def test_briefing_cache_source(tmp_path):
    cache = tmp_path / "briefing.json"
    cache.write_text(json.dumps({
        "_cached_at": datetime.utcnow().isoformat(),
        "briefing": {
            "equity_now": 107204.19,
            "day_pl_pct": 0.0113,
        },
    }))
    from trader.equity_state import get_equity_state
    s = get_equity_state(briefing_cache=cache, prefer="briefing_cache")
    assert s.source == "briefing_cache"
    assert s.equity_now == 107204.19


def test_today_pl_only_set_when_session_open(monkeypatch, tmp_path):
    """The bug we fixed in v3.65.1: today_pl_dollar must be None on
    weekends, even though last_session_pl_dollar is set."""
    db = tmp_path / "j.db"
    with sqlite3.connect(db) as c:
        c.execute("""CREATE TABLE daily_snapshot (
            date TEXT PRIMARY KEY, equity REAL, cash REAL,
            positions_json TEXT, benchmark_spy_close REAL)""")
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-05-01", 106503.15, 31853.39, '{}', 0.0))
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-04-30", 105000.00, 31000.00, '{}', 0.0))
        c.commit()
    # Pin "now" to Sunday 2026-05-03 11am ET via monkeypatching
    # market_session_now to return CLOSED
    from trader import market_session as ms
    real_now = ms.market_session_now
    def fake_now(now=None):
        return real_now(datetime(2026, 5, 3, 11, 0, tzinfo=ET))
    monkeypatch.setattr("trader.equity_state.market_session_now", fake_now)
    from trader.equity_state import get_equity_state
    s = get_equity_state(journal_db=db, prefer="journal_snapshot")
    assert s.session.label == "CLOSED_WEEKEND"
    # The critical assertion: today_pl is suppressed
    assert s.today_pl_dollar is None
    assert s.today_pl_pct is None
    # But last_session_pl is set (because there were 2 journal rows)
    assert s.last_session_pl_dollar is not None


def test_today_pl_set_during_session(monkeypatch, tmp_path):
    db = tmp_path / "j.db"
    with sqlite3.connect(db) as c:
        c.execute("""CREATE TABLE daily_snapshot (
            date TEXT PRIMARY KEY, equity REAL, cash REAL,
            positions_json TEXT, benchmark_spy_close REAL)""")
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-04-14", 100000, 30000, '{}', 0.0))
        c.execute("INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
                  ("2026-04-13", 99000, 30000, '{}', 0.0))
        c.commit()
    from trader import market_session as ms
    real_now = ms.market_session_now
    def fake_now(now=None):
        # Tuesday 2026-04-14 11am ET — RTH
        return real_now(datetime(2026, 4, 14, 11, 0, tzinfo=ET))
    monkeypatch.setattr("trader.equity_state.market_session_now", fake_now)
    from trader.equity_state import get_equity_state
    s = get_equity_state(journal_db=db, prefer="journal_snapshot")
    assert s.session.is_open is True
    assert s.today_pl_dollar is not None
    assert s.today_pl_dollar == 1000.0


def test_short_provenance_format():
    from trader.equity_state import EquityState
    from trader.market_session import market_session_now
    sess = market_session_now()
    s = EquityState(
        equity_now=100000, cash=30000, n_positions=5,
        today_pl_dollar=None, today_pl_pct=None,
        last_session_pl_dollar=500, last_session_pl_pct=0.005,
        last_session_date="2026-05-01",
        source="live_broker", source_age_seconds=42, session=sess,
    )
    p = s.short_provenance()
    assert "live_broker" in p
    assert "42s ago" in p


def test_is_stale_only_during_open_session(monkeypatch):
    """Stale = source > 5min old AND market open. Off-hours never stale."""
    from trader.equity_state import EquityState
    from trader.market_session import market_session_now
    # Closed session: not stale even if old
    closed_sess = market_session_now(datetime(2026, 5, 3, 11, 0, tzinfo=ET))
    s = EquityState(
        equity_now=1, cash=0, n_positions=0,
        today_pl_dollar=None, today_pl_pct=None,
        last_session_pl_dollar=None, last_session_pl_pct=None,
        last_session_date="x", source="live_broker",
        source_age_seconds=99999, session=closed_sess,
    )
    assert s.is_stale is False
    # Open session: stale if > 300s
    open_sess = market_session_now(datetime(2026, 4, 14, 11, 0, tzinfo=ET))
    s = EquityState(
        equity_now=1, cash=0, n_positions=0,
        today_pl_dollar=0, today_pl_pct=0,
        last_session_pl_dollar=None, last_session_pl_pct=None,
        last_session_date="x", source="live_broker",
        source_age_seconds=400, session=open_sess,
    )
    assert s.is_stale is True


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_version_v3_66_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "v3.66.0" in text


def test_dashboard_imports_get_equity_state():
    """Dashboard must wire the canonical EquityState helper through.
    v3.67.0+: the actual import lives in trader/dashboard_ui.py;
    dashboard.py just re-exports `_get_equity_state` and
    `_equity_state_cached` aliases."""
    base = Path(__file__).resolve().parent.parent
    db_text = (base / "scripts" / "dashboard.py").read_text()
    ui_text = (base / "src" / "trader" / "dashboard_ui.py").read_text()
    # Either dashboard.py or dashboard_ui.py imports get_equity_state
    assert ("from trader.equity_state import get_equity_state"
            in (db_text + ui_text))
    # Dashboard preserves the underscore-prefixed accessor names views call
    assert "def _get_equity_state" in db_text
    assert "_equity_state_cached" in db_text


def _ui_text():
    """v3.67.0+: render helpers in trader/dashboard_ui.py."""
    base = Path(__file__).resolve().parent.parent
    return (base / "src" / "trader" / "dashboard_ui.py").read_text()


def test_dashboard_has_day_pl_helper():
    text = _ui_text()
    assert "def render_day_pl_card" in text


def test_dashboard_views_consume_equity_state():
    """view_live_positions must call _render_day_pl_card with the
    canonical EquityState (not inline the logic)."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    lp_idx = text.index("def view_live_positions")
    next_def_idx = text.index("\ndef ", lp_idx + 1)
    lp_body = text[lp_idx:next_def_idx]
    assert "_render_day_pl_card" in lp_body
    assert "_get_equity_state" in lp_body
    # The inline relabel branch must be gone from the view body
    assert "Last session (" not in lp_body


def test_market_session_now_loud_fails():
    """v3.67.0+: market_session() helper lives in dashboard_ui.py.
    Loud-fails (st.warning) on errors instead of synthetic-OPEN."""
    text = _ui_text()
    sess_idx = text.index("def market_session(")
    next_def_idx = text.index("\ndef ", sess_idx + 1)
    body = text[sess_idx:next_def_idx]
    assert "st.warning" in body
    assert '"CLOSED_OVERNIGHT"' in body
    assert '"OPEN", True,' not in body


def test_color_audit_fab_uses_flat_blue():
    """Color audit: FAB no longer uses the purple gradient. Lives in
    dashboard_ui.py as of v3.67.0."""
    text = _ui_text()
    assert "linear-gradient(135deg,#2563eb,#7c3aed)" not in text
    assert "background: #2563eb;" in text


def test_price_headline_shows_provenance():
    """Price headline includes 'src: ... · Xs ago' so the user knows
    the equity source. Lives in dashboard_ui.py."""
    text = _ui_text()
    head_idx = text.index("def render_price_headline")
    next_def_idx = text.index("\ndef ", head_idx + 1)
    body = text[head_idx:next_def_idx]
    assert "src:" in body
    assert "state.source" in body

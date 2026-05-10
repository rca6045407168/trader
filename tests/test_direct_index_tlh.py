"""Tests for v6 direct-indexing + tax-loss-harvesting module."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ALPACA_API_KEY", "test")


# ============================================================
# REPLACEMENT MAP
# ============================================================
def test_replacement_map_has_no_self_references():
    from trader.direct_index_tlh import REPLACEMENT_MAP
    for sym, subs in REPLACEMENT_MAP.items():
        assert sym not in subs, f"{sym} lists itself as a replacement"


def test_replacement_map_covers_universe():
    """Every name in the 50-name universe has at least one replacement."""
    from trader.direct_index_tlh import REPLACEMENT_MAP
    from trader.universe import DEFAULT_LIQUID_50
    missing = [s for s in DEFAULT_LIQUID_50 if s not in REPLACEMENT_MAP]
    assert not missing, f"missing replacements for: {missing}"


def test_replacement_subs_are_known_tickers():
    """Substitutes must themselves be in the universe (otherwise we'd
    swap to a ticker we can't trade in the trader's universe)."""
    from trader.direct_index_tlh import REPLACEMENT_MAP
    from trader.universe import DEFAULT_LIQUID_50
    uni = set(DEFAULT_LIQUID_50)
    for sym, subs in REPLACEMENT_MAP.items():
        unknown = [s for s in subs if s not in uni]
        assert not unknown, f"{sym}'s subs {unknown} not in universe"


# ============================================================
# CAP-WEIGHTED BASKET
# ============================================================
def test_cap_weighted_sums_to_target_gross():
    from trader.direct_index_tlh import cap_weighted_targets
    from trader.universe import DEFAULT_LIQUID_50
    b = cap_weighted_targets(DEFAULT_LIQUID_50, gross=0.70)
    assert abs(sum(b.values()) - 0.70) < 1e-6


def test_cap_weighted_aapl_msft_top_weights():
    """Sanity: AAPL and MSFT should be the biggest names."""
    from trader.direct_index_tlh import cap_weighted_targets
    from trader.universe import DEFAULT_LIQUID_50
    b = cap_weighted_targets(DEFAULT_LIQUID_50, gross=1.0)
    top = sorted(b.items(), key=lambda kv: -kv[1])[:3]
    top_names = {t for t, _ in top}
    assert "AAPL" in top_names and "MSFT" in top_names


def test_cap_weighted_no_negative_weights():
    from trader.direct_index_tlh import cap_weighted_targets
    from trader.universe import DEFAULT_LIQUID_50
    b = cap_weighted_targets(DEFAULT_LIQUID_50, gross=0.70)
    assert all(w >= 0 for w in b.values())


# ============================================================
# WASH-SALE TRACKING
# ============================================================
def test_wash_sale_returns_empty_on_empty_db(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import get_wash_sale_blocked
    assert get_wash_sale_blocked(db_path=db) == set()


def test_wash_sale_catches_recent_loss(tmp_path, monkeypatch):
    """A loss-realizing close within the last 31 days should block the ticker."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    # Closed 5 days ago with loss
    five_days_ago = (date.today() - timedelta(days=5)).isoformat()
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
        "open_price, closed_at, close_price, realized_pnl) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?)",
        ("AAPL", "direct_index_core", "2026-04-01", 100.0, 180.0,
         five_days_ago, 170.0, -1000.0),
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import get_wash_sale_blocked
    blocked = get_wash_sale_blocked(db_path=db)
    assert "AAPL" in blocked


def test_wash_sale_releases_after_31_days(tmp_path, monkeypatch):
    """A loss closed > 31 days ago should NOT block."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    old_close = (date.today() - timedelta(days=40)).isoformat()
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
        "open_price, closed_at, close_price, realized_pnl) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?)",
        ("AAPL", "direct_index_core", "2026-01-01", 100.0, 180.0,
         old_close, 170.0, -1000.0),
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import get_wash_sale_blocked
    blocked = get_wash_sale_blocked(db_path=db)
    assert "AAPL" not in blocked


def test_wash_sale_only_flags_loss_closes(tmp_path, monkeypatch):
    """A GAIN close should NOT block — wash-sale rule applies only to losses."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    one_day_ago = (date.today() - timedelta(days=1)).isoformat()
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
        "open_price, closed_at, close_price, realized_pnl) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?)",
        ("AAPL", "direct_index_core", "2026-04-01", 100.0, 180.0,
         one_day_ago, 200.0, +2000.0),
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import get_wash_sale_blocked
    assert get_wash_sale_blocked(db_path=db) == set()


# ============================================================
# HARVEST PLANNING
# ============================================================
def _setup_db_with_loss_position(tmp_path, sym="AAPL", avg_cost=200.0,
                                    sleeve="direct_index_core"):
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, open_price) "
        "VALUES (?, ?, ?, ?, ?)",
        (sym, sleeve, "2026-04-01", 50.0, avg_cost),
    )
    con.commit()
    con.close()
    return db


def test_plan_emits_swap_when_position_at_loss(tmp_path, monkeypatch):
    """AAPL bought at $200, current price $170 → 15% loss → should harvest."""
    db = _setup_db_with_loss_position(tmp_path)
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import plan_tlh
    from trader.universe import DEFAULT_LIQUID_50
    plan = plan_tlh(
        universe=DEFAULT_LIQUID_50,
        current_prices={"AAPL": 170.0},
        db_path=db,
    )
    assert len(plan.swaps) == 1
    s = plan.swaps[0]
    assert s.sell_ticker == "AAPL"
    assert s.buy_ticker in {"MSFT", "GOOGL", "META"}  # AAPL's replacement set
    assert s.unrealized_loss_pct == pytest.approx(-0.15)


def test_plan_skips_position_below_min_loss(tmp_path, monkeypatch):
    """Position with only 2% loss (below 5% min) should NOT harvest."""
    db = _setup_db_with_loss_position(tmp_path)
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import plan_tlh
    from trader.universe import DEFAULT_LIQUID_50
    plan = plan_tlh(
        universe=DEFAULT_LIQUID_50,
        current_prices={"AAPL": 196.0},  # 2% loss
        db_path=db,
    )
    assert len(plan.swaps) == 0


def test_plan_skips_when_replacement_is_wash_sale_blocked(tmp_path, monkeypatch):
    """If AAPL's preferred replacement (MSFT) was sold for loss recently,
    the planner should skip to the next sub or skip entirely if all blocked."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    # AAPL open at $200 (loss candidate)
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, open_price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("AAPL", "direct_index_core", "2026-04-01", 50.0, 200.0),
    )
    # MSFT, GOOGL, META all recently sold at loss → all blocked
    five_days_ago = (date.today() - timedelta(days=5)).isoformat()
    for sym in ["MSFT", "GOOGL", "META"]:
        con.execute(
            "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
            "open_price, closed_at, close_price, realized_pnl) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (sym, "direct_index_core", "2026-01-01", 10.0, 400.0,
             five_days_ago, 380.0, -200.0),
        )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import plan_tlh
    from trader.universe import DEFAULT_LIQUID_50
    plan = plan_tlh(
        universe=DEFAULT_LIQUID_50,
        current_prices={"AAPL": 170.0},
        db_path=db,
    )
    # All AAPL replacements blocked → no swap emitted, AAPL appears in skipped
    assert len(plan.swaps) == 0
    assert any(sym == "AAPL" for sym, _ in plan.skipped)


def test_plan_target_weights_reflect_swap(tmp_path, monkeypatch):
    """After a swap, target_weights should reflect MSFT (replacement)
    getting AAPL's allocated weight."""
    db = _setup_db_with_loss_position(tmp_path)
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import plan_tlh
    from trader.universe import DEFAULT_LIQUID_50
    plan = plan_tlh(
        universe=DEFAULT_LIQUID_50,
        current_prices={"AAPL": 170.0},
        db_path=db,
    )
    # AAPL should be missing from target_weights (sold), MSFT should have
    # gotten AAPL's weight added to its own
    assert "AAPL" not in plan.target_weights or plan.target_weights["AAPL"] == 0
    # The replacement (one of MSFT/GOOGL/META) should be in there
    assert plan.swaps[0].buy_ticker in plan.target_weights


def test_cumulative_loss_aggregates_negatives(tmp_path, monkeypatch):
    """get_cumulative_realized_loss should sum only negative realized_pnl."""
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE position_lots ("
        "id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, sleeve TEXT NOT NULL, "
        "opened_at TEXT NOT NULL, qty REAL NOT NULL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    today = date.today().isoformat()
    rows = [
        ("AAPL", today, -500.0),
        ("MSFT", today, -300.0),
        ("NVDA", today, +1000.0),  # gain — should NOT be in the loss sum
        ("AMD", today, -200.0),
    ]
    for sym, when, pnl in rows:
        con.execute(
            "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
            "open_price, closed_at, close_price, realized_pnl) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (sym, "direct_index_core", "2026-01-01", 10.0, 100.0,
             when, 90.0, pnl),
        )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.direct_index_tlh.DB_PATH", db)
    from trader.direct_index_tlh import get_cumulative_realized_loss
    total = get_cumulative_realized_loss(db_path=db)
    # -500 + -300 + -200 = -1000  (NVDA gain excluded)
    assert total == pytest.approx(-1000.0)

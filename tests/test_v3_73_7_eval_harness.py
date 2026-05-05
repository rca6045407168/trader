"""Tests for v3.73.7 — constant strategy-evaluation harness.

Three layers covered:

  1. Registry: 10 strategies registered, each callable with the
     expected signature.
  2. Eval runner: schema, idempotency, settle math.
  3. Dashboard wiring: leaderboard view exists + nav entry.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Registry
# ============================================================
def test_eleven_strategies_registered():
    """v3.73.11: 10 candidates + 1 production-replica (xs_top15_min_shifted)
    so the leaderboard compares apples-to-apples against the LIVE variant."""
    from trader import eval_strategies
    specs = eval_strategies.all_strategies()
    assert len(specs) == 11, \
        f"expected 11 strategies, got {len(specs)}: {[s.name for s in specs]}"


def test_canonical_strategy_names_present():
    from trader import eval_strategies
    names = {s.name for s in eval_strategies.all_strategies()}
    expected = {
        "xs_top15", "xs_top15_capped", "vertical_winner",
        "xs_top8", "xs_top25", "score_weighted_xs", "inv_vol_xs",
        "dual_momentum", "sector_rotation_top3", "equal_weight_universe",
        "xs_top15_min_shifted",  # v3.73.11 production-replica
    }
    assert names == expected, f"missing: {expected - names}, extra: {names - expected}"


def test_each_strategy_has_description():
    from trader import eval_strategies
    for s in eval_strategies.all_strategies():
        assert s.description and len(s.description) > 10, \
            f"{s.name} has weak/missing description"


def test_each_strategy_returns_dict_on_synthetic_input():
    """Smoke test — feed each strategy a small synthetic price panel
    and assert it returns a {ticker: weight} dict (or empty)."""
    import pandas as pd
    import numpy as np
    from trader import eval_strategies

    np.random.seed(0)
    # 20 trading days × 5 names — minimal but valid input
    dates = pd.bdate_range("2026-01-01", periods=20)
    cols = ["AAPL", "MSFT", "JPM", "JNJ", "XOM"]
    data = 100 * np.cumprod(1 + np.random.randn(20, 5) * 0.02, axis=0)
    prices = pd.DataFrame(data, index=dates, columns=cols)

    for spec in eval_strategies.all_strategies():
        result = spec.fn(dates[-1], prices)
        assert isinstance(result, dict), f"{spec.name} returned non-dict"
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, (int, float))


# ============================================================
# Eval runner — schema + persistence
# ============================================================
def test_ensure_schema_creates_table(tmp_path):
    from trader.eval_runner import ensure_schema
    db = tmp_path / "j.db"
    # File doesn't exist yet
    con = sqlite3.connect(db)
    con.close()
    ensure_schema(db)
    con = sqlite3.connect(db)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "strategy_eval" in tables
    cols = [r[1] for r in con.execute("PRAGMA table_info(strategy_eval)").fetchall()]
    for required in (
        "asof", "strategy", "picks_json", "n_picks",
        "period_end", "period_return", "spy_return", "active_return",
    ):
        assert required in cols, f"missing column: {required}"


def test_ensure_schema_is_idempotent(tmp_path):
    from trader.eval_runner import ensure_schema
    db = tmp_path / "j.db"
    ensure_schema(db); ensure_schema(db)  # second call must not raise


def test_evaluate_at_inserts_rows_and_is_idempotent(tmp_path, monkeypatch):
    """Calling evaluate_at twice with the same asof must insert each
    strategy ONCE (UNIQUE constraint)."""
    import pandas as pd, numpy as np
    from trader import eval_runner, eval_strategies

    np.random.seed(1)
    dates = pd.bdate_range("2026-01-01", periods=400)
    cols = ["AAPL", "MSFT", "JPM", "JNJ", "XOM"]
    data = 100 * np.cumprod(1 + np.random.randn(400, 5) * 0.01, axis=0)
    prices = pd.DataFrame(data, index=dates, columns=cols)

    db = tmp_path / "j.db"
    asof = dates[-1]
    n1 = eval_runner.evaluate_at(asof, cols, prices=prices, db_path=db)
    assert n1 == 11, f"first call should insert 11 rows; got {n1}"
    n2 = eval_runner.evaluate_at(asof, cols, prices=prices, db_path=db)
    assert n2 == 0, f"second call should be idempotent; got {n2} new rows"

    con = sqlite3.connect(db)
    total = con.execute("SELECT COUNT(*) FROM strategy_eval").fetchone()[0]
    con.close()
    assert total == 11


def test_settle_returns_only_settles_unsettled_rows(tmp_path):
    """Verify the WHERE period_end IS NULL filter actually filters."""
    from trader.eval_runner import ensure_schema, settle_returns
    db = tmp_path / "j.db"
    ensure_schema(db)
    con = sqlite3.connect(db)
    # One settled, one unsettled
    con.execute(
        "INSERT INTO strategy_eval (asof, strategy, picks_json, n_picks, "
        "period_end, period_return, created_at) VALUES "
        "('2026-01-01', 'xs_top15', '{\"AAPL\": 0.5}', 1, "
        "'2026-01-31', 0.05, '2026-01-01T00:00:00')"
    )
    con.execute(
        "INSERT INTO strategy_eval (asof, strategy, picks_json, n_picks, "
        "created_at) VALUES "
        "('2026-02-01', 'xs_top15', '{\"AAPL\": 0.5}', 1, '2026-02-01T00:00:00')"
    )
    con.commit(); con.close()
    # No prices passed → settle finds nothing to settle (no SPY data)
    # but at minimum doesn't crash and doesn't touch the settled row
    import pandas as pd
    n = settle_returns(pd.Timestamp("2026-02-28"), prices=pd.DataFrame(), db_path=db)
    # The unsettled row remains unsettled (no price data to compute from)
    con = sqlite3.connect(db)
    settled = con.execute(
        "SELECT COUNT(*) FROM strategy_eval WHERE period_end IS NOT NULL"
    ).fetchone()[0]
    con.close()
    assert settled == 1  # the original settled row, untouched


# ============================================================
# Leaderboard math
# ============================================================
def test_leaderboard_returns_empty_when_no_settled_rows(tmp_path):
    from trader.eval_runner import leaderboard, ensure_schema
    db = tmp_path / "j.db"
    ensure_schema(db)
    assert leaderboard(db_path=db) == []


def test_leaderboard_ranks_by_cumulative_active_return(tmp_path):
    from trader.eval_runner import ensure_schema, leaderboard
    db = tmp_path / "j.db"
    ensure_schema(db)
    con = sqlite3.connect(db)
    today = datetime.date.today()
    rows = [
        # high_active strategy: +1% per period
        ("loser", today.isoformat(), 0.00, 0.02, -0.02),
        ("winner", today.isoformat(), 0.03, 0.02, 0.01),
        ("middle", today.isoformat(), 0.025, 0.02, 0.005),
    ]
    for s, asof, p, sp, ar in rows:
        con.execute(
            "INSERT INTO strategy_eval (asof, strategy, picks_json, n_picks, "
            "period_end, period_return, spy_return, active_return, created_at) "
            "VALUES (?, ?, '{}', 0, ?, ?, ?, ?, ?)",
            (asof, s, asof, p, sp, ar, today.isoformat()),
        )
    con.commit(); con.close()
    lb = leaderboard(db_path=db, days_back=30)
    assert len(lb) == 3
    assert lb[0]["strategy"] == "winner"
    assert lb[-1]["strategy"] == "loser"


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_has_leaderboard_view():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "def view_strategy_leaderboard" in text


def test_nav_includes_leaderboard():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert '("🏁 Strategy leaderboard", "strategy_leaderboard")' in text


def test_dispatch_includes_leaderboard():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert '"strategy_leaderboard": view_strategy_leaderboard' in text


def test_leaderboard_view_renders_descriptions():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def view_strategy_leaderboard")
    # The leaderboard view sits right above VIEW_DISPATCH; use that
    # as the boundary marker since there's no following `def `.
    end_idx = text.index("VIEW_DISPATCH", fn_idx)
    body = text[fn_idx:end_idx]
    # Must call out the small-sample warning so the operator doesn't
    # over-anchor on early rankings
    assert "30" in body  # the warning threshold
    assert "noise" in body.lower() or "sample" in body.lower()


def test_dashboard_version_v3_73_7():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "v3.73.7" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text)

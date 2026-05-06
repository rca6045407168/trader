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
def test_eighteen_strategies_registered():
    """v3.73.17: 12 active candidates + 3 passive baselines + 3
    sizing-aware candidates (vol-targeted, vol-parity, reactor-
    trimmed). Total 18."""
    from trader import eval_strategies
    specs = eval_strategies.all_strategies()
    assert len(specs) == 18, \
        f"expected 18 strategies, got {len(specs)}: {[s.name for s in specs]}"


def test_canonical_strategy_names_present():
    from trader import eval_strategies
    names = {s.name for s in eval_strategies.all_strategies()}
    expected = {
        # Active candidates (12)
        "xs_top15", "xs_top15_capped", "vertical_winner",
        "xs_top8", "xs_top25", "score_weighted_xs", "inv_vol_xs",
        "dual_momentum", "sector_rotation_top3", "equal_weight_universe",
        "xs_top15_min_shifted",
        "long_short_momentum",
        # Passive baselines (3) — v3.73.14
        "buy_and_hold_spy",
        "boglehead_three_fund",
        "simple_60_40",
        # Sizing-aware (3) — v3.73.17
        "xs_top15_vol_targeted",
        "score_weighted_vol_parity",
        "xs_top15_reactor_trimmed",
    }
    assert names == expected, f"missing: {expected - names}, extra: {names - expected}"


def test_passive_baselines_handle_missing_tickers():
    """If VXUS/BND/AGG aren't in the price panel (cheaper backfill),
    the baselines must still return a valid allocation rather than
    crash or return empty."""
    import pandas as pd, numpy as np
    from trader.eval_strategies import (
        buy_and_hold_spy, boglehead_three_fund, simple_60_40,
    )

    np.random.seed(7)
    dates = pd.bdate_range("2025-01-01", periods=100)
    # Only SPY available — neither VTI/VXUS/BND/AGG
    prices = pd.DataFrame(
        100 * np.cumprod(1 + np.random.randn(100, 1) * 0.01, axis=0),
        index=dates, columns=["SPY"],
    )

    spy_only = buy_and_hold_spy(dates[-1], prices)
    assert spy_only == {"SPY": 1.00}

    bog = boglehead_three_fund(dates[-1], prices)
    assert "SPY" in bog
    assert abs(sum(bog.values()) - 1.0) < 1e-6, \
        f"3-fund weights should normalize to 1.0, got {sum(bog.values())}"

    sixty_forty = simple_60_40(dates[-1], prices)
    # SPY+AGG → just SPY when AGG missing → renormalize to 1.0
    assert "SPY" in sixty_forty
    assert abs(sum(sixty_forty.values()) - 1.0) < 1e-6


def test_long_short_returns_negative_weights():
    """The short side must produce negative weights so the eval
    runner's `weight * (p1/p0 - 1)` accounting works correctly."""
    import pandas as pd
    import numpy as np
    from trader.eval_strategies import long_short_momentum
    from trader.sectors import SECTORS

    np.random.seed(7)
    cols = list(SECTORS.keys())[:30]
    dates = pd.bdate_range("2025-01-01", periods=350)
    data = 100 * np.cumprod(1 + np.random.randn(len(dates), len(cols)) * 0.01, axis=0)
    prices = pd.DataFrame(data, index=dates, columns=cols)

    picks = long_short_momentum(dates[-1], prices)
    longs = {t: w for t, w in picks.items() if w > 0}
    shorts = {t: w for t, w in picks.items() if w < 0}
    assert len(longs) == 15, f"expected 15 longs, got {len(longs)}"
    assert len(shorts) == 5, f"expected 5 shorts, got {len(shorts)}"
    # Net gross: 0.70 long - 0.30 short = 0.40 net
    assert abs(sum(longs.values()) - 0.70) < 1e-3
    assert abs(sum(shorts.values()) + 0.30) < 1e-3


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
    # v3.73.13/14/17: evaluate_at skips empty-picks strategies. Of
    # the 18 registered strategies on the 5-name test panel:
    #   12 active candidates: 11 produce picks (long_short_momentum
    #     fails — needs 20+ names for top-15 + bottom-5)
    #   3 passive baselines: all return non-empty (SPY fallback)
    #   3 sizing-aware (v3.73.17): vol-targeted + vol-parity + reactor-
    #     trimmed. All 3 derive from xs_top15_min_shifted, so on the
    #     5-name panel they produce ≤5 picks (small-universe fallback).
    # Expected inserts: 11 + 3 + 3 = 17.
    n1 = eval_runner.evaluate_at(asof, cols, prices=prices, db_path=db)
    assert n1 == 17, f"first call should insert 17 rows; got {n1}"
    n2 = eval_runner.evaluate_at(asof, cols, prices=prices, db_path=db)
    assert n2 == 0, f"second call should be idempotent; got {n2} new rows"

    con = sqlite3.connect(db)
    total = con.execute("SELECT COUNT(*) FROM strategy_eval").fetchone()[0]
    con.close()
    assert total == 17


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

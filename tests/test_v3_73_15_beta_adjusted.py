"""Tests for v3.73.15 — beta-adjusted scoreboard.

The leaderboard now decomposes cum-active into:
  - β (regression beta to benchmark)
  - cum α (compound alpha after β-adjustment)
  - α annualized (mean alpha × 12)
  - α IR (alpha mean / alpha stdev × √12)
  - max relative DD (peak-to-trough on rel-equity curve)

These tests verify the math against known cases.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


def _seed_strategy_eval(db_path: Path, strategy: str,
                          port_returns: list, spy_returns: list):
    from trader.eval_runner import ensure_schema
    ensure_schema(db_path)
    con = sqlite3.connect(db_path)
    today = datetime.date.today()
    for i, (p, s) in enumerate(zip(port_returns, spy_returns)):
        asof = (today - datetime.timedelta(days=30 * (len(port_returns) - i))).isoformat()
        end = (today - datetime.timedelta(days=30 * (len(port_returns) - i - 1))).isoformat()
        con.execute(
            """INSERT INTO strategy_eval
               (asof, strategy, picks_json, n_picks, period_end,
                period_return, spy_return, active_return, created_at)
               VALUES (?, ?, '{}', 1, ?, ?, ?, ?, ?)""",
            (asof, strategy, end, p, s, p - s, asof),
        )
    con.commit(); con.close()


def test_zero_beta_zero_alpha_when_constant():
    """A strategy with constant zero return → β=0, α=0."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "flat", [0.0] * 12, [0.01] * 12)
    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    flat = next(r for r in lb if r["strategy"] == "flat")
    # Constant returns → variance 0 → beta=0 (safe fallback)
    assert flat["beta"] == 0.0
    # α_t = 0 - 0*0.01 = 0; cum α = 0
    assert abs(flat["cum_alpha_pct"]) < 1e-6


def test_beta_one_when_portfolio_is_benchmark():
    """If port_t = spy_t exactly, β should be 1 and α should be ~0."""
    import tempfile
    import random
    random.seed(11)
    spy = [random.gauss(0.005, 0.04) for _ in range(20)]
    port = list(spy)  # exact replica
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "spy_clone", port, spy)
    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    r = next(x for x in lb if x["strategy"] == "spy_clone")
    assert abs(r["beta"] - 1.0) < 1e-6, f"beta should be 1.0, got {r['beta']}"
    # α_t = port_t - 1.0 * spy_t = 0 → cum α = 0
    assert abs(r["cum_alpha_pct"]) < 1e-3


def test_beta_two_when_portfolio_amplifies_benchmark():
    """If port_t = 2 * spy_t, β should be 2 and α should be ~0
    (no alpha — purely leveraged beta)."""
    import tempfile
    import random
    random.seed(11)
    spy = [random.gauss(0.005, 0.04) for _ in range(30)]
    port = [2 * x for x in spy]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "two_x", port, spy)
    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    r = next(x for x in lb if x["strategy"] == "two_x")
    assert abs(r["beta"] - 2.0) < 1e-3, f"beta should be 2.0, got {r['beta']}"
    # α_t = 2*spy - 2*spy = 0
    assert abs(r["cum_alpha_pct"]) < 1.0, \
        f"alpha should be ~0 for pure-leveraged-beta; got {r['cum_alpha_pct']}"
    # cum_active will be HUGE (2x the cum_spy delta) but cum_alpha is 0
    assert r["cum_active_pct"] > 5, \
        "cum_active should be large (leveraged beta amplifies)"


def test_alpha_positive_for_real_alpha():
    """Port = spy + 0.005 (50bps/period independent of spy) → β=1 (or
    near it depending on noise), α positive."""
    import tempfile
    import random
    random.seed(11)
    spy = [random.gauss(0.005, 0.04) for _ in range(30)]
    port = [x + 0.005 for x in spy]  # spy plus a constant alpha
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "alpha_const", port, spy)
    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    r = next(x for x in lb if x["strategy"] == "alpha_const")
    assert abs(r["beta"] - 1.0) < 1e-3
    # +0.5% per period × ~30 periods compounded ≈ +16%
    assert r["cum_alpha_pct"] > 10, \
        f"cum alpha should be substantially positive; got {r['cum_alpha_pct']}"


def test_max_relative_dd_zero_when_replica():
    """Replica strategy → port_NAV / spy_NAV is constant → max DD = 0."""
    import tempfile
    import random
    random.seed(11)
    spy = [random.gauss(0.005, 0.04) for _ in range(20)]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "replica", list(spy), spy)
    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    r = next(x for x in lb if x["strategy"] == "replica")
    assert abs(r["max_relative_dd_pct"]) < 0.01


def test_leaderboard_sorted_by_cum_alpha():
    """The new leaderboard sorts by cum_alpha, not cum_active.
    A high-beta strategy with no real alpha should rank LOWER than
    a low-beta strategy with positive alpha — even if its
    cum_active is higher (due to leverage)."""
    import tempfile
    import random
    random.seed(7)

    spy_rets = [random.gauss(0.008, 0.03) for _ in range(30)]
    # Strategy A: 1.5x leveraged spy (high cum-active, zero alpha)
    a_rets = [1.5 * x for x in spy_rets]
    # Strategy B: spy + 0.3% alpha per period (lower cum-active, real alpha)
    b_rets = [x + 0.003 for x in spy_rets]

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    _seed_strategy_eval(db, "high_beta_no_alpha", a_rets, spy_rets)
    _seed_strategy_eval(db, "low_beta_real_alpha", b_rets, spy_rets)

    from trader.eval_runner import leaderboard
    lb = leaderboard(db_path=db, days_back=1000)
    a = next(r for r in lb if r["strategy"] == "high_beta_no_alpha")
    b = next(r for r in lb if r["strategy"] == "low_beta_real_alpha")

    # high_beta has bigger cum_active
    assert a["cum_active_pct"] > b["cum_active_pct"]
    # but low_beta has more cum_alpha
    assert b["cum_alpha_pct"] > a["cum_alpha_pct"]

    # Verify the leaderboard ordering reflects this
    a_rank = next(i for i, r in enumerate(lb) if r["strategy"] == "high_beta_no_alpha")
    b_rank = next(i for i, r in enumerate(lb) if r["strategy"] == "low_beta_real_alpha")
    assert b_rank < a_rank, \
        f"low_beta_real_alpha should rank above high_beta_no_alpha"


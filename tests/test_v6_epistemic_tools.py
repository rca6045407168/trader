"""Tests for the v6 epistemic-discipline tools.

Five modules:
  1. trader.uplift_monte_carlo — variance bands on uplift estimate
  2. scripts/strategy_pruning_audit.py — registry pruning advisor
  3. scripts/platform_state.py — state-in-2-pages snapshot
  4. trader.data_quality — yfinance data sanity checks
  5. scripts/quarterly_review.py — forced assumption review
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ============================================================
# 1. Monte Carlo
# ============================================================
def test_uplift_monte_carlo_returns_n_samples():
    from trader.uplift_monte_carlo import simulate
    samples = simulate(n_iter=1000, seed=1)
    assert len(samples) == 1000


def test_uplift_monte_carlo_deterministic_with_seed():
    from trader.uplift_monte_carlo import simulate
    a = simulate(n_iter=500, seed=42)
    b = simulate(n_iter=500, seed=42)
    assert a == b


def test_uplift_monte_carlo_percentiles_ordered():
    from trader.uplift_monte_carlo import simulate, percentiles
    samples = simulate(n_iter=5000, seed=7)
    pcs = percentiles(samples)
    # Percentiles must monotonically increase
    prior = -float("inf")
    for p in [5, 10, 25, 50, 75, 90, 95]:
        assert pcs[p] >= prior
        prior = pcs[p]


def test_uplift_monte_carlo_mean_in_plausible_band():
    """Default component means sum to ~+7.6 %/yr. Tolerance ±2."""
    from trader.uplift_monte_carlo import simulate
    samples = simulate(n_iter=5000, seed=7)
    mean = sum(samples) / len(samples)
    assert 5.0 < mean < 10.0, f"unexpected mean: {mean}"


def test_uplift_monte_carlo_renders_report():
    from trader.uplift_monte_carlo import simulate, render_report
    samples = simulate(n_iter=500, seed=1)
    out = render_report(samples)
    assert "UPLIFT MONTE CARLO" in out
    assert "Mean uplift" in out
    assert "80 % CI" in out


# ============================================================
# 2. Strategy pruning audit
# ============================================================
def test_pruning_audit_handles_missing_db(tmp_path):
    from strategy_pruning_audit import audit
    out = audit(tmp_path / "no.db")
    assert "error" in out


def test_pruning_audit_categorizes_correctly(tmp_path):
    """Build a minimal DB; verify silent/sparse/healthy split.

    Uses REAL strategy names from the registry (the audit filters to
    `eval_strategies.all_strategies()` so synthetic names won't show
    up). xs_top15 + xs_top8 are stable canonical strategies."""
    from strategy_pruning_audit import audit
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE strategy_eval (asof TEXT, strategy TEXT, n_picks INTEGER)"
    )
    today = date.today().isoformat()
    # xs_top15 gets 10 rows → "healthy" (max_rows=10, threshold=8)
    for i in range(10):
        con.execute(
            "INSERT INTO strategy_eval VALUES (?, 'xs_top15', 15)",
            (today,),
        )
    # xs_top8 gets 2 rows → "sparse"
    for i in range(2):
        con.execute(
            "INSERT INTO strategy_eval VALUES (?, 'xs_top8', 8)",
            (today,),
        )
    # xs_top25 has nothing → "silent"
    con.commit()
    con.close()
    out = audit(db, window_months=12)
    assert "xs_top15" in out["healthy"]
    assert "xs_top8" in out["sparse"]
    assert "xs_top25" in out["silent"]


# ============================================================
# 3. Platform state
# ============================================================
def test_platform_state_runs_without_db(monkeypatch, tmp_path):
    """The script gracefully handles missing journal."""
    from trader import config as _cfg
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "missing.db")
    # Force re-import of the script module so the patched DB_PATH is used
    import importlib, scripts.platform_state as ps
    importlib.reload(ps)
    # main() prints to stdout; just verify it returns 0 and doesn't raise
    rc = ps.main()
    assert rc == 0


# ============================================================
# 4. Data quality
# ============================================================
def test_dq_freshness_pass_on_recent_data():
    from trader.data_quality import check_freshness
    dates = pd.bdate_range(end=date.today(), periods=10)
    px = pd.DataFrame({"AAPL": list(range(len(dates)))}, index=dates)
    issues = check_freshness(px, asof=date.today())
    assert not issues


def test_dq_freshness_halts_on_old_data():
    from trader.data_quality import check_freshness
    # Build prices ending 10 business days ago
    dates = pd.bdate_range(end=date.today() - pd.Timedelta(days=20), periods=10)
    px = pd.DataFrame({"AAPL": range(10)}, index=dates)
    issues = check_freshness(px, asof=date.today(), max_stale_business_days=3)
    assert any(i.severity == "HALT" and i.check == "freshness" for i in issues)


def test_dq_freshness_halts_on_empty_panel():
    from trader.data_quality import check_freshness
    issues = check_freshness(pd.DataFrame(), asof=date.today())
    assert any(i.severity == "HALT" for i in issues)


def test_dq_extreme_jumps_catches_25pct_move_when_spy_calm():
    from trader.data_quality import check_extreme_jumps
    # SPY flat, AAPL up 25% — data error or massive idiosyncratic event
    px = pd.DataFrame({
        "AAPL": [100, 125],
        "SPY":  [100, 100.1],
    }, index=pd.bdate_range("2026-05-08", periods=2))
    issues = check_extreme_jumps(px)
    assert any(i.sym == "AAPL" and i.check == "extreme_jump" for i in issues)


def test_dq_extreme_jumps_no_flag_when_spy_also_moves():
    """Flash-crash day: AAPL -25%, SPY -22% — real move, no flag."""
    from trader.data_quality import check_extreme_jumps
    px = pd.DataFrame({
        "AAPL": [100, 75],
        "SPY":  [100, 78],
    }, index=pd.bdate_range("2026-05-08", periods=2))
    issues = check_extreme_jumps(px)
    aapl_issues = [i for i in issues if i.sym == "AAPL"]
    assert not aapl_issues


def test_dq_dead_zero_flags_zero_price():
    from trader.data_quality import check_dead_zeros
    px = pd.DataFrame({
        "AAPL": [100, 100, 0, 0, 0],  # 3 zeros in last 5 rows
    }, index=pd.bdate_range("2026-05-04", periods=5))
    issues = check_dead_zeros(px)
    assert any(i.sym == "AAPL" and i.check == "dead_zero" for i in issues)


def test_dq_should_halt_respects_env(monkeypatch):
    from trader.data_quality import QualityIssue, should_halt
    halt_issues = [QualityIssue("HALT", "X", "test", "fake")]
    monkeypatch.setenv("DATA_QUALITY_HALT_ENABLED", "1")
    assert should_halt(halt_issues)
    monkeypatch.setenv("DATA_QUALITY_HALT_ENABLED", "0")
    assert not should_halt(halt_issues)


def test_dq_format_issues_no_issues():
    from trader.data_quality import format_issues
    assert "all checks pass" in format_issues([])


# ============================================================
# 5. Quarterly review
# ============================================================
def test_quarterly_review_print_mode_outputs_assumptions(capsys):
    from quarterly_review import ASSUMPTIONS, print_review
    from datetime import datetime
    print_review(datetime(2026, 5, 10))
    out = capsys.readouterr().out
    assert "QUARTERLY ASSUMPTION REVIEW" in out
    for a in ASSUMPTIONS:
        assert a.key in out


def test_quarterly_review_log_to_journal_creates_table(tmp_path):
    """The log function should create the quarterly_reviews table."""
    from quarterly_review import log_to_journal
    from datetime import datetime
    db = tmp_path / "j.db"
    results = [{"key": "test_assumption", "status": "ack", "note": ""}]
    log_to_journal(datetime(2026, 5, 10), results, db)
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT n_ack, n_flag, n_skip FROM quarterly_reviews"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0] == (1, 0, 0)


def test_quarterly_review_acknowledges_all(tmp_path, capsys):
    from quarterly_review import main as qr_main
    from trader.config import DB_PATH
    db = tmp_path / "j.db"
    # Use --acknowledge-all to avoid interactive prompts
    rc = qr_main(["--acknowledge-all", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "blanket ack" in out


# ============================================================
# main.py wiring of data quality
# ============================================================
def test_main_imports_data_quality():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    assert "from .data_quality import" in txt
    assert "run_all_checks" in txt
    assert "should_halt" in txt
    assert '"halt_type": "data_quality"' in txt

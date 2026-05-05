"""Tests for v3.73.6 — SP500 benchmark-relative tracking."""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# compute_metrics — pure function, full coverage without network
# ============================================================
def _mk_snapshots(port_returns: list[float], bench_returns: list[float]):
    """Build snapshots from daily-return series. Both series start at
    100 nominal."""
    snaps = []
    p, b = 100.0, 100.0
    d = datetime.date(2026, 1, 1)
    snaps.append((d, p, b))
    for pr, br in zip(port_returns, bench_returns):
        d = d + datetime.timedelta(days=1)
        p *= (1 + pr); b *= (1 + br)
        snaps.append((d, p, b))
    return snaps


def test_metrics_returns_none_below_min_obs():
    from trader.benchmark_track import compute_metrics
    assert compute_metrics([]) is None
    assert compute_metrics([(datetime.date(2026, 1, 1), 100, 100)]) is None
    short = _mk_snapshots([0.01, 0.01], [0.01, 0.01])
    assert compute_metrics(short) is None


def test_metrics_active_return_when_winning():
    from trader.benchmark_track import compute_metrics
    # Portfolio gains 10%; SPY gains 5%. Active return = 5pp.
    snaps = _mk_snapshots([0.10] + [0]*9, [0.05] + [0]*9)
    m = compute_metrics(snaps)
    assert m is not None
    assert m.is_winning
    assert abs(m.active_return_pct - 5.0) < 0.5


def test_metrics_active_return_when_losing():
    from trader.benchmark_track import compute_metrics
    snaps = _mk_snapshots([0.02] + [0]*9, [0.10] + [0]*9)
    m = compute_metrics(snaps)
    assert m is not None
    assert not m.is_winning
    assert m.active_return_pct < 0


def test_metrics_beta_one_when_perfectly_correlated():
    """If portfolio = SPY exactly, beta should be 1.0 and IR ~0."""
    from trader.benchmark_track import compute_metrics
    rets = [0.01, -0.005, 0.02, 0.0, -0.01, 0.015, 0.0, 0.005, -0.01, 0.01]
    snaps = _mk_snapshots(rets, rets)
    m = compute_metrics(snaps)
    assert m is not None
    assert abs(m.beta - 1.0) < 1e-3
    assert abs(m.correlation - 1.0) < 1e-3
    # No active return → IR ~0 (or undefined; we return 0)
    assert abs(m.information_ratio) < 1e-3


def test_metrics_beta_two_when_amplified():
    """Portfolio = 2 × SPY. Beta should be 2."""
    from trader.benchmark_track import compute_metrics
    spy_rets = [0.01, -0.005, 0.02, 0.0, -0.01, 0.015, 0.0, 0.005, -0.01, 0.01]
    port_rets = [r * 2 for r in spy_rets]
    snaps = _mk_snapshots(port_rets, spy_rets)
    m = compute_metrics(snaps)
    assert m is not None
    assert abs(m.beta - 2.0) < 1e-2


def test_metrics_negative_alpha_for_underperformer():
    """Beta-1 portfolio that consistently lags SPY by 0.5pp daily →
    negative alpha. Use noisy returns so beta is well-defined."""
    from trader.benchmark_track import compute_metrics
    import random
    random.seed(42)
    spy_rets = [random.gauss(0.001, 0.012) for _ in range(60)]
    # Portfolio = SPY return - 0.5pp/day. Same beta, lower mean.
    port_rets = [r - 0.005 for r in spy_rets]
    snaps = _mk_snapshots(port_rets, spy_rets)
    m = compute_metrics(snaps)
    assert m is not None
    assert abs(m.beta - 1.0) < 0.05, f"beta should be ~1.0, got {m.beta}"
    assert m.alpha_annualized < 0, f"alpha should be negative, got {m.alpha_annualized}"


def test_metrics_handles_zero_volatility_safely():
    """Constant returns → variance 0; must not divide-by-zero."""
    from trader.benchmark_track import compute_metrics
    snaps = _mk_snapshots([0.0]*10, [0.0]*10)
    m = compute_metrics(snaps)
    assert m is not None
    # No vol → IR/beta defined as 0 by convention
    assert m.information_ratio == 0
    assert m.beta == 0


def test_metrics_winrate_is_50_pct_for_zero_active():
    from trader.benchmark_track import compute_metrics
    rets = [0.01, -0.01] * 5
    snaps = _mk_snapshots(rets, rets)
    m = compute_metrics(snaps)
    # Active is identically zero → no day strictly > → win_rate=0
    assert m.win_rate == 0


# ============================================================
# nav_series_for_chart
# ============================================================
def test_nav_series_normalizes_both_to_100():
    from trader.benchmark_track import nav_series_for_chart
    snaps = _mk_snapshots([0.1, 0.05], [0.05, 0.0])
    dates, port, spy = nav_series_for_chart(snaps)
    assert len(dates) == len(port) == len(spy) == 3
    # Both start at 100
    assert abs(port[0] - 100.0) < 1e-6
    assert abs(spy[0] - 100.0) < 1e-6
    # Port grew 10% then 5% → 115.5
    assert abs(port[-1] - 115.5) < 1e-3
    # SPY grew 5% then 0 → 105
    assert abs(spy[-1] - 105.0) < 1e-3


def test_nav_series_handles_empty():
    from trader.benchmark_track import nav_series_for_chart
    d, p, s = nav_series_for_chart([])
    assert d == [] and p == [] and s == []


# ============================================================
# Persistence — round-trip with temp DB
# ============================================================
def test_load_snapshots_filters_zero_spy(tmp_path):
    from trader.benchmark_track import load_snapshots
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE daily_snapshot (date TEXT PRIMARY KEY, "
                "equity REAL, cash REAL, positions_json TEXT, "
                "benchmark_spy_close REAL)")
    con.executemany(
        "INSERT INTO daily_snapshot VALUES (?, ?, ?, ?, ?)",
        [("2026-04-01", 100000, 0, "{}", 700.0),
         ("2026-04-02", 101000, 0, "{}", 0.0),  # filtered (no spy)
         ("2026-04-03", 102000, 0, "{}", 705.0)],
    )
    con.commit(); con.close()
    snaps = load_snapshots(db_path=db)
    assert len(snaps) == 2
    assert snaps[0][0] == datetime.date(2026, 4, 1)
    assert snaps[1][0] == datetime.date(2026, 4, 3)


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_has_benchmark_panel():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "def _render_benchmark_panel" in text


def test_overview_invokes_benchmark_panel():
    """The benchmark panel must actually be called from view_overview
    — without this it's dead code."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    view_idx = text.index("def view_overview")
    next_def = text.index("\ndef ", view_idx + 1)
    body = text[view_idx:next_def]
    assert "_render_benchmark_panel()" in body


def test_benchmark_panel_renders_chart():
    """Chart is the headline; without it the panel is just numbers."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_benchmark_panel")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "line_chart" in body
    assert "nav_series_for_chart" in body


def test_benchmark_panel_surfaces_required_kpis():
    """Active return, IR, beta, alpha — the 4 metrics an allocator
    asks about."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_benchmark_panel")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "active_return_pct" in body
    assert "information_ratio" in body
    assert "beta" in body
    assert "alpha" in body.lower()


def test_benchmark_panel_warns_on_small_sample():
    """Don't let the operator anchor on noisy short-window IR/alpha."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_benchmark_panel")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "30" in body  # the threshold
    assert "statistically" in body.lower() or "sample" in body.lower()


def test_dashboard_version_v3_73_6():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "v3.73.6" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text)

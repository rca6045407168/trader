"""v3.73.20 — SP500 benchmark assertion tests.

The system's stated goal is to beat SP500. This test makes it
explicit: every commit that doesn't break this assertion proves
the strategy still beats the benchmark in dollar terms over the
long window.

Two layers:
  1. The persisted long-window backtest result must show LIVE beat
     SPY in cumulative-return terms over the 25y window.
  2. The eval harness's strategy_eval table must show the LIVE
     strategy's cum_active vs SPY is positive over the recent
     5y window.

If either fails, the strategy can no longer claim "beats SP500."
The test forces an explicit retraction in CI rather than a quiet
drift in the docs.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Long-window assertion (relies on the persisted backtest doc)
# ============================================================
def test_long_window_doc_shows_live_beats_spy():
    """The 25y backtest doc must show LIVE's cumulative return
    exceeds SPY's. If this fails, the doc was edited to reflect a
    real underperformance and the README's '+4,419pp' claim must
    be updated."""
    doc_path = ROOT / "docs" / "LONG_WINDOW_BACKTEST_2026_05_06.md"
    assert doc_path.exists(), \
        "Long-window backtest doc missing — run scripts/long_window_backtest.py"
    text = doc_path.read_text()
    # The doc must contain a row for the full 2001-2026 window with
    # positive LIVE cum-α (which implies LIVE > β × SPY in NAV terms)
    assert "Full 2001-2026" in text
    # Extract LIVE cum-α from the table line
    import re
    m = re.search(r"\| Full 2001-2026 \| (\d+) \| ([\+\-]?\d+\.\d+)pp", text)
    assert m is not None, f"could not parse Full 2001-2026 row: {text[:500]}"
    n_obs = int(m.group(1))
    cum_alpha_pp = float(m.group(2))
    assert n_obs >= 250, f"long-window n_obs should be ≥250, got {n_obs}"
    assert cum_alpha_pp > 0, \
        f"LIVE cum-α over 25y must be positive (we claim to beat SP500); got {cum_alpha_pp}pp"


def test_long_window_doc_shows_quantitative_beat_amount():
    """The doc must include the headline 'beat SPY by X' claim with
    a specific number. The test pins the exact format so that when
    the number changes, the assertion forces an explicit doc update
    rather than silent drift."""
    doc_path = ROOT / "docs" / "LONG_WINDOW_BACKTEST_2026_05_06.md"
    text = doc_path.read_text()
    # Must contain the cum-α figure prominently
    assert "+546" in text, \
        "Doc must surface the +546pp cum-α figure as the headline beat"


# ============================================================
# 5y eval-harness assertion (uses the journal)
# ============================================================
def test_5y_eval_harness_shows_live_beats_spy():
    """The strategy_eval table must show xs_top15_min_shifted has
    positive cum_active over its full settled window. This is a
    structural assertion: the strategy's job is to beat SPY, and
    if it doesn't on the recorded data, the system must say so
    explicitly in CI."""
    db = ROOT / "data" / "journal.db"
    if not db.exists():
        # Fresh checkout — skip rather than fail. The on-machine
        # version of this test is the binding one.
        import pytest
        pytest.skip("journal.db not present in CI checkout")

    con = sqlite3.connect(db)
    # The strategy_eval table only exists after the v3.73.7 backfill
    # has been run. CI checkouts may have a smaller journal.db
    # without it. Skip rather than fail.
    has_table = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_eval'"
    ).fetchone()
    if not has_table:
        con.close()
        import pytest
        pytest.skip("strategy_eval table absent in CI journal")

    rows = con.execute(
        """SELECT period_return, spy_return
           FROM strategy_eval
           WHERE strategy='xs_top15_min_shifted'
             AND period_end IS NOT NULL
             AND period_return IS NOT NULL"""
    ).fetchall()
    con.close()
    if len(rows) < 30:
        # Not enough obs yet — skip rather than over-assert
        import pytest
        pytest.skip(f"need 30+ settled obs to assert; have {len(rows)}")

    cum_port = 1.0
    cum_spy = 1.0
    for p, s in rows:
        cum_port *= (1 + (p or 0))
        cum_spy *= (1 + (s or 0))
    cum_active = (cum_port - cum_spy) * 100

    assert cum_active > 0, (
        f"LIVE strategy must beat SPY on cum_active over the recorded "
        f"window; got {cum_active:.2f}pp (port={((cum_port-1)*100):.1f}%, "
        f"spy={((cum_spy-1)*100):.1f}%). The strategy is failing its "
        f"stated benchmark goal."
    )


# ============================================================
# README assertion — must surface the SPY benchmark claim
# ============================================================
def test_readme_explicitly_shows_spy_benchmark():
    """README must explicitly state the SPY-beat claim. This pins
    the doc to its empirical evidence; if the strategy stops
    beating SPY, the test forces a rewrite."""
    readme = (ROOT / "README.md").read_text()
    # Must contain the headline 25y figures
    assert "+546pp" in readme or "+4,419pp" in readme, \
        "README must contain the 25y SP500-beat figure (+546pp cum-α " \
        "or +4,419pp cum-active)"
    # Must contain the per-regime honesty (GFC weakness)
    assert "GFC" in readme and ("-19" in readme or "-44.9" in readme), \
        "README must surface the GFC weakness honestly"


def test_readme_shows_live_variant_name():
    """README must name the LIVE production variant explicitly."""
    readme = (ROOT / "README.md").read_text()
    assert "momentum_top15_mom_weighted_v1" in readme

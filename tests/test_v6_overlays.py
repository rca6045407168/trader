"""v6.0.x overlay tests — HIFO, quality tilt, drawdown gross scalar.

The vol-target overlay (Moreira–Muir) was already tested in
test_v3_73_17_sizing.py. This file covers the four NEW overlays
added in v6:

  1. HIFO lot selection in close_lots()
  2. close_lots_auto() env-driven selection (default HIFO in v6)
  3. quality_tilted_targets() — Novy-Marx tilt
  4. drawdown_gross_scalar() — Asness 2014 conservative version
  5. plan_tlh(quality_tilt=...) wiring
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# 1. HIFO close_lots — strict lot ordering
# ============================================================
def _seed_three_lots_at_different_costs(monkeypatch, tmp_path):
    """Helper: monkeypatch DB_PATH and seed 3 open lots."""
    db = tmp_path / "journal.db"
    monkeypatch.setattr("trader.config.DB_PATH", db)
    # Re-import journal to pick up patched DB_PATH
    import importlib, trader.journal
    importlib.reload(trader.journal)
    j = trader.journal
    j.init_db()
    # Three lots: $100, $200, $150 (high, low, mid)
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=100.0)
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=200.0)
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=150.0)
    return db, j


def test_close_lots_fifo_takes_oldest(monkeypatch, tmp_path):
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    # Close 1 share at $180 — FIFO takes the first inserted ($100 cost)
    closed = j.close_lots("AAPL", "TEST", qty=1.0, close_price=180.0,
                            selection="FIFO")
    assert len(closed) == 1
    # Realized = ($180 - $100) * 1 = $80
    assert abs(closed[0]["realized_pnl"] - 80.0) < 1e-9


def test_close_lots_hifo_takes_most_expensive(monkeypatch, tmp_path):
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    # Close 1 share at $180 — HIFO takes the $200 cost lot (MAX LOSS)
    closed = j.close_lots("AAPL", "TEST", qty=1.0, close_price=180.0,
                            selection="HIFO")
    assert len(closed) == 1
    # Realized = ($180 - $200) * 1 = -$20 (a LOSS)
    assert abs(closed[0]["realized_pnl"] - (-20.0)) < 1e-9


def test_close_lots_hifo_yields_more_loss_than_fifo(monkeypatch, tmp_path):
    """The whole point: at any given close price, HIFO realizes more
    loss (or less gain) than FIFO. This is THE multiplier."""
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    fifo = j.close_lots("AAPL", "TEST", qty=1.0, close_price=180.0,
                         selection="FIFO")[0]["realized_pnl"]
    # reset and try HIFO
    db.unlink()
    j.init_db()
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=100.0)
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=200.0)
    j.open_lot("AAPL", "TEST", qty=1.0, open_price=150.0)
    hifo = j.close_lots("AAPL", "TEST", qty=1.0, close_price=180.0,
                         selection="HIFO")[0]["realized_pnl"]
    # HIFO < FIFO when close price is below max-cost lot
    assert hifo < fifo
    assert abs(fifo - 80.0) < 1e-9 and abs(hifo - (-20.0)) < 1e-9


def test_close_lots_auto_defaults_to_hifo(monkeypatch, tmp_path):
    """v6 default: TLH_LOT_SELECTION unset → HIFO."""
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    monkeypatch.delenv("TLH_LOT_SELECTION", raising=False)
    closed = j.close_lots_auto("AAPL", "TEST", qty=1.0, close_price=180.0)
    # HIFO would pick the $200 lot → realized = -$20
    assert abs(closed[0]["realized_pnl"] - (-20.0)) < 1e-9


def test_close_lots_auto_honors_fifo_env(monkeypatch, tmp_path):
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    monkeypatch.setenv("TLH_LOT_SELECTION", "FIFO")
    closed = j.close_lots_auto("AAPL", "TEST", qty=1.0, close_price=180.0)
    # FIFO picks the $100 lot → realized = +$80
    assert abs(closed[0]["realized_pnl"] - 80.0) < 1e-9


def test_close_lots_fifo_legacy_wrapper_unchanged(monkeypatch, tmp_path):
    """Existing callers using close_lots_fifo() still get FIFO behavior."""
    db, j = _seed_three_lots_at_different_costs(monkeypatch, tmp_path)
    closed = j.close_lots_fifo("AAPL", "TEST", qty=1.0, close_price=180.0)
    assert abs(closed[0]["realized_pnl"] - 80.0) < 1e-9  # FIFO


# ============================================================
# 2. Quality-tilted basket
# ============================================================
def test_quality_tilted_zero_strength_equals_cap_weight():
    """tilt_strength=0 → identical to cap_weighted_targets."""
    from trader.direct_index_tlh import (
        quality_tilted_targets, cap_weighted_targets
    )
    universe = ["AAPL", "MSFT", "JPM", "BAC", "XOM"]
    cap = cap_weighted_targets(universe, gross=1.0)
    q = quality_tilted_targets(universe, gross=1.0, tilt_strength=0)
    for t in universe:
        assert abs(cap[t] - q[t]) < 1e-9


def test_quality_tilted_skews_toward_high_quality():
    """At tilt=1, AAPL (q=1.45) should gain weight vs INTC (q=0.75)."""
    from trader.direct_index_tlh import (
        quality_tilted_targets, cap_weighted_targets
    )
    universe = ["AAPL", "INTC"]
    cap = cap_weighted_targets(universe, gross=1.0)
    q = quality_tilted_targets(universe, gross=1.0, tilt_strength=1.0)
    # AAPL has weight UP, INTC weight DOWN, relative to pure cap-weight
    assert q["AAPL"] > cap["AAPL"]
    assert q["INTC"] < cap["INTC"]


def test_quality_tilted_sums_to_target_gross():
    from trader.direct_index_tlh import quality_tilted_targets
    universe = ["AAPL", "MSFT", "JPM", "BAC", "XOM"]
    out = quality_tilted_targets(universe, gross=0.70, tilt_strength=0.5)
    assert abs(sum(out.values()) - 0.70) < 1e-9


def test_quality_tilted_clamps_strength():
    from trader.direct_index_tlh import quality_tilted_targets
    universe = ["AAPL", "MSFT"]
    # negative strength
    out_neg = quality_tilted_targets(universe, gross=1.0, tilt_strength=-1)
    out_zero = quality_tilted_targets(universe, gross=1.0, tilt_strength=0)
    for t in universe:
        assert abs(out_neg[t] - out_zero[t]) < 1e-9
    # >1 strength
    out_two = quality_tilted_targets(universe, gross=1.0, tilt_strength=2.0)
    out_one = quality_tilted_targets(universe, gross=1.0, tilt_strength=1.0)
    for t in universe:
        assert abs(out_two[t] - out_one[t]) < 1e-9


def test_quality_score_coverage_matches_universe():
    """Every ticker in REPLACEMENT_MAP should have a QUALITY_SCORE
    (or we'd silently default to 1.0 — defensible but worth checking)."""
    from trader.direct_index_tlh import REPLACEMENT_MAP, QUALITY_SCORES
    missing = [t for t in REPLACEMENT_MAP if t not in QUALITY_SCORES]
    assert missing == [], f"missing quality scores: {missing}"


# ============================================================
# 3. Drawdown gross scalar
# ============================================================
def test_drawdown_scalar_no_dd_returns_one():
    from trader.direct_index_tlh import drawdown_gross_scalar
    assert drawdown_gross_scalar(0.0) == 1.0
    assert drawdown_gross_scalar(-0.01) == 1.0
    assert drawdown_gross_scalar(-0.04) == 1.0


def test_drawdown_scalar_max_dd_returns_floor():
    from trader.direct_index_tlh import drawdown_gross_scalar
    assert abs(drawdown_gross_scalar(-0.10) - 0.70) < 1e-9
    assert abs(drawdown_gross_scalar(-0.20) - 0.70) < 1e-9


def test_drawdown_scalar_linear_taper():
    """At -7.5% drawdown (midpoint of [-5, -10] band), scalar = 0.85."""
    from trader.direct_index_tlh import drawdown_gross_scalar
    s = drawdown_gross_scalar(-0.075)
    assert abs(s - 0.85) < 1e-9


def test_drawdown_scalar_never_above_one():
    from trader.direct_index_tlh import drawdown_gross_scalar
    # Even with positive DD (gain above HWM, which shouldn't happen
    # but just in case), the function returns 1.0
    assert drawdown_gross_scalar(0.05) == 1.0


def test_drawdown_scalar_custom_band():
    from trader.direct_index_tlh import drawdown_gross_scalar
    # Tighter band: reduce gross from -3% to -8% drawdown
    s = drawdown_gross_scalar(-0.055, reduce_band=(-0.03, -0.08), floor=0.50)
    # Midpoint of [-0.03, -0.08] = -0.055; expect midpoint of [1.0, 0.50] = 0.75
    assert abs(s - 0.75) < 1e-9


# ============================================================
# 4. plan_tlh integration with quality tilt
# ============================================================
def test_plan_tlh_with_quality_tilt(tmp_path):
    from trader.direct_index_tlh import plan_tlh
    universe = ["AAPL", "MSFT", "JPM", "BAC", "XOM"]
    db = tmp_path / "empty.db"
    plan = plan_tlh(
        universe=universe,
        current_prices=None,
        core_pct=0.70,
        db_path=db,
        quality_tilt=0.5,
    )
    # No prices → no swaps, but target_weights should be quality-tilted
    assert len(plan.target_weights) == len(universe)
    assert abs(sum(plan.target_weights.values()) - 0.70) < 1e-9
    # AAPL (q=1.45) should weight higher than its pure cap proportion
    note_text = " ".join(plan.notes)
    assert "quality tilt" in note_text


def test_plan_tlh_no_quality_tilt_unchanged(tmp_path):
    """Default behavior (quality_tilt=0) preserves v6.0.0 cap-weight."""
    from trader.direct_index_tlh import plan_tlh, cap_weighted_targets
    universe = ["AAPL", "MSFT", "JPM"]
    db = tmp_path / "empty.db"
    plan = plan_tlh(
        universe=universe,
        current_prices=None,
        core_pct=0.70,
        db_path=db,
    )
    cap = cap_weighted_targets(universe, gross=0.70)
    for t in universe:
        assert abs(plan.target_weights[t] - cap[t]) < 1e-9


# ============================================================
# 5. Default-on env confirmations (v6 behavior changes)
# ============================================================
def test_vol_target_default_is_on():
    """v6: VOL_TARGET_ENABLED unset → main.py reads as '1' (on)."""
    from pathlib import Path as _P
    txt = _P(__file__).resolve().parent.parent.joinpath(
        "src/trader/main.py"
    ).read_text()
    # The relevant line should default to "1"
    assert 'os.environ.get("VOL_TARGET_ENABLED", "1")' in txt


def test_hifo_default_is_on():
    """v6: TLH_LOT_SELECTION unset → close_lots_auto uses HIFO."""
    import os, importlib, trader.journal
    importlib.reload(trader.journal)
    # If unset, default is HIFO (verify by looking at source)
    from pathlib import Path as _P
    txt = _P(trader.journal.__file__).read_text()
    assert "TLH_LOT_SELECTION" in txt
    # The default in close_lots_auto should be 'HIFO'
    assert '_os.environ.get("TLH_LOT_SELECTION", "HIFO")' in txt


def test_drawdown_aware_default_is_on():
    from pathlib import Path as _P
    txt = _P(__file__).resolve().parent.parent.joinpath(
        "src/trader/main.py"
    ).read_text()
    assert 'os.environ.get(\n        "DRAWDOWN_AWARE_ENABLED", "1"' in txt or \
           'os.environ.get("DRAWDOWN_AWARE_ENABLED", "1")' in txt

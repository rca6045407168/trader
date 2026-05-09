"""Tests for the v5.0.0 multi-strategy auto-router.

The auto-router is load-bearing: it's how the LIVE slot is decided on
every rebalance, and V5_DISPOSITION §3's exit criteria depend on it
firing correctly. These tests exercise:

  - Eligibility filter (min-evidence, max-β, max-DD, exclusion list)
  - Hysteresis behavior (incumbent kept when within margin)
  - "No eligible candidate" halt path
  - Journal round-trip (render_decision_for_journal -> _load_incumbent)

The leaderboard is monkeypatched in each test so the auto-router
runs against a known input, not against live journal state.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ALPACA_API_KEY", "test")


def _row(name: str, alpha_ir: float, beta: float = 0.9, n_obs: int = 6,
          cum_alpha: float = 5.0, max_dd: float = -10.0) -> dict:
    """Helper: build a fake leaderboard row with sensible defaults."""
    return {
        "strategy": name,
        "n_obs": n_obs,
        "cum_active_pct": cum_alpha + 5.0,
        "cum_port_pct": 0.0,
        "cum_spy_pct": 0.0,
        "mean_active_pct": 0.0,
        "win_rate": 0.5,
        "ir": alpha_ir,
        "beta": beta,
        "cum_alpha_pct": cum_alpha,
        "alpha_ann_pct": 0.0,
        "alpha_ir": alpha_ir,
        "max_relative_dd_pct": max_dd,
    }


# ============================================================
# Eligibility filter
# ============================================================
def test_eligibility_excludes_long_short_momentum(monkeypatch):
    """long_short_momentum is on the INELIGIBLE_LIVE_CANDIDATES list
    because it has no short-cost modeling."""
    from trader.auto_router import select_live, INELIGIBLE_LIVE_CANDIDATES
    assert "long_short_momentum" in INELIGIBLE_LIVE_CANDIDATES

    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [_row("long_short_momentum", alpha_ir=10.0)],
    )
    d = select_live(incumbent=None)
    assert d.selected is None
    assert d.eligible_count == 0
    assert "no candidate cleared" in d.reason


def test_eligibility_excludes_passive_baselines(monkeypatch):
    """All buy_and_hold_* + boglehead + simple_60_40 + equal_weight_sp500
    are excluded — they don't need an active orchestrator slot."""
    from trader.auto_router import INELIGIBLE_LIVE_CANDIDATES, select_live
    for name in ("buy_and_hold_spy", "buy_and_hold_qqq", "buy_and_hold_mtum",
                 "buy_and_hold_schg", "buy_and_hold_vug", "buy_and_hold_xlk",
                 "equal_weight_sp500", "boglehead_three_fund", "simple_60_40"):
        assert name in INELIGIBLE_LIVE_CANDIDATES, f"{name} should be excluded"

    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [_row("buy_and_hold_spy", alpha_ir=10.0)],
    )
    d = select_live(incumbent=None)
    assert d.selected is None


def test_eligibility_rejects_insufficient_evidence(monkeypatch):
    """Strategies with < MIN_EVIDENCE_MONTHS observations are excluded."""
    from trader.auto_router import select_live, MIN_EVIDENCE_MONTHS
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("xs_top15", alpha_ir=2.0, n_obs=MIN_EVIDENCE_MONTHS - 1),
        ],
    )
    d = select_live(incumbent=None)
    assert d.selected is None
    assert d.eligible_count == 0


def test_eligibility_rejects_high_beta(monkeypatch):
    """β > MAX_BETA is excluded."""
    from trader.auto_router import select_live, MAX_BETA
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("xs_top15", alpha_ir=2.0, beta=MAX_BETA + 0.05),
        ],
    )
    d = select_live(incumbent=None)
    assert d.selected is None


def test_eligibility_rejects_severe_dd(monkeypatch):
    """max_relative_dd_pct < MIN_DD_PCT is excluded."""
    from trader.auto_router import select_live, MIN_DD_PCT
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("xs_top15", alpha_ir=2.0, max_dd=MIN_DD_PCT - 1.0),
        ],
    )
    d = select_live(incumbent=None)
    assert d.selected is None


# ============================================================
# Selection (eligible, sorted by IR)
# ============================================================
def test_select_picks_highest_alpha_ir(monkeypatch):
    """Among eligibles, pick the one with the highest alpha_ir."""
    from trader.auto_router import select_live
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("xs_top15", alpha_ir=0.5),
            _row("vertical_winner", alpha_ir=2.5),
            _row("naive_top15_12mo_return", alpha_ir=1.5),
        ],
    )
    d = select_live(incumbent=None)
    assert d.selected == "vertical_winner"
    assert d.eligible_count == 3
    assert d.runner_up == "naive_top15_12mo_return"
    assert d.hysteresis_applied is False


# ============================================================
# Hysteresis
# ============================================================
def test_hysteresis_keeps_incumbent_within_margin(monkeypatch):
    """If incumbent still eligible and within HYSTERESIS_MARGIN of the
    new winner, keep incumbent."""
    from trader.auto_router import select_live, HYSTERESIS_MARGIN
    # winner beats incumbent by less than the margin — keep incumbent
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("naive_top15_12mo_return", alpha_ir=1.50),
            _row("vertical_winner", alpha_ir=1.50 + HYSTERESIS_MARGIN / 2),
        ],
    )
    d = select_live(incumbent="naive_top15_12mo_return")
    assert d.selected == "naive_top15_12mo_return"
    assert d.hysteresis_applied is True
    assert d.runner_up == "vertical_winner"


def test_hysteresis_promotes_when_margin_exceeded(monkeypatch):
    """If winner beats incumbent by MORE than HYSTERESIS_MARGIN,
    promote winner."""
    from trader.auto_router import select_live, HYSTERESIS_MARGIN
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("naive_top15_12mo_return", alpha_ir=1.0),
            _row("vertical_winner", alpha_ir=1.0 + HYSTERESIS_MARGIN + 0.05),
        ],
    )
    d = select_live(incumbent="naive_top15_12mo_return")
    assert d.selected == "vertical_winner"
    assert d.hysteresis_applied is False


def test_hysteresis_no_op_when_incumbent_is_winner(monkeypatch):
    """Incumbent is the winner anyway — no hysteresis needed."""
    from trader.auto_router import select_live
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("vertical_winner", alpha_ir=2.0),
            _row("xs_top15", alpha_ir=1.0),
        ],
    )
    d = select_live(incumbent="vertical_winner")
    assert d.selected == "vertical_winner"
    assert d.hysteresis_applied is False


def test_hysteresis_promotes_when_incumbent_no_longer_eligible(monkeypatch):
    """If the incumbent fails the eligibility filter (e.g., β cap
    breach), it can't be kept — promote the winner."""
    from trader.auto_router import select_live, MAX_BETA
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            # Incumbent now ineligible (β too high)
            _row("naive_top15_12mo_return", alpha_ir=2.0, beta=MAX_BETA + 0.1),
            # Eligible challenger
            _row("vertical_winner", alpha_ir=1.0),
        ],
    )
    d = select_live(incumbent="naive_top15_12mo_return")
    assert d.selected == "vertical_winner"
    # Hysteresis didn't apply because incumbent isn't in the eligible list
    assert d.hysteresis_applied is False


# ============================================================
# Halt path (no eligible candidate)
# ============================================================
def test_no_eligible_returns_None_with_diagnostic(monkeypatch):
    """When the filter rejects everything, return selected=None
    with a reason that mentions the V5_DISPOSITION §3.1 trigger."""
    from trader.auto_router import select_live
    monkeypatch.setattr(
        "trader.auto_router.leaderboard",
        lambda days_back: [
            _row("xs_top15", alpha_ir=2.0, n_obs=2),  # n_obs too low
        ],
    )
    d = select_live(incumbent=None)
    assert d.selected is None
    assert d.eligible_count == 0
    assert "V5_DISPOSITION §3.1" in d.reason


def test_empty_leaderboard_returns_None(monkeypatch):
    """Edge case: leaderboard returns no rows at all."""
    from trader.auto_router import select_live
    monkeypatch.setattr("trader.auto_router.leaderboard", lambda days_back: [])
    d = select_live(incumbent=None)
    assert d.selected is None
    assert d.eligible_count == 0


# ============================================================
# Journal round-trip
# ============================================================
def test_render_decision_for_journal_with_selection():
    """The serialized form must include LIVE_AUTO=<name> so the next
    run's _load_incumbent() can read it back."""
    from trader.auto_router import RouterDecision, render_decision_for_journal
    d = RouterDecision(
        selected="vertical_winner",
        reason="...",
        eligible_count=4,
        runner_up="naive_top15_12mo_return",
        hysteresis_applied=False,
        incumbent=None,
    )
    s = render_decision_for_journal(d)
    assert s.startswith("LIVE_AUTO=vertical_winner")
    assert "hyst=N" in s
    assert "eligible=4" in s
    assert "runner_up=naive_top15_12mo_return" in s


def test_render_decision_for_journal_when_no_selection():
    """When selected is None, journal token must reflect that."""
    from trader.auto_router import RouterDecision, render_decision_for_journal
    d = RouterDecision(
        selected=None,
        reason="no candidate cleared the eligibility filter",
        eligible_count=0,
    )
    s = render_decision_for_journal(d)
    assert s.startswith("LIVE_AUTO=NONE")
    assert "eligible=0" in s


def test_render_decision_marks_hysteresis_when_applied():
    """Hysteresis token in journal payload."""
    from trader.auto_router import RouterDecision, render_decision_for_journal
    d = RouterDecision(
        selected="naive_top15_12mo_return",
        reason="...",
        eligible_count=2,
        runner_up="vertical_winner",
        hysteresis_applied=True,
        incumbent="naive_top15_12mo_return",
    )
    s = render_decision_for_journal(d)
    assert "hyst=Y" in s


def test_load_incumbent_reads_back_LIVE_AUTO_token(tmp_path, monkeypatch):
    """The full round-trip: render -> write to runs.notes -> load_incumbent
    reads it back."""
    import sqlite3
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "completed_at TEXT, status TEXT NOT NULL, notes TEXT)"
    )
    con.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
        (
            "2026-05-08-test-1",
            "2026-05-08T10:00:00",
            "2026-05-08T10:05:00",
            "completed",
            "15 targets LIVE_AUTO=vertical_winner hyst=N eligible=4",
        ),
    )
    con.commit()
    con.close()

    monkeypatch.setattr("trader.auto_router.DB_PATH", db)
    from trader.auto_router import _load_incumbent
    assert _load_incumbent() == "vertical_winner"


def test_load_incumbent_returns_None_when_no_prior_run(tmp_path, monkeypatch):
    """First run (no prior LIVE_AUTO token in journal) returns None."""
    import sqlite3
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "completed_at TEXT, status TEXT NOT NULL, notes TEXT)"
    )
    con.commit()
    con.close()

    monkeypatch.setattr("trader.auto_router.DB_PATH", db)
    from trader.auto_router import _load_incumbent
    assert _load_incumbent() is None


def test_load_incumbent_handles_legacy_notes_without_token(tmp_path, monkeypatch):
    """Older runs from before v5.0.0 won't have the LIVE_AUTO token.
    _load_incumbent must return None, not raise."""
    import sqlite3
    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "completed_at TEXT, status TEXT NOT NULL, notes TEXT)"
    )
    con.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
        (
            "2026-04-30-legacy",
            "2026-04-30T10:00:00",
            None,
            "completed",
            "15 targets, 5 mom, 0 bot",  # v4-era notes; no LIVE_AUTO token
        ),
    )
    con.commit()
    con.close()

    monkeypatch.setattr("trader.auto_router.DB_PATH", db)
    from trader.auto_router import _load_incumbent
    assert _load_incumbent() is None

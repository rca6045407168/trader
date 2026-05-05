"""Tests for v3.69.0 — ReactorSignalRule wires reactor signals into
the rebalance gate.

Covers status state machine, direction/materiality/recency gates,
the trim ceiling (never to 0), env config, INERT/SHADOW/LIVE
behavior, and the main.py wiring.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Module structure + status state machine
# ============================================================
def test_module_imports():
    from trader.reactor_rule import (
        ReactorSignalRule, TrimDecision,
        TRIM_DIRECTIONS, TRIM_SURPRISE_DIRECTIONS,
    )
    assert "BEARISH" in TRIM_DIRECTIONS
    assert "SURPRISE" in TRIM_DIRECTIONS
    assert "MISSED" in TRIM_SURPRISE_DIRECTIONS


def test_default_status_is_shadow(monkeypatch):
    """Safe default — never silently auto-trims on first install."""
    monkeypatch.delenv("REACTOR_RULE_STATUS", raising=False)
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule().status() == "SHADOW"


def test_status_is_env_overridable(monkeypatch):
    from trader.reactor_rule import ReactorSignalRule
    for s in ("LIVE", "SHADOW", "INERT"):
        monkeypatch.setenv("REACTOR_RULE_STATUS", s)
        assert ReactorSignalRule().status() == s
    # Lowercase normalized to upper
    monkeypatch.setenv("REACTOR_RULE_STATUS", "live")
    assert ReactorSignalRule().status() == "LIVE"


def test_default_min_materiality_is_4(monkeypatch):
    """M3 = worth a PM's attention. M4 = warrants position adjustment.
    Default to M4 so we don't trim on borderline events."""
    monkeypatch.delenv("REACTOR_TRIM_MIN_MATERIALITY", raising=False)
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule().min_materiality == 4


def test_min_materiality_clamped_into_band(monkeypatch):
    """Out-of-range values clamp to [1, 5] instead of raising."""
    from trader.reactor_rule import ReactorSignalRule
    monkeypatch.setenv("REACTOR_TRIM_MIN_MATERIALITY", "99")
    assert ReactorSignalRule().min_materiality == 5
    monkeypatch.setenv("REACTOR_TRIM_MIN_MATERIALITY", "0")
    assert ReactorSignalRule().min_materiality == 1
    monkeypatch.setenv("REACTOR_TRIM_MIN_MATERIALITY", "garbage")
    assert ReactorSignalRule().min_materiality == 4


def test_default_trim_pct_is_50(monkeypatch):
    monkeypatch.delenv("REACTOR_TRIM_PCT", raising=False)
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule().trim_to_pct == 0.5


def test_trim_pct_floor_protects_from_full_exit(monkeypatch):
    """Even with REACTOR_TRIM_PCT=0, we floor at 0.1 — a single AI
    flag should never cause a full exit. Belt-and-suspenders against
    typos like REACTOR_TRIM_PCT=0.0."""
    from trader.reactor_rule import ReactorSignalRule
    monkeypatch.setenv("REACTOR_TRIM_PCT", "0")
    assert ReactorSignalRule().trim_to_pct == 0.1
    monkeypatch.setenv("REACTOR_TRIM_PCT", "1.5")
    assert ReactorSignalRule().trim_to_pct == 1.0


def test_default_lookback_is_14_days(monkeypatch):
    monkeypatch.delenv("REACTOR_TRIM_LOOKBACK_DAYS", raising=False)
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule().lookback_days == 14


# ============================================================
# Direction / materiality gates
# ============================================================
def _bare_signal(**overrides):
    """Synthesize a signal dict shaped like a row from earnings_signals."""
    base = {
        "symbol": "X", "accession": "A1", "filed_at": "2026-05-04",
        "materiality": 4, "direction": "BEARISH",
        "surprise_direction": "NONE", "summary": "test",
        "error": None,
    }
    base.update(overrides)
    return base


def test_bearish_m4_is_trim_worthy():
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(_bare_signal()) is True


def test_bullish_m5_does_not_trigger_trim():
    """Critical: BULLISH never triggers an auto-adjustment. Boost
    decisions stay with the human per the article's universal pattern."""
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(
        _bare_signal(direction="BULLISH", materiality=5)) is False


def test_neutral_m5_does_not_trigger_trim():
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(
        _bare_signal(direction="NEUTRAL", materiality=5)) is False


def test_surprise_with_missed_triggers_trim():
    """SURPRISE direction is ambiguous unless surprise_direction is MISSED."""
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(_bare_signal(
        direction="SURPRISE", surprise_direction="MISSED",
        materiality=4)) is True


def test_surprise_with_beat_does_not_trigger_trim():
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(_bare_signal(
        direction="SURPRISE", surprise_direction="BEAT",
        materiality=5)) is False


def test_below_threshold_is_not_trim_worthy(monkeypatch):
    monkeypatch.setenv("REACTOR_TRIM_MIN_MATERIALITY", "4")
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(
        _bare_signal(materiality=3)) is False


def test_signal_with_error_is_not_trim_worthy():
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule()._is_trim_worthy(
        _bare_signal(error="claude API rate limited")) is False


# ============================================================
# compute_trims — recency + per-symbol most-recent-wins
# ============================================================
def _seed_signals(db: Path, rows: list[dict]):
    from trader.earnings_reactor import _ensure_signals_table, _persist_signal
    from trader.earnings_reactor import ReactionResult
    _ensure_signals_table(db)
    for r in rows:
        _persist_signal(db, ReactionResult(
            symbol=r["symbol"], accession=r["accession"],
            filed_at=r["filed_at"],
            materiality=r.get("materiality", 1),
            direction=r.get("direction", "NEUTRAL"),
            surprise_direction=r.get("surprise_direction", "NONE"),
            summary=r.get("summary", ""),
            items=r.get("items", []),
            model="claude-sonnet-4-6",
        ))


def test_compute_trims_returns_decisions_for_held_symbols(tmp_path):
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "A1",
         "filed_at": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
         "materiality": 4, "direction": "BEARISH",
         "summary": "$6.5B debt raise"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    targets = {"INTC": 0.10, "NVDA": 0.08}
    decisions = ReactorSignalRule().compute_trims(targets, journal_db=db)
    assert "INTC" in decisions
    assert "NVDA" not in decisions
    d = decisions["INTC"]
    assert d.old_weight == 0.10
    assert d.new_weight == 0.05  # default 50% trim
    assert d.materiality == 4
    assert d.direction == "BEARISH"


def test_compute_trims_skips_signals_for_unheld_symbols(tmp_path):
    """Reactor flagged FOO but FOO isn't in our book — no trim."""
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "FOO", "accession": "A1",
         "filed_at": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
         "materiality": 5, "direction": "BEARISH"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    targets = {"INTC": 0.10}  # FOO not held
    assert ReactorSignalRule().compute_trims(targets, journal_db=db) == {}


def test_compute_trims_respects_lookback_window(tmp_path, monkeypatch):
    """Old signals (>14d) are ignored — they've decayed in relevance."""
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "OLD",
         "filed_at": (datetime.utcnow() - timedelta(days=60)).date().isoformat(),
         "materiality": 5, "direction": "BEARISH",
         "summary": "ancient news"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    decisions = ReactorSignalRule().compute_trims({"INTC": 0.10},
                                                     journal_db=db)
    assert decisions == {}


def test_compute_trims_takes_most_recent_per_symbol(tmp_path):
    """If a held name has TWO recent trim-worthy signals, only the
    most recent (highest filed_at) is used — older signals are
    superseded."""
    db = tmp_path / "j.db"
    today = datetime.utcnow().date()
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "OLD",
         "filed_at": (today - timedelta(days=10)).isoformat(),
         "materiality": 4, "direction": "BEARISH",
         "summary": "older bearish"},
        {"symbol": "INTC", "accession": "NEW",
         "filed_at": (today - timedelta(days=2)).isoformat(),
         "materiality": 5, "direction": "BEARISH",
         "summary": "fresher and worse"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    decisions = ReactorSignalRule().compute_trims({"INTC": 0.10},
                                                     journal_db=db)
    assert decisions["INTC"].accession == "NEW"
    assert decisions["INTC"].materiality == 5


def test_compute_trims_handles_missing_db(tmp_path):
    """Fresh install — no journal db at all. Must return {} not raise."""
    from trader.reactor_rule import ReactorSignalRule
    missing = tmp_path / "nope.db"
    assert ReactorSignalRule().compute_trims({"X": 0.1},
                                                journal_db=missing) == {}


def test_compute_trims_handles_missing_table(tmp_path):
    """DB exists but earnings_signals table not yet created. Must
    return {} not raise."""
    db = tmp_path / "j.db"
    sqlite3.connect(db).close()  # creates an empty file
    from trader.reactor_rule import ReactorSignalRule
    assert ReactorSignalRule().compute_trims({"X": 0.1},
                                                journal_db=db) == {}


# ============================================================
# apply() — the LIVE / SHADOW / INERT state-machine semantics
# ============================================================
def test_apply_shadow_logs_but_does_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("REACTOR_RULE_STATUS", "SHADOW")
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "A1",
         "filed_at": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
         "materiality": 4, "direction": "BEARISH"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    targets = {"INTC": 0.10, "NVDA": 0.08}
    new_targets, decisions = ReactorSignalRule().apply(
        targets, journal_db=db)
    # Targets unchanged
    assert new_targets == {"INTC": 0.10, "NVDA": 0.08}
    # But decisions logged so caller can show would-trim list
    assert "INTC" in decisions


def test_apply_live_actually_mutates(tmp_path, monkeypatch):
    monkeypatch.setenv("REACTOR_RULE_STATUS", "LIVE")
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "A1",
         "filed_at": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
         "materiality": 4, "direction": "BEARISH"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    new_targets, decisions = ReactorSignalRule().apply(
        {"INTC": 0.10, "NVDA": 0.08}, journal_db=db)
    # INTC trimmed to 50% of original
    assert new_targets["INTC"] == 0.05
    # NVDA untouched
    assert new_targets["NVDA"] == 0.08


def test_apply_inert_returns_unchanged_and_no_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("REACTOR_RULE_STATUS", "INERT")
    db = tmp_path / "j.db"
    _seed_signals(db, [
        {"symbol": "INTC", "accession": "A1",
         "filed_at": (datetime.utcnow() - timedelta(days=2)).date().isoformat(),
         "materiality": 5, "direction": "BEARISH"},
    ])
    from trader.reactor_rule import ReactorSignalRule
    new_targets, decisions = ReactorSignalRule().apply(
        {"INTC": 0.10}, journal_db=db)
    assert new_targets == {"INTC": 0.10}
    assert decisions == {}


# ============================================================
# main.py wiring
# ============================================================
def test_main_py_invokes_reactor_rule():
    """main.py must construct + apply ReactorSignalRule between
    EarningsRule and validate_targets."""
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    text = p.read_text()
    assert "from .reactor_rule import ReactorSignalRule" in text
    assert "ReactorSignalRule()" in text
    # Must call .apply() (not just .compute_trims())
    assert ".apply(final_targets)" in text
    # Order matters: reactor rule comes AFTER EarningsRule
    earnings_idx = text.index("EarningsRule")
    reactor_idx = text.index("ReactorSignalRule")
    assert reactor_idx > earnings_idx


def test_main_py_only_mutates_targets_when_live():
    """Critical: in SHADOW mode, main.py must log but NOT mutate
    final_targets. Otherwise SHADOW becomes silently LIVE."""
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    text = p.read_text()
    # Locate the ReactorSignalRule block
    block_start = text.index("ReactorSignalRule")
    block_end = text.index("# v0.9: validate targets", block_start)
    block = text[block_start:block_end]
    # The mutation `final_targets = new_targets` must be guarded by a
    # status() == LIVE check
    assert 'rsr.status() == "LIVE"' in block
    assert "final_targets = new_targets" in block


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_surfaces_rule_status():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "ReactorSignalRule" in text
    # Earnings reactor view shows status + would-trim list
    view_idx = text.index("def view_earnings_reactor")
    next_def = text.index("\ndef ", view_idx + 1)
    body = text[view_idx:next_def]
    assert "Rebalance gate" in body
    # Status badge surfaces the env-controlled value
    assert "rsr.status()" in body
    # Recovery hint: how to flip from SHADOW → LIVE
    assert "REACTOR_RULE_STATUS=LIVE" in body


def test_dashboard_version_v3_69_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.69.0 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.69.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"

"""Tests for v3.67.2 — hotfix the missed _headline_metrics() consumer
that was still reading raw daily_snapshot, producing two disagreeing
"Equity" cards on the same Overview page.

Surfaced by user 2026-05-04: "one tab shows 104k one shows 106k why?"
Live broker: $104,969 (post-rebalance, today's drawdown).
Journal snapshot: $106,503 (Friday, pre-rebalance).
The big-block headline used the live broker mark; the 6-up grid below
it used the stale journal — same page.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def test_headline_metrics_uses_equity_state():
    """The 6-up metric grid must consume _get_equity_state() so its
    Equity card matches the big-block price headline."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    fn_idx = text.index("def _headline_metrics")
    next_def_idx = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    # Must call into the canonical EquityState helper
    assert "_get_equity_state()" in body, \
        "_headline_metrics must consume EquityState (was reading raw _cached_snapshots)"
    # State.equity_now is the new authoritative source
    assert "state.equity_now" in body


def test_headline_metrics_falls_back_to_journal_only_offline():
    """Journal snapshot should now ONLY be read when EquityState
    has no equity (broker unreachable). Not as the primary source."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    fn_idx = text.index("def _headline_metrics")
    next_def_idx = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    # The fallback must be gated on equity_now is None / not None
    assert "state.equity_now is not None" in body or \
           "state.equity_now is None" in body
    # The journal-snapshot-only branch must mention it's a fallback
    assert ("broker unreachable" in body.lower() or
            "fallback" in body.lower())


def test_headline_metrics_shows_provenance_tooltip():
    """Help tooltip must show the equity source so future "why does
    this not match?" questions answer themselves."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    fn_idx = text.index("def _headline_metrics")
    next_def_idx = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    assert "state.source" in body
    assert "help=" in body


def test_dashboard_version_v3_67_2():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.67.2 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.67.2" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"


def test_no_other_consumer_renders_equity_from_raw_snapshot():
    """Regression guard: no top-level Equity metric should read
    `latest['equity']` outside _headline_metrics' explicit fallback
    branch. Catches the next missed consumer before it ships."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Find every occurrence of `latest["equity"]` reads — there should
    # be exactly one (the documented fallback inside _headline_metrics).
    # If a future consumer adds another, this fails so we know to
    # migrate it to EquityState.
    n = text.count('float(latest["equity"])')
    assert n <= 1, (
        f"Expected ≤1 raw `float(latest[\"equity\"])` read (the "
        f"documented offline fallback), got {n}. The new consumer "
        f"must use _get_equity_state() instead — see v3.67.2 hotfix.")

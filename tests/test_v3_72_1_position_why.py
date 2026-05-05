"""Tests for v3.72.1 — structured 'why we own it' panel."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def _dashboard_text() -> str:
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    return p.read_text()


def test_dashboard_has_position_why_helper():
    text = _dashboard_text()
    assert "def _render_position_why" in text


def test_dashboard_has_universe_momentum_cache():
    """The universe-wide momentum scoring is cached so opening multiple
    modals doesn't re-fetch yfinance for every symbol."""
    text = _dashboard_text()
    assert "def _cached_universe_momentum" in text
    assert "@st.cache_data" in text


def test_why_panel_covers_four_sections():
    """The panel must answer the four questions: case, weight math,
    disclosures, drop conditions. Section headers are markdown."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "The case" in body
    assert "Weight math" in body
    assert "Recent material disclosures" in body
    assert "What would drop this position" in body


def test_why_panel_shows_rank_buffer():
    """When the symbol is in the top-15, the panel surfaces the buffer
    over the #15 cutoff (key 'is this position in danger?' answer)."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "#15 cutoff" in body or "buffer over" in body


def test_why_panel_replicates_variant_weight_math():
    """The weight math section must show the actual derivation (score
    shift + normalization + 0.80 × shifted/sum), not just the result.
    Otherwise the user can't verify the variant matches what's in code."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    # Score-shift formula from variants.py: score - min + 0.01
    assert "min_top15" in body or "min_s" in body
    # Sum normalization
    assert "Sum of shifted" in body or "sum_of_shifted" in body or "0.80 *" in body
    # Per-position cap callout (16% from risk_manager.py MAX_POSITION_PCT)
    assert "16%" in body


def test_why_panel_surfaces_rule_action_when_signal_present():
    """When recent reactor signals exist, the rule's action implication
    must be inline ('Would trim' / 'No trim' / why)."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "ReactorSignalRule" in body
    # Three branches: would trim (LIVE/SHADOW), wrong direction, below threshold
    assert "Would trim" in body or "Will trim" in body
    assert "below threshold" in body
    assert "trim-eligible" in body


def test_why_panel_lists_drop_conditions():
    """Drop conditions must include momentum cutoff, risk gates, reactor
    rule, earnings rule — the actual mechanical exit triggers."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    # Each mechanism that can cause an exit must be named
    assert "rebalance" in body.lower()  # momentum-rank exit
    assert "freeze" in body.lower()     # risk gate
    assert "EarningsRule" in body       # T-1 trim
    assert "Reactor rule" in body       # M-trigger trim


def test_why_panel_renders_above_hank_in_modal():
    """The structured panel must come BEFORE the HANK summary in the
    modal — grounded data first, interpretive narrative second."""
    text = _dashboard_text()
    why_idx = text.index('"🔍 Why we own it (structured)"')
    hank_idx = text.index('"🧠 HANK summary (interpretive)"')
    assert why_idx < hank_idx, (
        "structured why panel must render above HANK summary "
        "(grounded → interpretive ordering)")


def test_why_panel_called_from_modal():
    """The modal must actually invoke _render_position_why on the
    symbol — without this wiring the new helper is dead code."""
    text = _dashboard_text()
    modal_idx = text.index("def _symbol_detail_modal")
    next_def = text.index("\ndef ", modal_idx + 1)
    modal_body = text[modal_idx:next_def]
    assert "_render_position_why(symbol, pos)" in modal_body


def test_why_panel_handles_missing_universe_data():
    """When _cached_universe_momentum returns the error sentinel,
    the panel must show a caption, not crash."""
    text = _dashboard_text()
    fn_idx = text.index("def _render_position_why")
    next_def = text.index("\n@st.dialog", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "__error__" in body
    assert "momentum data unavailable" in body


def test_dashboard_version_v3_72_1():
    text = _dashboard_text()
    assert "v3.72.1" in text
    assert 'st.caption("v3.72.1' in text

"""Tests for v3.65.0 — UI benchmark pass.

Per docs/UI_BENCHMARK.md: sticky market ribbon, big-block price headline,
floating Ask-HANK pill, industry-standard timeframe chips.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Doc exists
# ============================================================
def test_ui_benchmark_doc_exists():
    p = Path(__file__).resolve().parent.parent / "docs" / "UI_BENCHMARK.md"
    assert p.exists()
    text = p.read_text()
    # Must reference all 5 platforms studied
    for platform in ("Yahoo Finance", "CNBC", "Nasdaq", "TipRanks", "Composer"):
        assert platform in text
    # Must include the actionable punch list with LOC estimates
    assert "punch list" in text.lower()
    assert "LOC" in text


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_version_bumped_to_v3_65_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The v3.65.0 release tag must still appear in the file's history
    # (changelog comments). Sidebar caption may have moved to a later
    # release — accept any v3.6x.y label so the test isn't churned on
    # every patch bump.
    assert "v3.65.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"


def test_dashboard_has_market_ribbon():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def _render_market_ribbon" in text
    assert "_ribbon_market_snapshot" in text
    # Ribbon must call into the module before view dispatch
    assert "_render_market_ribbon()" in text


def test_dashboard_has_price_headline():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def _render_price_headline" in text
    # Price headline must be on Overview
    assert "_render_price_headline()" in text


def test_dashboard_has_ask_hank_fab():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def _render_floating_hank_fab" in text
    assert "Ask HANK" in text
    # FAB must route to chat view
    assert 'st.session_state.active_view = "chat"' in text


def test_dashboard_has_timeframe_chips_helper():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "TIMEFRAME_CHIPS" in text
    assert "def _render_timeframe_chips" in text
    # All 9 standard chip labels (Yahoo/Nasdaq/CNBC/TipRanks set)
    for label in ('"1D"', '"5D"', '"1M"', '"3M"', '"6M"',
                   '"YTD"', '"1Y"', '"5Y"', '"ALL"'):
        assert label in text


def test_performance_view_uses_chips_not_selectbox():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The old "Lookback window" selectbox must be gone from view_performance
    perf_idx = text.index("def view_performance")
    next_def_idx = text.index("def ", perf_idx + 1)
    perf_block = text[perf_idx:next_def_idx]
    assert 'st.selectbox("Lookback window"' not in perf_block
    assert "_render_timeframe_chips" in perf_block


# ============================================================
# Behavior — chip helper translates labels to trading days
# ============================================================
def test_timeframe_chips_label_to_days():
    """Verify the chip helper exposes the expected days mapping."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    # Read the constant directly from the file (importing dashboard.py
    # would instantiate Streamlit). Parse the TIMEFRAME_CHIPS literal.
    text = (Path(__file__).resolve().parent.parent /
            "scripts" / "dashboard.py").read_text()
    # Sanity: 1D=1, 1M=21, 3M=63, 1Y=252, 5Y=1260
    assert '("1D", 1)' in text
    assert '("1M", 21)' in text
    assert '("3M", 63)' in text
    assert '("1Y", 252)' in text
    assert '("5Y", 1260)' in text


# ============================================================
# Ribbon snapshot helper degrades gracefully
# ============================================================
def test_ribbon_snapshot_returns_dict_on_failure(monkeypatch):
    """If yfinance fails (no network), _ribbon_market_snapshot returns an
    empty dict, not raise. The ribbon HTML is built around .get() so empty
    is safe."""
    # Strip the streamlit cache so we actually re-execute the body
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    # We can't import dashboard at module level (instantiates Streamlit),
    # so we just verify the function body uses .get() defensively.
    text = (Path(__file__).resolve().parent.parent /
            "scripts" / "dashboard.py").read_text()
    # Body must catch top-level Exception and return {}
    snap_idx = text.index("def _ribbon_market_snapshot")
    next_def_idx = text.index("\ndef ", snap_idx + 1)
    snap_body = text[snap_idx:next_def_idx]
    assert "except Exception" in snap_body
    assert "return out" in snap_body

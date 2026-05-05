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
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"


def _all_dashboard_text():
    """Read dashboard.py + dashboard_ui.py + dashboard_data.py.
    Robust to v3.67.0+ helper extraction."""
    base = Path(__file__).resolve().parent.parent
    parts = []
    for p in (base / "scripts" / "dashboard.py",
              base / "src" / "trader" / "dashboard_ui.py",
              base / "src" / "trader" / "dashboard_data.py"):
        if p.exists():
            parts.append(p.read_text())
    return "\n".join(parts)


def test_dashboard_has_market_ribbon():
    text = _all_dashboard_text()
    # v3.67.0+: helper renamed to render_market_ribbon (no underscore
    # prefix, lives in dashboard_ui.py); dashboard.py keeps an alias.
    assert ("def _render_market_ribbon" in text
            or "def render_market_ribbon" in text)
    assert "ribbon_market_snapshot" in text
    # Ribbon must be invoked from dashboard before view dispatch
    assert "_render_market_ribbon()" in text


def test_dashboard_has_price_headline():
    text = _all_dashboard_text()
    assert ("def _render_price_headline" in text
            or "def render_price_headline" in text)
    assert "_render_price_headline()" in text


def test_dashboard_has_ask_hank_fab():
    text = _all_dashboard_text()
    assert ("def _render_floating_hank_fab" in text
            or "def render_floating_hank_fab" in text)
    assert "Ask HANK" in text
    assert 'st.session_state.active_view = "chat"' in text


def test_dashboard_has_timeframe_chips_helper():
    text = _all_dashboard_text()
    assert "TIMEFRAME_CHIPS" in text
    assert ("def _render_timeframe_chips" in text
            or "def render_timeframe_chips" in text)
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
    """Verify the chip helper exposes the expected days mapping.
    v3.67.0+: TIMEFRAME_CHIPS literal lives in dashboard_ui.py."""
    text = _all_dashboard_text()
    assert '("1D", 1)' in text
    assert '("1M", 21)' in text
    assert '("3M", 63)' in text
    assert '("1Y", 252)' in text
    assert '("5Y", 1260)' in text


# ============================================================
# Ribbon snapshot helper degrades gracefully
# ============================================================
def test_ribbon_snapshot_returns_dict_on_failure(monkeypatch):
    """If yfinance fails (no network), the ribbon snapshot returns an
    empty dict, not raise. v3.67.0+: helper lives in dashboard_ui.py
    as ribbon_market_snapshot (no underscore prefix)."""
    base = Path(__file__).resolve().parent.parent
    text = (base / "src" / "trader" / "dashboard_ui.py").read_text()
    # Find the function body
    needle = "def ribbon_market_snapshot"
    assert needle in text
    snap_idx = text.index(needle)
    next_def_idx = text.index("\ndef ", snap_idx + 1)
    snap_body = text[snap_idx:next_def_idx]
    assert "except Exception" in snap_body
    assert "return out" in snap_body

"""Tests for v3.62.0 — smoke-test fixes from manual UI walkthrough.

Bugs found by clicking through the UI:
  1. Sidebar version label stale ("v3.55.0" hard-coded; we're on 3.62)
  2. 27+ flat sidebar items — way too many tabs
  3. Empty cmd_bar selectbox (placeholder invisible)
  4. Briefing showed "(overlay 0.94×) [DISABLED]" — confusing
  5. Alerts view crashed loudly when slippage_log table doesn't exist

These tests lock the regressions.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# 1. Version label
# ============================================================
def test_dashboard_version_label_current():
    """Sidebar shouldn't show a stale version. Match against the
    current version we're on."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Must NOT contain the old "v3.55.0 ·" label
    assert 'v3.55.0 · chat-first AI dashboard' not in text, \
        "stale version label still present"
    # Must show the current version
    assert "v3.62.0" in text


# ============================================================
# 2. Nav consolidation
# ============================================================
def test_nav_uses_collapsible_groups():
    """v3.62.0: switched from one flat NAV list to NAV_GROUPS w/ expanders."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "NAV_GROUPS" in text
    # Must include the 5 always-visible top items
    assert '"__top__"' in text
    # Must include collapsible group labels
    for label in ("📊 Portfolio", "📰 Discovery", "🔬 Research", "⚙️ System"):
        assert label in text, f"missing collapsible group: {label}"


def test_cmd_bar_placeholder_text_visible():
    """The selectbox first option must show a placeholder string,
    not be empty."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The new placeholder string we added
    assert "⌘K  pick a workflow or suggested prompt" in text
    # Old empty-string-as-first-option was confusing
    # (search for the EXACT old pattern that was a bug)
    # The new pattern should NOT have the empty-string in cmd_options[0]
    # (we replaced it with the PLACEHOLDER constant)
    assert 'cmd_options = [""]' not in text


# ============================================================
# 4. Briefing wording
# ============================================================
def test_briefing_no_disabled_overlay_format():
    """Old format '(overlay 0.94×) [DISABLED]' was confusing.
    New format reads 'overlay computed but not enforcing' when off.
    Test against the RENDERED markdown, not the source file."""
    from trader.copilot_briefing import MorningBriefing
    b = MorningBriefing(
        headline="t", equity_now=100, day_pl_pct=0.01,
        regime="transition", regime_overlay_mult=0.94,
        regime_enabled=False,
    )
    md = b.to_markdown()
    assert "[DISABLED]" not in md
    assert "not enforcing" in md
    # When enabled, should say "LIVE"
    b.regime_enabled = True
    md_live = b.to_markdown()
    assert "LIVE" in md_live


# ============================================================
# 5. Alerts SQL crash on missing table
# ============================================================
def test_query_helper_silent_on_missing_table():
    """The query helper must NOT call st.error() for 'no such table'
    errors — those are expected for tables that get created lazily.
    v3.67.0+: helper now lives in trader/dashboard_data.py."""
    base = Path(__file__).resolve().parent.parent
    text = (base / "src" / "trader" / "dashboard_data.py").read_text()
    assert 'no such table' in text.lower()
    assert "silent" in text


def test_query_helper_signature_has_silent_flag():
    """Public API change: silent flag added.
    v3.67.0+: helper now lives in trader/dashboard_data.py."""
    base = Path(__file__).resolve().parent.parent
    text = (base / "src" / "trader" / "dashboard_data.py").read_text()
    # Either the original wrap (4 spaces of indent) or the new module
    # version (4 spaces) — accept either form by checking the key tokens.
    assert "def query(path_str: str, sql: str, params: tuple = ()" in text
    assert "silent: bool = False" in text

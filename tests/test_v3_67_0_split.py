"""Tests for v3.67.0 — dashboard.py file split.

Pure helpers extracted to trader/dashboard_ui.py and
trader/dashboard_data.py so they can be unit-tested without
instantiating Streamlit.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# New modules exist + parse cleanly
# (We do file-based assertions instead of `import` to dodge the
# pre-existing numpy x86_64/arm64 architecture-mismatch in this dev
# env — runtime import isn't possible there. Tests inside the
# Streamlit container have no such constraint.)
# ============================================================
def _ui_path():
    return Path(__file__).resolve().parent.parent / "src" / "trader" / "dashboard_ui.py"


def _data_path():
    return Path(__file__).resolve().parent.parent / "src" / "trader" / "dashboard_data.py"


def test_dashboard_ui_module_exposes_expected_names():
    text = _ui_path().read_text()
    for name in ("market_session", "render_day_pl_card",
                  "render_floating_hank_fab", "render_timeframe_chips",
                  "tier_emoji", "render_citation_pills",
                  "render_tool_artifact", "ribbon_market_snapshot",
                  "render_market_ribbon", "render_price_headline",
                  "get_equity_state", "equity_state_cached"):
        assert f"def {name}" in text, f"dashboard_ui missing def {name}"
    assert "TIMEFRAME_CHIPS = [" in text


def test_dashboard_data_module_exposes_expected_names():
    text = _data_path().read_text()
    for name in ("query", "read_state_file", "live_portfolio",
                  "cached_snapshots"):
        assert f"def {name}" in text, f"dashboard_data missing def {name}"


def test_dashboard_ui_module_parses():
    """Sanity: the file is syntactically valid Python."""
    import ast
    ast.parse(_ui_path().read_text())


def test_dashboard_data_module_parses():
    import ast
    ast.parse(_data_path().read_text())


# ============================================================
# Dashboard wires through the new modules
# ============================================================
def test_dashboard_imports_dashboard_ui():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "from trader import dashboard_ui" in text
    # Re-export aliases preserve the underscore-prefixed names views call
    for alias in ("_market_session", "_render_day_pl_card",
                   "_render_floating_hank_fab", "_render_timeframe_chips",
                   "_tier_emoji", "_render_citation_pills",
                   "_render_tool_artifact", "_ribbon_market_snapshot",
                   "TIMEFRAME_CHIPS"):
        assert alias in text, f"dashboard.py missing alias {alias}"


def test_dashboard_imports_dashboard_data():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "from trader import dashboard_data" in text
    # Re-export aliases for the data helpers
    for alias in ("query = _data.query",
                   "read_state_file = _data.read_state_file",
                   "_live_portfolio = _data.live_portfolio",
                   "_cached_snapshots = _data.cached_snapshots"):
        assert alias in text, f"dashboard.py missing alias `{alias}`"


def test_dashboard_version_v3_67_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The v3.67.0 release tag must remain in changelog comments;
    # sidebar caption may have moved to a later patch.
    assert "v3.67.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"


# ============================================================
# Behavior — extracted helpers still work
# (File-text-based assertions to dodge the dev-env numpy issue.
# Inside the Streamlit container, `from trader.dashboard_data import
# query` works fine.)
# ============================================================
def test_query_handles_missing_table_silently():
    """The 'no such table' branch must short-circuit without raising."""
    text = _data_path().read_text()
    q_idx = text.index("def query")
    next_def = text.index("\ndef ", q_idx + 1)
    body = text[q_idx:next_def]
    assert 'no such table' in body.lower()
    assert "return pd.DataFrame()" in body


def test_read_state_file_returns_empty_on_miss():
    text = _data_path().read_text()
    f_idx = text.index("def read_state_file")
    next_def = text.index("\ndef ", f_idx + 1)
    body = text[f_idx:next_def]
    # Missing file → return {}
    assert "return {}" in body


def test_timeframe_chips_const():
    """Verify the chip mapping in dashboard_ui.py is the 9-label
    Yahoo/Nasdaq/CNBC/TipRanks set."""
    text = _ui_path().read_text()
    for pair in ('("1D", 1)', '("5D", 5)', '("1M", 21)', '("3M", 63)',
                  '("6M", 126)', '("1Y", 252)', '("5Y", 1260)'):
        assert pair in text


def test_tier_emoji_branches():
    """tier_emoji body branches on read_only/sim/live."""
    text = _ui_path().read_text()
    e_idx = text.index("def tier_emoji")
    next_def = text.index("\ndef ", e_idx + 1)
    body = text[e_idx:next_def]
    assert '"read_only": "📖"' in body
    assert '"sim": "🧪"' in body
    assert '"live": "🚨"' in body


def test_dashboard_py_shrank_after_split():
    """Sanity: dashboard.py should be smaller after the split."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    n_lines = len(p.read_text().splitlines())
    # Before split: ~5663 lines. After: should be < 5400.
    assert n_lines < 5400, \
        f"dashboard.py is {n_lines} lines — split should have shrunk it below 5400"


# ============================================================
# Underlying market_session module still works (pure stdlib import,
# unaffected by the dev env numpy issue)
# ============================================================
def test_market_session_helper_returns_session_state():
    from trader.market_session import market_session_now
    s = market_session_now()
    assert s.label in {"OPEN", "CLOSED_PREMARKET", "CLOSED_AFTERHOURS",
                        "CLOSED_OVERNIGHT", "CLOSED_WEEKEND",
                        "CLOSED_HOLIDAY"}

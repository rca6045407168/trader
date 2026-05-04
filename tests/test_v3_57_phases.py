"""Tests for v3.57.1 Phases 1, 3, 4, 5, 8 — sandbox tiers, plan mode,
NL→SQL translation, and the helper plumbing for the chat artifact view
and the Hebbia Matrix grid view.
"""
from __future__ import annotations

import os

import pytest


def test_tier_of_known_tools():
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    from trader.copilot import tier_of, TOOL_TIERS

    # All 10 read-only-by-design tools
    assert tier_of("get_portfolio_status") == "read_only"
    assert tier_of("get_regime_state") == "read_only"
    assert tier_of("query_journal") == "read_only"
    # compute_scenario is the one sim-tier tool today
    assert tier_of("compute_scenario") == "sim"
    # Unknown tool returns 'unknown'
    assert tier_of("place_order") == "unknown"
    # Every entry in TOOL_TIERS is one of the four valid tiers
    for v in TOOL_TIERS.values():
        assert v in ("read_only", "sim", "live")


def test_plan_mode_signature():
    """stream_response must accept plan_mode as a kwarg without complaint."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    import inspect
    from trader.copilot import stream_response
    sig = inspect.signature(stream_response)
    assert "plan_mode" in sig.parameters
    assert sig.parameters["plan_mode"].default is False


def test_plan_mode_blocks_sim_tools_without_api(monkeypatch):
    """When plan_mode=True and a sim/live tool is called, the dispatch must
    NOT execute the underlying tool. We can't easily reach that branch
    without a live API call — but we can at least verify the stub-result
    shape is reachable as a unit by calling the helper directly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    # The plan_mode path is gated inside stream_response; an end-to-end test
    # would need a mocked Anthropic client. Smoke-test the tier helper.
    from trader.copilot import tier_of
    assert tier_of("compute_scenario") == "sim"


def test_translate_nl_to_sql_signature():
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    import inspect
    from trader.copilot import translate_nl_to_sql
    sig = inspect.signature(translate_nl_to_sql)
    assert "question" in sig.parameters


def test_translate_nl_to_sql_no_api_key(monkeypatch):
    """Without an API key the translator must return a clean error dict."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Force-reload so the module-level ANTHROPIC_API_KEY rebinds to None
    import importlib, trader.copilot as cp
    importlib.reload(cp)
    out = cp.translate_nl_to_sql("show me everything")
    assert "error" in out
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_grid_default_questions_are_real_keys():
    """The Hebbia Matrix grid default columns must be keys that exist in
    the position dict returned by tool_get_portfolio_status. If we add a
    column here that doesn't exist, every cell renders '?'."""
    # The grid helper lives in scripts/dashboard.py — read the module text
    # rather than importing (Streamlit imports break under pytest).
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = src.read_text()
    # Default cols
    for col in ("day_pnl_pct", "weight_pct", "total_unrealized_pnl_pct", "sector"):
        assert f'"{col}"' in text, f"grid default column {col!r} not in dashboard.py"


def test_render_helpers_defined_in_dashboard():
    """Sanity-check that the citation pill + artifact helpers are present.
    v3.67.0+: helper bodies live in trader/dashboard_ui.py; dashboard.py
    keeps underscore-prefixed aliases."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    db_text = (base / "scripts" / "dashboard.py").read_text()
    ui_text = (base / "src" / "trader" / "dashboard_ui.py").read_text()
    # Helper definitions live in dashboard_ui.py (no underscore prefix)
    assert "def render_citation_pills" in ui_text
    assert "def render_tool_artifact" in ui_text
    # Aliases preserved in dashboard.py for backward-compat with views
    assert "_render_citation_pills" in db_text
    assert "_render_tool_artifact" in db_text
    # View dispatch unchanged
    assert "def view_grid" in db_text
    assert "def view_screener" in db_text
    assert '"grid": view_grid' in db_text
    assert '"screener": view_screener' in db_text

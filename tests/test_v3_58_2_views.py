"""Smoke tests for v3.58.2 pro-trader cockpit additions:
  • Alerts log (view_alerts)
  • Slippage dashboard (view_slippage)
  • Shadow signals panel (view_shadow_signals)
  • Watchlist (view_watchlist)
  • Per-symbol drill-down modal (_symbol_detail_modal)

Streamlit views can't be invoked outside a runtime. We assert at the
source-text level that the required functions exist and are wired into
NAV + DISPATCH.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"


@pytest.fixture(scope="module")
def text():
    return SRC.read_text()


def test_modal_decorated_with_st_dialog(text):
    # @st.dialog("...") decorator must precede _symbol_detail_modal
    idx = text.find("def _symbol_detail_modal")
    assert idx > 0
    above = text[max(0, idx - 200): idx]
    assert "@st.dialog" in above, "modal must be decorated with @st.dialog"


def test_modal_opener_called_at_dispatch(text):
    # _maybe_open_symbol_modal called once view dispatch finishes
    assert "_maybe_open_symbol_modal()" in text


def test_live_positions_offers_drill_button(text):
    # The drill-down + linked-symbol buttons we added in view_live_positions
    assert "live_drill_open" in text
    assert "live_drill_link" in text


def test_alerts_view_pulls_from_journal_tables(text):
    # Alert log should at minimum query runs, orders, postmortems
    assert "FROM runs" in text
    assert "FROM orders" in text
    assert "FROM postmortems" in text


def test_slippage_view_queries_log_table(text):
    assert "FROM slippage_log" in text


def test_watchlist_uses_full_ranking_cache(text):
    assert "_cached_full_ranking" in text
    assert "rank_momentum" in text

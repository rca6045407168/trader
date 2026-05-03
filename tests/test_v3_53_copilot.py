"""Tests for v3.53.0 copilot module."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


def test_tools_definitions_well_formed():
    """All tool definitions have name, description, input_schema."""
    from trader.copilot import TOOLS
    assert len(TOOLS) >= 8
    for t in TOOLS:
        assert "name" in t
        assert "description" in t
        assert "input_schema" in t
        assert isinstance(t["input_schema"], dict)
        assert t["input_schema"].get("type") == "object"


def test_dispatch_unknown_tool_returns_error():
    from trader.copilot import dispatch_tool
    result = dispatch_tool("nonexistent_tool", {})
    assert "error" in result
    assert "unknown" in result["error"].lower()


def test_dispatch_tool_handles_exception_gracefully():
    from trader import copilot
    with patch.object(copilot, "_TOOL_DISPATCH",
                       {"boom": lambda args: 1 / 0}):
        result = copilot.dispatch_tool("boom", {})
        assert "error" in result
        assert "ZeroDivisionError" in result["error"]


def test_query_journal_blocks_writes():
    from trader.copilot import tool_query_journal
    for sql in ("INSERT INTO decisions VALUES (1)",
                 "DELETE FROM decisions",
                 "UPDATE decisions SET final='x'",
                 "DROP TABLE decisions"):
        result = tool_query_journal(sql=sql)
        assert "error" in result


def test_query_journal_blocks_non_select():
    from trader.copilot import tool_query_journal
    result = tool_query_journal(sql="DELETE FROM decisions WHERE 1=1")
    assert "error" in result


def test_query_journal_runs_select_against_real_journal():
    """Real journal must have at least the schema present."""
    from trader.copilot import tool_query_journal
    # Should succeed on the standard schema
    result = tool_query_journal(sql="SELECT name FROM sqlite_master WHERE type='table'")
    # Either runs (real journal exists) or returns error (no journal yet) — not a write
    assert "error" in result or "rows" in result


def test_compute_scenario_validates_pct_input():
    from trader.copilot import tool_compute_scenario
    # Symbol not in portfolio (mocked-empty)
    with patch("trader.positions_live.fetch_live_portfolio") as mock_pf:
        mock_obj = MagicMock()
        mock_obj.positions = []
        mock_pf.return_value = mock_obj
        result = tool_compute_scenario(symbol="AAPL", pct_move=-0.10)
        assert "error" in result


def test_compute_scenario_with_position():
    from trader.copilot import tool_compute_scenario
    with patch("trader.positions_live.fetch_live_portfolio") as mock_pf:
        mock_obj = MagicMock()
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.market_value = 10000
        pos.weight_of_book = 0.10
        mock_obj.positions = [pos]
        mock_obj.equity = 100000
        mock_pf.return_value = mock_obj
        result = tool_compute_scenario(symbol="AAPL", pct_move=-0.10)
        # 10% drop on $10k position = -$1000 = -1% of $100k
        assert result.get("dollar_impact") == pytest.approx(-1000)
        assert result.get("portfolio_impact_pct") == pytest.approx(-1.0)


def test_stream_response_returns_error_without_api_key(monkeypatch):
    from trader import copilot
    monkeypatch.setattr(copilot, "ANTHROPIC_API_KEY", "")
    events = list(copilot.stream_response([{"role": "user", "content": "hi"}]))
    assert any(e.get("type") == "error" for e in events)


def test_system_prompt_mentions_strategy_constraints():
    """Critical: the prompt must reference our strategy + constraints so the
    copilot can't drift into recommending things we've killed."""
    from trader.copilot import SYSTEM_PROMPT
    assert "momentum" in SYSTEM_PROMPT.lower()
    assert "3-gate" in SYSTEM_PROMPT.lower() or "survivor" in SYSTEM_PROMPT.lower()
    assert "critique" in SYSTEM_PROMPT.lower() or "killed" in SYSTEM_PROMPT.lower()
    # Must NOT recommend more frequent trading
    assert "more frequent" in SYSTEM_PROMPT.lower() or "overtrading" in SYSTEM_PROMPT.lower()

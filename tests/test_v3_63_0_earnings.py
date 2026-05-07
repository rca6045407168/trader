"""Tests for v3.63.0 — multi-source earnings calendar."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def test_module_imports():
    from trader.earnings_calendar import (
        next_earnings_date, earnings_within_window, status,
    )
    assert callable(next_earnings_date)
    assert callable(earnings_within_window)
    assert callable(status)


def test_status_with_no_keys(monkeypatch):
    """When no API keys configured, status should reflect that."""
    for k in ("POLYGON_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_KEY"):
        monkeypatch.delenv(k, raising=False)
    from trader.earnings_calendar import status
    s = status()
    assert s["polygon_configured"] is False
    assert s["finnhub_configured"] is False
    assert s["alpha_vantage_configured"] is False
    assert s["any_paid_source_configured"] is False
    assert s["yfinance_available"] is True


def test_status_with_polygon_key(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    from trader.earnings_calendar import status
    s = status()
    assert s["polygon_configured"] is True
    assert s["any_paid_source_configured"] is True


def test_polygon_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    from trader.earnings_calendar import _polygon
    assert _polygon("AAPL") is None


def test_finnhub_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    from trader.earnings_calendar import _finnhub
    assert _finnhub("AAPL") is None


def test_alpha_vantage_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    from trader.earnings_calendar import _alpha_vantage
    assert _alpha_vantage("AAPL") is None


def test_cache_round_trip(tmp_path, monkeypatch):
    """cache_set + cache_get should round-trip a date."""
    monkeypatch.setattr("trader.earnings_calendar.CACHE_FILE",
                         tmp_path / "earnings_cache.json")
    from trader.earnings_calendar import _cache_set, _cache_get
    today = datetime.utcnow().date()
    _cache_set("AAPL", today + timedelta(days=5), "polygon")
    out = _cache_get("AAPL")
    assert out == today + timedelta(days=5)


def test_cache_returns_none_for_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.earnings_calendar.CACHE_FILE",
                         tmp_path / "earnings_cache.json")
    from trader.earnings_calendar import _cache_get
    assert _cache_get("NONEXISTENT") is None


def test_next_earnings_date_falls_back_to_none_when_all_sources_empty(monkeypatch):
    """With no API keys + yfinance returning nothing, function returns None."""
    for k in ("POLYGON_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("trader.earnings_calendar._yfinance",
                         lambda sym, days_ahead=30: None)
    # Use a fresh cache
    import tempfile
    monkeypatch.setattr("trader.earnings_calendar.CACHE_FILE",
                         Path(tempfile.mkdtemp()) / "earnings_cache.json")
    from trader.earnings_calendar import next_earnings_date
    assert next_earnings_date("AAPL", days_ahead=14) is None


def test_earnings_within_window_signature():
    """Bulk lookup must have the right shape."""
    import inspect
    from trader.earnings_calendar import earnings_within_window
    sig = inspect.signature(earnings_within_window)
    assert "symbols" in sig.parameters
    assert "start" in sig.parameters
    assert "end" in sig.parameters


def test_main_py_uses_new_calendar():
    """v3.63.0 fix: main.py must import from earnings_calendar, not events_calendar
    for the EarningsRule path."""
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    text = p.read_text()
    # Must reference the new module
    assert "from .earnings_calendar import" in text
    # Must specifically use next_earnings_date in the EarningsRule block
    assert "next_earnings_date" in text


def test_dashboard_surfaces_earnings_status():
    """Alerts view must surface the earnings calendar status panel."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "📅 Earnings calendar sources" in text
    assert "POLYGON_API_KEY" in text
    assert "EarningsRule LIVE has been DOING NOTHING" in text


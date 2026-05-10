"""Tests for trader.decisions_renderer — paragraph reasoning per
decision row.

The renderer is a pure function over a row-dict; tests construct
realistic row inputs (mirroring what view_decisions reads from the
journal) and assert on the produced paragraphs."""
from __future__ import annotations

import os
import json

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

from trader.decisions_renderer import (
    fmt_why, fmt_reasoning, parse_rationale,
)


# ============================================================
# parse_rationale
# ============================================================
def test_parse_rationale_empty():
    assert parse_rationale(None) == {}
    assert parse_rationale("") == {}
    assert parse_rationale(0) == {}


def test_parse_rationale_json_string():
    out = parse_rationale('{"trailing_return": 0.5, "lookback_months": 12}')
    assert out["trailing_return"] == 0.5
    assert out["lookback_months"] == 12


def test_parse_rationale_already_dict():
    d = {"foo": "bar"}
    assert parse_rationale(d) == d


def test_parse_rationale_bad_json_returns_empty():
    assert parse_rationale("not json {{{") == {}


def test_parse_rationale_quoted_string_returns_empty():
    """The orchestration rows store rationale as a quoted string, not
    a dict — should fall back to empty."""
    assert parse_rationale('"selected by auto_router: vertical_winner"') == {}


# ============================================================
# fmt_why — short one-liners
# ============================================================
def test_fmt_why_momentum():
    s = fmt_why('{"trailing_return": 0.4815, "lookback_months": 12}')
    assert "12-1 mom +48.1%" in s


def test_fmt_why_negative_momentum():
    s = fmt_why('{"trailing_return": -0.15}')
    assert "12-1 mom -15.0%" in s


def test_fmt_why_with_rsi():
    s = fmt_why('{"rsi": 28.5}')
    assert "RSI 28" in s or "RSI 29" in s


def test_fmt_why_with_z_score():
    s = fmt_why('{"z_score": -2.5}')
    assert "z -2.50" in s


def test_fmt_why_combined_signals():
    s = fmt_why('{"trailing_return": 0.1, "rsi": 30, "z_score": -1.5}')
    assert "12-1 mom" in s and "RSI" in s and "z " in s


def test_fmt_why_empty():
    assert fmt_why(None) == ""
    assert fmt_why("") == ""


# ============================================================
# fmt_reasoning — MOMENTUM
# ============================================================
def test_reasoning_momentum_strong_signal():
    """Score > 1.5 → 'top quintile' phrasing."""
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "INTC",
        "action": "BUY",
        "style": "MOMENTUM",
        "score": 2.15,
        "rationale_json": '{"trailing_return": 2.154, "lookback_months": 12}',
        "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)",
    }
    out = fmt_reasoning(row)
    assert "INTC" in out
    assert "purchased" in out
    assert "MOMENTUM" in out
    assert "+215.4%" in out
    assert "top quintile" in out
    assert "8.9%" in out
    assert "vertical_winner" in out
    assert "Jegadeesh-Titman" in out


def test_reasoning_momentum_modest_signal():
    """Score < 0.5 → 'positive but modest' phrasing."""
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "TSLA",
        "action": "BUY",
        "style": "MOMENTUM",
        "score": 0.48,
        "rationale_json": '{"trailing_return": 0.4815, "lookback_months": 12}',
        "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)",
    }
    out = fmt_reasoning(row)
    assert "modest" in out
    assert "+48.1%" in out


def test_reasoning_momentum_middle_signal():
    """Score in [0.5, 1.5] → 'middle ranks' phrasing."""
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "WMT",
        "action": "BUY",
        "style": "MOMENTUM",
        "score": 0.55,
        "rationale_json": '{"trailing_return": 0.554, "lookback_months": 12}',
        "final": "LIVE_AUTO_BUY @ 8.9%",
    }
    out = fmt_reasoning(row)
    assert "middle ranks" in out


def test_reasoning_momentum_no_weight():
    """Missing weight in final → fallback phrasing."""
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "WMT",
        "action": "BUY",
        "style": "MOMENTUM",
        "score": 0.55,
        "rationale_json": '{"trailing_return": 0.554}',
        "final": "LIVE_AUTO_BUY",
    }
    out = fmt_reasoning(row)
    assert "Position size was set by the sleeve" in out


# ============================================================
# fmt_reasoning — live_auto orchestration
# ============================================================
def test_reasoning_live_auto():
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "LIN",
        "action": "BUY",
        "style": "live_auto",
        "score": 0.0,
        "rationale_json": '"selected by auto_router: vertical_winner"',
        "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)",
    }
    out = fmt_reasoning(row)
    assert "auto-router" in out
    assert "vertical_winner" in out
    assert "LIN" in out
    assert "MIN_EVIDENCE_MONTHS" in out  # eligibility-filter mention
    assert "hysteresis" in out
    assert "orchestration-level" in out


# ============================================================
# fmt_reasoning — BOTTOM_CATCH
# ============================================================
def test_reasoning_bottom_catch_with_all_signals():
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "NVDA",
        "action": "BUY",
        "style": "BOTTOM_CATCH",
        "score": -2.5,
        "rationale_json": '{"z_score": -2.5, "rsi": 28, "trailing_return": -0.12}',
        "final": "BOTTOM_BUY @ 3.0%",
    }
    out = fmt_reasoning(row)
    assert "NVDA" in out
    assert "BOTTOM_CATCH" in out
    assert "counter-trend" in out
    assert "z-score" in out
    assert "-2.50" in out
    assert "RSI" in out
    assert "oversold" in out
    assert "-12.0%" in out
    assert "5-20 day" in out
    assert "3.0%" in out


# ============================================================
# fmt_reasoning — EARNINGS_REACT
# ============================================================
def test_reasoning_earnings_react():
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "META",
        "action": "BUY",
        "style": "EARNINGS_REACT",
        "score": None,
        "rationale_json": None,
        "final": "EARN_BUY @ 2.0%",
    }
    out = fmt_reasoning(row)
    assert "earnings reactor" in out
    assert "META" in out
    assert "EPS surprise" in out
    assert "2.0%" in out


# ============================================================
# fmt_reasoning — unknown style fallback
# ============================================================
def test_reasoning_unknown_style_with_rationale():
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "FOO",
        "action": "BUY",
        "style": "EXPERIMENTAL_SLEEVE",
        "score": 1.0,
        "rationale_json": '{"custom_field": 42, "another": "value"}',
        "final": "EXP_BUY @ 1.5%",
    }
    out = fmt_reasoning(row)
    assert "FOO" in out
    assert "EXPERIMENTAL_SLEEVE" in out
    assert "custom_field=42" in out
    assert "another=value" in out


def test_reasoning_unknown_style_no_rationale():
    row = {
        "ts": "2026-05-10T21:22:23",
        "ticker": "FOO",
        "action": "SELL",
        "style": "MANUAL",
        "score": None,
        "rationale_json": None,
        "final": "MANUAL_CLOSE",
    }
    out = fmt_reasoning(row)
    assert "FOO" in out
    assert "sold" in out
    assert "MANUAL" in out


# ============================================================
# Real-data calibration — matches the screenshot in the user's report
# ============================================================
def test_reasoning_real_data_from_screenshot():
    """Verify on rows matching the user's screenshot output."""
    rows = [
        {"ts": "2026-05-10T21:22:23.972758", "ticker": "TSLA",
         "action": "BUY", "style": "MOMENTUM", "score": 0.4815,
         "rationale_json": '{"trailing_return": 0.4815, "lookback_months": 12, "as_of": "2026-05-10"}',
         "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)"},
        {"ts": "2026-05-10T21:22:23.968712", "ticker": "CAT",
         "action": "BUY", "style": "MOMENTUM", "score": 1.849,
         "rationale_json": '{"trailing_return": 1.849, "lookback_months": 12, "as_of": "2026-05-10"}',
         "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)"},
    ]
    for r in rows:
        out = fmt_reasoning(r)
        assert r["ticker"] in out
        assert len(out) > 100, "Reasoning should be a full paragraph, not a phrase"
        assert "LIVE strategy" in out
        assert "8.9%" in out

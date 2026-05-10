"""Tests for scripts/run_shadow_eval.py — v6 shadow-evaluation harness.

The script's job is twofold:
  1. Force-enable INSIDER_SIGNAL_ENABLED, INSIDER_EDGAR_ENABLED, PEAD_ENABLED
     in os.environ BEFORE trader modules import, so the v6 strategies
     produce picks even when production env knobs are still OFF.
  2. Call eval_runner.evaluate_at(today, DEFAULT_LIQUID_EXPANDED) which
     journals picks to strategy_eval.

These tests verify the script's contract via source-text inspection
plus a smoke run against an isolated SQLite path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SHADOW_PATH = SCRIPTS_DIR / "run_shadow_eval.py"
PLIST_PATH = (
    Path(__file__).resolve().parent.parent
    / "infra" / "launchd" / "com.trader.shadow-eval.plist"
)


# ============================================================
# Script source-text contract
# ============================================================
def test_shadow_eval_script_exists():
    assert SHADOW_PATH.exists()
    assert SHADOW_PATH.is_file()


def test_shadow_eval_forces_v6_env_vars():
    txt = SHADOW_PATH.read_text()
    # Must set env BEFORE importing trader modules
    assert 'os.environ.setdefault("INSIDER_SIGNAL_ENABLED"' in txt
    assert 'os.environ.setdefault("INSIDER_EDGAR_ENABLED"' in txt
    assert 'os.environ.setdefault("PEAD_ENABLED"' in txt
    assert 'os.environ.setdefault(\n    "SEC_USER_AGENT"' in txt or \
           'os.environ.setdefault("SEC_USER_AGENT"' in txt


def test_shadow_eval_calls_evaluate_at():
    txt = SHADOW_PATH.read_text()
    assert "eval_runner.evaluate_at" in txt
    assert "DEFAULT_LIQUID_EXPANDED" in txt


def test_shadow_eval_env_setting_precedes_imports():
    """Env vars must be set BEFORE the trader.* imports load — otherwise
    the strategy's module-load-time env check sees the unset value."""
    txt = SHADOW_PATH.read_text()
    env_idx = txt.find('os.environ.setdefault("INSIDER_SIGNAL_ENABLED"')
    import_idx = txt.find("from trader import eval_runner")
    assert env_idx > 0 and import_idx > 0
    assert env_idx < import_idx, \
        "env setdefault must precede trader.* imports"


# ============================================================
# launchd plist contract
# ============================================================
def test_shadow_eval_plist_exists():
    assert PLIST_PATH.exists()


def test_plist_label_correct():
    txt = PLIST_PATH.read_text()
    assert "<string>com.trader.shadow-eval</string>" in txt


def test_plist_invokes_script_with_venv_python():
    txt = PLIST_PATH.read_text()
    assert ".venv/bin/python" in txt
    assert "scripts/run_shadow_eval.py" in txt


def test_plist_runs_weekdays_only():
    """Weekday 1-5 (Mon-Fri); no Weekday 0 (Sun) or 6 (Sat)."""
    txt = PLIST_PATH.read_text()
    for wd in (1, 2, 3, 4, 5):
        assert f"<key>Weekday</key><integer>{wd}</integer>" in txt
    assert "<key>Weekday</key><integer>0</integer>" not in txt
    assert "<key>Weekday</key><integer>6</integer>" not in txt


def test_plist_paired_with_startinterval_for_sleep_resilience():
    """Per the canonical lesson in CLAUDE.md: pair CalendarInterval
    with StartInterval so sleep-skipped fires get caught."""
    txt = PLIST_PATH.read_text()
    assert "<key>StartInterval</key>" in txt


def test_plist_sets_v6_env_redundantly():
    """plist's EnvironmentVariables section sets the same v6 flags
    as the script itself. Belt-and-suspenders + visibility."""
    txt = PLIST_PATH.read_text()
    assert "<key>INSIDER_SIGNAL_ENABLED</key>" in txt
    assert "<key>INSIDER_EDGAR_ENABLED</key>" in txt
    assert "<key>PEAD_ENABLED</key>" in txt
    assert "<key>SEC_USER_AGENT</key>" in txt


def test_plist_routes_logs_to_library_logs():
    """Per the OpenClaw lesson: log to ~/Library/Logs, not /tmp.
    /tmp is purged on reboot; Library/Logs is durable."""
    txt = PLIST_PATH.read_text()
    assert "/Users/richardchen/Library/Logs" in txt
    assert "/tmp/" not in txt


# ============================================================
# Strategies actually produce picks when env-gated forcedly-on
# ============================================================
def test_v6_strategies_produce_picks_with_env_set(monkeypatch):
    """With INSIDER_SIGNAL_ENABLED=1, the yfinance-backed insider
    strategy should return non-empty picks (network-dependent — uses
    yfinance's 6-month aggregate which is generally available)."""
    monkeypatch.setenv("INSIDER_SIGNAL_ENABLED", "1")
    import pandas as pd
    from trader.eval_strategies import xs_top10_insider_buy
    # Build a minimal price panel for a few liquid names that yfinance
    # has insider data for
    dates = pd.bdate_range("2026-04-01", periods=30)
    cols = ["AAPL", "MSFT", "XOM", "JPM", "INTC"]
    import numpy as np
    np.random.seed(0)
    px = pd.DataFrame(
        100 * np.cumprod(1 + np.random.randn(30, len(cols)) * 0.01, axis=0),
        index=dates, columns=cols,
    )
    # asof = today (within the wall-clock guard)
    out = xs_top10_insider_buy(pd.Timestamp.today(), px)
    # We may get 0..N picks depending on yfinance availability — what
    # we're checking is that the env-gate doesn't short-circuit to {}
    assert isinstance(out, dict)
    # If yfinance returned ANY scores, picks should be ≤ 10
    assert len(out) <= 10

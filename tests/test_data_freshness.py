"""v3.73.27 — tests for the data-freshness kill-switch trigger.

The kill_switch module's docstring has claimed since day one that it
halts on stale yfinance data. v3.73.27 actually implements that.
These tests verify the implementation does what the docstring promised.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ALPACA_API_KEY", "test")


def test_freshness_returns_true_on_recent_data(monkeypatch):
    """A SPY series with the latest row dated today must be deemed fresh."""
    monkeypatch.setenv("SKIP_DATA_FRESHNESS_CHECK", "false")
    # CI fix: use today's timestamp directly (skip bdate_range, which
    # returns empty on some weekend wall-clock conditions). The
    # freshness check uses bdate_range(latest+1, today) internally to
    # count business days behind — that count is 0 when latest == today,
    # regardless of whether today is a business day.
    fresh_df = pd.DataFrame(
        {"SPY": [600.0]},
        index=[pd.Timestamp.today().normalize()],
    )
    monkeypatch.setattr(
        "trader.kill_switch.fetch_history",
        lambda *a, **k: fresh_df,
        raising=False,
    )
    # Inline import to pick up the patched fetch_history
    import trader.kill_switch as ks  # noqa
    from trader.data import fetch_history  # noqa

    # Patch fetch_history at the import path used inside _check_data_freshness
    monkeypatch.setattr("trader.data.fetch_history", lambda *a, **k: fresh_df)
    is_fresh, msg = ks._check_data_freshness()
    assert is_fresh is True, f"expected fresh, got msg={msg!r}"


def test_freshness_halts_on_stale_data(monkeypatch):
    """A SPY series with the latest row 10 business days old must trigger halt."""
    stale_end = pd.Timestamp.today() - pd.tseries.offsets.BDay(10)
    stale_df = pd.DataFrame(
        {"SPY": [600.0, 601.0, 602.0]},
        index=pd.bdate_range(end=stale_end, periods=3),
    )
    monkeypatch.setattr("trader.data.fetch_history", lambda *a, **k: stale_df)
    import trader.kill_switch as ks
    is_fresh, msg = ks._check_data_freshness()
    assert is_fresh is False
    assert msg is not None
    assert "stale" in msg.lower()
    assert "business days behind" in msg.lower()


def test_freshness_halts_on_empty_data(monkeypatch):
    """Empty yfinance return must be treated as stale (positive confirmation)."""
    monkeypatch.setattr(
        "trader.data.fetch_history",
        lambda *a, **k: pd.DataFrame(),
    )
    import trader.kill_switch as ks
    is_fresh, msg = ks._check_data_freshness()
    assert is_fresh is False
    assert "empty" in msg.lower()


def test_freshness_halts_on_fetch_exception(monkeypatch):
    """yfinance throwing must halt (better safe than dangerous)."""
    def boom(*a, **k):
        raise RuntimeError("yfinance is down")
    monkeypatch.setattr("trader.data.fetch_history", boom)
    import trader.kill_switch as ks
    is_fresh, msg = ks._check_data_freshness()
    assert is_fresh is False
    assert "freshness check failed" in msg.lower()


def test_kill_switch_reports_stale_data_as_reason(monkeypatch):
    """check_kill_triggers must surface a stale-data reason."""
    stale_end = pd.Timestamp.today() - pd.tseries.offsets.BDay(10)
    stale_df = pd.DataFrame(
        {"SPY": [600.0, 601.0]},
        index=pd.bdate_range(end=stale_end, periods=2),
    )
    monkeypatch.setattr("trader.data.fetch_history", lambda *a, **k: stale_df)
    monkeypatch.setattr("trader.kill_switch.recent_snapshots", lambda days=30: [])
    monkeypatch.setenv("SKIP_DATA_FRESHNESS_CHECK", "false")

    import trader.kill_switch as ks
    halt, reasons = ks.check_kill_triggers(equity=100_000.0)
    assert halt is True
    assert any("stale" in r.lower() for r in reasons)


def test_kill_switch_skip_flag_disables_freshness_check(monkeypatch):
    """SKIP_DATA_FRESHNESS_CHECK=true must bypass the check.

    This is for offline tests, weekend backfills, and similar
    intentional out-of-band runs.
    """
    monkeypatch.setattr("trader.kill_switch.recent_snapshots", lambda days=30: [])
    monkeypatch.setenv("SKIP_DATA_FRESHNESS_CHECK", "true")
    # Even if fetch_history would explode, the skip flag should short-circuit
    monkeypatch.setattr("trader.data.fetch_history",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    import trader.kill_switch as ks
    halt, reasons = ks.check_kill_triggers(equity=100_000.0)
    assert halt is False
    assert reasons == []

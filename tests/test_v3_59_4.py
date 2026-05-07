"""Tests for v3.59.4 — rank_momentum end_date + walk-forward + sensitivity."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# rank_momentum end_date refactor
# ============================================================

def test_rank_momentum_accepts_end_date_kwarg():
    """The signature must include end_date — Cat 9 (determinism) depends on this."""
    import inspect
    from trader.strategy import rank_momentum
    sig = inspect.signature(rank_momentum)
    assert "end_date" in sig.parameters
    # Default is None (preserves original behavior)
    assert sig.parameters["end_date"].default is None


def test_rank_momentum_end_date_changes_picks(monkeypatch):
    """Different end_dates produce different rationale.as_of strings.
    We don't actually fetch data here (would hit yfinance); we just
    verify the rationale field gets stamped."""
    import pandas as pd
    from trader import strategy
    # 5 years of business days spanning 2020-2025 — enough history for
    # the 12+1 month lookback even at the early test date.
    def _fake_fetch_history(symbols, start=None):
        idx = pd.date_range(start="2020-01-01", end="2025-06-01", freq="B")
        # Trending series so momentum_score is non-NaN
        prices = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
        return pd.DataFrame({s: prices for s in symbols})
    def _fake_fetch_ohlcv(s, start=None):
        return pd.DataFrame()
    monkeypatch.setattr(strategy, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(strategy, "fetch_ohlcv", _fake_fetch_ohlcv)

    early = strategy.rank_momentum(["AAA", "BBB", "CCC"], top_n=2,
                                     end_date="2024-01-15")
    later = strategy.rank_momentum(["AAA", "BBB", "CCC"], top_n=2,
                                     end_date="2025-01-15")
    assert early, "rank_momentum returned no candidates with synthetic data"
    assert later
    assert early[0].rationale.get("as_of") == "2024-01-15"
    assert later[0].rationale.get("as_of") == "2025-01-15"


def test_rank_momentum_default_behavior_unchanged(monkeypatch):
    """No end_date arg → uses today (original behavior preserved)."""
    import pandas as pd
    from trader import strategy
    def _fake_fetch_history(symbols, start=None):
        # 5 years of trending data ending today
        idx = pd.date_range(end=pd.Timestamp.today(), periods=1500, freq="B")
        prices = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
        return pd.DataFrame({s: prices for s in symbols})
    monkeypatch.setattr(strategy, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(strategy, "fetch_ohlcv", lambda s, start=None: pd.DataFrame())
    cands = strategy.rank_momentum(["A", "B"], top_n=1)
    assert cands  # didn't crash, default path works
    # Default path stamps as_of with today's date
    assert "as_of" in cands[0].rationale


# ============================================================
# walk_forward
# ============================================================

def test_walk_forward_anchored_handles_no_grid():
    """Empty grid (test_end before first_test_start) returns empty summary."""
    from trader.walk_forward import run_anchored_walk_forward
    out = run_anchored_walk_forward(
        strategy_fn=lambda asof: [],
        price_panel_fn=lambda s, e, syms: {},
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_end="2024-06-30",  # before train_end → no windows
        test_days=63, step_days=63,
    )
    assert out.n_windows == 0


def test_walk_forward_synthetic_returns():
    """Run anchored walk-forward with synthetic strategy + price data.
    Verifies the harness aggregates correctly when given known returns."""
    from trader.walk_forward import run_anchored_walk_forward

    class FakeCandidate:
        def __init__(self, ticker): self.ticker = ticker

    def fake_strategy(asof):
        return [FakeCandidate("AAA"), FakeCandidate("BBB")]

    def fake_panel(start, end, picks):
        # 60 trading days of constant +0.001 returns per pick
        from datetime import datetime as _dt, timedelta as _td
        s = _dt.fromisoformat(start).date()
        out = {}
        for sym in picks:
            seq = []
            price = 100.0
            for i in range(60):
                d = s + _td(days=i)
                price *= 1.001
                seq.append((d, price))
            out[sym] = seq
        return out

    summary = run_anchored_walk_forward(
        strategy_fn=fake_strategy,
        price_panel_fn=fake_panel,
        train_start="2023-01-01",
        train_end="2024-01-01",
        test_end="2024-12-31",
        test_days=60, step_days=60,
    )
    assert summary.n_windows >= 4  # ~6 windows fit in 12 months
    valid = [w for w in summary.windows if w.period_return is not None]
    assert valid, "no valid windows in walk-forward"
    # Each window's picks should be the synthetic strategy's output
    assert all("AAA" in w.picks for w in valid)
    # All windows should be positive (constant +0.1% daily returns)
    assert summary.pct_windows_positive == 1.0


def test_walk_forward_rolling_signature():
    """Rolling variant exposes same surface."""
    import inspect
    from trader.walk_forward import run_rolling_walk_forward
    sig = inspect.signature(run_rolling_walk_forward)
    assert "train_days" in sig.parameters
    assert "first_test_start" in sig.parameters


def test_walk_forward_summary_dataclass_has_all_fields():
    from trader.walk_forward import WalkForwardSummary
    s = WalkForwardSummary(
        n_windows=0, mean_period_return=None, median_period_return=None,
        mean_annualized_return=None, mean_sharpe=None, median_sharpe=None,
        sharpe_stdev=None, pct_windows_positive=None,
        worst_window_return=None, best_window_return=None,
    )
    # All fields are optional; instantiating with Nones must not raise
    assert s.n_windows == 0
    assert s.windows == []


# ============================================================
# parameter_sensitivity script
# ============================================================

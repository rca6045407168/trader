"""v3.73.13 — Frozen-snapshot regression test for rank_momentum.

The DD identified this gap: a refactor of rank_momentum (or any
upstream momentum_score / fetch_history change) could silently change
the picks. No test currently catches that.

This test pins a known-input → known-output for rank_momentum on a
synthetic, deterministic price panel. Any refactor that changes the
output triggers the test failure.

The synthetic panel uses a fixed numpy seed so the test is reproducible
without network access.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


def _synthetic_panel():
    """Deterministic 30-month price panel for 10 names. Fixed seed
    means the test is reproducible. The names + dates + scores are
    stable across runs."""
    import numpy as np
    import pandas as pd

    np.random.seed(2026)
    cols = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
            "JPM", "JNJ", "XOM", "CAT", "WMT"]
    # 30 months × ~21 trading days = ~630 days
    dates = pd.bdate_range("2024-01-01", periods=630)
    # Different drift per name so the rank is stable
    drifts = np.array([0.0010, 0.0008, 0.0012, 0.0009, 0.0011,
                        0.0005, 0.0003, 0.0004, 0.0007, 0.0002])
    daily_rets = np.random.randn(len(dates), len(cols)) * 0.012 + drifts
    panel = 100 * np.cumprod(1 + daily_rets, axis=0)
    return pd.DataFrame(panel, index=dates, columns=cols)


def test_rank_momentum_frozen_snapshot():
    """Pin the picks rank_momentum produces on a deterministic panel.
    A refactor that changes the strategy's picks must explicitly
    update this snapshot — the test catches silent drift."""
    from trader.strategy import rank_momentum
    import pandas as pd

    panel = _synthetic_panel()
    asof = panel.index[-1]

    # Mock fetch_history to return the synthetic panel
    import trader.strategy as strategy_mod

    original_fetch = strategy_mod.fetch_history
    strategy_mod.fetch_history = lambda universe, start: panel

    try:
        candidates = rank_momentum(
            list(panel.columns),
            top_n=5,
            end_date=asof,
        )
    finally:
        strategy_mod.fetch_history = original_fetch

    # Frozen snapshot — these are the picks that the v3.73.13 baseline
    # implementation produces. Any refactor that changes the output
    # MUST update this assertion explicitly. That's the point.
    picks_in_order = [c.ticker for c in candidates]
    # Captured 2026-05-05 against the v3.73.13 baseline. The drift
    # detector — any change to this list signals a strategy refactor.
    EXPECTED_PICKS = ["AMZN", "MSFT", "JPM", "GOOGL", "NVDA"]

    assert picks_in_order == EXPECTED_PICKS, (
        f"rank_momentum output drifted!\n"
        f"  baseline: {EXPECTED_PICKS}\n"
        f"  actual:   {picks_in_order}\n"
        f"\n"
        f"If this is an INTENTIONAL strategy change, update "
        f"EXPECTED_PICKS in this test and document the rationale "
        f"in the commit message. If unintentional, find and revert "
        f"the regression."
    )


def test_rank_momentum_score_signs_match_drifts():
    """A weak invariant: high-drift names (NVDA, AMZN) should have
    higher momentum scores than low-drift names (XOM, WMT). Catches
    a sign-flip bug in momentum_score."""
    from trader.strategy import rank_momentum
    import trader.strategy as strategy_mod

    panel = _synthetic_panel()
    asof = panel.index[-1]
    original_fetch = strategy_mod.fetch_history
    strategy_mod.fetch_history = lambda universe, start: panel

    try:
        candidates = rank_momentum(
            list(panel.columns),
            top_n=10,
            end_date=asof,
        )
    finally:
        strategy_mod.fetch_history = original_fetch

    by_ticker = {c.ticker: c.score for c in candidates}
    # The high-drift names should outrank the low-drift names
    assert by_ticker["NVDA"] > by_ticker["XOM"], \
        f"sign-flip? NVDA score {by_ticker['NVDA']:.3f} should beat XOM {by_ticker['XOM']:.3f}"
    assert by_ticker["AMZN"] > by_ticker["WMT"], \
        f"sign-flip? AMZN score {by_ticker['AMZN']:.3f} should beat WMT {by_ticker['WMT']:.3f}"


def test_score_universe_is_deterministic():
    """Calling _score_universe twice on the same input must produce
    identical output. Catches hidden randomness or non-determinism."""
    import pandas as pd
    from trader.eval_strategies import _score_universe

    panel = _synthetic_panel()
    asof = panel.index[-1]
    out_a = _score_universe(asof, panel)
    out_b = _score_universe(asof, panel)
    assert out_a == out_b, "non-deterministic scoring detected"

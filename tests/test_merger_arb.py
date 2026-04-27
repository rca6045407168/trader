"""Tests for merger_arb module."""
from datetime import date
import pytest
from trader.merger_arb import MergerDeal, analyze_deal


def test_attractive_deal_rated_buy():
    deal = MergerDeal(
        acquirer="BIG_CO",
        target_symbol="TGT",
        deal_price=100.0,
        deal_type="all_cash",
        announced_date=date(2026, 4, 1),
        expected_close=date(2026, 7, 1),  # 90 days
        break_risk_estimate=0.05,
    )
    # Trading at $97 — 3% gross spread, ~12% annualized
    a = analyze_deal(deal, market_price=97.0, asof=date(2026, 4, 27))
    assert a.spread_pct > 0.02
    assert a.verdict == "BUY"
    assert a.annualized_yield > 0.06


def test_compressed_spread_skipped():
    deal = MergerDeal(
        acquirer="BIG_CO",
        target_symbol="TGT",
        deal_price=100.0,
        deal_type="all_cash",
        announced_date=date(2026, 4, 1),
        expected_close=date(2026, 12, 31),  # long timeline
        break_risk_estimate=0.10,
    )
    # Trading at $99.5 — essentially closed; not worth bothering
    a = analyze_deal(deal, market_price=99.5, asof=date(2026, 4, 27))
    assert a.verdict == "SKIP"


def test_high_break_risk_demoted():
    deal = MergerDeal(
        acquirer="BIG_CO",
        target_symbol="TGT",
        deal_price=100.0,
        deal_type="all_cash",
        announced_date=date(2026, 4, 1),
        expected_close=date(2026, 7, 1),
        break_risk_estimate=0.40,  # very high regulatory risk
    )
    # Even with attractive spread, break risk dominates
    a = analyze_deal(deal, market_price=95.0, asof=date(2026, 4, 27))
    # high break risk → low EV → should NOT be BUY
    assert a.verdict in ("WATCH", "SKIP")


def test_already_above_deal_price_negative_spread():
    deal = MergerDeal(
        acquirer="BIG_CO",
        target_symbol="TGT",
        deal_price=100.0,
        deal_type="all_cash",
        announced_date=date(2026, 4, 1),
        expected_close=date(2026, 7, 1),
        break_risk_estimate=0.05,
    )
    # Trading ABOVE deal price (market expects bidder bump)
    a = analyze_deal(deal, market_price=102.0, asof=date(2026, 4, 27))
    assert a.spread_pct < 0
    assert a.verdict == "SKIP"

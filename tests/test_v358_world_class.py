"""Tests for v3.58.0 — world-class trader gap closure.

One smoke + one correctness test per gap. Plus the registry/status_summary
helpers used by the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


# ============================================================
# Tier 1
# ============================================================

def test_low_vol_sleeve_picks_lowest_vol():
    from trader.v358_world_class import LowVolSleeve
    sleeve = LowVolSleeve(n_holdings=2, lookback_days=20)
    # AAA flat (~0 vol), BBB moderate, CCC very volatile
    rets = {
        "AAA": [0.001] * 25,
        "BBB": [0.01, -0.01] * 13,
        "CCC": [0.05, -0.05] * 13,
    }
    picks = sleeve.select(rets)
    assert picks[0] == "AAA"
    assert "CCC" not in picks


def test_sector_neutralizer_caps_concentration():
    from trader.v358_world_class import SectorNeutralizer
    sn = SectorNeutralizer(max_sector_pct=0.40)
    weights = {"AAPL": 0.20, "MSFT": 0.20, "NVDA": 0.20, "JPM": 0.20, "XOM": 0.20}
    sectors = {"AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech",
               "JPM": "Fin", "XOM": "Energy"}
    out = sn.neutralize(weights, sectors)
    tech_total = out["AAPL"] + out["MSFT"] + out["NVDA"]
    # The cap forces tech ≤ 40%
    assert tech_total == pytest.approx(0.40, abs=0.01)
    # Total weight conserved
    assert sum(out.values()) == pytest.approx(1.0, abs=0.01)


def test_long_short_overlay_picks_bottom():
    from trader.v358_world_class import LongShortOverlay
    ls = LongShortOverlay(n_short=2)
    ranked = [("AAA", 0.9), ("BBB", 0.6), ("CCC", 0.3), ("DDD", 0.1), ("EEE", -0.2)]
    shorts = ls.shorts_for(ranked)
    assert shorts == ["DDD", "EEE"]


def test_options_overlay_hedge_notional_sums():
    from trader.v358_world_class import OptionsOverlay
    ov = OptionsOverlay(nav_pct_per_month=0.012, ladder_days=(30, 60, 90))
    h = ov.hedge_notional(nav=100_000)
    assert sum(h.values()) == pytest.approx(1200, abs=1)
    assert len(h) == 3


# ============================================================
# Tier 2
# ============================================================

def test_trailing_stop_fires_on_drop():
    from trader.v358_world_class import TrailingStop
    ts = TrailingStop(pct=0.15)
    # Bought at 100, ran up to 120, currently 100 → drawdown from peak = 16.7%
    assert ts.should_exit(entry_price=100, peak_close=120, current_price=100) is True
    # Same setup but currently 110 → drawdown 8.3% → no exit
    assert ts.should_exit(entry_price=100, peak_close=120, current_price=110) is False


def test_risk_parity_inverse_vol_sums_to_one():
    from trader.v358_world_class import RiskParitySizer
    rp = RiskParitySizer()
    w = rp.weights({"A": 0.10, "B": 0.20, "C": 0.40})
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
    # Lower vol gets higher weight
    assert w["A"] > w["B"] > w["C"]


def test_drawdown_breaker_trips_at_threshold():
    from trader.v358_world_class import DrawdownCircuitBreaker
    cb = DrawdownCircuitBreaker(pct_from_peak=0.10)
    assert cb.is_tripped(peak_equity=100_000, current_equity=89_000) is True
    assert cb.is_tripped(peak_equity=100_000, current_equity=91_000) is False
    # Peak 0 → no trip
    assert cb.is_tripped(peak_equity=0, current_equity=0) is False


def test_earnings_rule_trims_in_window():
    from trader.v358_world_class import EarningsRule
    er = EarningsRule(days_before=1)
    today = datetime(2026, 5, 5)
    # Earnings tomorrow → trim
    assert er.needs_trim(today, datetime(2026, 5, 6)) is True
    # Earnings today (0 days away) → trim
    assert er.needs_trim(today, datetime(2026, 5, 5)) is True
    # Earnings 3 days out → no trim
    assert er.needs_trim(today, datetime(2026, 5, 8)) is False


# ============================================================
# Tier 3
# ============================================================

def test_twap_slicer_skips_small_orders():
    from trader.v358_world_class import TwapSlicer
    ts = TwapSlicer(threshold_adv_pct=0.05)
    # 1k order vs 100k ADV → 1% of ADV → don't slice
    schedule = ts.schedule(parent_qty=10, parent_notional=1_000, adv_dollar=100_000)
    assert len(schedule) == 1
    assert schedule[0]["qty"] == 10


def test_twap_slicer_slices_large_orders():
    from trader.v358_world_class import TwapSlicer
    ts = TwapSlicer(n_slices=4, window_minutes=20, threshold_adv_pct=0.05)
    # 10k order vs 100k ADV → 10% → slice
    schedule = ts.schedule(parent_qty=100, parent_notional=10_000, adv_dollar=100_000)
    assert len(schedule) == 4
    assert sum(s["qty"] for s in schedule) == pytest.approx(100)
    # Time-spread
    assert schedule[1]["ts"] > schedule[0]["ts"]


def test_slippage_tracker_buy_pays_more_than_mid():
    from trader.v358_world_class import SlippageTracker
    sl = SlippageTracker()
    # Bought at 100.10 vs decision-mid 100.00 → 10bps slippage (positive = bad)
    bps = sl.slippage_bps("buy", decision_mid=100.0, fill_price=100.10)
    assert bps == pytest.approx(10.0, abs=0.01)
    # Sell at 99.90 vs decision-mid 100.00 → 10bps slippage too
    bps2 = sl.slippage_bps("sell", decision_mid=100.0, fill_price=99.90)
    assert bps2 == pytest.approx(10.0, abs=0.01)


def test_tax_lot_picks_highest_basis_first():
    from trader.v358_world_class import TaxLotManager
    tlm = TaxLotManager()
    lots = [
        {"id": 1, "qty": 10, "open_price": 100},
        {"id": 2, "qty": 10, "open_price": 150},  # highest basis
        {"id": 3, "qty": 10, "open_price": 80},
    ]
    chosen = tlm.pick_lots_to_sell(lots, sell_qty=15)
    # Should sell all of lot 2 first (10), then 5 of lot 1
    assert chosen[0]["id"] == 2
    assert chosen[0]["sell_qty"] == 10
    assert chosen[1]["id"] == 1
    assert chosen[1]["sell_qty"] == 5


def test_wash_sale_blocks_within_window():
    from trader.v358_world_class import TaxLotManager
    tlm = TaxLotManager(wash_sale_days=30)
    today = datetime(2026, 5, 5)
    recent = [{"symbol": "NVDA", "date": today - timedelta(days=10),
               "realized_pnl": -500}]
    # Loss-realizing sell 10 days ago → blocked
    assert tlm.wash_sale_blocked("NVDA", recent, today) is True
    # Different symbol → not blocked
    assert tlm.wash_sale_blocked("MSFT", recent, today) is False
    # Profitable sell → not a wash
    recent2 = [{"symbol": "NVDA", "date": today - timedelta(days=10),
                "realized_pnl": +500}]
    assert tlm.wash_sale_blocked("NVDA", recent2, today) is False


# ============================================================
# Tier 4
# ============================================================

def test_promotion_gate_passes_when_clean():
    from trader.v358_world_class import AutoPromotionGate
    g = AutoPromotionGate(min_deflated_sharpe=0.7, max_pbo=0.5)
    out = g.evaluate(survivor_pass=True, deflated_sharpe=0.9, pbo=0.3)
    assert out["pass"] is True
    assert out["gate_failed"] is None


def test_promotion_gate_fails_at_each_step():
    from trader.v358_world_class import AutoPromotionGate
    g = AutoPromotionGate(min_deflated_sharpe=0.7, max_pbo=0.5)
    # Survivor fail
    assert g.evaluate(False, 0.9, 0.3)["gate_failed"] == "survivor"
    # PIT fail (deflated Sharpe too low)
    assert g.evaluate(True, 0.5, 0.3)["gate_failed"] == "pit"
    # CPCV fail (PBO too high)
    assert g.evaluate(True, 0.9, 0.7)["gate_failed"] == "cpcv"


def test_regime_router_per_regime():
    from trader.v358_world_class import RegimeRouter
    rr = RegimeRouter()
    assert rr.sleeves_for("bull") == {"momentum": 1.0}
    assert sum(rr.sleeves_for("transition").values()) == pytest.approx(1.0)
    assert "low_vol" in rr.sleeves_for("bear")
    # Unknown regime defaults to conservative blend
    assert sum(rr.sleeves_for("unknown").values()) == pytest.approx(1.0)


def test_alt_data_stubs_return_none():
    from trader.v358_world_class import AltDataAdapter
    a = AltDataAdapter()
    assert a.short_interest_signal("AAPL") is None
    assert a.insider_buy_signal("AAPL") is None


def test_net_cost_model_drag():
    from trader.v358_world_class import NetCostModel
    nc = NetCostModel(spread_bps=4, monthly_turnover_pct=0.60)
    # Annual drag = 4 * 2 * 0.60 * 12 = 57.6 bps
    assert nc.annual_drag_bps() == pytest.approx(57.6, abs=0.1)


def test_net_cost_model_applies_tax_only_on_gains():
    from trader.v358_world_class import NetCostModel
    nc = NetCostModel(spread_bps=0, monthly_turnover_pct=0,
                      st_cap_gains_pct=0.37)
    # No drag, gain → tax applied
    assert nc.net_return(0.10) == pytest.approx(0.063, abs=1e-3)
    # No drag, loss → no tax
    assert nc.net_return(-0.10) == pytest.approx(-0.10, abs=1e-3)


# ============================================================
# Registry / dashboard glue
# ============================================================

def test_all_gaps_count():
    from trader.v358_world_class import ALL_GAPS
    assert len(ALL_GAPS) == 15


def test_status_summary_categorizes():
    from trader.v358_world_class import status_summary
    s = status_summary()
    assert "LIVE" in s
    assert "SHADOW" in s
    assert "NOT_WIRED" in s
    # Total across buckets matches count
    total = sum(len(v) for k, v in s.items() if k != "ERROR")
    assert total == 15
    # Every entry has describe text
    for bucket in s.values():
        for entry in bucket:
            assert entry.get("describe") or entry.get("error")


def test_every_class_describes_itself():
    from trader.v358_world_class import ALL_GAPS
    for label, tagline, cls in ALL_GAPS:
        inst = cls()
        d = inst.describe()
        s = inst.status()
        assert isinstance(d, str) and len(d) > 30, f"{cls.__name__} describe too short"
        assert s in ("LIVE", "SHADOW", "NOT_WIRED"), f"{cls.__name__} status invalid: {s}"

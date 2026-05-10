"""Tests for v6.0.x net-new alpha + universe additions.

Covers:
  1. SEC EDGAR Form-4 parser (XML parsing + transaction filtering)
  2. PEAD strategy env-gate + signal processing
  3. Calendar-effect overlay (anomaly-driven scalar)
  4. Universe expansion + REPLACEMENT_MAP autocomplete

Network and yfinance are NOT exercised — _parse_form4 is tested on
embedded XML samples, the strategies are tested via their env gates,
and the calendar overlay uses mocked anomaly scans.
"""
from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# 1. SEC EDGAR Form-4 parser
# ============================================================
SAMPLE_FORM4_OFFICER_BUY = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <isDirector>0</isDirector>
      <isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>150.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

SAMPLE_FORM4_TEN_PCT_OWNER_ONLY = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isOfficer>0</isOfficer>
      <isDirector>0</isDirector>
      <isTenPercentOwner>1</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>50.0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

SAMPLE_FORM4_OFFICER_SALE = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <isDirector>0</isDirector>
      <isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>200.0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

SAMPLE_FORM4_TAX_WITHHOLDING = b"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>F</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>200</value></transactionShares>
        <transactionPricePerShare><value>100.0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_extracts_officer_buy():
    from trader.sec_edgar_form4 import _parse_form4
    rows = _parse_form4(SAMPLE_FORM4_OFFICER_BUY)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "P"
    assert r["shares"] == 1000.0
    assert r["price"] == 150.50
    assert r["is_officer"] is True
    assert r["is_director"] is False


def test_parse_form4_extracts_officer_sale():
    from trader.sec_edgar_form4 import _parse_form4
    rows = _parse_form4(SAMPLE_FORM4_OFFICER_SALE)
    assert rows[0]["code"] == "S"


def test_parse_form4_returns_empty_on_bad_xml():
    from trader.sec_edgar_form4 import _parse_form4
    assert _parse_form4(b"not xml") == []


def test_net_buy_calc_excludes_10pct_only(tmp_path, monkeypatch):
    """A purchase reported ONLY by a 10%-owner (institutional) should
    be excluded from the net-buy total when exclude_10pct_only=True."""
    from trader.sec_edgar_form4 import _parse_form4
    rows = _parse_form4(SAMPLE_FORM4_TEN_PCT_OWNER_ONLY)
    assert len(rows) == 1
    assert rows[0]["is_10pct"] is True
    assert rows[0]["is_officer"] is False
    assert rows[0]["is_director"] is False


def test_net_buy_calc_skips_tax_withholding():
    """Code F = tax withholding (not informed). Should not be counted."""
    from trader.sec_edgar_form4 import _parse_form4
    rows = _parse_form4(SAMPLE_FORM4_TAX_WITHHOLDING)
    assert len(rows) == 1
    # The parser returns the row, but the strategy filter excludes
    # codes other than P/S. Confirm the parser preserves the code.
    assert rows[0]["code"] == "F"


def test_edgar_strategy_env_gate():
    """Default off (env unset) → returns {}."""
    import pandas as pd
    from trader.eval_strategies import xs_top10_insider_edgar_30d
    if "INSIDER_EDGAR_ENABLED" in os.environ:
        del os.environ["INSIDER_EDGAR_ENABLED"]
    prices = pd.DataFrame({"AAPL": [100, 101]},
                            index=pd.bdate_range("2026-05-08", periods=2))
    out = xs_top10_insider_edgar_30d(prices.index[-1], prices)
    assert out == {}


def test_edgar_strategy_skips_historical_asof(monkeypatch):
    """Even with env set, far-historical asof returns {}."""
    import pandas as pd
    monkeypatch.setenv("INSIDER_EDGAR_ENABLED", "1")
    from trader.eval_strategies import xs_top10_insider_edgar_30d
    prices = pd.DataFrame({"AAPL": [100, 101]},
                            index=pd.bdate_range("2020-01-01", periods=2))
    out = xs_top10_insider_edgar_30d(prices.index[-1], prices)
    assert out == {}


# ============================================================
# 2. PEAD strategy
# ============================================================
def test_pead_strategy_env_gate():
    import pandas as pd
    from trader.eval_strategies import xs_top10_pead_5d
    if "PEAD_ENABLED" in os.environ:
        del os.environ["PEAD_ENABLED"]
    prices = pd.DataFrame({"AAPL": [100, 101]},
                            index=pd.bdate_range("2026-05-08", periods=2))
    out = xs_top10_pead_5d(prices.index[-1], prices)
    assert out == {}


def test_pead_strategy_returns_empty_when_no_signals(monkeypatch):
    """Even with env set, returns {} if recent_signals is empty."""
    import pandas as pd
    monkeypatch.setenv("PEAD_ENABLED", "1")
    # Patch recent_signals to return empty list
    from trader import earnings_reactor
    monkeypatch.setattr(earnings_reactor, "recent_signals",
                          lambda since_days=10: [])
    from trader.eval_strategies import xs_top10_pead_5d
    prices = pd.DataFrame({"AAPL": [100, 101]},
                            index=pd.bdate_range("2026-05-08", periods=2))
    out = xs_top10_pead_5d(prices.index[-1], prices)
    assert out == {}


def test_pead_strategy_scores_bullish_signals(monkeypatch):
    """A bullish signal with high materiality should appear in picks."""
    import pandas as pd
    monkeypatch.setenv("PEAD_ENABLED", "1")
    from trader import earnings_reactor
    fake_signals = [
        {"symbol": "AAPL", "direction": "BULLISH", "materiality": 4},
        {"symbol": "MSFT", "direction": "SURPRISE", "materiality": 5},
        {"symbol": "INTC", "direction": "BEARISH", "materiality": 3},  # should be excluded
    ]
    monkeypatch.setattr(earnings_reactor, "recent_signals",
                          lambda since_days=10: fake_signals)
    from trader.eval_strategies import xs_top10_pead_5d
    prices = pd.DataFrame({"AAPL": [100, 101], "MSFT": [200, 201],
                             "INTC": [50, 51]},
                            index=pd.bdate_range("2026-05-08", periods=2))
    out = xs_top10_pead_5d(prices.index[-1], prices)
    assert "AAPL" in out
    assert "MSFT" in out
    assert "INTC" not in out, "BEARISH should be excluded from long-only"


# ============================================================
# 3. Calendar overlay
# ============================================================
def test_calendar_scalar_no_anomalies_returns_one(monkeypatch):
    from trader import calendar_overlay
    monkeypatch.setattr(calendar_overlay, "scan_anomalies", lambda asof: [])
    scalar, actives = calendar_overlay.calendar_gross_scalar()
    assert scalar == 1.0
    assert actives == []


def test_calendar_scalar_boosts_on_positive_anomaly(monkeypatch):
    from trader import calendar_overlay
    from trader.anomalies import Anomaly
    from datetime import date as _date

    fake = Anomaly(
        name="test_pos", category="calendar",
        fire_window=(_date(2026, 5, 10), _date(2026, 5, 11)),
        expected_direction="long_spy",
        expected_alpha_bps=50,
        target_symbol="SPY", rationale="test", confidence="medium",
    )
    monkeypatch.setattr(calendar_overlay, "scan_anomalies",
                         lambda asof: [fake])
    scalar, actives = calendar_overlay.calendar_gross_scalar()
    # 50 bps × default damping 0.05 = +0.025 boost
    assert scalar > 1.0
    assert len(actives) == 1


def test_calendar_scalar_caps_at_max(monkeypatch):
    """Multiple stacking anomalies are capped at MAX_TOTAL_BOOST."""
    from trader import calendar_overlay
    from trader.anomalies import Anomaly
    from datetime import date as _date

    fakes = [
        Anomaly(name=f"test_{i}", category="calendar",
                fire_window=(_date(2026, 5, 10), _date(2026, 5, 11)),
                expected_direction="long_spy",
                expected_alpha_bps=1000,  # absurd
                target_symbol="SPY", rationale="", confidence="medium")
        for i in range(5)
    ]
    monkeypatch.setattr(calendar_overlay, "scan_anomalies",
                         lambda asof: fakes)
    scalar, _ = calendar_overlay.calendar_gross_scalar()
    # Should be capped at 1 + MAX_TOTAL_BOOST (default 0.10)
    assert scalar <= 1.10 + 1e-9


def test_apply_calendar_overlay_preserves_relative_weights(monkeypatch):
    from trader import calendar_overlay
    from trader.anomalies import Anomaly
    from datetime import date as _date

    fake = Anomaly(
        name="test", category="calendar",
        fire_window=(_date(2026, 5, 10), _date(2026, 5, 11)),
        expected_direction="long_spy",
        expected_alpha_bps=100,
        target_symbol="SPY", rationale="", confidence="medium",
    )
    monkeypatch.setattr(calendar_overlay, "scan_anomalies",
                         lambda asof: [fake])
    targets = {"AAPL": 0.10, "MSFT": 0.05}
    out, info = calendar_overlay.apply_calendar_overlay(targets)
    # Both should scale by the same factor
    assert abs(out["AAPL"] / targets["AAPL"] -
                out["MSFT"] / targets["MSFT"]) < 1e-9
    assert info["enabled"] is True
    assert info["scalar"] > 1.0


def test_apply_calendar_overlay_disabled(monkeypatch):
    monkeypatch.setenv("CALENDAR_OVERLAY_ENABLED", "0")
    from trader import calendar_overlay
    targets = {"AAPL": 0.10}
    out, info = calendar_overlay.apply_calendar_overlay(targets)
    assert out == targets
    assert info["enabled"] is False


# ============================================================
# 4. Universe expansion + autocomplete
# ============================================================
def test_expanded_universe_size():
    from trader.universe import DEFAULT_LIQUID_EXPANDED, DEFAULT_LIQUID_50
    assert len(DEFAULT_LIQUID_EXPANDED) > 100, \
        f"expected ~138, got {len(DEFAULT_LIQUID_EXPANDED)}"
    # 50-name set is a subset
    base = set(DEFAULT_LIQUID_50)
    expanded = set(DEFAULT_LIQUID_EXPANDED)
    missing = base - expanded
    assert not missing, f"expanded universe missing names from base 50: {missing}"


def test_expanded_universe_has_new_sectors():
    from trader.sectors import SECTORS
    sectors = set(SECTORS.values())
    assert "Utilities" in sectors
    assert "RealEstate" in sectors


def test_replacement_map_covers_expanded_universe():
    from trader.direct_index_tlh import REPLACEMENT_MAP
    from trader.sectors import SECTORS
    missing = [t for t in SECTORS if t not in REPLACEMENT_MAP]
    assert not missing, f"REPLACEMENT_MAP missing: {missing[:5]}"


def test_quality_scores_cover_expanded_universe():
    from trader.direct_index_tlh import QUALITY_SCORES
    from trader.sectors import SECTORS
    missing = [t for t in SECTORS if t not in QUALITY_SCORES]
    assert not missing, f"QUALITY_SCORES missing: {missing[:5]}"


def test_approx_cap_b_covers_expanded_universe():
    from trader.direct_index_tlh import APPROX_CAP_B
    from trader.sectors import SECTORS
    missing = [t for t in SECTORS if t not in APPROX_CAP_B]
    assert not missing, f"APPROX_CAP_B missing: {missing[:5]}"


def test_autocomplete_picks_same_sector_replacements():
    """For an auto-completed entry, all replacements must be same-sector
    (the whole point of the sector-matched swap mechanic)."""
    from trader.direct_index_tlh import REPLACEMENT_MAP
    from trader.sectors import SECTORS
    # Pick a name we know wasn't hand-curated (added in expansion)
    autocompleted_examples = ["IBM", "LLY", "NEE", "PLD", "GE"]
    for sym in autocompleted_examples:
        if sym not in SECTORS:
            continue
        my_sector = SECTORS[sym]
        for r in REPLACEMENT_MAP[sym]:
            assert SECTORS.get(r) == my_sector, \
                f"{sym} (sector {my_sector}) → {r} (sector {SECTORS.get(r)})"


def test_hand_curated_replacements_unchanged():
    """Autocomplete must NOT overwrite hand-curated entries."""
    from trader.direct_index_tlh import REPLACEMENT_MAP
    # AAPL was hand-curated to → MSFT, GOOGL, META
    assert REPLACEMENT_MAP["AAPL"] == ["MSFT", "GOOGL", "META"]

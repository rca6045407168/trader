"""Tests for v3.59.2 — thesis ledger (operator-alpha scaffold)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """Redirect ledger DB to a tmp path."""
    import trader.thesis_ledger as tl
    monkeypatch.setattr(tl, "LEDGER_PATH", tmp_path / "thesis.db")
    return tl


def test_add_and_list(isolated_ledger):
    tl = isolated_ledger
    obs_id = tl.add_observation("ODFL", "positive", 4, "internal_meeting",
                                  "Customer reports rate hikes accepted without pushback")
    assert isinstance(obs_id, str) and len(obs_id) > 10
    obs = tl.list_observations(ticker="ODFL")
    assert len(obs) == 1
    assert obs[0]["ticker"] == "ODFL"
    assert obs[0]["direction"] == "positive"
    assert obs[0]["confidence"] == 4


def test_invalid_direction_rejected(isolated_ledger):
    tl = isolated_ledger
    with pytest.raises(ValueError):
        tl.add_observation("ODFL", "going_up", 4, "internal_meeting", "")


def test_invalid_confidence_rejected(isolated_ledger):
    tl = isolated_ledger
    with pytest.raises(ValueError):
        tl.add_observation("ODFL", "positive", 7, "internal_meeting", "")
    with pytest.raises(ValueError):
        tl.add_observation("ODFL", "positive", 0, "internal_meeting", "")


def test_72h_lag_enforced(isolated_ledger):
    """Newly logged observation should NOT be tradeable for 72h."""
    tl = isolated_ledger
    obs_id = tl.add_observation("KNX", "negative", 3, "wechat", "Capacity loosening")
    # Just-added observation is < 72h old → not tradeable
    assert tl.is_tradeable(obs_id) is False


def test_unknown_obs_not_tradeable(isolated_ledger):
    tl = isolated_ledger
    assert tl.is_tradeable("nonexistent-id") is False


def test_update_outcome(isolated_ledger):
    tl = isolated_ledger
    obs_id = tl.add_observation("XPO", "positive", 5, "earnings_call",
                                  "Mgmt called bottom in spot rates")
    assert tl.update_outcome(obs_id, "validated_+8pct_60d") is True
    obs = tl.list_observations(ticker="XPO")
    assert obs[0]["realized_outcome"] == "validated_+8pct_60d"


def test_update_unknown_returns_false(isolated_ledger):
    tl = isolated_ledger
    assert tl.update_outcome("nonexistent", "whatever") is False


def test_stats_by_direction(isolated_ledger):
    tl = isolated_ledger
    tl.add_observation("ODFL", "positive", 4, "internal_meeting", "")
    tl.add_observation("KNX", "positive", 3, "apollo", "")
    tl.add_observation("FDX", "negative", 2, "linkedin", "")
    s = tl.stats_by_direction()
    assert s.get("positive", {}).get("count") == 2
    assert s.get("negative", {}).get("count") == 1


def test_min_lag_constant():
    from trader.thesis_ledger import MIN_LAG_HOURS
    assert MIN_LAG_HOURS == 72  # BLINDSPOTS guardrail

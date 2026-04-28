"""Tests for the A/B framework."""
import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.db"
        monkeypatch.setattr("trader.journal.DB_PATH", path)
        # Also clear the in-memory registry between tests
        from trader import ab
        ab._REGISTRY.clear()
        yield path


def test_register_and_get_live():
    from trader.ab import register_variant, get_live, get_shadows

    def dummy_fn(universe, equity, account_state, **kwargs):
        return {"AAPL": 0.10}

    register_variant("v1", "test", "1.0", "live", dummy_fn, description="test live")
    register_variant("v2", "test", "2.0", "shadow", dummy_fn, description="test shadow")
    register_variant("v3", "test", "1.5", "shadow", dummy_fn, description="test shadow 2")

    live = get_live()
    assert live is not None
    assert live.variant_id == "v1"
    shadows = get_shadows()
    assert len(shadows) == 2
    assert {s.variant_id for s in shadows} == {"v2", "v3"}


def test_multiple_live_raises():
    from trader.ab import register_variant, get_live

    def dummy_fn(universe, equity, account_state, **kwargs):
        return {}

    register_variant("v1", "x", "1.0", "live", dummy_fn)
    register_variant("v2", "y", "1.0", "live", dummy_fn)
    with pytest.raises(RuntimeError):
        get_live()


def test_shadow_decisions_persisted():
    from trader.ab import register_variant, run_shadows
    from trader.journal import _conn

    captured_calls = []

    def dummy_fn(universe, equity, account_state, **kwargs):
        captured_calls.append((universe, equity))
        return {"NVDA": 0.05, "AMD": 0.05}

    register_variant("shadow_v1", "test_shadow", "1.0", "shadow", dummy_fn,
                     description="test")
    results = run_shadows(universe=["NVDA", "AMD"], equity=100000, account_state={})

    assert "shadow_v1" in results
    assert results["shadow_v1"]["targets"] == {"NVDA": 0.05, "AMD": 0.05}

    with _conn() as c:
        rows = c.execute("SELECT variant_id, targets_json FROM shadow_decisions").fetchall()
    assert len(rows) == 1
    assert rows[0]["variant_id"] == "shadow_v1"
    import json
    assert json.loads(rows[0]["targets_json"]) == {"NVDA": 0.05, "AMD": 0.05}


def test_shadow_error_doesnt_crash():
    """If a shadow variant raises, run_shadows logs the error but continues."""
    from trader.ab import register_variant, run_shadows

    def broken_fn(universe, equity, account_state, **kwargs):
        raise ValueError("intentional break")

    def good_fn(universe, equity, account_state, **kwargs):
        return {"SPY": 0.5}

    register_variant("broken", "broken", "1.0", "shadow", broken_fn)
    register_variant("good", "good", "1.0", "shadow", good_fn)
    results = run_shadows(universe=["SPY"], equity=100000, account_state={})
    assert "error" in results["broken"]
    assert "ValueError" in results["broken"]["error"]
    assert results["good"]["targets"] == {"SPY": 0.5}


def test_invalid_status_rejected():
    from trader.ab import register_variant
    with pytest.raises(ValueError):
        register_variant("bad", "x", "1.0", "deployed-and-fancy",
                         lambda **k: {})

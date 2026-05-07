"""v3.73.26 — tests for the weekly ENFORCING canary.

Critical: the canary itself MUST pass under ENFORCING. If this test
fails in CI, the brake is broken — fail loud.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_canary_passes_under_enforcing(monkeypatch):
    """The canary's run_canary() must report ok=True under ENFORCING
    on a -13% synthetic DD. If this fails, the drawdown protocol is
    broken; the 30-run clock is meaningless until it's fixed."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    # Force-reload canary so it re-reads the env-driven mode
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)

    result = canary.run_canary(dd_pct=-0.13)
    assert result["ok"] is True, (
        f"ENFORCING canary FAILED — drawdown protocol is not mutating "
        f"targets correctly under -13% DD. detail={result['detail']}"
    )
    # ESCALATION should fire (-13% is in -12% to -15% band)
    assert result["tier"] in ("ESCALATION", "CATASTROPHIC"), \
        f"expected ESCALATION/CATASTROPHIC, got {result['tier']}"
    # Targets should mutate
    assert result["targets_mutated"] is True
    # Gross should reduce — at -13% (ESCALATION) we expect TRIM_TO_TOP5
    # to take it to ~30%; at deeper DDs LIQUIDATE_ALL takes it to 0%
    assert result["final_gross"] < result["initial_gross"]


def test_canary_advisory_does_not_mutate(monkeypatch):
    """Under ADVISORY, tier should fire but targets must NOT change.
    This proves ADVISORY is a true warning-only mode."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ADVISORY")
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)

    result = canary.run_canary(dd_pct=-0.13)
    assert result["ok"] is True
    assert result["tier"] in ("ESCALATION", "CATASTROPHIC")
    assert result["targets_mutated"] is False
    assert abs(result["final_gross"] - result["initial_gross"]) < 1e-6


def test_canary_synthetic_snaps_have_correct_dd():
    """The synthetic snapshot ramp must produce ~-13% drawdown."""
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore

    snaps = canary._build_synthetic_snaps(-0.13)
    eq = [s["equity"] for s in snaps]
    peak = max(eq)
    current = snaps[0]["equity"]  # newest first
    dd = current / peak - 1
    assert abs(dd - (-0.13)) < 0.001, f"expected ~-13%, got {dd*100:.2f}%"


# ============================================================
# v3.73.27 — tier-by-tier sweep tests. Each tier must produce
# the exact expected behavior. A partial regression (e.g.
# CATASTROPHIC silently broken but ESCALATION still works) would
# pass v3.73.26's single-DD canary but FAIL these.
# ============================================================
def test_full_tier_sweep_passes_under_enforcing(monkeypatch):
    """All 5 tiers must produce their exact expected behavior."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)

    sweep = canary.run_full_tier_sweep()
    assert sweep["ok"] is True, (
        f"Tier sweep FAILED: {sweep['passed']}/{sweep['total']} passed. "
        f"Failed tiers: " + "; ".join(
            r["detail"] for r in sweep["results"] if not r["ok"])
    )
    assert sweep["passed"] == 5
    assert sweep["total"] == 5


def test_green_tier_unchanged(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)
    r = canary.run_one_tier(-0.03, "GREEN", 0.7999, 0.8001, "NONE", "no DD")
    assert r["ok"] is True
    assert r["tier"] == "GREEN"
    assert r["action"] == "NONE"


def test_escalation_tier_trims_to_30pct(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)
    r = canary.run_one_tier(
        -0.13, "ESCALATION", 0.2999, 0.3001, "TRIM_TO_TOP5", "trim")
    assert r["ok"] is True
    assert abs(r["final_gross"] - 0.30) < 0.001


def test_catastrophic_tier_liquidates_all(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    if "weekly_enforcing_canary" in sys.modules:
        del sys.modules["weekly_enforcing_canary"]
    import weekly_enforcing_canary as canary  # type: ignore
    importlib.reload(canary)
    r = canary.run_one_tier(
        -0.17, "CATASTROPHIC", 0.0, 0.0001,
        "LIQUIDATE_ALL", "liquidate")
    assert r["ok"] is True
    assert r["final_gross"] < 0.001

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

"""Tests for v3.73.2 — four-threshold drawdown protocol.

Per docs/RISK_FRAMEWORK.md §6, the existing -8% kill is preserved
(unchanged behavior in ADVISORY mode) and three new tiers are added
around it (-5% / -12% / -15%) with pre-committed response actions.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Tier evaluator — direction × magnitude
# ============================================================
def test_tier_at_zero_dd_is_green():
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(0.0)
    assert t.name == "GREEN"


def test_tier_just_below_yellow_threshold_is_green():
    from trader.risk_manager import evaluate_drawdown_tier
    # -4% is below the -5% threshold
    t = evaluate_drawdown_tier(-0.04)
    assert t.name == "GREEN"


def test_tier_at_yellow_threshold_fires():
    """Boundary: exactly -5% should be YELLOW (>= comparison)."""
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(-0.05)
    assert t.name == "YELLOW"
    assert t.enforce_action == "PAUSE_GROWTH"


def test_tier_at_red_threshold_fires():
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(-0.08)
    assert t.name == "RED"
    assert t.enforce_action == "HALT_ALL"


def test_tier_at_escalation_threshold_fires():
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(-0.12)
    assert t.name == "ESCALATION"
    assert t.enforce_action == "TRIM_TO_TOP5"


def test_tier_at_catastrophic_threshold_fires():
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(-0.15)
    assert t.name == "CATASTROPHIC"
    assert t.enforce_action == "LIQUIDATE_ALL"


def test_tier_returns_worst_crossed():
    """At -20% DD we're past every threshold. Must return CATASTROPHIC,
    not the first matching tier."""
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(-0.20)
    assert t.name == "CATASTROPHIC"


def test_tier_handles_positive_dd():
    """Positive 'DD' (account UP from peak — shouldn't happen but
    handle defensively)."""
    from trader.risk_manager import evaluate_drawdown_tier
    t = evaluate_drawdown_tier(0.05)
    assert t.name == "GREEN"


# ============================================================
# Mode state machine
# ============================================================
def test_mode_default_is_advisory(monkeypatch):
    monkeypatch.delenv("DRAWDOWN_PROTOCOL_MODE", raising=False)
    from trader.risk_manager import drawdown_protocol_mode
    assert drawdown_protocol_mode() == "ADVISORY"


def test_mode_env_override_to_enforcing(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    from trader.risk_manager import drawdown_protocol_mode
    assert drawdown_protocol_mode() == "ENFORCING"


def test_mode_normalizes_case(monkeypatch):
    """User typing lowercase shouldn't break the comparison."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "enforcing")
    from trader.risk_manager import drawdown_protocol_mode
    assert drawdown_protocol_mode() == "ENFORCING"


# ============================================================
# apply_drawdown_protocol — ADVISORY behavior (no target mutation)
# ============================================================
def test_apply_advisory_returns_targets_unchanged(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ADVISORY")
    from trader.risk_manager import apply_drawdown_protocol
    targets = {"NVDA": 0.10, "AAPL": 0.08, "GOOGL": 0.07}
    snapshots = [
        {"date": "2026-04-01", "equity": 100_000},  # peak
        {"date": "2026-05-05", "equity": 90_000},   # -10% DD → RED
    ]
    new_targets, tier, warnings = apply_drawdown_protocol(
        equity=90_000, targets=targets, snapshots=snapshots,
    )
    assert new_targets == targets  # ADVISORY does NOT mutate
    assert tier.name == "RED"
    # warnings include the tier label + DD percentage so the user
    # knows why
    assert any("Red" in w for w in warnings)
    assert any("ADVISORY" in w for w in warnings)


def test_apply_with_no_snapshots_returns_green(tmp_path):
    """Edge case: fresh install with no daily_snapshot rows. Must
    return GREEN tier (no DD detectable), not crash."""
    from trader.risk_manager import apply_drawdown_protocol
    targets = {"NVDA": 0.10}
    new_targets, tier, warnings = apply_drawdown_protocol(
        equity=100_000, targets=targets, snapshots=None,
    )
    assert new_targets == targets
    assert tier.name == "GREEN"


def test_apply_with_no_drawdown_returns_green():
    """Equity at peak — no DD. Must return GREEN regardless of mode."""
    from trader.risk_manager import apply_drawdown_protocol
    targets = {"NVDA": 0.10}
    snapshots = [
        {"date": "2026-04-01", "equity": 100_000},
        {"date": "2026-05-05", "equity": 102_000},  # +2% UP
    ]
    new_targets, tier, _ = apply_drawdown_protocol(
        equity=102_000, targets=targets, snapshots=snapshots,
    )
    assert tier.name == "GREEN"


# ============================================================
# apply_drawdown_protocol — ENFORCING behavior (mutation)
# ============================================================
def test_apply_enforcing_escalation_trims_to_top5(monkeypatch):
    """At ESCALATION (-12% DD), with momentum_ranks provided,
    targets must be trimmed to the top-5 by rank, rescaled to 30% gross."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    from trader.risk_manager import apply_drawdown_protocol
    targets = {
        "A": 0.10, "B": 0.08, "C": 0.07, "D": 0.06, "E": 0.05,
        "F": 0.04, "G": 0.03, "H": 0.02, "I": 0.01, "J": 0.005,
    }
    snapshots = [
        {"date": "2026-04-01", "equity": 100_000},
        {"date": "2026-05-05", "equity": 87_000},  # -13% DD → ESCALATION
    ]
    momentum_ranks = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    new_targets, tier, _ = apply_drawdown_protocol(
        equity=87_000, targets=targets, snapshots=snapshots,
        momentum_ranks=momentum_ranks,
    )
    assert tier.name == "ESCALATION"
    # Only top 5 names remain
    assert set(new_targets.keys()) == {"A", "B", "C", "D", "E"}
    # Rescaled to 30% gross
    assert abs(sum(new_targets.values()) - 0.30) < 1e-6


def test_apply_enforcing_catastrophic_liquidates(monkeypatch):
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    from trader.risk_manager import apply_drawdown_protocol
    targets = {"A": 0.10, "B": 0.08}
    snapshots = [
        {"date": "2026-04-01", "equity": 100_000},
        {"date": "2026-05-05", "equity": 80_000},  # -20% DD → CATASTROPHIC
    ]
    new_targets, tier, _ = apply_drawdown_protocol(
        equity=80_000, targets=targets, snapshots=snapshots,
    )
    assert tier.name == "CATASTROPHIC"
    # All targets zeroed for liquidation
    assert all(w == 0.0 for w in new_targets.values())
    assert set(new_targets.keys()) == {"A", "B"}


def test_apply_enforcing_escalation_without_ranks_warns_not_mutates(
    monkeypatch,
):
    """If momentum_ranks isn't provided to ESCALATION, we should
    surface a warning rather than silently mutate or crash."""
    monkeypatch.setenv("DRAWDOWN_PROTOCOL_MODE", "ENFORCING")
    from trader.risk_manager import apply_drawdown_protocol
    targets = {"A": 0.10, "B": 0.08}
    snapshots = [
        {"date": "2026-04-01", "equity": 100_000},
        {"date": "2026-05-05", "equity": 87_000},
    ]
    new_targets, tier, warnings = apply_drawdown_protocol(
        equity=87_000, targets=targets, snapshots=snapshots,
        momentum_ranks=None,
    )
    assert tier.name == "ESCALATION"
    assert new_targets == targets  # not mutated
    assert any("ranks" in w.lower() for w in warnings)


# ============================================================
# Constants are correct values per RISK_FRAMEWORK §6
# ============================================================
def test_threshold_constants_match_doc():
    """The four threshold values must match docs/RISK_FRAMEWORK.md §6
    exactly. This protects against typo regressions."""
    from trader.risk_manager import (
        DRAWDOWN_YELLOW_PCT, DRAWDOWN_RED_PCT,
        DRAWDOWN_ESCALATION_PCT, DRAWDOWN_CATASTROPHIC_PCT,
    )
    assert DRAWDOWN_YELLOW_PCT == 0.05
    assert DRAWDOWN_RED_PCT == 0.08          # = MAX_DRAWDOWN_HALT_PCT (existing)
    assert DRAWDOWN_ESCALATION_PCT == 0.12
    assert DRAWDOWN_CATASTROPHIC_PCT == 0.15


def test_red_threshold_aliases_existing_kill():
    """v3.73.2 must NOT change the existing -8% kill behavior. The
    new RED tier is an ALIAS for the existing MAX_DRAWDOWN_HALT_PCT,
    not a replacement that could change the kill semantics."""
    from trader.risk_manager import (
        DRAWDOWN_RED_PCT, MAX_DRAWDOWN_HALT_PCT,
    )
    assert DRAWDOWN_RED_PCT == MAX_DRAWDOWN_HALT_PCT


# ============================================================
# Dashboard surface
# ============================================================
def test_dashboard_has_drawdown_panel_helper():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "def _render_drawdown_protocol_panel" in text


def test_overview_invokes_drawdown_panel():
    """The Overview view must actually call the panel — without this
    the tier evaluation is dead code from the user's perspective."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    view_idx = text.index("def view_overview")
    next_def = text.index("\ndef ", view_idx + 1)
    body = text[view_idx:next_def]
    assert "_render_drawdown_protocol_panel()" in body


def test_panel_shows_mode_and_flip_instructions():
    """The panel must surface the mode + how to flip it. Otherwise
    users don't know they can opt into ENFORCING."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_drawdown_protocol_panel")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "DRAWDOWN_PROTOCOL_MODE=ENFORCING" in body
    assert "ADVISORY" in body
    assert "ENFORCING" in body


def test_panel_links_to_source_doc():
    """The panel must point at RISK_FRAMEWORK.md §6 so the user can
    verify the tiers + responses match the spec."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_drawdown_protocol_panel")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "RISK_FRAMEWORK.md" in body


def test_dashboard_version_v3_73_2():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "v3.73.2" in text
    assert 'st.caption("v3.73.2' in text

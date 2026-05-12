"""Tests for scripts/first_live_dry_run.py — BROKER=public_live preview.

The script does live broker reads against Public.com, so we don't
end-to-end test it in CI (would require institutional API access).
Instead we verify the script's structure + safety contract via
source-text checks.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "first_live_dry_run.py"
)


def test_script_exists():
    assert SCRIPT_PATH.exists()


def test_script_loads_dotenv():
    """Script must load .env so PUBLIC_API_SECRET resolves the same
    way as test_public_connection.py."""
    txt = SCRIPT_PATH.read_text()
    assert "load_dotenv" in txt
    assert 'load_dotenv(ROOT / ".env"' in txt


def test_script_forces_broker_public_live():
    """In-process override only — must not touch launchctl env."""
    txt = SCRIPT_PATH.read_text()
    assert 'os.environ["BROKER"] = "public_live"' in txt
    # Resets the broker singleton so the override actually takes effect
    assert "reset_broker_client_for_testing" in txt


def test_script_never_submits_orders():
    """Source-text: must NOT call submit_market_order, submit_order,
    place_order, close_position, or similar broker-write methods."""
    txt = SCRIPT_PATH.read_text()
    # Allowed: read paths and OrderRecord references
    forbidden = [
        "submit_market_order(",
        "submit_order(",
        "place_order(",
        "close_position(",
        "place_target_weights(",
        "place_bracket_order(",
    ]
    for fn in forbidden:
        assert fn not in txt, (
            f"first_live_dry_run.py contains {fn} — must be read-only"
        )


def test_script_never_mutates_launchctl_env():
    """Source-text: must NOT call launchctl setenv / system / subprocess
    in a way that mutates the daemon env."""
    txt = SCRIPT_PATH.read_text()
    assert "launchctl setenv" not in txt
    assert "os.system" not in txt
    # subprocess is OK in principle, but not for env mutation
    if "subprocess" in txt:
        assert "launchctl" not in txt


def test_script_has_all_required_sections():
    """The script's banners cover the operator's mental model."""
    txt = SCRIPT_PATH.read_text()
    for section in [
        "ACCOUNT STATE",
        "MARKET CLOCK",
        "CURRENT POSITIONS",
        "STRATEGY TARGETS",
        "ORDER PLAN",
        "RISK / SAFETY GATES",
        "REHEARSAL SUMMARY",
    ]:
        assert section in txt


def test_script_surfaces_catastrophic_dd_bug():
    """The cross-broker drawdown false-positive must be loudly flagged."""
    txt = SCRIPT_PATH.read_text()
    assert "all_zero" in txt
    assert "CATASTROPHIC" in txt
    assert "DO NOT FLIP" in txt
    # Documents the fix in-line
    assert "deployment_anchor" in txt or "deployment anchor" in txt


def test_ops_runbook_links_the_script():
    """The OPS_RUNBOOK should reference the rehearsal step."""
    doc = (
        Path(__file__).resolve().parent.parent
        / "docs" / "OPS_RUNBOOK.md"
    ).read_text()
    assert "first_live_dry_run.py" in doc
    assert "Before the flip" in doc or "pre-flight rehearsal" in doc.lower()

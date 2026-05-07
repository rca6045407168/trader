"""Tests for v3.73.21 — drawdown protocol ENFORCING wired into main.py."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


def test_main_imports_apply_drawdown_protocol():
    """The orchestrator must import apply_drawdown_protocol — without
    this, the rule is shipped-but-not-wired (the v3.73.20 critique)."""
    text = (ROOT / "src" / "trader" / "main.py").read_text()
    assert "apply_drawdown_protocol" in text
    assert "DRAWDOWN_PROTOCOL_MODE" in text or "drawdown_protocol_mode" in text


def test_main_uses_adjusted_targets_in_enforcing_mode():
    """When mode is ENFORCING and the function returns mutated
    targets, main.py must actually use them."""
    text = (ROOT / "src" / "trader" / "main.py").read_text()
    fn_idx = text.index("apply_drawdown_protocol")
    next500 = text[fn_idx:fn_idx + 2000]
    # Must call the function, capture (adjusted, tier, warnings)
    assert "adjusted" in next500
    # Must assign back to momentum_targets when ENFORCING fires
    assert "momentum_targets = adjusted" in next500


def test_advisory_default():
    """Default mode must remain ADVISORY — flipping to ENFORCING is
    an explicit operator decision, not a passive default."""
    from trader.risk_manager import drawdown_protocol_mode
    # With env not set, default should be ADVISORY
    if "DRAWDOWN_PROTOCOL_MODE" in os.environ:
        del os.environ["DRAWDOWN_PROTOCOL_MODE"]
    assert drawdown_protocol_mode() == "ADVISORY"


def test_enforcing_mode_via_env():
    """Setting DRAWDOWN_PROTOCOL_MODE=ENFORCING must flip the mode."""
    from trader.risk_manager import drawdown_protocol_mode
    os.environ["DRAWDOWN_PROTOCOL_MODE"] = "ENFORCING"
    try:
        assert drawdown_protocol_mode() == "ENFORCING"
    finally:
        del os.environ["DRAWDOWN_PROTOCOL_MODE"]

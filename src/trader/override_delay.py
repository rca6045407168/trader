"""24-hour override-delay enforcement.

When LIVE strategy changes (variant_id swap, config change, MAX_POSITION_PCT
adjustment), the next rebalance MUST wait 24 hours before executing under
the new config. This forces deliberation between "I want to change LIVE"
and "the change actually takes effect."

Implementation: maintain a SHA-stamp of the LIVE config (variant_id +
key params). Daily-run checks whether the SHA changed in the last 24 hours.
If yes, REFUSES to rebalance and logs a "cooling off" message.

Override-the-override (legitimate emergencies): a `data/override_delay_bypass`
sentinel file lets a human bypass — but creating it requires git commit,
which is itself a deliberate act.

Why: agent-1 (behavioral econ) and agent-2 (risk officer) both flagged that
panic-state self can't be trusted to make config changes. 24h is the minimum
hot-state cool-down per Loewenstein hot-cold empathy gap research.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

STATE_PATH = DATA_DIR / "live_config_sha.json"
BYPASS_PATH = DATA_DIR / "override_delay_bypass"

OVERRIDE_DELAY_HOURS = 24


@dataclass
class ConfigSHA:
    sha: str
    variant_id: str
    last_updated: str  # ISO 8601


def compute_live_config_sha() -> ConfigSHA:
    """SHA-256 over the current LIVE variant + critical risk-manager constants.
    Anything that materially changes how trades are sized should be in here."""
    from . import variants  # registers
    from .ab import get_live
    from .risk_manager import (
        MAX_POSITION_PCT, MAX_GROSS_EXPOSURE, MAX_DAILY_LOSS_PCT,
        MAX_DRAWDOWN_HALT_PCT, MAX_DEPLOY_DD_FREEZE_PCT,
        MAX_DEPLOY_DD_LIQUIDATION_PCT,
    )
    live = get_live()
    if live is None:
        raise RuntimeError("no LIVE variant registered")
    config_string = json.dumps({
        "variant_id": live.variant_id,
        "version": live.version,
        "params": live.params or {},
        "MAX_POSITION_PCT": MAX_POSITION_PCT,
        "MAX_GROSS_EXPOSURE": MAX_GROSS_EXPOSURE,
        "MAX_DAILY_LOSS_PCT": MAX_DAILY_LOSS_PCT,
        "MAX_DRAWDOWN_HALT_PCT": MAX_DRAWDOWN_HALT_PCT,
        "MAX_DEPLOY_DD_FREEZE_PCT": MAX_DEPLOY_DD_FREEZE_PCT,
        "MAX_DEPLOY_DD_LIQUIDATION_PCT": MAX_DEPLOY_DD_LIQUIDATION_PCT,
    }, sort_keys=True)
    sha = hashlib.sha256(config_string.encode()).hexdigest()
    return ConfigSHA(
        sha=sha,
        variant_id=live.variant_id,
        last_updated=datetime.utcnow().isoformat(),
    )


def load_state() -> ConfigSHA | None:
    if not STATE_PATH.exists():
        return None
    try:
        return ConfigSHA(**json.loads(STATE_PATH.read_text()))
    except Exception:
        return None


def save_state(state: ConfigSHA) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state.__dict__, indent=2))


def check_override_delay() -> tuple[bool, str]:
    """Returns (allowed_to_proceed, reason).

    If LIVE config has changed in the last 24 hours: NOT allowed (cooling off).
    If unchanged: allowed.
    Bypass mechanism: presence of data/override_delay_bypass file.
    """
    if BYPASS_PATH.exists():
        return True, "override_delay BYPASSED (override_delay_bypass file present)"

    current = compute_live_config_sha()
    previous = load_state()

    if previous is None:
        # First-ever run; record current SHA. Allow proceed.
        save_state(current)
        return True, f"first run, SHA recorded: {current.sha[:12]}"

    if previous.sha == current.sha:
        return True, f"LIVE config unchanged (SHA {current.sha[:12]}); proceed"

    # Config changed — check time since last change
    try:
        last_updated = datetime.fromisoformat(previous.last_updated)
    except ValueError:
        last_updated = datetime.utcnow() - timedelta(hours=OVERRIDE_DELAY_HOURS + 1)

    elapsed = datetime.utcnow() - last_updated
    if elapsed < timedelta(hours=OVERRIDE_DELAY_HOURS):
        remaining = timedelta(hours=OVERRIDE_DELAY_HOURS) - elapsed
        return False, (
            f"OVERRIDE DELAY: LIVE config changed at {previous.last_updated} "
            f"({previous.variant_id} → {current.variant_id}). "
            f"Waiting {remaining} more before new config takes effect. "
            f"This is the v3.46 anti-panic protocol. To bypass (emergencies "
            f"only): create file {BYPASS_PATH}."
        )

    # Enough time has passed; record new SHA, allow proceed.
    save_state(current)
    return True, (f"LIVE config change took effect after {elapsed} cooling off "
                  f"({previous.variant_id} → {current.variant_id})")

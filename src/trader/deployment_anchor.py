"""Deployment-anchor tracking.

Records the equity at which the strategy was "deployed" — i.e., the
baseline against which since-deployment drawdown is measured.

v6.0.x: anchors are now broker-scoped. The JSON file stores a dict
keyed by broker:
  {
    "alpaca_paper": {"equity_at_deploy": 100000.0, ...},
    "public_live":  {"equity_at_deploy":  25000.0, ...}
  }

This means flipping BROKER=public_live no longer false-positive
drawdowns against the Alpaca-paper deployment anchor. A separate
anchor is auto-set the first time each broker sees a daily-run.

Migration: legacy single-anchor JSON gets converted to the dict form
under the 'alpaca_paper' key on first load.

Storage: deployment_anchor.json in data/. Each broker's anchor is
set once on its first daily-run; never updated unless explicitly
reset via reset_anchor() (which requires a written post-mortem).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

ANCHOR_PATH = DATA_DIR / "deployment_anchor.json"


@dataclass
class DeploymentAnchor:
    equity_at_deploy: float
    deploy_timestamp: str  # ISO 8601
    source: str = "auto"   # "auto" | "manual_reset" | "post_mortem_reset"
    notes: str = ""


def _current_broker() -> str:
    """Mirror trader.journal._current_broker — pick up BROKER env."""
    return os.environ.get("BROKER", "alpaca_paper").lower()


def _read_all() -> dict[str, dict]:
    """Read the multi-broker anchor file. Migrates legacy single-anchor
    format → dict-by-broker on first read. Returns an empty dict if no
    file exists."""
    if not ANCHOR_PATH.exists():
        return {}
    try:
        data = json.loads(ANCHOR_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # Legacy format: top-level keys are anchor fields (equity_at_deploy etc.)
    # rather than broker names. Migrate by wrapping under 'alpaca_paper'.
    if "equity_at_deploy" in data:
        migrated = {"alpaca_paper": data}
        try:
            ANCHOR_PATH.write_text(json.dumps(migrated, indent=2))
        except Exception:
            pass  # best-effort; in-memory migration is still correct
        return migrated
    return data


def _write_all(anchors: dict[str, dict]) -> None:
    ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANCHOR_PATH.write_text(json.dumps(anchors, indent=2))


def load_anchor(broker: Optional[str] = None) -> DeploymentAnchor | None:
    """Load the anchor for the current (or specified) broker.
    Returns None if no anchor is set for that broker."""
    if broker is None:
        broker = _current_broker()
    anchors = _read_all()
    d = anchors.get(broker)
    if d is None:
        return None
    try:
        return DeploymentAnchor(**d)
    except Exception:
        return None


def save_anchor(anchor: DeploymentAnchor, broker: Optional[str] = None) -> None:
    """Save the anchor under the broker-scoped key."""
    if broker is None:
        broker = _current_broker()
    anchors = _read_all()
    anchors[broker] = anchor.__dict__
    _write_all(anchors)


def get_or_set_anchor(current_equity: float,
                       broker: Optional[str] = None) -> DeploymentAnchor:
    """Return existing anchor for the current (or specified) broker, or
    auto-create one with current_equity if no anchor exists for that
    broker yet. Other brokers' anchors are untouched."""
    if broker is None:
        broker = _current_broker()
    existing = load_anchor(broker=broker)
    if existing is not None:
        return existing
    anchor = DeploymentAnchor(
        equity_at_deploy=float(current_equity),
        deploy_timestamp=datetime.utcnow().isoformat(),
        source="auto",
        notes=f"auto-set on first daily-run for broker={broker}",
    )
    save_anchor(anchor, broker=broker)
    return anchor


def drawdown_from_deployment(current_equity: float,
                              broker: Optional[str] = None) -> tuple[float, DeploymentAnchor]:
    """Returns (drawdown_pct as decimal, anchor used).
    drawdown_pct < 0 means we're below the anchor for the current broker.
    """
    anchor = get_or_set_anchor(current_equity, broker=broker)
    if anchor.equity_at_deploy <= 0:
        return 0.0, anchor
    dd = current_equity / anchor.equity_at_deploy - 1
    return float(dd), anchor


def reset_anchor(new_equity: float, reason: str,
                  post_mortem_path: str = "",
                  broker: Optional[str] = None) -> DeploymentAnchor:
    """Reset the deployment anchor for the current (or specified)
    broker. Should ONLY be called after a written post-mortem (per the
    -33% liquidation gate). Refuses without a reason.
    """
    if not reason or len(reason) < 50:
        raise ValueError(
            "reset_anchor requires a written reason ≥50 chars. "
            "This forces deliberation per the v3.46 anti-panic protocol."
        )
    if broker is None:
        broker = _current_broker()
    anchor = DeploymentAnchor(
        equity_at_deploy=float(new_equity),
        deploy_timestamp=datetime.utcnow().isoformat(),
        source="post_mortem_reset",
        notes=f"broker={broker}; reason={reason}; post_mortem={post_mortem_path}",
    )
    save_anchor(anchor, broker=broker)
    return anchor

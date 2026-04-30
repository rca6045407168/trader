"""Deployment-anchor tracking.

Records the equity at which the strategy was "deployed" — i.e., the baseline
against which since-deployment drawdown is measured.

For paper trading: anchor = $100k initial seed.
For live: anchor = first equity snapshot after ALPACA_PAPER flipped to false.

Why this matters: agent-2 (institutional risk) and agent-3 (retail veteran)
both flagged that the most catastrophic drawdowns happen WHEN you've forgotten
how much you started with. -25% from peak feels different than -25% from
deployment. The latter is the real bankruptcy threshold.

Storage: anchor.json in data/. Set once on first daily-run; never updated
unless explicitly reset (which requires a written post-mortem).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

ANCHOR_PATH = DATA_DIR / "deployment_anchor.json"


@dataclass
class DeploymentAnchor:
    equity_at_deploy: float
    deploy_timestamp: str  # ISO 8601
    source: str = "auto"   # "auto" | "manual_reset" | "post_mortem_reset"
    notes: str = ""


def load_anchor() -> DeploymentAnchor | None:
    if not ANCHOR_PATH.exists():
        return None
    try:
        data = json.loads(ANCHOR_PATH.read_text())
        return DeploymentAnchor(**data)
    except Exception:
        return None


def save_anchor(anchor: DeploymentAnchor) -> None:
    ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANCHOR_PATH.write_text(json.dumps(anchor.__dict__, indent=2))


def get_or_set_anchor(current_equity: float) -> DeploymentAnchor:
    """Return existing anchor, or create one with current equity if none exists."""
    existing = load_anchor()
    if existing is not None:
        return existing
    anchor = DeploymentAnchor(
        equity_at_deploy=float(current_equity),
        deploy_timestamp=datetime.utcnow().isoformat(),
        source="auto",
        notes="auto-set on first daily-run",
    )
    save_anchor(anchor)
    return anchor


def drawdown_from_deployment(current_equity: float) -> tuple[float, DeploymentAnchor]:
    """Returns (drawdown_pct as decimal, anchor used).
    drawdown_pct < 0 means we're below the anchor.
    """
    anchor = get_or_set_anchor(current_equity)
    if anchor.equity_at_deploy <= 0:
        return 0.0, anchor
    dd = current_equity / anchor.equity_at_deploy - 1
    return float(dd), anchor


def reset_anchor(new_equity: float, reason: str, post_mortem_path: str = "") -> DeploymentAnchor:
    """Reset the deployment anchor. Should ONLY be called after a written
    post-mortem (per the -33% liquidation gate). Refuses without a reason.
    """
    if not reason or len(reason) < 50:
        raise ValueError(
            "reset_anchor requires a written reason ≥50 chars. "
            "This forces deliberation per the v3.46 anti-panic protocol."
        )
    anchor = DeploymentAnchor(
        equity_at_deploy=float(new_equity),
        deploy_timestamp=datetime.utcnow().isoformat(),
        source="post_mortem_reset",
        notes=f"reason={reason}; post_mortem={post_mortem_path}",
    )
    save_anchor(anchor)
    return anchor

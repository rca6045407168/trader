"""A/B testing framework for safe strategy iteration.

Built v2.9 in response to a 20-agent adversarial-debate-with-data exercise
that revealed 4 of 5 "obviously good" strategy changes actually HURT empirically
when backtested. Conclusion: every future strategy change must run as a SHADOW
first, gathering live evidence, before promotion to live capital.

Architecture:
  - Strategy variants are registered in the `variants` table with status
    ('live' | 'shadow' | 'paper' | 'retired')
  - Live variant gets capital; shadow variants get logged-only decisions
  - Daily orchestrator runs ALL variants; live places orders, shadow appends
    to `shadow_decisions` table for later analysis
  - After ≥30 days of evidence, scripts/compare_variants.py computes
    per-variant Sharpe, alpha, and statistical-significance test
  - Promotion only when shadow Sharpe beats live by ≥0.2 over ≥30 days

Each variant is a callable: variant(universe, equity, account_state) -> dict[str, float]
returning target portfolio weights {ticker: pct (0-1)}.

This file does not place orders. It only records decisions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol


@dataclass
class Variant:
    variant_id: str
    name: str
    version: str
    status: str  # 'live' | 'shadow' | 'paper' | 'retired'
    description: str
    fn: Callable[..., dict[str, float]]
    params: dict[str, Any] | None = None


# Module-level registry. Populated by register_variant() at import time.
_REGISTRY: dict[str, Variant] = {}


def register_variant(
    variant_id: str,
    name: str,
    version: str,
    status: str,
    fn: Callable[..., dict[str, float]],
    description: str = "",
    params: dict[str, Any] | None = None,
) -> Variant:
    """Register a strategy variant. Persists metadata to the variants table."""
    if status not in ("live", "shadow", "paper", "retired"):
        raise ValueError(f"invalid status: {status}")
    v = Variant(variant_id=variant_id, name=name, version=version, status=status,
                description=description, fn=fn, params=params)
    _REGISTRY[variant_id] = v
    _persist_variant(v)
    return v


def _persist_variant(v: Variant) -> None:
    from .journal import _conn, init_db
    init_db()
    with _conn() as c:
        existing = c.execute("SELECT variant_id, status FROM variants WHERE variant_id = ?",
                              (v.variant_id,)).fetchone()
        if existing:
            # Update status only; don't re-stamp created_at
            c.execute(
                "UPDATE variants SET status = ?, params_json = ?, description = ? WHERE variant_id = ?",
                (v.status, json.dumps(v.params or {}), v.description, v.variant_id),
            )
        else:
            c.execute(
                """INSERT INTO variants (variant_id, name, version, status, params_json, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (v.variant_id, v.name, v.version, v.status, json.dumps(v.params or {}),
                 v.description, datetime.utcnow().isoformat()),
            )


def get_live() -> Variant | None:
    """Return the unique live variant, or None if not registered."""
    live = [v for v in _REGISTRY.values() if v.status == "live"]
    if len(live) > 1:
        raise RuntimeError(f"multiple variants marked live: {[v.variant_id for v in live]}")
    return live[0] if live else None


def get_shadows() -> list[Variant]:
    """Return all shadow variants (log decisions, no capital)."""
    return [v for v in _REGISTRY.values() if v.status == "shadow"]


def log_shadow_decision(variant_id: str, targets: dict[str, float],
                        rationale: str = "", market_context: dict | None = None) -> None:
    """Persist a shadow variant's decision for later analysis."""
    from .journal import _conn, init_db
    init_db()
    with _conn() as c:
        c.execute(
            """INSERT INTO shadow_decisions (variant_id, ts, targets_json, rationale, market_context_json)
               VALUES (?, ?, ?, ?, ?)""",
            (variant_id, datetime.utcnow().isoformat(),
             json.dumps(targets), rationale,
             json.dumps(market_context or {}, default=str)),
        )


def run_shadows(universe: list[str], equity: float, account_state: dict[str, Any],
                market_context: dict | None = None) -> dict[str, dict]:
    """Run every registered shadow variant and log its decision.

    Returns a dict of variant_id -> {targets, rationale} for inspection.
    Errors in individual shadows are caught so they don't crash the live run.
    """
    out = {}
    for v in get_shadows():
        try:
            targets = v.fn(universe=universe, equity=equity, account_state=account_state)
            rationale = f"shadow {v.variant_id} v{v.version}"
            log_shadow_decision(v.variant_id, targets, rationale=rationale,
                                market_context=market_context)
            out[v.variant_id] = {"targets": targets, "rationale": rationale}
        except Exception as e:
            out[v.variant_id] = {"error": f"{type(e).__name__}: {e}"}
    return out

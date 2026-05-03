"""[v3.59.3 — TESTING_PRACTICES Cat 12] Pre-registration audit infra.

Per BLINDSPOTS §7 + TESTING_PRACTICES Cat 12: before running the 3-gate
on a new sleeve, write down expected Sharpe / drawdown / falsifying
conditions. After running the gate, compare actual to pre-registered.
Persistent optimism = optimism bias; adjust your priors.

This module:
  • register(sleeve_name, expectations) → writes data/preregistrations/<name>_<ts>.json
  • record_actuals(sleeve_name, actuals) → fills in the actual results
  • audit() → returns optimism-bias statistics across all completed pre-regs

Schema:
  {
    "sleeve_name": str,
    "registered_at": ISO,
    "expected": {
      "sharpe": float, "cagr_pct": float, "max_dd_pct": float,
      "win_rate": float (optional)
    },
    "falsifying_conditions": [str, ...]   # plain English
    "actual": {...} | null,
    "actual_recorded_at": ISO | null,
  }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


PREREG_DIR = DATA_DIR / "preregistrations"


@dataclass
class Expectations:
    sharpe: float
    cagr_pct: float
    max_dd_pct: float
    win_rate: Optional[float] = None


@dataclass
class Actuals:
    sharpe: float
    cagr_pct: float
    max_dd_pct: float
    win_rate: Optional[float] = None


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in s)


def register(sleeve_name: str,
              expected: Expectations,
              falsifying_conditions: list[str]) -> Path:
    """Write a new pre-registration. Returns the file path.
    The file is timestamped so multiple registrations per sleeve are
    distinguishable (e.g., before each major param change).
    """
    PREREG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().isoformat().replace(":", "-")
    fname = f"{_slug(sleeve_name)}_{ts}.json"
    path = PREREG_DIR / fname
    payload = {
        "sleeve_name": sleeve_name,
        "registered_at": datetime.utcnow().isoformat(),
        "expected": asdict(expected),
        "falsifying_conditions": list(falsifying_conditions),
        "actual": None,
        "actual_recorded_at": None,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def list_registrations(sleeve_name: Optional[str] = None) -> list[dict]:
    if not PREREG_DIR.exists():
        return []
    out = []
    for f in sorted(PREREG_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if sleeve_name and d.get("sleeve_name") != sleeve_name:
            continue
        d["_path"] = str(f)
        out.append(d)
    return out


def record_actuals(prereg_path: Path, actual: Actuals) -> bool:
    """Fill in actual results for a previously-registered sleeve.
    Returns True if updated, False if file missing or malformed."""
    if not prereg_path.exists():
        return False
    try:
        d = json.loads(prereg_path.read_text())
    except Exception:
        return False
    d["actual"] = asdict(actual)
    d["actual_recorded_at"] = datetime.utcnow().isoformat()
    prereg_path.write_text(json.dumps(d, indent=2))
    return True


def audit() -> dict:
    """Aggregate optimism-bias stats across all completed pre-registrations.

    Returns:
      {
        n_completed: int, n_pending: int,
        sharpe_bias_avg: float (expected - actual),
        cagr_bias_avg: float,
        dd_bias_avg: float (expected_dd - actual_dd; positive = actual was worse),
        per_sleeve: [{sleeve_name, ratio_actual_to_expected_sharpe, ...}]
      }
    """
    regs = list_registrations()
    completed = [r for r in regs if r.get("actual")]
    pending = [r for r in regs if not r.get("actual")]

    sharpe_biases = []
    cagr_biases = []
    dd_biases = []
    per_sleeve = []

    for r in completed:
        exp = r["expected"]; act = r["actual"]
        sb = (exp.get("sharpe") or 0) - (act.get("sharpe") or 0)
        cb = (exp.get("cagr_pct") or 0) - (act.get("cagr_pct") or 0)
        # max_dd is negative; expected -10%, actual -25% → bias = -10 - -25 = 15 (we under-feared)
        db = (exp.get("max_dd_pct") or 0) - (act.get("max_dd_pct") or 0)
        sharpe_biases.append(sb)
        cagr_biases.append(cb)
        dd_biases.append(db)
        per_sleeve.append({
            "sleeve_name": r["sleeve_name"],
            "expected_sharpe": exp.get("sharpe"),
            "actual_sharpe": act.get("sharpe"),
            "sharpe_bias": sb,
            "expected_cagr_pct": exp.get("cagr_pct"),
            "actual_cagr_pct": act.get("cagr_pct"),
            "cagr_bias": cb,
            "expected_max_dd_pct": exp.get("max_dd_pct"),
            "actual_max_dd_pct": act.get("max_dd_pct"),
            "dd_bias": db,
        })

    def _avg(xs): return sum(xs) / len(xs) if xs else None

    return {
        "n_completed": len(completed),
        "n_pending": len(pending),
        "sharpe_bias_avg": _avg(sharpe_biases),
        "cagr_bias_avg": _avg(cagr_biases),
        "dd_bias_avg": _avg(dd_biases),
        "per_sleeve": per_sleeve,
        "interpretation": _interpret_bias(_avg(sharpe_biases),
                                            _avg(cagr_biases),
                                            _avg(dd_biases)),
    }


def _interpret_bias(s: Optional[float], c: Optional[float],
                     d: Optional[float]) -> str:
    if s is None:
        return "no completed registrations yet"
    parts = []
    if s > 0.3:
        parts.append(f"⚠️ optimistic Sharpe by {s:.2f} on average")
    elif s < -0.3:
        parts.append(f"✓ pessimistic Sharpe (good) by {-s:.2f}")
    else:
        parts.append("Sharpe expectations roughly calibrated")
    if d and d > 0:
        parts.append(f"⚠️ under-feared drawdown by {d:.1f}pp")
    elif d and d < 0:
        parts.append(f"✓ over-feared drawdown (cautious)")
    return "; ".join(parts)

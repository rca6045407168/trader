"""ReactorSignalRule — pre-trade gate that trims positions when the
v3.68.x earnings reactor has flagged a high-materiality BEARISH event.

Bridges v3.68.x analysis layer to v3.69 trading layer. Honors the
FT/LLMQuant article principle (AI as analysis, human as decision) by:

  1. Default status = SHADOW. Logs would-be trims without executing.
     The user reviews + flips to LIVE explicitly via env var.
  2. Direction-gated: only BEARISH or SURPRISE-with-MISSED trigger trims.
     BULLISH signals do NOT auto-add weight (that crosses the boundary
     hedge funds explicitly avoid).
  3. Materiality-gated: default M≥4 ("warrants position adjustment").
     M3 = "worth a PM's attention" — too low for an automated cut.
  4. Bounded: trim to 50% of target weight, never to 0. A
     catastrophic-but-wrong AI flag shouldn't fully exit a position.
  5. Recency-gated: only signals from the last 14 days count. Older
     signals have decayed in relevance.
  6. Reversible: set REACTOR_RULE_STATUS=INERT to disable entirely;
     SHADOW to log without trimming; LIVE to trim.

## Env config

  REACTOR_RULE_STATUS         = "SHADOW" (default) | "LIVE" | "INERT"
  REACTOR_TRIM_MIN_MATERIALITY = "4" (default 4; integer 1-5)
  REACTOR_TRIM_PCT             = "0.5" (default 0.5; trim to this fraction)
  REACTOR_TRIM_LOOKBACK_DAYS   = "14" (default 14)
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_JOURNAL_DB = (Path(__file__).resolve().parent.parent.parent
                       / "data" / "journal.db")


# Directions that warrant a defensive trim. BULLISH doesn't trigger
# auto-adjustments — boost decisions are kept for the human.
TRIM_DIRECTIONS = {"BEARISH", "SURPRISE"}

# For SURPRISE direction, only treat as trim-worthy if surprise_direction
# is MISSED (downside surprise). UP surprises can be neutral.
TRIM_SURPRISE_DIRECTIONS = {"MISSED"}


@dataclass
class TrimDecision:
    symbol: str
    old_weight: float
    new_weight: float
    materiality: int
    direction: str
    surprise_direction: str
    accession: str
    filed_at: str
    summary: str
    reason: str


class ReactorSignalRule:
    """Pre-trade rule that consumes earnings_signals + adjusts target
    weights. Designed to slot into main.py after the EarningsRule and
    before validate_targets, mirroring the existing rule-stack pattern."""

    def status(self) -> str:
        """LIVE / SHADOW / INERT. Defaults to SHADOW (log only)."""
        return os.getenv("REACTOR_RULE_STATUS", "SHADOW").upper()

    @property
    def min_materiality(self) -> int:
        try:
            return max(1, min(5, int(os.getenv("REACTOR_TRIM_MIN_MATERIALITY", "4"))))
        except ValueError:
            return 4

    @property
    def trim_to_pct(self) -> float:
        """Fraction of original weight to keep. 0.5 = trim to 50%."""
        try:
            v = float(os.getenv("REACTOR_TRIM_PCT", "0.5"))
            # Clamp into a sane band so a typo can't fully exit a position
            return max(0.1, min(1.0, v))
        except ValueError:
            return 0.5

    @property
    def lookback_days(self) -> int:
        try:
            return max(1, int(os.getenv("REACTOR_TRIM_LOOKBACK_DAYS", "14")))
        except ValueError:
            return 14

    def describe(self) -> str:
        return (
            f"Reactor-signal trim ({self.status()}): when an earnings_signals "
            f"row with materiality ≥ {self.min_materiality} and direction in "
            f"{sorted(TRIM_DIRECTIONS)} (or SURPRISE-MISSED) was filed within "
            f"the last {self.lookback_days} days for a held name, trim that "
            f"position's target weight to {self.trim_to_pct*100:.0f}% of "
            f"original."
        )

    def _is_trim_worthy(self, signal: dict) -> bool:
        """Filter logic — does this signal warrant a defensive trim?"""
        if (signal.get("materiality") or 0) < self.min_materiality:
            return False
        if signal.get("error"):
            return False
        direction = (signal.get("direction") or "").upper()
        if direction == "BEARISH":
            return True
        if direction == "SURPRISE":
            sd = (signal.get("surprise_direction") or "").upper()
            return sd in TRIM_SURPRISE_DIRECTIONS
        return False

    def compute_trims(
        self,
        targets: dict[str, float],
        journal_db: Optional[Path] = None,
        as_of: Optional[datetime] = None,
    ) -> dict[str, TrimDecision]:
        """For each held symbol, find the most recent trim-worthy signal
        within the lookback window. Return dict of trim decisions
        keyed by symbol. Empty dict means no positions trigger a trim.

        This method does NOT mutate `targets` — caller decides whether
        to apply the new_weight (LIVE) or merely log it (SHADOW)."""
        if journal_db is None:
            journal_db = DEFAULT_JOURNAL_DB
        if not targets:
            return {}
        if not journal_db.exists():
            return {}

        as_of = as_of or datetime.utcnow()
        cutoff = (as_of - timedelta(days=self.lookback_days)).date().isoformat()

        symbols = list(targets.keys())
        placeholders = ",".join("?" * len(symbols))

        try:
            with sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    f"SELECT symbol, accession, filed_at, materiality, "
                    f"direction, surprise_direction, summary, error "
                    f"FROM earnings_signals "
                    f"WHERE filed_at >= ? AND symbol IN ({placeholders}) "
                    f"ORDER BY filed_at DESC, materiality DESC",
                    [cutoff, *symbols],
                ).fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist (e.g. fresh install) — no trims
            return {}

        out: dict[str, TrimDecision] = {}
        for row in rows:
            sym = row["symbol"]
            if sym in out:
                continue  # already kept the most recent trim-worthy signal
            sig = dict(row)
            if not self._is_trim_worthy(sig):
                continue
            old_w = targets[sym]
            new_w = old_w * self.trim_to_pct
            reason = (
                f"M{sig['materiality']} {sig['direction']}"
                + (f"/{sig['surprise_direction']}"
                   if sig['direction'] == "SURPRISE" else "")
                + f" filed {sig['filed_at']}"
            )
            out[sym] = TrimDecision(
                symbol=sym,
                old_weight=old_w,
                new_weight=new_w,
                materiality=sig["materiality"] or 0,
                direction=sig["direction"] or "",
                surprise_direction=sig["surprise_direction"] or "",
                accession=sig["accession"] or "",
                filed_at=sig["filed_at"] or "",
                summary=sig["summary"] or "",
                reason=reason,
            )
        return out

    def apply(
        self,
        targets: dict[str, float],
        journal_db: Optional[Path] = None,
        as_of: Optional[datetime] = None,
    ) -> tuple[dict[str, float], dict[str, TrimDecision]]:
        """Applies the rule. Returns (new_targets, trim_decisions).

        - INERT: returns targets unchanged + empty decisions.
        - SHADOW: returns targets unchanged + decisions (caller logs).
        - LIVE: returns trimmed targets + decisions.
        """
        decisions = self.compute_trims(targets, journal_db, as_of)
        if not decisions or self.status() == "INERT":
            return dict(targets), {}
        if self.status() == "SHADOW":
            return dict(targets), decisions
        # LIVE
        new_targets = dict(targets)
        for sym, d in decisions.items():
            new_targets[sym] = d.new_weight
        return new_targets, decisions

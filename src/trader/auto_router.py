"""Multi-strategy auto-router for the LIVE slot.

v5.0.0 disposition replaces the hardcoded `register_variant(..., status="live")`
pattern. On every rebalance, the auto-router reads `strategy_eval` (settled
forward returns from the eval harness) and picks which registered candidate
fills the LIVE slot for that rebalance.

Selection rule (per V5_DISPOSITION.md §1):

    1. Eligibility filter:
       - In the eligible-candidate set (excludes long_short_momentum + passive
         baselines)
       - At least MIN_EVIDENCE_MONTHS months of settled forward returns
       - Realized β over the eligibility window <= MAX_BETA
       - Max relative-DD over the eligibility window >= MIN_DD
    2. Score: rolling alpha-IR (annualized monthly) over the eligibility window
    3. Pick: highest IR among eligible
    4. Hysteresis: if previous LIVE is still eligible and within
       HYSTERESIS_MARGIN of the new winner, keep it. Avoids monthly thrashing.
    5. Fallback: if no candidate clears eligibility, return None. Caller MUST
       halt the rebalance with a "no LIVE candidate" reason (per §3 exit
       criterion 1, three consecutive halts on this trigger -> review).

The selected strategy's last-state is persisted to journal.runs.notes so
operators can see the swap history.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .config import DB_PATH


# v5.0.0 §1.2 — defended individually in V5_DISPOSITION.md
MIN_EVIDENCE_MONTHS = int(os.getenv("AUTO_ROUTER_MIN_EVIDENCE_MONTHS", "6"))
MAX_BETA = float(os.getenv("AUTO_ROUTER_MAX_BETA", "1.20"))
MIN_DD_PCT = float(os.getenv("AUTO_ROUTER_MIN_DD_PCT", "-25.0"))
HYSTERESIS_MARGIN = float(os.getenv("AUTO_ROUTER_HYSTERESIS_MARGIN", "0.10"))

# §1.1 — exclusions. long_short has no short-cost modeling, passive baselines
# don't need an active orchestrator slot.
INELIGIBLE_LIVE_CANDIDATES = frozenset({
    "long_short_momentum",
    "buy_and_hold_spy",
    "buy_and_hold_qqq",
    "buy_and_hold_mtum",
    "buy_and_hold_schg",
    "buy_and_hold_vug",
    "buy_and_hold_xlk",
    "equal_weight_sp500",
    "boglehead_three_fund",
    "simple_60_40",
})


@dataclass
class RouterDecision:
    """The auto-router's per-rebalance verdict."""
    selected: Optional[str]            # strategy name, or None if no eligible
    reason: str                         # human-readable explanation
    eligible_count: int                 # how many cleared the filter
    runner_up: Optional[str] = None     # second-best by IR, for the dashboard
    hysteresis_applied: bool = False
    incumbent: Optional[str] = None     # previous LIVE, if any


def _load_incumbent() -> Optional[str]:
    """Read the LIVE strategy from the most recent run's notes.
    Returns None if no prior run / can't parse."""
    try:
        import sqlite3
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute(
                "SELECT notes FROM runs WHERE notes LIKE '%LIVE_AUTO=%' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return None
        # notes format: "... LIVE_AUTO=<strategy_name> ..."
        notes = row[0]
        for tok in notes.split():
            if tok.startswith("LIVE_AUTO="):
                name = tok[len("LIVE_AUTO="):]
                return name if name else None
    except Exception:
        return None
    return None


def select_live(
    days_back_for_evidence: int = MIN_EVIDENCE_MONTHS * 31,
    incumbent: Optional[str] = None,
) -> RouterDecision:
    """Pick the LIVE strategy for the upcoming rebalance.

    Args:
        days_back_for_evidence: how far back to read strategy_eval. Default
            is MIN_EVIDENCE_MONTHS * 31 days.
        incumbent: if provided, use as the previous LIVE for hysteresis.
            If None, the function reads it from journal.runs.

    Returns a RouterDecision. The orchestrator must inspect .selected; if
    None, halt the rebalance.
    """
    from .eval_runner import leaderboard

    if incumbent is None:
        incumbent = _load_incumbent()

    # Pull the leaderboard. Note: leaderboard() already sorts by cum_alpha_pct
    # descending. We re-sort by alpha_ir below because IR is the right metric
    # under the v5.0.0 frame (rolling-window risk-adjusted skill).
    rows = leaderboard(days_back=days_back_for_evidence)

    # Filter
    eligible = []
    for r in rows:
        name = r["strategy"]
        if name in INELIGIBLE_LIVE_CANDIDATES:
            continue
        if r["n_obs"] < MIN_EVIDENCE_MONTHS:
            continue
        if r["beta"] is None or r["beta"] > MAX_BETA:
            continue
        if r["max_relative_dd_pct"] < MIN_DD_PCT:
            continue
        eligible.append(r)

    if not eligible:
        return RouterDecision(
            selected=None,
            reason=(
                f"no candidate cleared the eligibility filter "
                f"(min_evidence_months={MIN_EVIDENCE_MONTHS}, "
                f"max_beta={MAX_BETA:.2f}, min_dd_pct={MIN_DD_PCT:.0f}%); "
                f"{len(rows)} candidates surveyed, 0 eligible. "
                f"Per V5_DISPOSITION §3.1, three consecutive halts on this "
                f"trigger require operator review."
            ),
            eligible_count=0,
            incumbent=incumbent,
        )

    # Sort eligibles by alpha_ir descending
    eligible.sort(key=lambda r: -r["alpha_ir"])
    winner = eligible[0]
    runner_up = eligible[1] if len(eligible) > 1 else None

    # Hysteresis: if incumbent is still eligible AND within margin, keep it
    if incumbent is not None and incumbent != winner["strategy"]:
        for r in eligible:
            if r["strategy"] == incumbent:
                ir_gap = winner["alpha_ir"] - r["alpha_ir"]
                if ir_gap < HYSTERESIS_MARGIN:
                    return RouterDecision(
                        selected=incumbent,
                        reason=(
                            f"hysteresis: winner={winner['strategy']} "
                            f"(IR={winner['alpha_ir']:+.3f}) beats incumbent "
                            f"(IR={r['alpha_ir']:+.3f}) by {ir_gap:.3f} which "
                            f"is below margin={HYSTERESIS_MARGIN:.2f}; "
                            f"keeping incumbent."
                        ),
                        eligible_count=len(eligible),
                        runner_up=winner["strategy"],
                        hysteresis_applied=True,
                        incumbent=incumbent,
                    )
                break  # incumbent found but loses by enough; promote winner

    return RouterDecision(
        selected=winner["strategy"],
        reason=(
            f"selected {winner['strategy']} with alpha_ir="
            f"{winner['alpha_ir']:+.3f}, beta={winner['beta']:.2f}, "
            f"n_obs={winner['n_obs']}, cum_alpha_pct="
            f"{winner['cum_alpha_pct']:+.2f}%; "
            f"{len(eligible)} eligible of {len(rows)} surveyed."
        ),
        eligible_count=len(eligible),
        runner_up=runner_up["strategy"] if runner_up else None,
        hysteresis_applied=False,
        incumbent=incumbent,
    )


def render_decision_for_journal(decision: RouterDecision) -> str:
    """Format a RouterDecision for journal.runs.notes — the LIVE_AUTO=
    token is what _load_incumbent() reads back on the next run."""
    if decision.selected is None:
        return f"LIVE_AUTO=NONE eligible={decision.eligible_count} reason='{decision.reason[:100]}'"
    h = "Y" if decision.hysteresis_applied else "N"
    return (
        f"LIVE_AUTO={decision.selected} hyst={h} "
        f"eligible={decision.eligible_count} "
        f"runner_up={decision.runner_up or 'none'}"
    )

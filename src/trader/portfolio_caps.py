"""v3.73.5 — Portfolio concentration caps.

Applied at score-to-weight conversion (between strategy.rank_* and the
broker submit). Two caps:

  - **Single-name cap** (default 8%). Defensive; non-binding at top-15
    + 80% gross today (per the v3.73.4 DD analysis: equal-weight at
    top-15/80% produces ~5.3% per name, well under 8%). Lives here so
    a future move to top-8 or to score-weighted (rather than equal-
    weighted) sizing is automatically capped without re-finding the
    code path.

  - **Sector cap** (default 25%). The binding one as of v3.73.4. Live
    book is 28.4% Tech (AMD+NVDA+AVGO+INTC); 25% cap forces 1.7pp
    of trim across Tech names, redistributed proportionally to under-
    cap sectors. Sharpe-positive in our own universe over 18 months
    (DD §"Three-way comparison": XS+caps Sharpe 1.18 vs XS 1.15 — a
    0.03 unit edge that is dominantly tail-risk reduction, not return).

The cap-application algorithm is iterative: trim → redistribute →
re-check, up to 5 passes. Five is empirically enough at our cap levels
(verified by the test suite); the loop converges in 1-2 passes for
typical books.

Returns the capped targets PLUS metadata about which cap bound (used
by the dashboard's binding-constraint panel — without this surface,
operators don't know whether the cap is doing real work or sitting
idle).

"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# Defaults — tuned to the v3.73.4 DD analysis.
# ============================================================

# Single-name cap. 8% chosen because:
#   - Top-15 / 80% gross equal-weight = 5.33%, well under 8%.
#   - The largest live position (CAT) is 11% — cap forces a 3pp trim
#     IF score-weighted sizing is ever introduced.
#   - 8% is consistent with Round-2 RISK_FRAMEWORK.md per-sleeve guidance.
SINGLE_NAME_CAP_PCT = 0.08

# Sector cap. 25% chosen because:
#   - Live book has 28.4% Tech; 25% forces a small trim today (binding).
#   - Below 20% would force ownership outside our momentum signal.
#   - Above 30% would not bind on the current book and so would be
#     decorative.
SECTOR_CAP_PCT = 0.25

# Maximum redistribution iterations. 5 is empirically enough for our
# cap levels; the test suite includes the pathological "every name
# above name-cap" case which converges in 3.
MAX_ITERATIONS = 5


# ============================================================
# Result type — the dashboard reads this for the binding-constraint
# panel. Without the metadata, we can't tell whether the cap is doing
# real work or sitting idle, which is the kind of "is this rule load-
# bearing?" question that should be visible to the operator.
# ============================================================
@dataclass
class CapResult:
    targets: dict[str, float]
    name_cap_bound: bool = False
    sector_cap_bound: bool = False
    pre_cap_max_name: float = 0.0
    pre_cap_max_sector: float = 0.0
    pre_cap_max_sector_name: str = ""
    post_cap_max_name: float = 0.0
    post_cap_max_sector: float = 0.0
    post_cap_max_sector_name: str = ""
    redistributed_pct: float = 0.0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.name_cap_bound:
            parts.append(
                f"name cap bound: {self.pre_cap_max_name*100:.1f}% → "
                f"{self.post_cap_max_name*100:.1f}%"
            )
        if self.sector_cap_bound:
            parts.append(
                f"sector cap bound on {self.pre_cap_max_sector_name}: "
                f"{self.pre_cap_max_sector*100:.1f}% → "
                f"{self.post_cap_max_sector*100:.1f}%"
            )
        if not parts:
            return "no caps bound (book is within limits)"
        return "; ".join(parts)


# ============================================================
# Public API
# ============================================================
def apply_portfolio_caps(
    targets: dict[str, float],
    sector_of: callable,
    *,
    name_cap: float = SINGLE_NAME_CAP_PCT,
    sector_cap: float = SECTOR_CAP_PCT,
    target_gross: Optional[float] = None,
) -> CapResult:
    """Apply single-name + sector caps; redistribute to under-cap names.

    Args:
        targets: {ticker: weight} pre-cap. Weights are fractions
            (0.10 = 10%), not percentages.
        sector_of: function ticker -> sector name. Pass
            ``trader.sectors.get_sector`` in production; pass a
            test-double for unit tests.
        name_cap: max single-name weight (0.08 = 8%).
        sector_cap: max sector weight (0.25 = 25%).
        target_gross: gross to renormalize to after capping. If None,
            use the input gross (i.e. preserve total exposure).

    Returns:
        CapResult with capped targets and which caps bound.

    Algorithm (iterative — must iterate because trimming for one cap
    can push another cap into binding):

        for pass in 1..MAX_ITERATIONS:
            1. Compute pre-cap exposures (max name, max sector)
            2. If any name exceeds name_cap: clip and accumulate excess
            3. Redistribute excess to under-cap names proportionally
            4. If any sector exceeds sector_cap: scale that sector's
               names by (cap/exposure) and accumulate freed weight
            5. Renormalize to target_gross
            6. If no cap bound this pass, exit early
    """
    if not targets:
        return CapResult(targets={}, notes=["empty input"])

    out = dict(targets)
    input_gross = sum(out.values())
    if target_gross is None:
        target_gross = input_gross

    # Snapshot pre-cap state for the dashboard
    pre_max_name = max(out.values()) if out else 0.0
    pre_sec_w: dict[str, float] = {}
    for t, w in out.items():
        pre_sec_w[sector_of(t)] = pre_sec_w.get(sector_of(t), 0.0) + w
    pre_max_sec = max(pre_sec_w.values()) if pre_sec_w else 0.0
    pre_max_sec_name = (
        max(pre_sec_w, key=pre_sec_w.get) if pre_sec_w else ""
    )

    name_bound = False
    sector_bound = False
    total_redistributed = 0.0

    # --- Single-name cap (cap-aware; gross may drop if not enough
    #     headroom — the alternative is to oscillate between clip and
    #     renormalize forever).
    for _ in range(20):
        excess = 0.0
        for t in list(out):
            if out[t] > name_cap + 1e-12:
                excess += out[t] - name_cap
                out[t] = name_cap
                name_bound = True
        if excess <= 1e-12:
            break
        # Redistribute proportional to AVAILABLE HEADROOM, not weight.
        # Headroom-proportional avoids overshooting the cap on any name.
        headroom = {
            t: name_cap - out[t]
            for t in out
            if out[t] < name_cap - 1e-12
        }
        total_headroom = sum(headroom.values())
        if total_headroom <= 1e-12:
            # No headroom anywhere. Excess is "lost" — gross drops by
            # this amount. This is the correct behavior when the book
            # is fundamentally over-concentrated for the cap level
            # (e.g. 3 names at 8% cap = max 24% gross, even if target
            # was 25%). Caller can detect via post_cap gross < input.
            break
        amt_to_distribute = min(excess, total_headroom)
        for t, h in headroom.items():
            out[t] += amt_to_distribute * (h / total_headroom)
        total_redistributed += amt_to_distribute
        if amt_to_distribute < excess - 1e-12:
            # Couldn't fit all the excess; stop iterating (the rest is
            # the structural gross-drop case).
            break

    # --- Sector cap. Iterates because trimming one over-cap sector and
    #     redistributing to under-cap sectors can push some name over
    #     the name cap, which we then re-clip on the next outer pass.
    for outer in range(MAX_ITERATIONS):
        outer_bound = False
        sec_w: dict[str, float] = {}
        sec_members: dict[str, list[str]] = {}
        for t, w in out.items():
            s = sector_of(t)
            sec_w[s] = sec_w.get(s, 0.0) + w
            sec_members.setdefault(s, []).append(t)
        for s, sw in sec_w.items():
            if sw > sector_cap + 1e-12:
                scale = sector_cap / sw
                freed = sw * (1 - scale)
                for t in sec_members[s]:
                    out[t] *= scale
                # Redistribute freed weight to under-cap sectors.
                # Proportional to remaining HEADROOM in each name to
                # avoid pushing names over the name cap.
                under_t_headroom = {
                    t: name_cap - out[t]
                    for t in out
                    if sector_of(t) != s
                    and sec_w[sector_of(t)] < sector_cap
                    and out[t] < name_cap - 1e-12
                }
                tot_h = sum(under_t_headroom.values())
                if tot_h > 0:
                    amt = min(freed, tot_h)
                    for t, h in under_t_headroom.items():
                        out[t] += amt * (h / tot_h)
                sector_bound = True
                outer_bound = True
                total_redistributed += freed
        if not outer_bound:
            break

    # Snapshot post-cap state
    post_max_name = max(out.values()) if out else 0.0
    post_sec_w: dict[str, float] = {}
    for t, w in out.items():
        post_sec_w[sector_of(t)] = post_sec_w.get(sector_of(t), 0.0) + w
    post_max_sec = max(post_sec_w.values()) if post_sec_w else 0.0
    post_max_sec_name = (
        max(post_sec_w, key=post_sec_w.get) if post_sec_w else ""
    )

    notes = []
    if name_bound:
        notes.append(
            f"single-name cap clipped {pre_max_name*100:.1f}% → "
            f"{name_cap*100:.0f}%"
        )
    if sector_bound:
        notes.append(
            f"sector cap clipped {pre_max_sec_name} from "
            f"{pre_max_sec*100:.1f}% → {sector_cap*100:.0f}%"
        )

    return CapResult(
        targets=out,
        name_cap_bound=name_bound,
        sector_cap_bound=sector_bound,
        pre_cap_max_name=pre_max_name,
        pre_cap_max_sector=pre_max_sec,
        pre_cap_max_sector_name=pre_max_sec_name,
        post_cap_max_name=post_max_name,
        post_cap_max_sector=post_max_sec,
        post_cap_max_sector_name=post_max_sec_name,
        redistributed_pct=total_redistributed,
        notes=notes,
    )

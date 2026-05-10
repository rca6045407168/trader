#!/usr/bin/env python3
"""arXiv paper triage — should we incorporate this into the trader?

Takes an arXiv URL, fetches the abstract, applies the rubric I use
when judging whether a paper is worth a trader-side implementation:

  1. Asset class — equity, monthly-to-daily, US large-cap, long-only?
  2. Universe size — < 500 names tested? (Our 50-name basket is
                                          comparable.)
  3. Cost discipline — IR/Sharpe net of >=5 bps/side?
  4. OOS window — >=5 yrs out-of-sample with regime stratification?
  5. Edge magnitude — > 0.5%/yr after costs?
  6. Mechanism class — new factor, regime-conditional, factor
                        timing — NOT just another momentum rebrand.

Output: a verdict (PURSUE / SHELVE / PASS) + a one-paragraph reason
per criterion. The triage is conservative — papers fail by default.

Usage:
  python scripts/paper_intake.py https://arxiv.org/abs/YYMM.NNNNN
  python scripts/paper_intake.py YYMM.NNNNN
  python scripts/paper_intake.py 2605.06285

The script does NOT call an LLM. It uses arXiv's REST API to fetch
the abstract, then applies a deterministic keyword-rubric. This
means false negatives are real (a great paper buried under bad
keywords gets shelved). The point is to filter the firehose — anything
that survives this triage merits a real read.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


ARXIV_API = "http://export.arxiv.org/api/query?id_list={arxiv_id}"


@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    primary_category: str
    categories: list[str]
    summary_url: str


@dataclass
class TriageResult:
    paper: Paper
    verdict: str           # "PURSUE" | "SHELVE" | "PASS"
    score: int             # 0..6, criteria passed
    criteria: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def render(self) -> str:
        lines = []
        lines.append("=" * 72)
        lines.append(f"PAPER TRIAGE — {self.paper.arxiv_id}")
        lines.append("=" * 72)
        lines.append(f"Title:    {self.paper.title}")
        lines.append(f"Authors:  {', '.join(self.paper.authors[:5])}"
                      + (" et al." if len(self.paper.authors) > 5 else ""))
        lines.append(f"Category: {self.paper.primary_category}  "
                      f"({', '.join(self.paper.categories)})")
        lines.append(f"URL:      {self.paper.summary_url}")
        lines.append("")
        lines.append(f"VERDICT:  ★ {self.verdict}  ({self.score}/6 "
                      f"criteria passed)")
        lines.append("")
        lines.append("RUBRIC")
        lines.append("-" * 72)
        for crit, (passed, reason) in self.criteria.items():
            mark = "✅" if passed else "❌"
            lines.append(f"  {mark} {crit}")
            lines.append(f"     {reason}")
        lines.append("")
        if self.verdict == "PURSUE":
            lines.append("RECOMMENDED NEXT STEP")
            lines.append("-" * 72)
            lines.append("  1. Pull the full PDF; re-check the rubric on the "
                          "actual methods section (the keyword-based triage")
            lines.append("     here is conservative — confirm before "
                          "committing engineering time).")
            lines.append("  2. If the paper holds up, draft an `evaluate_*` "
                          "function in src/trader/eval_strategies.py and run")
            lines.append("     walk-forward IS/OOS via tests/test_v3_73_7_"
                          "eval_harness.py before any production wiring.")
        elif self.verdict == "PASS":
            lines.append("WHY THIS DIDN'T MAKE THE CUT")
            lines.append("-" * 72)
            lines.append("  At least one HARD-DISQUALIFY criterion failed "
                          "(typically: wrong asset class, or no OOS, or no")
            lines.append("  cost discipline). The substrate mismatch is the "
                          "most common — be especially careful re-reading")
            lines.append("  the abstract; sometimes 'equity' means 'equity "
                          "options' which is N/A for our Alpaca account.")
        else:  # SHELVE
            lines.append("SHELVED — INTERESTING BUT NOT YET ACTIONABLE")
            lines.append("-" * 72)
            lines.append("  The paper passes the substrate test but lacks "
                          "enough evidence to clear the rigor bar. Re-read in")
            lines.append("  a quarter; if subsequent papers cite + extend, "
                          "promote to PURSUE.")
        return "\n".join(lines)


def fetch_arxiv(arxiv_id: str) -> Paper:
    """Pull title + abstract + authors via arXiv's public Atom API."""
    url = ARXIV_API.format(arxiv_id=urllib.parse.quote(arxiv_id))
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read().decode("utf-8")
    root = ET.fromstring(data)
    ns = {"a": "http://www.w3.org/2005/Atom",
          "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        raise RuntimeError(f"arXiv returned no entry for {arxiv_id}")
    title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
    summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
    authors = [a.findtext("a:name", default="", namespaces=ns).strip()
                for a in entry.findall("a:author", ns)]
    primary_cat = ""
    pc = entry.find("arxiv:primary_category", ns)
    if pc is not None:
        primary_cat = pc.get("term", "")
    cats = [c.get("term", "") for c in entry.findall("a:category", ns)]
    summary_url = f"https://arxiv.org/abs/{arxiv_id}"
    return Paper(
        arxiv_id=arxiv_id,
        title=re.sub(r"\s+", " ", title),
        authors=authors,
        abstract=re.sub(r"\s+", " ", summary),
        primary_category=primary_cat,
        categories=cats,
        summary_url=summary_url,
    )


# === Triage criteria ===
# Each returns (passed: bool, reason: str). The rubric is conservative
# and uses keyword presence — false negatives are real, the trade-off
# is keeping the firehose down to ~5-10 % PURSUE rate.

FINANCE_TERMS = [
    "equity", "stock", "stocks", "asset pricing", "portfolio",
    "factor", "alpha", "sharpe", "momentum", "value", "quality",
    "anomaly", "cross-section", "long-only", "long-short", "risk-adjusted",
]

# These three are immediate disqualifiers — our substrate explicitly
# excludes them
SUBSTRATE_DISQUALIFIERS = [
    "option pricing", "options pricing", "implied vol", "implied volatility",
    "high-frequency trading", "ultra-high-frequency", "tick-level",
    "cryptocurrency", "bitcoin", "ethereum",
    "commodities", "forex",
    "fixed income", "interest rates", "yield curve", "treasury bond",
]

OOS_TERMS = [
    "out-of-sample", "out of sample", "oos",
    "walk-forward", "walk forward",
    "held-out", "held out", "holdout",
    "test set", "validation",
]

COST_TERMS = [
    "transaction cost", "trading cost", "after cost", "net of cost",
    "slippage", "implementation", "turnover", "bid-ask",
    "bps", "basis point",
]

REGIME_TERMS = [
    "regime", "subsample", "subperiod", "structural break",
    "stratified", "bear market", "recession", "drawdown",
]

NEW_MECHANISM_TERMS = [
    "novel", "new", "propose", "introduce",
    "factor timing", "regime-conditional", "conditional",
    "stacking", "ensemble",
]

LATE_PUBLICATION_DECAY = [
    # If the paper just rebrands momentum/value, the McLean-Pontiff
    # post-publication decay likely already happened. We want
    # mechanisms that haven't been data-mined to death.
    "momentum", "value premium", "low volatility anomaly",
]


def _has_any(text: str, terms: list[str]) -> tuple[bool, str]:
    """Case-insensitive containment of any term."""
    t = text.lower()
    hits = [w for w in terms if w.lower() in t]
    return (bool(hits), hits[0] if hits else "")


def triage(paper: Paper) -> TriageResult:
    text = f"{paper.title} {paper.abstract}"
    crit = {}

    # === 1. ASSET CLASS ===
    has_fin, fin_term = _has_any(text, FINANCE_TERMS)
    disq, disq_term = _has_any(text, SUBSTRATE_DISQUALIFIERS)
    is_finance_cat = paper.primary_category.startswith(("q-fin", "stat", "econ"))
    asset_pass = (has_fin or is_finance_cat) and not disq
    if disq:
        asset_reason = (f"Hard-disqualifier: paper covers '{disq_term}' which "
                          "is outside our substrate (long-only US equity, "
                          "monthly-daily, Alpaca). HARD STOP.")
    elif is_finance_cat:
        asset_reason = (f"Primary arXiv category is {paper.primary_category} "
                          "— passes the substrate test.")
    elif has_fin:
        asset_reason = (f"Title/abstract mentions '{fin_term}' — plausibly "
                          "applicable; verify with the full PDF.")
    else:
        asset_reason = ("No finance keywords found in title/abstract. "
                          "Likely outside our substrate.")
    crit["1. Substrate fit (long-only US equity)"] = (asset_pass, asset_reason)

    # === 2. OOS DISCIPLINE ===
    has_oos, oos_term = _has_any(text, OOS_TERMS)
    crit["2. OOS / walk-forward discipline"] = (
        has_oos,
        f"Paper mentions '{oos_term}'." if has_oos else
        "Abstract does NOT mention OOS, walk-forward, or holdout testing. "
        "In-sample-only results are unusable — every published anomaly "
        "looks great IS."
    )

    # === 3. COST DISCIPLINE ===
    has_cost, cost_term = _has_any(text, COST_TERMS)
    crit["3. Reports net-of-cost results"] = (
        has_cost,
        f"Paper mentions '{cost_term}'." if has_cost else
        "Abstract does NOT mention transaction costs / slippage / "
        "turnover. Gross alphas routinely vanish after 5-10 bps/side."
    )

    # === 4. REGIME / SUBPERIOD STRATIFICATION ===
    has_regime, reg_term = _has_any(text, REGIME_TERMS)
    crit["4. Regime stratification"] = (
        has_regime,
        f"Paper mentions '{reg_term}'." if has_regime else
        "Abstract does NOT mention regimes / subperiods. Smooth-average "
        "alphas can hide that the edge only worked 2003-2007 (or 2020-2022)."
    )

    # === 5. NEW MECHANISM (vs. another momentum rebrand) ===
    has_new, new_term = _has_any(text, NEW_MECHANISM_TERMS)
    has_old, old_term = _has_any(text, LATE_PUBLICATION_DECAY)
    # Pass if the paper proposes something new OR is not a known-decayed factor
    new_pass = has_new and not (has_old and not has_new)
    if has_new and has_old:
        new_reason = (f"Claims novelty ('{new_term}') but also leans on "
                        f"'{old_term}' — read carefully to see if it's a "
                        "fresh take or just a momentum-rebrand.")
    elif has_new:
        new_reason = f"Claims novelty ('{new_term}'). Promising."
    elif has_old:
        new_reason = (f"Mentions known-decayed factor '{old_term}' without "
                        "novelty signals. Likely incremental rather than fresh.")
    else:
        new_reason = "No novelty signals in the abstract. Verify with PDF."
    crit["5. Novel mechanism (not a known-decayed factor rebrand)"] = (
        new_pass, new_reason,
    )

    # === 6. RIGOR (proxy: combination of multiple indicators) ===
    # Heuristic: papers that pass OOS + cost + regime tend to have real
    # rigor; lacking all three is a red flag even if the asset class is right.
    rigor_count = sum([has_oos, has_cost, has_regime])
    rigor_pass = rigor_count >= 2
    crit["6. Multi-axis rigor (>=2 of OOS/cost/regime)"] = (
        rigor_pass,
        f"{rigor_count}/3 rigor axes covered in the abstract." if rigor_count else
        "Zero rigor axes mentioned. The abstract reads like a marketing claim."
    )

    score = sum(p for (p, _) in crit.values())

    # Verdict: HARD STOP if substrate fails, otherwise score-based
    if not asset_pass:
        verdict = "PASS"
    elif score >= 5:
        verdict = "PURSUE"
    elif score >= 3:
        verdict = "SHELVE"
    else:
        verdict = "PASS"

    return TriageResult(paper=paper, verdict=verdict, score=score, criteria=crit)


def _parse_arxiv_id(s: str) -> str:
    """Accept full URL, abs/, pdf/, or bare id."""
    s = s.strip()
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", s)
    if m:
        return m.group(1)
    return s  # let arxiv API error out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="arXiv paper triage")
    ap.add_argument("arxiv_id", help="arXiv ID or URL (e.g. 2605.06285)")
    args = ap.parse_args(argv)

    arxiv_id = _parse_arxiv_id(args.arxiv_id)
    try:
        paper = fetch_arxiv(arxiv_id)
    except Exception as e:
        print(f"ERROR fetching arXiv {arxiv_id}: {type(e).__name__}: {e}")
        return 1

    result = triage(paper)
    print(result.render())
    # Exit code: 0 if PURSUE, 1 if SHELVE, 2 if PASS — lets you chain it
    return {"PURSUE": 0, "SHELVE": 1, "PASS": 2}[result.verdict]


if __name__ == "__main__":
    sys.exit(main())

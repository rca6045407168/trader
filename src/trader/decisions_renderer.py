"""Decision-row formatters used by the dashboard.

Extracted from scripts/dashboard.py so the logic is testable in
isolation (no Streamlit context required). The dashboard imports
these via:

    from trader.decisions_renderer import (
        parse_rationale, fmt_why, fmt_reasoning,
    )

Three public functions:
  - parse_rationale(raw)  → dict
  - fmt_why(raw)          → str   (compact one-liner for the table)
  - fmt_reasoning(row)    → str   (full-paragraph plain-English why)
"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_rationale(raw: Any) -> dict:
    """Tolerant parser: returns a dict (or empty) from the
    rationale_json column. Handles JSON strings, dicts, and the
    occasional raw quoted string left from the auto-router log."""
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def fmt_why(raw: Any) -> str:
    """Compact one-liner for the dataframe `why` column.

    Picks the highest-signal field from the rationale dict and
    renders it as a short fragment ('12-1 mom +48.1%')."""
    d = parse_rationale(raw)
    if not d:
        return str(raw)[:120] if raw else ""
    bits: list[str] = []
    tr = d.get("trailing_return", d.get("momentum"))
    if tr is not None:
        bits.append(f"12-1 mom {tr * 100:+.1f}%")
    if d.get("rsi") is not None:
        bits.append(f"RSI {d['rsi']:.0f}")
    if d.get("z_score") is not None:
        bits.append(f"z {d['z_score']:+.2f}")
    return " · ".join(bits) if bits else str(d)[:120]


def fmt_reasoning(row: dict) -> str:
    """Full-paragraph English explanation of a single decision row.

    Uses every available field — ts, ticker, action, style, score,
    rationale_json, final — and emits 2-4 sentences explaining what
    the trade was, what signal fired, how the strategy ranked it,
    and how it landed in the live book."""
    d = parse_rationale(row.get("rationale_json"))
    ticker = row.get("ticker", "?")
    action = (row.get("action") or "").upper()
    style = row.get("style") or "?"
    score = row.get("score")
    final = row.get("final") or ""
    ts = row.get("ts", "")

    # Parse weight + variant out of the "final" string (e.g.
    # "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)")
    w_match = re.search(r"@\s*([\d.]+)%", final)
    weight = w_match.group(1) if w_match else None
    variant_match = re.search(r"selected=(\S+?)\)?$", final)
    variant = variant_match.group(1) if variant_match else None

    verb_map = {"BUY": "purchased", "SELL": "sold", "HOLD": "held"}
    verb = verb_map.get(action, action.lower() or "acted on")

    # === Per-style branches ===
    if style == "MOMENTUM":
        tr = d.get("trailing_return", d.get("momentum"))
        lookback = d.get("lookback_months", 12)
        if tr is not None:
            mom_txt = (
                f"a trailing {lookback - 1}-month price-momentum reading "
                f"of {tr * 100:+.1f}%"
            )
        else:
            mom_txt = (
                f"a momentum score of {score:.4f}"
                if score is not None else "a momentum signal"
            )

        ranking = ""
        if score is not None:
            if score > 1.5:
                ranking = (
                    "This is in the top quintile of the universe — a "
                    "strong-trend name that the cross-sectional sleeve "
                    "is overweighting."
                )
            elif score > 0.5:
                ranking = (
                    "This places the name in the middle ranks of the "
                    "momentum scan; included as part of the top-N "
                    "selection to maintain breadth."
                )
            else:
                ranking = (
                    "Momentum is positive but modest — the name made "
                    "the top-N cut largely on relative rank rather "
                    "than on an outsized signal."
                )

        size_txt = (
            f"The position was sized at {weight}% of book"
            if weight else "Position size was set by the sleeve"
        )
        variant_txt = (
            f", and the auto-router selected the `{variant}` variant "
            "this run after the eligibility filter + hysteresis check."
            if variant else "."
        )
        return (
            f"On {ts[:10]}, the LIVE strategy {verb} **{ticker}** out "
            f"of the **{style}** sleeve. The trade was triggered by "
            f"{mom_txt}, computed as the trailing {lookback - 1}-month "
            f"total return excluding the most recent month (the "
            f"standard 12-1 momentum specification from "
            f"Jegadeesh-Titman 1993). {ranking} {size_txt}{variant_txt}"
        )

    if style == "live_auto":
        variant_txt = (
            f"`{variant}`" if variant else "the highest-scoring variant"
        )
        size_txt = (
            f" sized at {weight}% of book." if weight else "."
        )
        return (
            f"On {ts[:10]}, the auto-router cycled through the "
            f"eval-pool and selected **{variant_txt}** as the LIVE "
            f"variant for this run, after applying the eligibility "
            f"filter (MIN_EVIDENCE_MONTHS≥6, MAX_BETA≤1.20, "
            f"MIN_DD≥-25%) and the hysteresis rule that prevents "
            f"churn between variants of similar score. The decision "
            f"spilled into a notional position in **{ticker}**"
            f"{size_txt} This row is the orchestration-level record; "
            f"per-name decisions in the same run appear as separate "
            f"MOMENTUM rows."
        )

    if style == "BOTTOM_CATCH":
        z = d.get("z_score")
        rsi = d.get("rsi")
        tr = d.get("trailing_return")
        extras = []
        if z is not None:
            extras.append(
                f"the name's 60-day z-score was {z:+.2f}σ "
                "below its trailing mean"
            )
        if rsi is not None:
            extras.append(f"RSI was at {rsi:.0f} (oversold territory)")
        if tr is not None:
            extras.append(f"trailing return was {tr * 100:+.1f}%")
        ext_txt = (
            ", and ".join(extras) if extras
            else "the name registered an oversold reading"
        )
        # Capitalise the first letter only; preserve acronyms like RSI
        if ext_txt:
            ext_txt = ext_txt[0].upper() + ext_txt[1:]
        size_txt = (
            f"The position was sized at {weight}% of book."
            if weight else ""
        )
        return (
            f"On {ts[:10]}, the LIVE strategy {verb} **{ticker}** out "
            f"of the **BOTTOM_CATCH** sleeve — a counter-trend overlay "
            f"that buys names on statistically extreme drawdowns. "
            f"{ext_txt}. The sleeve targets a 5-20 day "
            f"mean-reversion holding period and the position will "
            f"close at the time-exit threshold if no recovery "
            f"materialises. {size_txt}"
        )

    if style in ("EARNINGS_REACT", "earnings_reactor"):
        size_txt = f" Sized at {weight}% of book." if weight else ""
        return (
            f"On {ts[:10]}, the earnings reactor {verb} **{ticker}** "
            f"in response to a fresh earnings signal. The reactor "
            f"monitors EPS surprises and post-earnings drift; this "
            f"trade reflects the post-event directional bias the "
            f"model assigned at the time of the signal.{size_txt}"
        )

    # === Default / unknown style ===
    size_txt = f" sized at {weight}% of book" if weight else ""
    if d:
        details = ", ".join(
            f"{k}={v}" for k, v in d.items() if not isinstance(v, dict)
        )
        return (
            f"On {ts[:10]}, the LIVE strategy {verb} **{ticker}** "
            f"under the **{style}** style{size_txt}. Rationale fields "
            f"recorded at decision time: {details}. The final outcome "
            f"was: {final}."
        )
    return (
        f"On {ts[:10]}, the LIVE strategy {verb} **{ticker}** under "
        f"the **{style}** style{size_txt}. Final action: {final}."
    )

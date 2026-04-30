"""Activist 13D signal scanner.

Pulls SEC EDGAR full-text search for 13D filings by known activist funds.
Each 13D (>5% beneficial ownership disclosure) is a strong signal: the
activist has done $10M+ of due diligence, has a thesis, and is willing to
take a public stake.

Academic edge: Brav, Jiang, Partnoy, Thomas (2008): activist 13D filings
generate 10-15% excess return over 6 months. Replicated multiple times.

Why this works (per the literature):
  - Activists do real DD; their stake is a signal of conviction
  - Filing forces public attention to undervaluation thesis
  - Buyback / catalyst pressure compresses the discount over months
  - Most activists hold 6-24 months — gives us a window

Universe of activists tracked (top funds by AUM + track record):
  - Pershing Square (Ackman)
  - Elliott Investment Management (Singer)
  - Carl Icahn / Icahn Enterprises
  - Third Point (Dan Loeb)
  - Starboard Value
  - Trian Partners (Peltz)
  - ValueAct Capital
  - Glenview Capital
  - Engaged Capital
  - Engine No. 1
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
USER_AGENT = "trader-research/1.0 contact@richardchen.com"

KNOWN_ACTIVISTS = [
    "Pershing Square Capital Management",
    "Elliott Investment Management",
    "Elliott Associates",
    "Icahn",  # broad — catches Icahn Enterprises, Icahn Capital, Carl Icahn
    "Third Point",
    "Starboard Value",
    "Trian Fund Management",
    "ValueAct Capital",
    "Glenview Capital",
    "Engaged Capital",
    "Engine No. 1",
    "Sachem Head Capital",
    "Land and Buildings",
    "JANA Partners",
]


@dataclass
class ActivistFiling:
    activist: str
    target_ticker: str
    target_name: str
    file_date: datetime
    form: str  # "SC 13D" (initial) or "SC 13D/A" (amendment)
    accession: str


def _extract_target_ticker(display_names: list[str], activist_query: str) -> Optional[str]:
    """Extract target company ticker from display_names list.

    The list usually has 2 entries: target + filer. Filer matches activist_query.
    Target is the OTHER one. Ticker is in parens after company name.
    """
    activist_lower = activist_query.lower()
    for name in display_names:
        if activist_lower in name.lower():
            continue  # skip filer
        # Match "(TICKER)" pattern — ticker is uppercase letters/dots, max 5 chars
        m = re.search(r"\(([A-Z][A-Z0-9.\-]{0,5})\)", name)
        if m:
            return m.group(1)
    return None


def fetch_activist_filings(activist: str, start_date: str, end_date: str,
                           initial_only: bool = True) -> list[ActivistFiling]:
    """Fetch 13D filings by `activist` between start_date and end_date.

    initial_only=True: only initial 13D filings (the actionable signal),
                       not amendments which are usually position adjustments.
    """
    params = {
        "q": f'"{activist}"',
        "forms": "SC 13D",
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
    }
    out = []
    seen_accessions = set()
    try:
        r = requests.get(EDGAR_SEARCH_URL, params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [activist={activist}] fetch failed: {e}")
        return out

    hits = data.get("hits", {}).get("hits", [])
    for h in hits:
        src = h.get("_source", {})
        acc = src.get("adsh")
        if not acc or acc in seen_accessions:
            continue
        seen_accessions.add(acc)
        form = src.get("form", "")
        if initial_only and form != "SC 13D":
            continue  # skip amendments
        display_names = src.get("display_names", [])
        target_ticker = _extract_target_ticker(display_names, activist)
        if not target_ticker:
            continue
        # Get target name (the non-filer entry)
        target_name = next(
            (n for n in display_names if activist.lower() not in n.lower()),
            ""
        )
        try:
            file_date = datetime.strptime(src["file_date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        out.append(ActivistFiling(
            activist=activist,
            target_ticker=target_ticker,
            target_name=target_name.split("(")[0].strip(),
            file_date=file_date,
            form=form,
            accession=acc,
        ))
    return out


def fetch_all_activist_filings(start_date: str, end_date: str,
                                activists: Optional[list[str]] = None,
                                initial_only: bool = True) -> list[ActivistFiling]:
    """Pull 13D filings from all known activists in the date range.

    Rate-limited per SEC guidance (10 req/sec max).
    """
    activists = activists or KNOWN_ACTIVISTS
    all_filings = []
    for activist in activists:
        print(f"  [{activist}] fetching...")
        filings = fetch_activist_filings(activist, start_date, end_date, initial_only)
        print(f"    found {len(filings)} 13D filings")
        all_filings.extend(filings)
        time.sleep(0.2)  # SEC rate limit
    # Deduplicate by accession
    seen = set()
    deduped = []
    for f in all_filings:
        if f.accession in seen:
            continue
        seen.add(f.accession)
        deduped.append(f)
    return deduped

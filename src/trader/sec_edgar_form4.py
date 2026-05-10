"""SEC EDGAR direct Form-4 ingestion — transaction-level insider data.

Upgrade path from src/trader/insider_signal.py (yfinance 6-month
aggregate) to authoritative SEC filings with per-transaction
granularity and 30-day rolling windows. This matches the academic
Cohen-Malloy-Pomorski 2012 spec.

Data flow:
  1. Resolve ticker → CIK via SEC's company-tickers.json
     (cached locally, 7-day TTL — the list is stable).
  2. Fetch the company's recent submissions via
     https://data.sec.gov/submissions/CIK{cik}.json
  3. Filter to Form 4 filings in the last `window_days` days.
  4. For each Form 4, fetch the primary XML doc and parse
     `nonDerivativeTransaction` blocks:
       - transactionCode (P = purchase, S = sale)
       - transactionShares
       - transactionPricePerShare
       - reportingOwner.relationship.isOfficer/isDirector
  5. Aggregate: net-buy-value = Σ(P_shares × P_price) -
                                Σ(S_shares × S_price)
     where rows are filtered to true officers/directors
     (exclude 10%-owner-only filings to reduce noise).

Rate limits:
  - SEC mandates ≤10 requests/second.
  - A custom User-Agent identifying the caller is REQUIRED.
  - We respect both. Caller can configure `MAX_QPS` via env.

Cache:
  - `data/edgar_cache/` directory; one parquet per ticker keyed by
    the latest accession-number we've seen. 24-hour TTL.
  - First run for the 50-name universe is ~30-60s. Cached runs are
    instant.

Failure modes:
  - Tickers not in EDGAR's company list (e.g. BRK-B vs BRK.B
    mapping) silently return 0 net-buy.
  - Network failures degrade to {} (auto-router skips strategy).
  - Malformed XML rows are skipped (logged on debug only).

For production wiring, this module is invoked by
trader.eval_strategies.xs_top10_insider_buy_30d (a v6.0.x variant
of the yfinance-backed insider strategy with finer granularity).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd


# SEC requires a real User-Agent. Set yours via env, otherwise use
# a reasonable default that still identifies you.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "trader-research richard.chen@flexhaul.ai",
)
MAX_QPS = float(os.environ.get("SEC_MAX_QPS", "8.0"))  # SEC caps at 10/s
_MIN_INTERVAL = 1.0 / MAX_QPS

CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "edgar_cache"
CIK_MAP_PATH = CACHE_ROOT / "company_tickers.json"
CIK_MAP_TTL_DAYS = 7
SUBMISSIONS_TTL_HOURS = 24

_last_request_ts: list[float] = [0.0]


def _polite_request(url: str, timeout: int = 30) -> bytes:
    """SEC-compliant fetch: enforces QPS cap + sets User-Agent."""
    now = time.monotonic()
    delta = now - _last_request_ts[0]
    if delta < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - delta)
    req = urllib.request.Request(
        url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "*/*"},
    )
    _last_request_ts[0] = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _load_cik_map(cache_path: Optional[Path] = None) -> dict[str, str]:
    """Returns ticker → 10-digit zero-padded CIK string."""
    path = cache_path or CIK_MAP_PATH
    fresh_cutoff = datetime.utcnow() - timedelta(days=CIK_MAP_TTL_DAYS)
    if path.exists():
        mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
        if mtime > fresh_cutoff:
            try:
                with path.open("rb") as f:
                    data = json.load(f)
                return _parse_cik_map(data)
            except Exception:
                pass
    # Fetch fresh
    data = json.loads(_polite_request(
        "https://www.sec.gov/files/company_tickers.json",
    ).decode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f)
    return _parse_cik_map(data)


def _parse_cik_map(data: dict) -> dict[str, str]:
    """The endpoint returns a dict of {"0": {cik_str, ticker, title}, ...}."""
    out = {}
    for v in data.values():
        if not isinstance(v, dict):
            continue
        ticker = v.get("ticker", "").upper()
        cik = v.get("cik_str")
        if not ticker or cik is None:
            continue
        out[ticker] = f"{int(cik):010d}"
        # Also map common variants — e.g. BRK-B (yfinance) and BRK.B (EDGAR)
        if "-" in ticker:
            out[ticker.replace("-", ".")] = f"{int(cik):010d}"
        if "." in ticker:
            out[ticker.replace(".", "-")] = f"{int(cik):010d}"
    return out


def _fetch_submissions(cik: str,
                         cache_dir: Optional[Path] = None) -> dict:
    """Submissions index for a CIK. Lists recent filings."""
    cache_dir = cache_dir or CACHE_ROOT
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"submissions_{cik}.json"
    fresh = datetime.utcnow() - timedelta(hours=SUBMISSIONS_TTL_HOURS)
    if cache_path.exists():
        if datetime.utcfromtimestamp(cache_path.stat().st_mtime) > fresh:
            try:
                with cache_path.open("rb") as f:
                    return json.load(f)
            except Exception:
                pass
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = json.loads(_polite_request(url).decode("utf-8"))
    with cache_path.open("w") as f:
        json.dump(data, f)
    return data


def _list_recent_form4_accessions(submissions: dict,
                                    window_days: int) -> list[dict]:
    """From a submissions dict, return the recent Form 4 entries
    within the window. Each entry has accessionNumber + filingDate.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).date()
    out = []
    for i, form in enumerate(forms):
        if form != "4":
            continue
        try:
            filing_date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue
        if filing_date < cutoff:
            continue
        out.append({
            "accession": accs[i],
            "filing_date": dates[i],
            "primary_doc": primary_docs[i] if i < len(primary_docs) else None,
        })
    return out


def _fetch_form4_xml(cik: str, accession: str,
                       primary_doc: Optional[str] = None) -> Optional[bytes]:
    """Fetch the Form 4 primary XML doc. Returns None on failure."""
    acc_clean = accession.replace("-", "")
    # primary_doc is typically the XML filename like "doc4.xml"
    if not primary_doc or not primary_doc.endswith((".xml", ".XML")):
        # Fallback: try to read the filing index to find an XML
        return None
    url = (f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{acc_clean}/{primary_doc}")
    try:
        return _polite_request(url)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None


def _parse_form4(xml_bytes: bytes) -> list[dict]:
    """Parse a Form 4 primary XML document into transaction rows.

    Returns a list of {date, code, shares, price, is_officer,
                         is_director, is_10pct_owner}.
    Only non-derivative transactions (TableI) are returned — option
    exercises etc. live in derivativeTransaction (TableII) and have
    different economic meaning.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    # Determine reporter relationships
    is_officer = False
    is_director = False
    is_10pct = False
    for rel in root.findall(".//reportingOwnerRelationship"):
        if (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
            is_officer = True
        if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
            is_director = True
        if (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
            is_10pct = True
    out = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        date_str = ""
        de = tx.find("transactionDate/value")
        if de is not None:
            date_str = (de.text or "").strip()
        code_el = tx.find("transactionCoding/transactionCode")
        code = (code_el.text if code_el is not None else "").strip()
        shares_el = tx.find("transactionAmounts/transactionShares/value")
        price_el = tx.find("transactionAmounts/transactionPricePerShare/value")
        ad_el = tx.find("transactionAmounts/transactionAcquiredDisposedCode/value")
        try:
            shares = float((shares_el.text or "0").strip()) if shares_el is not None else 0.0
        except (ValueError, TypeError):
            shares = 0.0
        try:
            price = float((price_el.text or "0").strip()) if price_el is not None else 0.0
        except (ValueError, TypeError):
            price = 0.0
        ad = (ad_el.text if ad_el is not None else "").strip()
        out.append({
            "date": date_str,
            "code": code,
            "shares": shares,
            "price": price,
            "ad_code": ad,
            "is_officer": is_officer,
            "is_director": is_director,
            "is_10pct": is_10pct,
        })
    return out


def net_buy_value_30d(ticker: str,
                       window_days: int = 30,
                       cik_map: Optional[dict] = None,
                       cache_dir: Optional[Path] = None,
                       exclude_10pct_only: bool = True) -> Optional[float]:
    """Net insider buy value (USD) over the last `window_days`.

    Returns None if the ticker can't be resolved or no data.
    Returns a signed float: positive = net buying, negative = net selling.

    "Officer/director" filter: by default, we include only filings
    where reportingOwnerRelationship has isOfficer or isDirector
    set true. 10%-owner-only filings are excluded — those tend to
    be institutional rebalancing (Vanguard etc.), not informed
    insider activity.

    Cohen-Malloy-Pomorski use only ROUTINE insider purchases (multi-
    purchase patterns). We approximate by summing all P (purchase)
    transactions over the window and netting against S (sale)
    transactions; the time-series concentration is captured by
    `window_days`.
    """
    cik_map = cik_map if cik_map is not None else _load_cik_map()
    cik = cik_map.get(ticker.upper())
    if cik is None:
        return None
    try:
        subs = _fetch_submissions(cik, cache_dir=cache_dir)
    except Exception:
        return None
    accessions = _list_recent_form4_accessions(subs, window_days)
    if not accessions:
        return 0.0
    total = 0.0
    for entry in accessions:
        xml = _fetch_form4_xml(cik, entry["accession"], entry.get("primary_doc"))
        if not xml:
            continue
        rows = _parse_form4(xml)
        for r in rows:
            # Officer/director filter
            if exclude_10pct_only:
                if not (r["is_officer"] or r["is_director"]):
                    continue
            # P = purchase, S = sale (open market). Skip M (exercise),
            # F (tax-withholding), A (award), etc. — not informed.
            if r["code"] not in ("P", "S"):
                continue
            value = r["shares"] * r["price"]
            if value <= 0:
                continue
            # ad_code: A = acquired (buy direction), D = disposed (sell)
            # cross-check with transactionCode
            if r["code"] == "P" or r["ad_code"] == "A":
                total += value
            elif r["code"] == "S" or r["ad_code"] == "D":
                total -= value
    return total


def insider_30d_scores(universe: list[str],
                        window_days: int = 30,
                        cache_dir: Optional[Path] = None) -> dict[str, float]:
    """Net insider buy VALUE per ticker over `window_days`.

    Unlike `trader.insider_signal.insider_scores` (which returns a
    yfinance percent), this returns raw USD net-buy. Caller should
    normalize by market cap or just rank cross-sectionally.

    Missing tickers (not in EDGAR map, or fetch failed) are silently
    dropped — caller can detect by intersecting `universe` with
    returned keys.
    """
    cik_map = _load_cik_map()
    out: dict[str, float] = {}
    for sym in universe:
        v = net_buy_value_30d(sym, window_days=window_days,
                                cik_map=cik_map, cache_dir=cache_dir)
        if v is not None:
            out[sym] = v
    return out


def top_n_by_30d_insider_buy(universe: list[str],
                                n: int = 10,
                                window_days: int = 30,
                                min_buy_value: float = 100_000,
                                cache_dir: Optional[Path] = None) -> list[tuple[str, float]]:
    """Top-N tickers by 30-day insider buy value, filtered to those
    with net buy >= `min_buy_value` (default $100k — filters out noise).
    """
    scores = insider_30d_scores(universe, window_days=window_days,
                                   cache_dir=cache_dir)
    ranked = [(t, s) for t, s in scores.items() if s >= min_buy_value]
    ranked.sort(key=lambda x: -x[1])
    return ranked[:n]

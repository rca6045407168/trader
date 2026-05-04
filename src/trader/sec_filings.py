"""SEC EDGAR filings fetcher (v3.68.0).

Free, always-available, no API key. Hits SEC's public JSON endpoints:

  https://www.sec.gov/files/company_tickers.json   ← ticker → CIK map
  https://data.sec.gov/submissions/CIK{cik:010d}.json ← per-company filings list
  https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodashes}/{primary_doc}

SEC requires a User-Agent string identifying the requester. We pass
"trader-research <contact>" — change `SEC_USER_AGENT` env if needed.
SEC rate-limit: 10 req/sec — we don't come anywhere close.

## Why 8-K matters most

For event-driven trading, 8-K is the highest-signal-density form:
- Item 2.02 = Results of Operations (the earnings press release)
- Item 1.01 = Material agreements
- Item 5.02 = Officer changes
- Item 7.01 = Reg FD disclosures (often guidance)
- Item 8.01 = Other material events

A typical 8-K announcing earnings is filed within minutes of the
press release crossing the wire. Faster than the analyst transcript
which lags 1-7 days.

## What this module does NOT do

- Doesn't store filings (that's `filings_archive.py`)
- Doesn't analyze with Claude (that's `earnings_reactor.py`)
- Doesn't follow XBRL inline-tagged data (we treat filings as plain
  text). Future enhancement.
"""
from __future__ import annotations

import os
import re
import time
import urllib.request
import urllib.error
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "trader-research richard@flexhaul.ai"
)
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Module-level cache so we don't re-download the 1.4MB ticker map per call
_ticker_to_cik_cache: Optional[dict[str, int]] = None


@dataclass
class FilingMetadata:
    """A filing as advertised by EDGAR's submissions endpoint. Doesn't
    include the document body — `download_filing()` fetches that."""
    accession: str          # canonical SEC key, e.g. "0001628280-26-001234"
    form_type: str          # "8-K" | "10-Q" | "10-K" etc.
    filed_at: str           # ISO date
    primary_doc: str        # filename of the main exhibit
    cik: int
    items: list[str] = field(default_factory=list)  # 8-K Items, e.g. ["2.02"]
    primary_doc_description: str = ""

    @property
    def archive_url(self) -> str:
        """URL to fetch the primary document text."""
        accession_nodash = self.accession.replace("-", "")
        return (f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
                f"{accession_nodash}/{self.primary_doc}")

    @property
    def filing_index_url(self) -> str:
        """URL of the filing's index page (for human inspection)."""
        accession_nodash = self.accession.replace("-", "")
        return (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={self.cik:010d}&type={self.form_type}")


def _http_get(url: str, timeout: int = 30) -> Optional[bytes]:
    """SEC-compliant HTTP GET. Returns bytes or None on any failure."""
    req = urllib.request.Request(url, headers={
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json, text/html",
        "Host": url.split("/")[2],
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    except Exception:
        return None


def _ticker_to_cik(ticker: str) -> Optional[int]:
    """Resolve ticker → CIK via SEC's free company_tickers.json."""
    global _ticker_to_cik_cache
    if _ticker_to_cik_cache is None:
        raw = _http_get(TICKER_MAP_URL, timeout=30)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
        _ticker_to_cik_cache = {
            entry["ticker"].upper(): int(entry["cik_str"])
            for entry in data.values()
            if "ticker" in entry and "cik_str" in entry
        }
    return _ticker_to_cik_cache.get(ticker.upper())


def fetch_recent_filings(
    symbol: str,
    form_types: tuple[str, ...] = ("8-K", "10-Q", "10-K"),
    since: Optional[str] = None,
    limit: int = 50,
) -> list[FilingMetadata]:
    """List recent filings for a ticker, newest first.

    `since` (ISO date) restricts to filings filed on or after.
    """
    cik = _ticker_to_cik(symbol)
    if cik is None:
        return []

    raw = _http_get(SUBMISSIONS_URL.format(cik=cik), timeout=30)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    accession_list = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filed_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    primary_descs = recent.get("primaryDocDescription", [])
    items_list = recent.get("items", [])

    out: list[FilingMetadata] = []
    for i, form in enumerate(forms):
        if form not in form_types:
            continue
        filed = filed_dates[i] if i < len(filed_dates) else ""
        if since and filed < since:
            continue
        items_str = items_list[i] if i < len(items_list) else ""
        items = [s.strip() for s in items_str.split(",") if s.strip()]
        out.append(FilingMetadata(
            accession=accession_list[i],
            form_type=form,
            filed_at=filed,
            primary_doc=primary_docs[i] if i < len(primary_docs) else "",
            cik=cik,
            items=items,
            primary_doc_description=(primary_descs[i]
                                      if i < len(primary_descs) else ""),
        ))
        if len(out) >= limit:
            break
    return out


def download_filing(meta: FilingMetadata,
                     timeout: int = 60) -> Optional[str]:
    """Fetch the primary document text. Returns None on failure.

    HTML is returned raw — caller may want to strip tags via
    `strip_html()` for Claude-friendly text."""
    raw = _http_get(meta.archive_url, timeout=timeout)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_WS_RE = re.compile(r"\s+")


def strip_html(html: str) -> str:
    """Crude HTML → text. Good enough to feed Claude — preserves the
    English prose, drops tags + scripts. Not a full parser."""
    # Drop <script> and <style> blocks entirely (with their content)
    html = re.sub(r"<script[^>]*>.*?</script>", " ",
                   html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", " ",
                   html, flags=re.IGNORECASE | re.DOTALL)
    # Replace <br> and </p> with newlines for readability
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n\n", html, flags=re.IGNORECASE)
    # Strip remaining tags
    text = _HTML_TAG_RE.sub(" ", html)
    # Decode common entities
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                 .replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&quot;", '"').replace("&apos;", "'")
                 .replace("&#8217;", "'").replace("&#8220;", '"')
                 .replace("&#8221;", '"').replace("&#8211;", "-"))
    text = _HTML_ENTITY_RE.sub("", text)
    # Collapse whitespace
    text = _WS_RE.sub(" ", text)
    return text.strip()


def is_earnings_8k(meta: FilingMetadata) -> bool:
    """Item 2.02 = Results of Operations. The unambiguous earnings 8-K."""
    return meta.form_type == "8-K" and "2.02" in meta.items


def is_material_8k(meta: FilingMetadata) -> bool:
    """Items considered high-signal for event-driven traders."""
    if meta.form_type != "8-K":
        return False
    material_items = {"1.01", "2.02", "5.02", "7.01", "8.01", "1.03", "2.03"}
    return any(item in material_items for item in meta.items)


def fetch_and_pack(symbol: str,
                    form_types: tuple[str, ...] = ("8-K",),
                    since: Optional[str] = None,
                    limit: int = 10,
                    sleep_between: float = 0.15,
                    max_chars: int = 200_000,
                    ) -> list[tuple[FilingMetadata, str]]:
    """Convenience: fetch metadata + body for each filing, returning
    (meta, text) tuples. Sleeps `sleep_between` seconds between
    downloads to stay polite to SEC. Trims each document to
    `max_chars` so we don't pass 10MB blobs into Claude."""
    metas = fetch_recent_filings(symbol, form_types, since, limit)
    out: list[tuple[FilingMetadata, str]] = []
    for m in metas:
        body = download_filing(m)
        if body is None:
            continue
        # Strip HTML if it looks like HTML
        if "<html" in body[:500].lower() or "<!doctype" in body[:200].lower():
            body = strip_html(body)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n\n[truncated]"
        out.append((m, body))
        if sleep_between > 0:
            time.sleep(sleep_between)
    return out

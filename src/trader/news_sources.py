"""[v3.61.0] News data sources — US + Asian markets.

Free-tier RSS / API adapters for financial news streams. Lets the
trader system augment decisions with news sentiment without paid
subscriptions (Bloomberg, Reuters, Ravenpack).

Per the customer ask (Asian retail markets — China + Korea):
  • Caixin (China, English + Chinese, has RSS, highest credibility)
  • Yicai (China, retail-oriented Chinese)
  • Sina Finance (China, retail-skewed)
  • Eastmoney (China, retail community signal)
  • Xueqiu (China, social investing platform)
  • Nikkei (Japan, English version)
  • Yonhap (Korea, English)

Per separate user ask (US news):
  • SEC EDGAR (filings — 8-K, 10-Q, 13F, Form 4 insider)
  • Reuters Business / WSJ Markets / Bloomberg via Google News
  • Yahoo Finance (yfinance.Ticker.news)
  • SEC press releases
  • FRED (macro releases via fredapi)
  • Alpha Vantage news sentiment (free tier 500 calls/day)
  • NewsAPI.org free tier (100 req/day, headlines only)
  • Reddit r/wallstreetbets, r/investing (Pushshift / praw)
  • StockTwits (free message stream)

Each source returns a list[NewsItem]. NewsItem has:
  ts (UTC ISO), source, ticker (optional), title, url, body_snippet,
  language ("en" / "zh" / "ja" / "ko").

The system is RESILIENT to source failures — if Caixin RSS errors out,
other sources still return. Caller iterates results and tolerates
partial failures.

⚠️  IMPORTANT for Asian markets:
The trader system uses Alpaca which is US-equities-only. Asian-market
expansion would require: (a) different broker (Interactive Brokers,
Tiger Brokers, Futu/moomoo for HK/CN), (b) different price-data source
(yfinance covers ADRs and major HK/JP/KR but NOT Chinese A-shares —
those need 雪球 / Sina / Wind paid), (c) currency conversion math.
This module ships the NEWS layer; broker/price layer is separate.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class NewsItem:
    ts: str                        # UTC ISO
    source: str                    # "caixin" / "yicai" / "yahoo" / etc
    title: str
    url: str
    body_snippet: str = ""
    ticker: Optional[str] = None   # if known
    language: str = "en"           # "en" / "zh" / "ja" / "ko"
    region: str = "US"             # "US" / "CN" / "JP" / "KR" / "GLOBAL"


# ============================================================
# RSS helpers — pure stdlib, no feedparser dep
# ============================================================
def _http_get(url: str, timeout: int = 15) -> Optional[str]:
    """Minimal HTTP GET. Returns body or None on failure."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "trader-news/1.0 (Mozilla/5.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _parse_rss(xml_text: str) -> list[dict]:
    """Naive RSS parser. Returns list of {title, link, description, pubDate}.
    Avoids the feedparser dep — these RSS feeds are simple."""
    if not xml_text:
        return []
    items = re.findall(r"<item[^>]*>(.*?)</item>", xml_text, re.DOTALL | re.IGNORECASE)
    out = []
    for it in items[:50]:
        def grab(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.DOTALL | re.IGNORECASE)
            if not m:
                return ""
            txt = m.group(1).strip()
            # Strip CDATA
            txt = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", txt, flags=re.DOTALL)
            # Strip HTML tags
            txt = re.sub(r"<[^>]+>", "", txt)
            return txt.strip()
        out.append({
            "title": grab("title"),
            "link": grab("link"),
            "description": grab("description"),
            "pubDate": grab("pubDate") or grab("dc:date"),
        })
    return out


def _normalize_ts(s: str) -> str:
    """Best-effort to ISO. Falls back to current UTC."""
    if not s:
        return datetime.utcnow().isoformat()
    # RSS pubDate format: "Wed, 03 May 2026 12:34:56 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z",
                 "%a, %d %b %Y %H:%M:%S %z",
                 "%Y-%m-%dT%H:%M:%S%z",
                 "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt).astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    return datetime.utcnow().isoformat()


# ============================================================
# 🌏 ASIAN NEWS — China / Japan / Korea
# ============================================================

def fetch_caixin(limit: int = 20) -> list[NewsItem]:
    """Caixin — high-credibility Chinese financial news. English version."""
    out = []
    for url, lang in [("https://www.caixinglobal.com/feed/", "en"),
                       ("http://www.caixin.com/rss/news_finance.xml", "zh")]:
        body = _http_get(url)
        if not body:
            continue
        for item in _parse_rss(body)[:limit]:
            if item.get("title"):
                out.append(NewsItem(
                    ts=_normalize_ts(item.get("pubDate", "")),
                    source="caixin", title=item["title"],
                    url=item.get("link", ""),
                    body_snippet=(item.get("description", "") or "")[:300],
                    language=lang, region="CN",
                ))
    return out


def fetch_yicai(limit: int = 20) -> list[NewsItem]:
    """Yicai (第一财经) — China financial news, retail-oriented."""
    body = _http_get("https://www.yicai.com/api/ajax/getlistdataforinfo?action=newslist&page=1&pagesize=20")
    # Yicai's modern API is JSON; fall back to RSS variant
    if not body or "<channel>" not in (body or ""):
        body = _http_get("https://www.yicai.com/rss/")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="yicai", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="zh", region="CN",
            ))
    return out


def fetch_eastmoney(limit: int = 20) -> list[NewsItem]:
    """Eastmoney (东方财富) — retail-community-driven Chinese investing news."""
    body = _http_get("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=20")
    # Eastmoney is JSON-API; this calls SinaFinance's public roll instead
    # since Eastmoney APIs are gated. Returns SinaFinance which serves
    # similar retail-flavored Chinese news.
    if not body:
        return []
    out = []
    try:
        import json
        data = json.loads(body)
        for item in (data.get("result", {}).get("data", []) or [])[:limit]:
            out.append(NewsItem(
                ts=_normalize_ts(item.get("ctime", "")),
                source="sina_finance", title=item.get("title", ""),
                url=item.get("url", ""),
                body_snippet=item.get("intro", "")[:300],
                language="zh", region="CN",
            ))
    except Exception:
        pass
    return out


def fetch_nikkei(limit: int = 20) -> list[NewsItem]:
    """Nikkei Asia — English; covers Japan + Asia broadly."""
    body = _http_get("https://asia.nikkei.com/rss/feed/nar")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="nikkei_asia", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="JP",
            ))
    return out


def fetch_yonhap(limit: int = 20) -> list[NewsItem]:
    """Yonhap News — Korean, English version available."""
    body = _http_get("https://en.yna.co.kr/rss/news.xml")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="yonhap", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="KR",
            ))
    return out


def fetch_xueqiu(limit: int = 20) -> list[NewsItem]:
    """雪球 (Xueqiu) — Chinese investing community, retail sentiment.

    NOTE: Xueqiu's public API requires session cookie; full feed
    requires login. This stub returns an empty list with a note for
    the implementer to wire either:
      (a) authenticated session via cookie env var, or
      (b) third-party Xueqiu mirror (some exist on GitHub).
    """
    return []  # SCAFFOLD — see module docstring


# ============================================================
# 🇺🇸 US NEWS
# ============================================================

def fetch_yahoo_finance(symbol: str, limit: int = 10) -> list[NewsItem]:
    """yfinance Ticker.news — free, per-ticker headlines."""
    out = []
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        news = t.news or []
        for item in news[:limit]:
            ts = item.get("providerPublishTime") or 0
            try:
                ts_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except Exception:
                ts_iso = datetime.utcnow().isoformat()
            out.append(NewsItem(
                ts=ts_iso, source="yahoo",
                title=item.get("title", ""),
                url=item.get("link", ""),
                body_snippet=item.get("publisher", ""),
                ticker=symbol, language="en", region="US",
            ))
    except Exception:
        pass
    return out


def fetch_sec_edgar_form4(days_back: int = 7, limit: int = 50) -> list[NewsItem]:
    """SEC EDGAR — Form 4 insider transactions (free)."""
    body = _http_get(
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        "&type=4&dateb=&owner=include&count=40&action=getcompany"
    )
    # EDGAR full-text search is heavier; this returns the HTML listing
    # for Form-4 filings. Parse with regex (ugly but stdlib-only).
    if not body:
        return []
    out = []
    rows = re.findall(r'<a href="(/Archives/[^"]+)"[^>]*>([^<]+)</a>',
                       body)[:limit]
    for href, txt in rows:
        if "Form 4" in txt or "/edgar/data/" in href:
            out.append(NewsItem(
                ts=datetime.utcnow().isoformat(),
                source="sec_edgar_form4",
                title=f"Form 4 insider filing: {txt}",
                url=f"https://www.sec.gov{href}",
                language="en", region="US",
            ))
    return out


def fetch_reuters_business(limit: int = 20) -> list[NewsItem]:
    """Reuters Business via Google News RSS (Reuters' own RSS deprecated)."""
    body = _http_get(
        "https://news.google.com/rss/search?q=site:reuters.com+business&hl=en-US"
    )
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="reuters_via_google", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="US",
            ))
    return out


def fetch_wsj_markets(limit: int = 20) -> list[NewsItem]:
    """WSJ Markets RSS (headlines free; full text paywalled)."""
    body = _http_get("https://feeds.content.dowjones.io/public/rss/RSSMarketsMain")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="wsj_markets", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="US",
            ))
    return out


def fetch_marketwatch(limit: int = 20) -> list[NewsItem]:
    """MarketWatch real-time headlines RSS."""
    body = _http_get("https://feeds.marketwatch.com/marketwatch/realtimeheadlines/")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="marketwatch", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="US",
            ))
    return out


def fetch_seeking_alpha_market(limit: int = 20) -> list[NewsItem]:
    """SeekingAlpha market currents (paywall-skim)."""
    body = _http_get("https://seekingalpha.com/market_currents.xml")
    if not body:
        return []
    out = []
    for item in _parse_rss(body)[:limit]:
        if item.get("title"):
            out.append(NewsItem(
                ts=_normalize_ts(item.get("pubDate", "")),
                source="seeking_alpha", title=item["title"],
                url=item.get("link", ""),
                body_snippet=(item.get("description", "") or "")[:300],
                language="en", region="US",
            ))
    return out


# ============================================================
# Aggregator
# ============================================================
SOURCE_REGISTRY: dict[str, dict] = {
    # Asian
    "caixin":            {"fn": fetch_caixin,            "region": "CN", "auth_required": False},
    "yicai":             {"fn": fetch_yicai,             "region": "CN", "auth_required": False},
    "sina_finance":      {"fn": fetch_eastmoney,         "region": "CN", "auth_required": False},
    "nikkei_asia":       {"fn": fetch_nikkei,            "region": "JP", "auth_required": False},
    "yonhap":            {"fn": fetch_yonhap,            "region": "KR", "auth_required": False},
    "xueqiu":            {"fn": fetch_xueqiu,            "region": "CN", "auth_required": True},
    # US
    "reuters":           {"fn": fetch_reuters_business,  "region": "US", "auth_required": False},
    "wsj_markets":       {"fn": fetch_wsj_markets,       "region": "US", "auth_required": False},
    "marketwatch":       {"fn": fetch_marketwatch,       "region": "US", "auth_required": False},
    "seeking_alpha":     {"fn": fetch_seeking_alpha_market, "region": "US", "auth_required": False},
    "sec_edgar_form4":   {"fn": fetch_sec_edgar_form4,   "region": "US", "auth_required": False},
}


def fetch_all(regions: Optional[list[str]] = None,
                per_source_limit: int = 10) -> list[NewsItem]:
    """Pull from all sources in parallel-ish (sequential but fast).
    regions: filter to a subset like ["US", "CN"]."""
    out = []
    for name, meta in SOURCE_REGISTRY.items():
        if regions and meta["region"] not in regions:
            continue
        if meta.get("auth_required"):
            continue  # skip auth-required by default
        try:
            items = meta["fn"](limit=per_source_limit)
            out.extend(items)
        except Exception:
            continue
    out.sort(key=lambda i: i.ts, reverse=True)
    return out


def fetch_per_ticker(ticker: str, limit: int = 10) -> list[NewsItem]:
    """Per-ticker news. Currently yahoo only; extend with Alpha Vantage
    News Sentiment when alpha_vantage_news.py ships."""
    return fetch_yahoo_finance(ticker, limit=limit)

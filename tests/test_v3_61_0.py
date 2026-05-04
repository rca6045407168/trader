"""Tests for v3.61.0 — strategy registry + news sources + sentiment."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Strategy registry
# ============================================================

def test_strategy_registry_imports_and_has_entries():
    from trader.strategy_registry import REGISTRY, summary_counts
    assert len(REGISTRY) > 20
    counts = summary_counts()
    assert counts["total"] == len(REGISTRY)


def test_registry_includes_live_momentum():
    from trader.strategy_registry import find
    s = find("vanilla_momentum_top15")
    assert s is not None
    assert s.status == "LIVE"
    assert s.verification == "VERIFIED"


def test_registry_marks_refuted_strategies():
    """All the items refuted in the v3.60.1 audit must be marked as such."""
    from trader.strategy_registry import find
    for name in ("residual_momentum", "lowvol_sleeve",
                  "momentum_crash_detector", "trailing_stop_15pct",
                  "sector_neutralizer_35cap", "fomc_drift",
                  "earnings_rule_t1_trim50"):
        s = find(name)
        assert s is not None, f"missing registry entry: {name}"
        assert s.verification == "REFUTED", \
            f"{name} should be REFUTED, got {s.verification}"


def test_registry_categorizes_correctly():
    from trader.strategy_registry import by_category
    cat = by_category()
    # Should have at least: alpha, risk_overlay, execution
    assert "alpha" in cat
    assert "risk_overlay" in cat
    assert "execution" in cat


def test_registry_tracks_expected_vs_measured_sharpe():
    """Several strategies must have BOTH expected and measured Sharpe set,
    so we can audit the calibration gap."""
    from trader.strategy_registry import REGISTRY
    has_both = [s for s in REGISTRY
                if s.expected_sharpe is not None
                and s.measured_sharpe is not None]
    assert len(has_both) >= 3


# ============================================================
# News sources
# ============================================================

def test_news_sources_module_imports():
    from trader.news_sources import (
        SOURCE_REGISTRY, NewsItem, fetch_all, fetch_per_ticker,
        fetch_caixin, fetch_yicai, fetch_nikkei, fetch_yonhap,
        fetch_yahoo_finance, fetch_reuters_business, fetch_wsj_markets,
    )
    assert callable(fetch_all)


def test_news_source_registry_has_us_and_asian():
    from trader.news_sources import SOURCE_REGISTRY
    regions = {meta["region"] for meta in SOURCE_REGISTRY.values()}
    assert "US" in regions
    assert "CN" in regions
    assert "JP" in regions
    assert "KR" in regions


def test_news_item_dataclass_shape():
    from trader.news_sources import NewsItem
    n = NewsItem(ts="2026-05-03T00:00:00", source="test", title="t",
                  url="https://example.com/x", language="en", region="US")
    assert n.body_snippet == ""
    assert n.ticker is None


def test_fetch_all_handles_no_network(monkeypatch):
    """If all RSS calls fail, fetch_all should return empty list, not raise."""
    from trader import news_sources
    monkeypatch.setattr(news_sources, "_http_get", lambda url, timeout=15: None)
    out = news_sources.fetch_all(regions=["US"])
    assert isinstance(out, list)


def test_parse_rss_handles_minimal():
    from trader.news_sources import _parse_rss
    xml = """<rss><channel>
        <item><title>Test 1</title><link>http://a.com</link>
              <description>desc</description><pubDate>Wed, 03 May 2026 12:00:00 GMT</pubDate></item>
        <item><title>Test 2</title><link>http://b.com</link></item>
    </channel></rss>"""
    items = _parse_rss(xml)
    assert len(items) == 2
    assert items[0]["title"] == "Test 1"


def test_normalize_ts_falls_back_to_now():
    from trader.news_sources import _normalize_ts
    out = _normalize_ts("not a real date")
    # Should return ISO string starting with current year
    from datetime import datetime as _dt
    assert out.startswith(str(_dt.utcnow().year))


# ============================================================
# News sentiment
# ============================================================

def test_news_sentiment_module_imports():
    from trader.news_sentiment import (
        score_items, aggregate_per_ticker, SentimentScore,
    )
    assert callable(score_items)


def test_score_items_no_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is unset, score_items returns neutral scores
    rather than raising."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from trader.news_sentiment import score_items
    from trader.news_sources import NewsItem
    items = [NewsItem(ts="2026-05-03", source="test",
                       title="Test headline", url="https://a.com")]
    scores = score_items(items)
    assert len(scores) == 1
    assert scores[0].score == 0.0
    assert scores[0].confidence == 0.0


def test_aggregate_per_ticker():
    from trader.news_sentiment import SentimentScore, aggregate_per_ticker
    scores = [
        SentimentScore(url="a", title="t1", score=0.5, confidence=0.8,
                        tickers=["AAPL"]),
        SentimentScore(url="b", title="t2", score=-0.3, confidence=0.6,
                        tickers=["AAPL"]),
        SentimentScore(url="c", title="t3", score=0.2, confidence=0.4,
                        tickers=["MSFT"]),
    ]
    agg = aggregate_per_ticker(scores)
    assert "AAPL" in agg
    assert agg["AAPL"]["n_items"] == 2
    assert agg["MSFT"]["n_items"] == 1


# ============================================================
# News poller
# ============================================================

def test_news_poller_module_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import news_poller as np
    assert callable(np.main)


def test_news_poller_dedup_logic():
    """Verify the URL-hash dedup function."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import news_poller as np
    h1 = np._hash_url("https://example.com/a")
    h2 = np._hash_url("https://example.com/a")
    h3 = np._hash_url("https://example.com/b")
    assert h1 == h2  # idempotent
    assert h1 != h3  # different urls → different hashes


# ============================================================
# Dashboard wiring
# ============================================================

def test_strategy_lab_view_in_dashboard():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def view_strategy_lab" in text
    assert '"strategy_lab": view_strategy_lab' in text
    assert "🧪 Strategy Lab" in text


def test_news_view_in_dashboard():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def view_news" in text
    assert '"news": view_news' in text
    assert "📰 News" in text


# ============================================================
# Docs
# ============================================================

def test_research_reading_list_exists():
    p = Path(__file__).resolve().parent.parent / "docs" / "RESEARCH_READING_LIST.md"
    assert p.exists()
    text = p.read_text()
    # Must include key references
    for ref in ("López de Prado", "Daniel & Moskowitz", "Almgren",
                 "Carver", "Bouchaud"):
        assert ref in text


def test_data_integrations_roadmap_exists():
    p = Path(__file__).resolve().parent.parent / "docs" / "DATA_INTEGRATIONS_ROADMAP.md"
    assert p.exists()
    text = p.read_text()
    # Must mention the customer ask
    assert "Caixin" in text
    assert "Yicai" in text
    assert "Nikkei" in text
    assert "Yonhap" in text
    # Must include the honest Asian-market caveats
    assert "capital control" in text.lower() or "broker" in text.lower()


def test_research_reading_list_flags_strategy_mismatch():
    """The doc must surface the intraday-vs-monthly mismatch up front."""
    p = Path(__file__).resolve().parent.parent / "docs" / "RESEARCH_READING_LIST.md"
    text = p.read_text()
    assert "mismatch" in text.lower()
    assert "monthly" in text.lower()

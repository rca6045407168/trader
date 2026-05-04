"""[v3.61.0] News sentiment + translation via Claude.

Takes a list of NewsItem from news_sources.py and:
  • Translates non-English items (zh/ja/ko → en) for unified processing
  • Scores each item -1 (very bearish) to +1 (very bullish)
  • Identifies tickers mentioned in the title/body
  • Aggregates per-ticker sentiment over a window

Uses the existing trader.copilot.ANTHROPIC_API_KEY. Batches multiple
items per request to control cost. Caches results in
data/news_sentiment_cache.json keyed by URL hash.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


CACHE_FILE = DATA_DIR / "news_sentiment_cache.json"
MODEL = "claude-sonnet-4-6"


@dataclass
class SentimentScore:
    url: str
    title: str
    score: float                  # -1 to +1
    confidence: float             # 0 to 1
    tickers: list[str]            # extracted/inferred
    reasoning: str = ""
    translated_title: str = ""    # if original was non-English
    cached: bool = False


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def score_items(items: list, max_batch: int = 10) -> list[SentimentScore]:
    """items: list[NewsItem] from news_sources. Returns list[SentimentScore]
    aligned 1:1 with input. Cached by URL.
    """
    cache = _load_cache()
    results: list[Optional[SentimentScore]] = [None] * len(items)
    to_process: list[tuple[int, object]] = []

    for i, item in enumerate(items):
        h = _hash(item.url) if item.url else f"none-{i}"
        if h in cache:
            d = cache[h]
            results[i] = SentimentScore(
                url=item.url, title=item.title,
                score=d.get("score", 0.0),
                confidence=d.get("confidence", 0.5),
                tickers=d.get("tickers", []),
                reasoning=d.get("reasoning", ""),
                translated_title=d.get("translated_title", ""),
                cached=True,
            )
        else:
            to_process.append((i, item))

    if not to_process:
        return [r for r in results if r is not None]

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Fail-safe: return neutral scores for un-cached items
        for i, item in to_process:
            results[i] = SentimentScore(
                url=item.url, title=item.title, score=0.0,
                confidence=0.0, tickers=[],
                reasoning="ANTHROPIC_API_KEY not set",
            )
        return [r for r in results if r is not None]

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
    except Exception as e:
        for i, item in to_process:
            results[i] = SentimentScore(
                url=item.url, title=item.title, score=0.0,
                confidence=0.0, tickers=[],
                reasoning=f"anthropic SDK failed: {e}",
            )
        return [r for r in results if r is not None]

    # Process in batches of `max_batch`
    for batch_start in range(0, len(to_process), max_batch):
        batch = to_process[batch_start: batch_start + max_batch]
        prompt = (
            "You score financial news for trading impact. For each item below, "
            "return a JSON object on its own line with fields:\n"
            '  {"idx": int, "score": float [-1, +1], "confidence": float [0, 1], '
            '"tickers": [str, ...], "translated_title": str (only if original '
            'was non-English; else empty), "reasoning": str (≤30 words)}.\n'
            "score: -1=very bearish for tickers, 0=neutral, +1=very bullish.\n"
            "tickers: US-listed ticker symbols mentioned. ADRs OK. Empty list "
            "if no specific company mentioned.\n"
            "Output one JSON object per line, no markdown fences.\n\n"
            "Items:\n"
        )
        for local_i, (orig_i, item) in enumerate(batch):
            prompt += (f"\n[{local_i}] [{getattr(item, 'language', 'en')}] "
                        f"{item.title}\n"
                        f"     source: {getattr(item, 'source', '?')}, "
                        f"region: {getattr(item, 'region', '?')}\n"
                        f"     snippet: {(getattr(item, 'body_snippet', '') or '')[:200]}\n")

        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=2000,
                system="You are a precise news-to-sentiment scorer.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(getattr(b, "text", "") for b in resp.content
                            if getattr(b, "type", None) == "text")
            for line in text.split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                    local_i = int(obj.get("idx", -1))
                    if 0 <= local_i < len(batch):
                        orig_i, item = batch[local_i]
                        s = SentimentScore(
                            url=item.url, title=item.title,
                            score=float(obj.get("score", 0.0)),
                            confidence=float(obj.get("confidence", 0.5)),
                            tickers=obj.get("tickers", []) or [],
                            reasoning=obj.get("reasoning", ""),
                            translated_title=obj.get("translated_title", "") or "",
                        )
                        results[orig_i] = s
                        # Cache
                        h = _hash(item.url) if item.url else f"none-{orig_i}"
                        cache[h] = asdict(s)
                except Exception:
                    continue
        except Exception as e:
            # Fill the un-scored with neutrals
            for orig_i, item in batch:
                if results[orig_i] is None:
                    results[orig_i] = SentimentScore(
                        url=item.url, title=item.title, score=0.0,
                        confidence=0.0, tickers=[],
                        reasoning=f"API error: {type(e).__name__}",
                    )

    _save_cache(cache)
    return [r for r in results if r is not None]


def aggregate_per_ticker(scores: list[SentimentScore]) -> dict[str, dict]:
    """{ticker: {n_items, mean_score, weighted_score, latest_ts}}.
    Weighted = sum(score * confidence) / sum(confidence)."""
    by_ticker: dict[str, list[SentimentScore]] = {}
    for s in scores:
        for t in s.tickers:
            by_ticker.setdefault(t.upper(), []).append(s)
    out = {}
    for t, items in by_ticker.items():
        mean = sum(i.score for i in items) / len(items)
        wsum = sum(i.confidence for i in items)
        wscore = sum(i.score * i.confidence for i in items) / wsum if wsum > 0 else 0
        out[t] = {
            "n_items": len(items),
            "mean_score": mean,
            "weighted_score": wscore,
        }
    return out

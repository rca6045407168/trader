"""[v3.61.0] News poller — scheduled fetch from all configured sources.

Wires news_sources.fetch_all into a recurring job. Persists items to
data/news_stream.jsonl (one JSON object per line, append-only) so the
trader system can backtest sentiment over time.

Schedule options:
  • Run from cron:   `0 */1 * * * /path/to/python scripts/news_poller.py`
    (every hour)
  • Run from prewarm.py — already wired so each container restart
    fetches the latest. (Best-effort; doesn't replace cron.)
  • Run as systemd timer / launchd job

Idempotent: dedup by URL hash within a 7-day rolling window. Re-running
in the same hour adds nothing if no new items dropped.

Output:
  data/news_stream.jsonl  — append-only log
  data/news_poller_status.json — last-run summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

NEWS_STREAM = ROOT / "data" / "news_stream.jsonl"
STATUS_FILE = ROOT / "data" / "news_poller_status.json"
DEDUP_WINDOW_DAYS = 7


def _hash_url(url: str) -> str:
    return hashlib.sha256((url or "").encode()).hexdigest()[:16]


def _load_recent_hashes(days: int = DEDUP_WINDOW_DAYS) -> set[str]:
    """Read last N days of stream and return set of url-hashes already seen."""
    if not NEWS_STREAM.exists():
        return set()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    seen = set()
    try:
        with NEWS_STREAM.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    ts = d.get("ts", "")
                    if ts >= cutoff and d.get("url"):
                        seen.add(_hash_url(d["url"]))
                except Exception:
                    continue
    except Exception:
        pass
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="*", default=None,
                     help="Region filter, e.g. US CN. Default: all.")
    ap.add_argument("--per-source-limit", type=int, default=15)
    ap.add_argument("--score", action="store_true",
                     help="Also run sentiment scoring on fetched items.")
    args = ap.parse_args()

    print(f"=== news_poller · {datetime.utcnow().isoformat()} ===")

    try:
        from trader.news_sources import fetch_all
    except Exception as e:
        print(f"  ERROR importing news_sources: {e}")
        return 1

    print(f"  Regions: {args.regions or 'all'}")
    print(f"  Per-source limit: {args.per_source_limit}")
    items = fetch_all(regions=args.regions, per_source_limit=args.per_source_limit)
    print(f"  Total items pulled: {len(items)}")

    # Dedup against recent stream
    seen = _load_recent_hashes()
    new_items = [i for i in items if _hash_url(i.url) not in seen]
    print(f"  New (not seen in last {DEDUP_WINDOW_DAYS}d): {len(new_items)}")

    # Append to stream
    NEWS_STREAM.parent.mkdir(parents=True, exist_ok=True)
    if new_items:
        with NEWS_STREAM.open("a") as f:
            for item in new_items:
                f.write(json.dumps(asdict(item)) + "\n")
        print(f"  Appended {len(new_items)} items to {NEWS_STREAM.name}")

    # Optional: score sentiment
    n_scored = 0
    if args.score and new_items:
        try:
            from trader.news_sentiment import score_items
            print(f"  Scoring {len(new_items)} items via Claude...")
            scores = score_items(new_items[:50])
            n_scored = len(scores)
            print(f"  Scored {n_scored} items (cached: "
                  f"{sum(1 for s in scores if s.cached)})")
        except Exception as e:
            print(f"  Scoring failed (non-fatal): {e}")

    # Per-source breakdown
    by_source: dict[str, int] = {}
    for it in new_items:
        by_source[it.source] = by_source.get(it.source, 0) + 1
    if by_source:
        print("  Per-source:")
        for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {src:<25} {n}")

    # Status file
    status = {
        "last_run": datetime.utcnow().isoformat(),
        "n_pulled": len(items),
        "n_new": len(new_items),
        "n_scored": n_scored,
        "regions": args.regions or "all",
        "per_source": by_source,
    }
    try:
        STATUS_FILE.write_text(json.dumps(status, indent=2))
    except Exception:
        pass

    print(f"  Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Never block container startup
        print(f"news_poller: fatal (non-blocking): {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)

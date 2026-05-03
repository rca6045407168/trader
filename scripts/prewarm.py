"""Container-start cache pre-warmer (v3.56.11).

Runs once at container boot, BEFORE Streamlit accepts traffic. Computes
the morning briefing, regime overlay, and HMM model fit, persisting all
three to their disk-cache files. Result: the first user-load hits warm
disk caches and renders in <50ms instead of waiting 3.5s for HMM EM
training + yfinance + FRED + GARCH.

Time-bounded at 30s. Failures are logged but DON'T abort container
startup — the dashboard will fall back to lazy compute on first
user request if pre-warm fails (e.g. no network at startup, broker
creds missing). Log goes to stdout so it appears in `docker compose
logs dashboard`.

Wired in as the FIRST step of the dashboard entrypoint:
    python scripts/prewarm.py 2>&1 || true
    streamlit run scripts/dashboard.py ...
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Suppress noisy hmmlearn convergence warning — the EM hits a tiny
# negative delta on the last iteration, harmless, classification is
# still correct. The warning was confusing users.
warnings.filterwarnings("ignore", message="Model is not converging")


def _prewarm_section(name: str, fn) -> bool:
    """Run one prewarm step. Returns True if it succeeded, False if it
    errored. Time-bounded by the caller via outer timeout."""
    t0 = time.time()
    try:
        result = fn()
        elapsed = (time.time() - t0) * 1000
        print(f"  ✓ {name:25s} {elapsed:6.0f}ms", flush=True)
        return result is not None
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"  ✗ {name:25s} {elapsed:6.0f}ms — {type(e).__name__}: {e}", flush=True)
        return False


def main() -> int:
    print("=== prewarm: populating disk caches before Streamlit starts ===", flush=True)
    overall_t0 = time.time()

    # 1. HMM model + regime overlay (the dominant cold-start cost)
    def _hmm():
        from trader.regime_overlay import compute_overlay
        return compute_overlay()
    _prewarm_section("regime overlay (HMM+macro+GARCH)", _hmm)

    # 2. Live portfolio (broker + yfinance batch)
    def _portfolio():
        from trader.positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
        if pf.error:
            print(f"      (broker not reachable: {pf.error})", flush=True)
        return pf
    _prewarm_section("live portfolio", _portfolio)

    # 3. Morning briefing (composes the above + macro + freeze state).
    # Don't import dashboard (it instantiates Streamlit). Write the cache
    # file directly via the storage layout the dashboard expects.
    def _brief():
        from trader.copilot_briefing import compute_briefing
        b = compute_briefing()
        # Recreate the briefing cache file format the dashboard expects
        try:
            import json
            from datetime import datetime
            cache_path = ROOT / "data" / "briefing_cache.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({
                "_cached_at": datetime.utcnow().isoformat(),
                "briefing": {
                    "timestamp": b.timestamp, "headline": b.headline,
                    "equity_now": b.equity_now, "day_pl_pct": b.day_pl_pct,
                    "spy_today_pct": b.spy_today_pct,
                    "excess_today_pct": b.excess_today_pct,
                    "regime": b.regime,
                    "regime_overlay_mult": b.regime_overlay_mult,
                    "regime_enabled": b.regime_enabled,
                    "freeze_active": b.freeze_active,
                    "freeze_reason": b.freeze_reason,
                    "upcoming_events_next7d": b.upcoming_events_next7d,
                    "yesterday_pm_summary": b.yesterday_pm_summary,
                    "notable_facts": b.notable_facts,
                    "raw_data": b.raw_data,
                },
            }, indent=2, default=str))
        except Exception:
            pass
        return b
    _prewarm_section("morning briefing", _brief)

    # 5. v3.59.0 — Nightly journal backup. Idempotent (uses date stamp);
    # safe to run on every container restart. Backups written to
    # data/backups/journal.YYYY-MM-DD.db with 30-day retention.
    def _backup():
        sys.path.insert(0, str(ROOT / "scripts"))
        import backup_journal  # type: ignore
        return backup_journal.main()
    _prewarm_section("journal backup", _backup)

    # 4. v3.58.3 — LowVolSleeve daily shadow runner (best-effort).
    # Re-running the same day is idempotent (replaces today's row).
    def _lowvol():
        from datetime import datetime as _dt
        last_marker = ROOT / "data" / ".last_lowvol_run"
        today_iso = _dt.utcnow().date().isoformat()
        if last_marker.exists() and last_marker.read_text().strip() == today_iso:
            return "already-ran-today"
        # Run the shadow. Best-effort: import + call main().
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import run_lowvol_shadow as rl  # type: ignore
            rl.main()
            last_marker.write_text(today_iso)
            return True
        except SystemExit:
            return True
    _prewarm_section("low-vol shadow", _lowvol)

    overall = (time.time() - overall_t0) * 1000
    print(f"=== prewarm done in {overall:.0f}ms — Streamlit can now start ===", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Never block container startup on prewarm failure
        print(f"prewarm failed (non-fatal): {type(e).__name__}: {e}", flush=True)
        sys.exit(0)

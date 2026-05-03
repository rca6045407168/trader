"""V4 sleeve #1 — residual momentum re-validation spike.

v3.25 PIT validation FAILED for `momentum_top3_residual_v1`:
  Survivor mean Sharpe: +1.53 (claimed beat LIVE)
  PIT mean Sharpe:      +0.03 (vs PIT baseline +0.98 — -0.95 collapse!)
  Verdict: 100% survivor-bias artifact.

v4 plan calls for re-running with current methodology (different universe
composition since v3.25; methodology improvements). If it fails AGAIN,
the variant goes permanent kill-list and v4 moves to sleeve #2 (quality+low-vol).

USAGE:
    python scripts/validate_residual_momentum_v4.py
    # Or in container:
    docker run --rm -v $(pwd)/data:/app/data --entrypoint python \
      trader-test scripts/validate_residual_momentum_v4.py

This script does NOT modify variants.py. It only RUNS the validation and
prints a verdict. The user manually flips status to 'live' if it passes,
at which point override-delay catches the change and the adversarial-review
gate (v3.51) fires before LIVE arming.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    print(f"=== V4 sleeve #1: residual momentum validation ===")
    print(f"started @ {datetime.utcnow().isoformat()} UTC")
    print(f"(scaffold; full backtest harness wiring is the next step)")
    print()

    # Phase 1: load the variant
    try:
        from trader import variants  # noqa: F401  registers
        from trader.ab import _REGISTRY
        v = _REGISTRY.get("momentum_top3_residual_v1")
        if v is None:
            print("❌ variant 'momentum_top3_residual_v1' not in registry")
            return 1
        print(f"✓ variant loaded: {v.variant_id} (status={v.status})")
        print(f"   description: {v.description[:200]}...")
        print()
    except Exception as e:
        print(f"❌ registry load failed: {e}")
        return 1

    # Phase 2: confirm PIT history is the gate it failed before
    if v.params and v.params.get("pit_validated") is False:
        prior_finding = v.params.get("pit_finding", "unknown")
        print(f"⚠️  prior PIT verdict: {prior_finding}")
        print(f"   v4 must re-run survivor + PIT + CPCV with current methodology")
        print(f"   before this variant can be promoted.")
        print()

    # Phase 3: run the variant against current data + check for crashes
    try:
        from trader.universe import DEFAULT_LIQUID_50
        print(f"[scaffold] running variant function on universe of "
              f"{len(DEFAULT_LIQUID_50)} tickers...")
        targets = v.fn(universe=DEFAULT_LIQUID_50, equity=100_000.0,
                        account_state={})
        if targets:
            print(f"✓ variant returned {len(targets)} target(s):")
            for sym, w in sorted(targets.items(), key=lambda kv: -kv[1])[:10]:
                print(f"     {sym:6s} {w*100:5.2f}%")
        else:
            print("⚠️  variant returned empty targets — may be expected if"
                  " residual factor regression failed on current data")
    except Exception as e:
        print(f"❌ variant execution failed: {type(e).__name__}: {e}")
        return 1
    print()

    # Phase 4: outline what's still needed
    print("=== STILL TO BUILD ===")
    print("For a full v4 sleeve-1 promotion gate, the following must run")
    print("and report verdicts. Each is a separate script in scripts/:")
    print()
    print("  1. survivor 5-regime backtest:")
    print("     scripts/run_backtest.py with explicit residual-mom variant")
    print("     Required: Sharpe wins ≥ 4/5 regimes")
    print()
    print("  2. PIT validation:")
    print("     Re-run survivor on universe_pit.py (PIT membership as of date)")
    print("     Required: PIT Sharpe drop < 30% from in-sample")
    print("     This was the v3.25 failure point (-95% drop).")
    print()
    print("  3. CPCV:")
    print("     scripts/cpcv_backtest.py against the variant")
    print("     Required: PBO < 0.5 AND deflated Sharpe > 0")
    print()
    print("  4. Adversarial review (v3.51):")
    print("     from trader.adversarial_review import review_promotion")
    print("     Required: recommendation = APPROVE (not REVIEW or BLOCK)")
    print()
    print("  5. Override-delay (v3.46):")
    print("     SHA change detected; 24h cool-off before LIVE arming")
    print()
    print("  6. Shadow run ≥ 30 days collecting live evidence (per")
    print("     docs/BEHAVIORAL_PRECOMMIT.md)")
    print()
    print("=== END SCAFFOLD ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

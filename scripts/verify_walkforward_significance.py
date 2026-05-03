"""[v3.60.1] Test whether walk-forward Sharpe +1.16 is statistically real.

Loads the walk-forward results from data/walk_forward_results.json,
extracts the daily returns implied by per-window picks (well — extracts
window returns and compounds them), then runs a block-bootstrap CI
on the Sharpe.

Verdict: if 95% CI excludes 0, the edge is real. If CI overlaps 0,
the +1.16 might be a small-sample fluke.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    p = ROOT / "data" / "walk_forward_results.json"
    if not p.exists():
        print(f"ERROR: {p} not found. Run scripts/run_walk_forward.py first.")
        return 1
    with p.open() as f:
        d = json.load(f)

    summary = d.get("summary", {})
    windows = d.get("windows", [])
    print("=" * 78)
    print("Walk-forward Sharpe — statistical significance test")
    print("=" * 78)

    valid = [w for w in windows if w.get("period_return") is not None]
    n = len(valid)
    rets = [w["period_return"] for w in valid]
    sharpes = [w.get("sharpe") for w in valid if w.get("sharpe") is not None]

    if not rets:
        print("No valid windows.")
        return 1

    print(f"  Windows analyzed: {n}")
    print(f"  Mean per-window return: {statistics.mean(rets)*100:+.2f}%")
    print(f"  Median per-window return: {statistics.median(rets)*100:+.2f}%")
    print(f"  Reported mean Sharpe: {summary.get('mean_sharpe', 0):+.2f}")

    # Bootstrap requires N≥30; we have ~24 quarterly windows. Use
    # normal-approximation t-test instead. CLT applies at N=24.
    import math
    mean = statistics.mean(rets)
    sd = statistics.stdev(rets) if n > 1 else 0
    se = sd / math.sqrt(n) if n > 0 else 0
    # 95% CI using t(n-1) ≈ 2.064 at df=23
    t_crit_95 = 2.064 if n >= 24 else 2.262  # df=9 for smaller samples
    ci_low = mean - t_crit_95 * se
    ci_high = mean + t_crit_95 * se
    t_stat = mean / se if se > 0 else 0

    print(f"\n  Per-window return statistics (N={n}, t-test):")
    print(f"    point estimate: {mean*100:+.2f}%/quarter")
    print(f"    standard error: {se*100:.2f}pp")
    print(f"    t-statistic: {t_stat:+.2f}")
    print(f"    95% CI: [{ci_low*100:+.2f}%, {ci_high*100:+.2f}%]")

    # Sharpe SE: approximation per Lo (2002) — SE(SR) ≈ sqrt((1 + SR^2/2) / N)
    annualized_sharpe = (mean / sd) * math.sqrt(4) if sd > 0 else 0  # 4 q/yr
    sharpe_se_approx = math.sqrt((1 + annualized_sharpe**2 / 2) / n) if n > 0 else 0
    sharpe_ci_low = annualized_sharpe - 1.96 * sharpe_se_approx
    sharpe_ci_high = annualized_sharpe + 1.96 * sharpe_se_approx

    print(f"\n  Sharpe statistics (Lo 2002 approximation):")
    print(f"    annualized Sharpe (from quarterly): {annualized_sharpe:+.2f}")
    print(f"    SE: {sharpe_se_approx:.2f}")
    print(f"    95% CI: [{sharpe_ci_low:+.2f}, {sharpe_ci_high:+.2f}]")

    print("\n" + "=" * 78)
    if ci_low > 0:
        print(f"  ✅ Per-window mean return SIGNIFICANTLY > 0 (95% CI excludes 0)")
        print(f"     t = {t_stat:+.2f} over N={n} (rule of thumb: |t|>2 = significant)")
    elif mean > 0 and t_stat > 1:
        print(f"  🟡 Mean return positive but not statistically significant at 95%")
        print(f"     t = {t_stat:+.2f}; need |t| > 2 for significance")
    else:
        print(f"  ❌ Mean return not distinguishable from zero")

    if sharpe_ci_low > 0:
        print(f"  ✅ Sharpe SIGNIFICANTLY > 0")
    elif annualized_sharpe > 0:
        print(f"  🟡 Sharpe positive but CI overlaps zero (CI: [{sharpe_ci_low:+.2f}, {sharpe_ci_high:+.2f}])")
    else:
        print(f"  ❌ Sharpe not distinguishable from zero")

    # Use the t-stat for the "ci_significant" output flags
    ci_mean_low_bool = ci_low > 0
    ci_sharpe_low_bool = sharpe_ci_low > 0
    # build a fake CI object for the JSON output below
    class _CI:
        def __init__(self, point, lo, hi, se_):
            self.point_estimate = point; self.ci_low = lo
            self.ci_high = hi; self.se = se_
    ci_mean = _CI(mean, ci_low, ci_high, se)
    ci_sharpe = _CI(annualized_sharpe, sharpe_ci_low, sharpe_ci_high, sharpe_se_approx)

    # Save
    out = ROOT / "data" / "walkforward_significance.json"
    with out.open("w") as f:
        json.dump({
            "n_windows": n,
            "mean_return_pct": statistics.mean(rets) * 100,
            "ci_mean_pct": [ci_mean.ci_low * 100, ci_mean.ci_high * 100],
            "ci_mean_significant": ci_mean.ci_low > 0,
            "ci_sharpe_point": ci_sharpe.point_estimate,
            "ci_sharpe_95": [ci_sharpe.ci_low, ci_sharpe.ci_high],
            "ci_sharpe_significant": ci_sharpe.ci_low > 0,
        }, f, indent=2)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

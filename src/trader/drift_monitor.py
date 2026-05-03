"""[v3.59.3 — TESTING_PRACTICES Cat 11] Live drift detection.

Three weekly metrics, per TESTING_PRACTICES.md §11:

  1. Signal IC — correlation of factor scores with forward returns.
     Declining IC = signal decay. Alert if 4-week-rolling IC drops
     below 50% of trailing-12-week baseline.

  2. Realized Sharpe over rolling 60d vs backtest expectation.
     Alert if rolling-60d Sharpe drops below 50% of backtest baseline
     for 2 consecutive weeks.

  3. Feature distribution KS-distance vs training period. Alert if
     KS statistic > 0.20 on any feature for 2 consecutive weeks.

Pure functions; results are dicts that the dashboard surfaces and the
notify path emits. No automatic capital action — drift is advisory.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class DriftAlert:
    metric: str
    severity: str               # "info" / "warn" / "high"
    current_value: Optional[float]
    baseline_value: Optional[float]
    threshold_breached: bool
    message: str


def compute_ic(scores: Sequence[float],
                forward_returns: Sequence[float]) -> Optional[float]:
    """Pearson correlation between scores at t and forward returns at t+1.

    Returns None if too few samples or zero-variance.
    """
    if len(scores) != len(forward_returns) or len(scores) < 5:
        return None
    n = len(scores)
    mx = sum(scores) / n
    my = sum(forward_returns) / n
    num = sum((scores[i] - mx) * (forward_returns[i] - my) for i in range(n))
    dx = sum((s - mx) ** 2 for s in scores)
    dy = sum((r - my) ** 2 for r in forward_returns)
    if dx <= 0 or dy <= 0:
        return None
    return num / math.sqrt(dx * dy)


def ic_drift(recent_ic: Sequence[float],
              baseline_ic: Sequence[float],
              decay_threshold: float = 0.5) -> DriftAlert:
    """Detect signal-IC decay.

    recent: last 4 weekly IC observations.
    baseline: trailing 12-week IC observations BEFORE the recent window.
    decay_threshold: alert if recent_avg < baseline_avg * threshold."""
    if not recent_ic or not baseline_ic:
        return DriftAlert("ic_drift", "info", None, None, False,
                           "insufficient history")
    rec_avg = sum(recent_ic) / len(recent_ic)
    base_avg = sum(baseline_ic) / len(baseline_ic)
    if base_avg <= 0:
        return DriftAlert("ic_drift", "info", rec_avg, base_avg, False,
                           "baseline IC zero or negative — no decay test possible")
    breached = rec_avg < base_avg * decay_threshold
    sev = "high" if rec_avg < 0 else ("warn" if breached else "info")
    return DriftAlert(
        metric="ic_drift", severity=sev,
        current_value=rec_avg, baseline_value=base_avg,
        threshold_breached=breached,
        message=(
            f"recent IC {rec_avg:.3f} vs baseline {base_avg:.3f} "
            f"({rec_avg/base_avg*100:.0f}% of baseline)"
            + (" — DECAY" if breached else "")
        ),
    )


def rolling_sharpe_drift(rolling_returns: Sequence[float],
                          backtest_sharpe_baseline: float,
                          decay_threshold: float = 0.5,
                          periods_per_year: int = 252) -> DriftAlert:
    """Detect realized-Sharpe decay vs backtest expectation."""
    if len(rolling_returns) < 30:
        return DriftAlert("rolling_sharpe_drift", "info", None,
                           backtest_sharpe_baseline, False,
                           "insufficient history (<30d)")
    mean = statistics.mean(rolling_returns)
    sd = statistics.stdev(rolling_returns)
    if sd == 0:
        cur = 0.0
    else:
        cur = (mean / sd) * math.sqrt(periods_per_year)
    breached = cur < backtest_sharpe_baseline * decay_threshold
    sev = "high" if cur < 0 else ("warn" if breached else "info")
    return DriftAlert(
        metric="rolling_sharpe_drift", severity=sev,
        current_value=cur, baseline_value=backtest_sharpe_baseline,
        threshold_breached=breached,
        message=(f"rolling Sharpe {cur:.2f} vs backtest baseline "
                  f"{backtest_sharpe_baseline:.2f}"),
    )


def ks_distance(sample_a: Sequence[float],
                 sample_b: Sequence[float]) -> Optional[float]:
    """Kolmogorov-Smirnov 2-sample statistic. No scipy dep — manual.
    Returns max difference between empirical CDFs, in [0, 1]."""
    if len(sample_a) < 5 or len(sample_b) < 5:
        return None
    sa = sorted(sample_a)
    sb = sorted(sample_b)
    all_vals = sorted(set(sa) | set(sb))
    max_diff = 0.0
    for v in all_vals:
        cdf_a = sum(1 for x in sa if x <= v) / len(sa)
        cdf_b = sum(1 for x in sb if x <= v) / len(sb)
        max_diff = max(max_diff, abs(cdf_a - cdf_b))
    return max_diff


def feature_drift(recent_values: Sequence[float],
                    baseline_values: Sequence[float],
                    threshold: float = 0.20,
                    feature_name: str = "feature") -> DriftAlert:
    """KS-distance test for feature distribution change."""
    ks = ks_distance(recent_values, baseline_values)
    if ks is None:
        return DriftAlert(f"feature_drift_{feature_name}", "info",
                           None, None, False,
                           "insufficient samples")
    breached = ks > threshold
    sev = "warn" if breached else "info"
    return DriftAlert(
        metric=f"feature_drift_{feature_name}", severity=sev,
        current_value=ks, baseline_value=threshold,
        threshold_breached=breached,
        message=(f"KS distance {ks:.3f}"
                  + (f" > {threshold} threshold" if breached else "")),
    )


def residual_pnl(expected_daily_pnl: Sequence[float],
                  actual_daily_pnl: Sequence[float]) -> dict:
    """Strategy-level performance reconciliation: did the system earn
    what the backtest model said it would?
    Returns mean residual + sign + interpretation."""
    if len(expected_daily_pnl) != len(actual_daily_pnl):
        return {"ok": False, "error": "length mismatch"}
    if len(expected_daily_pnl) < 10:
        return {"ok": False, "error": "<10 days"}
    diffs = [a - e for a, e in zip(actual_daily_pnl, expected_daily_pnl)]
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0
    interp = "balanced"
    if mean > 0 and sd > 0 and (mean / sd) > 1:
        interp = "actual exceeds expected — investigate (free alpha or accounting bug)"
    elif mean < 0 and sd > 0 and (mean / sd) < -1:
        interp = "actual lags expected — investigate (cost leak or model drift)"
    return {
        "ok": True,
        "mean_residual": mean,
        "stdev_residual": sd,
        "n_days": len(diffs),
        "interpretation": interp,
    }

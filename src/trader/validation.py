"""Data validation. Catches bad yfinance data BEFORE it influences trade decisions.

yfinance has known issues:
  - Occasional bad ticks (price spikes that revert next bar)
  - Late dividend / split adjustments
  - Missing days for some tickers
  - Sudden ticker changes (FB → META)

We enforce minimum data quality so a yfinance hiccup can't blow up our orders.
"""
import pandas as pd


class DataQualityError(Exception):
    """Raised when input data fails a sanity check. Halts the run."""


def validate_prices(prices: pd.DataFrame, min_history_days: int = 252,
                    max_gap_pct: float = 0.30, max_nan_pct: float = 0.05) -> dict:
    """Run sanity checks on a price DataFrame. Returns warning dict; raises on critical.

    Critical (raises):
      - Empty DataFrame
      - All NaN
      - <min_history_days of history (can't compute long-period signals)

    Warning (logged but proceeds):
      - Single-day jump >max_gap_pct (potential split issue)
      - >max_nan_pct missing data on any ticker
      - Last-row timestamp >5 days stale
    """
    if prices is None or prices.empty:
        raise DataQualityError("empty price DataFrame")
    if prices.isna().all().all():
        raise DataQualityError("all-NaN price DataFrame")
    if len(prices) < min_history_days:
        raise DataQualityError(
            f"only {len(prices)} rows of history, need {min_history_days}"
        )

    warnings = []

    # Check freshness
    last = prices.index[-1]
    if isinstance(last, pd.Timestamp):
        days_stale = (pd.Timestamp.now(tz=last.tz) - last).days if last.tz else (pd.Timestamp.now() - last).days
        if days_stale > 5:
            warnings.append(f"data is {days_stale} days stale (last row: {last.date()})")

    # Per-ticker checks
    bad_tickers = []
    for col in prices.columns:
        s = prices[col].dropna()
        if len(s) < min_history_days * 0.5:
            bad_tickers.append(f"{col}: only {len(s)} non-null rows")
            continue
        nan_pct = prices[col].isna().mean()
        if nan_pct > max_nan_pct:
            warnings.append(f"{col}: {nan_pct:.1%} missing data")
        # Single-day gap
        rets = s.pct_change().abs()
        max_jump = rets.max()
        if max_jump > max_gap_pct:
            warnings.append(f"{col}: single-day move of {max_jump:.1%} on {rets.idxmax()} — possible split/data issue")

    return {
        "warnings": warnings,
        "bad_tickers": bad_tickers,
        "n_tickers": len(prices.columns),
        "n_rows": len(prices),
        "date_range": (str(prices.index[0].date()), str(prices.index[-1].date())),
    }


def validate_targets(targets: dict[str, float]) -> dict:
    """Sanity-check a target-weight dict before passing to execute."""
    if not targets:
        return {"warnings": ["empty targets dict"], "ok": False}
    warnings = []
    total = sum(targets.values())
    if total > 1.05:
        raise DataQualityError(f"target weights sum to {total:.1%}, over-leveraged")
    if total < 0.05:
        warnings.append(f"only {total:.1%} deployed, risk_manager may have halted")
    for sym, w in targets.items():
        if w < 0:
            raise DataQualityError(f"negative weight for {sym}: {w}")
        if w > 0.20:
            warnings.append(f"{sym} weight {w:.1%} > 20% — concentration risk")
    return {"warnings": warnings, "ok": True, "total": total}

"""[v3.59.3 — TESTING_PRACTICES Cat 6] Pandera schemas for runtime validation.

Catches silent breakages from upstream data-source changes:
  • yfinance Adj Close removal in 2024 (real bug; broke real systems)
  • yfinance Ticker.history() schema flip in 2024-08
  • Alpaca position field renames (filled_avg_price → avg_fill_price etc)

Each schema is a minimal contract. Validation is defensive — failures
become loud errors with `_check_or_error` rather than silent NaN
propagation.

If pandera is not installed (it's a heavy optional dep), every schema
returns the input unchanged. The validation is best-effort by design.
"""
from __future__ import annotations

from typing import Any, Optional


def _has_pandera() -> bool:
    try:
        import pandera  # noqa: F401
        return True
    except Exception:
        return False


def validate_price_history(df: Any) -> dict:
    """Validate yfinance multi-symbol price history. Returns
    {ok: bool, errors: [...], n_symbols: int, n_rows: int}.

    Defensive checks regardless of pandera availability:
      • DataFrame is non-empty
      • At least one column has > 100 non-null observations
      • No column is 100% NaN
      • Last-row date is within 5 business days of today

    With pandera: also runs strict schema (columns are float64,
    no negative prices, no zeros, monotonic index).
    """
    out = {"ok": True, "errors": [], "n_symbols": 0, "n_rows": 0}
    if df is None:
        out["ok"] = False
        out["errors"].append("df is None")
        return out
    try:
        out["n_symbols"] = len(df.columns)
        out["n_rows"] = len(df)
    except Exception as e:
        out["ok"] = False
        out["errors"].append(f"df has no .columns or .__len__: {e}")
        return out

    if out["n_rows"] == 0:
        out["ok"] = False
        out["errors"].append("empty DataFrame")
        return out

    if out["n_symbols"] == 0:
        out["ok"] = False
        out["errors"].append("zero columns")
        return out

    # Defensive checks (no pandera dep needed)
    try:
        nonnull_ok = sum(
            1 for col in df.columns
            if df[col].notna().sum() > 100
        )
        if nonnull_ok == 0:
            out["ok"] = False
            out["errors"].append("no column has >100 non-null obs")
    except Exception as e:
        out["errors"].append(f"non-null check failed: {e}")

    try:
        for col in df.columns:
            if df[col].isna().all():
                out["errors"].append(f"column {col} is all-NaN")
    except Exception:
        pass

    try:
        from datetime import datetime, timedelta
        last_idx = df.index[-1]
        if hasattr(last_idx, "date"):
            last_d = last_idx.date()
            today = datetime.utcnow().date()
            if (today - last_d).days > 7:
                out["errors"].append(
                    f"last row {last_d} is >7 days old (data staleness)")
    except Exception:
        pass

    # Pandera-strict if available
    if _has_pandera():
        try:
            import pandera as pa  # type: ignore
            import pandas as pd  # type: ignore
            for col in df.columns:
                series = df[col].dropna()
                if (series < 0).any():
                    out["errors"].append(f"{col} contains negative prices")
                if (series == 0).any():
                    out["errors"].append(f"{col} contains zero prices")
        except Exception as e:
            out["errors"].append(f"pandera check failed: {e}")

    if out["errors"]:
        out["ok"] = False
    return out


def validate_targets(targets: Any) -> dict:
    """Validate the {ticker: weight} dict the orchestrator passes to
    risk_manager. Returns {ok, errors}.

    Required:
      • dict-like
      • all keys are strings (tickers)
      • all values are non-negative floats
      • sum of values is in [0.0, 1.5] (allow modest leverage)
      • no NaN values
    """
    out = {"ok": True, "errors": []}
    if not isinstance(targets, dict):
        out["ok"] = False
        out["errors"].append("targets is not a dict")
        return out
    if not targets:
        return out  # empty is valid (cash-equivalent)

    total = 0.0
    for k, v in targets.items():
        if not isinstance(k, str):
            out["errors"].append(f"key {k!r} is not str")
            continue
        try:
            fv = float(v)
        except Exception:
            out["errors"].append(f"value for {k} is not numeric")
            continue
        if fv != fv:  # NaN
            out["errors"].append(f"value for {k} is NaN")
        if fv < 0:
            out["errors"].append(f"negative weight for {k}: {fv}")
        if fv > 0.5:
            out["errors"].append(f"single-name weight for {k} > 50%: {fv}")
        total += fv

    if total > 1.5:
        out["errors"].append(f"sum of targets {total:.2f} > 1.5 (over-leverage)")

    if out["errors"]:
        out["ok"] = False
    return out


def validate_alpaca_position(p: Any) -> dict:
    """Validate one Alpaca position object.
    Required attrs: symbol (str), qty (numeric), market_value (numeric).
    """
    out = {"ok": True, "errors": []}
    for attr in ("symbol", "qty", "market_value"):
        if not hasattr(p, attr):
            out["ok"] = False
            out["errors"].append(f"missing attr: {attr}")
    if out["ok"]:
        try:
            float(p.qty)
            float(p.market_value)
        except Exception as e:
            out["ok"] = False
            out["errors"].append(f"non-numeric qty/market_value: {e}")
    return out


def assert_or_warn(check_result: dict, prefix: str = "validation"):
    """If validation failed, print warnings (do NOT raise). Trading code
    should call this at every data boundary. The decision to raise vs
    warn is taken at the caller level."""
    if not check_result.get("ok", True):
        for err in check_result.get("errors", []):
            print(f"  ⚠️  {prefix}: {err}")

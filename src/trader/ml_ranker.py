"""Machine-learning cross-sectional stock ranker.

Trains Random Forest / Gradient Boosting on cross-sectional features to
predict forward returns. Used to rank stocks at each rebalance and pick
top-3 by predicted return.

Features per stock at each rebalance date:
  - Momentum at multiple horizons (1m, 3m, 6m, 12m return)
  - Realized volatility (60-day annualized)
  - Skewness (60-day)
  - Maximum drawdown over 12 months
  - Distance from 52-week high
  - Sector dummy (one-hot)

Label: forward 1-month return.

Why ML on cross-section:
  - Captures non-linear interactions (e.g., momentum + low-vol works
    differently than momentum alone)
  - Out-of-sample generalization via expanding-window cross-validation
  - Feature importances tell us which signals matter

Risk (HIGH): finance ML is notorious for overfitting. Need:
  - Strict expanding-window train/test (no peeking)
  - CPCV validation (the v3.36 gate)
  - Conservative model (RF with limited depth)

References:
  - Gu, Kelly, Xiu (2020) "Empirical Asset Pricing via Machine Learning"
    JFQA — ML on equity factors, expanding window, ~20% Sharpe lift
  - Lopez de Prado (2018) "Advances in Financial Machine Learning" Ch 7-8
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

warnings.filterwarnings("ignore", category=UserWarning)


def _compute_features(prices: pd.DataFrame, as_of: pd.Timestamp,
                      sectors: dict | None = None) -> pd.DataFrame:
    """Build cross-sectional feature matrix as of date `as_of`.
    Returns DataFrame with columns = features, rows = tickers."""
    sub = prices[prices.index <= as_of].dropna(how="all", axis=1)
    if len(sub) < 252:
        return pd.DataFrame()
    out_rows = []
    for ticker in sub.columns:
        s = sub[ticker].dropna()
        if len(s) < 252:
            continue
        try:
            ret_1m = float(s.iloc[-1] / s.iloc[-21] - 1)
            ret_3m = float(s.iloc[-1] / s.iloc[-63] - 1)
            ret_6m = float(s.iloc[-1] / s.iloc[-126] - 1)
            ret_12m = float(s.iloc[-1] / s.iloc[-252] - 1)
            daily_rets_60 = s.pct_change().dropna().iloc[-60:]
            if len(daily_rets_60) < 30:
                continue
            vol_60d = float(daily_rets_60.std() * np.sqrt(252))
            skew_60d = float(daily_rets_60.skew())
            max_dd_12m = float((s.iloc[-252:] / s.iloc[-252:].cummax() - 1).min())
            high_52w = float(s.iloc[-252:].max())
            dist_from_high = float(s.iloc[-1] / high_52w - 1)
            row = {
                "ticker": ticker,
                "ret_1m": ret_1m,
                "ret_3m": ret_3m,
                "ret_6m": ret_6m,
                "ret_12m": ret_12m,
                "vol_60d": vol_60d,
                "skew_60d": skew_60d if not pd.isna(skew_60d) else 0.0,
                "max_dd_12m": max_dd_12m,
                "dist_from_high": dist_from_high,
            }
            out_rows.append(row)
        except Exception:
            continue
    if not out_rows:
        return pd.DataFrame()
    df = pd.DataFrame(out_rows)
    return df.set_index("ticker")


def train_and_predict(prices: pd.DataFrame, as_of: pd.Timestamp,
                       train_window_years: int = 5, top_n: int = 3) -> list[str]:
    """Train RF on expanding window of cross-sectional features → forward 21d
    return labels. Predict for each ticker as of `as_of`. Return top-N by
    predicted forward return.

    Strict no-lookahead: training data ends 21 days BEFORE as_of (so labels
    are known at training time).
    """
    train_end = as_of - pd.Timedelta(days=21)
    train_start = train_end - pd.Timedelta(days=int(train_window_years * 365.25))

    # Build training set: at each month-end in train window, compute features +
    # labels, stack into one big regression dataset.
    train_dates = pd.date_range(train_start, train_end, freq="ME")
    if len(train_dates) < 12:
        return []

    train_X = []
    train_y = []
    for d in train_dates:
        # Features as-of d, label = 21d forward return
        feats = _compute_features(prices, d)
        if feats.empty:
            continue
        # Compute 21d forward returns for these tickers
        future_idx = prices.index.searchsorted(d + pd.Timedelta(days=21), side="right") - 1
        present_idx = prices.index.searchsorted(d, side="right") - 1
        if future_idx <= present_idx or future_idx >= len(prices):
            continue
        for ticker in feats.index:
            if ticker not in prices.columns:
                continue
            try:
                p_now = float(prices[ticker].iloc[present_idx])
                p_future = float(prices[ticker].iloc[future_idx])
                if p_now <= 0 or pd.isna(p_now) or pd.isna(p_future):
                    continue
                forward_ret = p_future / p_now - 1
                train_X.append(feats.loc[ticker].values.tolist())
                train_y.append(forward_ret)
            except Exception:
                continue

    if len(train_X) < 100:
        return []

    train_X = np.array(train_X)
    train_y = np.array(train_y)

    # Train RF (conservative: limited depth, many trees)
    try:
        model = RandomForestRegressor(
            n_estimators=200,
            max_depth=5,
            min_samples_split=20,
            min_samples_leaf=10,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(train_X, train_y)
    except Exception:
        return []

    # Predict for current as_of features
    today_feats = _compute_features(prices, as_of)
    if today_feats.empty:
        return []
    try:
        predictions = model.predict(today_feats.values)
    except Exception:
        return []
    today_feats["predicted_return"] = predictions
    top = today_feats.sort_values("predicted_return", ascending=False).head(top_n)
    return top.index.tolist()

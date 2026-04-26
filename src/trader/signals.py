"""Signal generators. Pure functions over price/OHLC series.

Design goal: every signal is independently testable, has a single numeric output,
and documents the academic basis for why it might carry alpha.
"""
import numpy as np
import pandas as pd

TRADING_DAYS_PER_MONTH = 21


def momentum_score(prices: pd.Series, lookback_months: int = 6, skip_months: int = 1) -> float:
    """Cross-sectional momentum (Jegadeesh & Titman 1993).

    Returns the trailing N-month return ending S months ago. Skipping the most
    recent month avoids the well-documented short-term reversal effect.
    """
    L = lookback_months * TRADING_DAYS_PER_MONTH
    S = skip_months * TRADING_DAYS_PER_MONTH
    if len(prices) < L + S + 1:
        return float("nan")
    end = prices.iloc[-S - 1] if S > 0 else prices.iloc[-1]
    start = prices.iloc[-(L + S) - 1]
    return float(end / start - 1.0)


def rsi(prices: pd.Series, window: int = 14) -> float:
    """Wilder's RSI. Returns the latest value in [0, 100].

    RSI < 30 = oversold; RSI > 70 = overbought. Classic.
    """
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))


def bollinger_z(prices: pd.Series, window: int = 20) -> float:
    """Z-score of the latest close vs trailing-window mean/std.

    -2 = price is two std-devs below the moving average (lower Bollinger band).
    """
    ma = prices.rolling(window).mean().iloc[-1]
    sd = prices.rolling(window).std().iloc[-1]
    if sd == 0 or pd.isna(sd):
        return 0.0
    return float((prices.iloc[-1] - ma) / sd)


def trend_intact(prices: pd.Series, fast: int = 50, slow: int = 200) -> bool:
    """Long-term trend filter: 50-day MA above 200-day MA.

    Used to avoid catching falling knives in stocks in long-term downtrends.
    """
    if len(prices) < slow:
        return False
    return float(prices.rolling(fast).mean().iloc[-1]) > float(prices.rolling(slow).mean().iloc[-1])


def volume_spike(volume: pd.Series, window: int = 20, threshold: float = 1.5) -> bool:
    """True when latest volume > threshold x trailing-window average.

    Capitulation/accumulation marker — sharp price drops on heavy volume often
    signal the end of a selloff.
    """
    if len(volume) < window:
        return False
    avg = volume.rolling(window).mean().iloc[-1]
    if pd.isna(avg) or avg == 0:
        return False
    return float(volume.iloc[-1]) > threshold * float(avg)


def atr(ohlc: pd.DataFrame, window: int = 14) -> float:
    """Average True Range. Used for position sizing — risk per trade in $ terms."""
    high, low, close = ohlc["High"], ohlc["Low"], ohlc["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    val = tr.rolling(window).mean().iloc[-1]
    return float(val) if not pd.isna(val) else 0.0


def breakout_52w_score(prices: pd.Series, lookback_days: int = 252, proximity: float = 0.02) -> tuple[float, dict]:
    """52-week-high breakout signal.

    Fires when today's close is within `proximity` of the trailing 52-week high
    AND today's close > yesterday's close (confirming follow-through).

    Academic basis: George & Hwang (2004) showed the 52-week high anomaly is
    distinct from price momentum — stocks near 52w highs continue to outperform
    even after controlling for momentum factor exposure.

    Returns (score 0-1, components).
    """
    if len(prices) < lookback_days + 2:
        return 0.0, {"distance_from_52w": float("nan"), "daily_change": float("nan")}
    high_52w = float(prices.iloc[-lookback_days:].max())
    last = float(prices.iloc[-1])
    prev = float(prices.iloc[-2])
    distance = (last - high_52w) / high_52w  # negative = below high, 0 = AT high
    daily_change = (last - prev) / prev
    components = {"distance_from_52w": distance, "daily_change": daily_change, "high_52w": high_52w}

    score = 0.0
    if -proximity <= distance <= 0:
        score += 0.5
    if distance == 0:  # exactly at high
        score += 0.2
    if daily_change > 0:
        score += 0.3
    return score, components


def bottom_catch_score(ohlc: pd.DataFrame) -> tuple[float, dict]:
    """Composite oversold-bounce score in [0, 1]. Returns (score, components).

    Confluence-based: multiple independent oversold signals must agree before
    a trade fires. Each component contributes a fixed weight; total > 0.55 is
    a candidate, > 0.75 is high-conviction.
    """
    close = ohlc["Close"]
    volume = ohlc["Volume"]
    components = {
        "rsi": rsi(close),
        "bollinger_z": bollinger_z(close),
        "trend_intact": trend_intact(close),
        "volume_spike": volume_spike(volume),
    }
    score = 0.0
    if components["rsi"] < 30:
        score += 0.30
    if components["rsi"] < 25:
        score += 0.10
    if components["bollinger_z"] < -2.0:
        score += 0.25
    if components["bollinger_z"] < -2.5:
        score += 0.10
    if components["trend_intact"]:
        score += 0.15
    if components["volume_spike"]:
        score += 0.10
    return score, components

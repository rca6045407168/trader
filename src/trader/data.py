"""yfinance data fetcher with on-disk parquet cache.

We cache aggressively — historical prices don't change, and yfinance is rate-limited.
"""
import hashlib
from datetime import datetime
import pandas as pd
import yfinance as yf

from .config import CACHE_DIR


def _cache_key(prefix: str, *parts) -> str:
    raw = "_".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.md5(raw.encode()).hexdigest()[:12]}.parquet"


def fetch_history(
    tickers: list[str],
    start: str,
    end: str | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Adjusted close prices, indexed by date, columns=tickers."""
    end = end or datetime.now().strftime("%Y-%m-%d")
    cache_path = CACHE_DIR / _cache_key("close", ",".join(sorted(tickers)), start, end)
    if cache_path.exists() and not force_refresh:
        return pd.read_parquet(cache_path)

    df = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True, threads=True)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {tickers[:5]}...")

    if isinstance(df.columns, pd.MultiIndex):
        close = df["Close"]
    else:
        # single-ticker case
        close = df[["Close"]].rename(columns={"Close": tickers[0]})

    close.index = pd.DatetimeIndex(close.index)
    close.to_parquet(cache_path)
    return close


def fetch_ohlcv(ticker: str, start: str, end: str | None = None, force_refresh: bool = False) -> pd.DataFrame:
    """OHLCV for a single ticker. Needed for ATR, volume signals, bottom-catch."""
    end = end or datetime.now().strftime("%Y-%m-%d")
    cache_path = CACHE_DIR / _cache_key("ohlcv", ticker, start, end)
    if cache_path.exists() and not force_refresh:
        return pd.read_parquet(cache_path)

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.DatetimeIndex(df.index)
    df.to_parquet(cache_path)
    return df

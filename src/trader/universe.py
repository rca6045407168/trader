"""Tradable universes. Wikipedia for full S&P 500 + a hand-picked liquid 50 fallback."""
from functools import lru_cache
import pandas as pd

# Top ~50 most liquid US large-caps. Used as the default universe and as the
# offline fallback when Wikipedia is unreachable. Hand-curated for stable tickers.
DEFAULT_LIQUID_50 = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "BRK-B", "JPM", "V",
    "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS", "BAC", "XOM", "PFE",
    "KO", "MRK", "PEP", "CSCO", "ABT", "TMO", "MCD", "COST", "AVGO", "CRM",
    "NFLX", "ADBE", "ACN", "NKE", "QCOM", "T", "DHR", "TXN", "LIN", "VZ",
    "AMD", "INTC", "ORCL", "WFC", "MS", "GS", "BLK", "CAT", "BA", "HON",
]


@lru_cache(maxsize=1)
def sp500_tickers() -> list[str]:
    """Current S&P 500 from Wikipedia. Falls back to DEFAULT_LIQUID_50."""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )
        symbols = tables[0]["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        return symbols
    except Exception as e:
        print(f"[universe] Wikipedia fetch failed ({e}); using DEFAULT_LIQUID_50")
        return DEFAULT_LIQUID_50

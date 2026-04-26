"""GICS sector mapping for the liquid-50 universe.

Used for sector-neutral selection and concentration limits. Hand-curated; in
production this would come from a real fundamentals data provider.
"""

SECTORS = {
    # Technology
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AVGO": "Tech", "AMD": "Tech",
    "INTC": "Tech", "ORCL": "Tech", "CSCO": "Tech", "ADBE": "Tech", "CRM": "Tech",
    "ACN": "Tech", "QCOM": "Tech", "TXN": "Tech",
    # Communication
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication", "T": "Communication", "VZ": "Communication",
    # Consumer Discretionary
    "AMZN": "ConsumerDisc", "TSLA": "ConsumerDisc", "HD": "ConsumerDisc",
    "MCD": "ConsumerDisc", "NKE": "ConsumerDisc",
    # Consumer Staples
    "WMT": "ConsumerStap", "PG": "ConsumerStap", "KO": "ConsumerStap",
    "PEP": "ConsumerStap", "COST": "ConsumerStap",
    # Healthcare
    "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare",
    "ABT": "Healthcare", "TMO": "Healthcare", "DHR": "Healthcare",
    # Financials
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "WFC": "Financials", "MS": "Financials", "GS": "Financials", "BLK": "Financials",
    "BRK-B": "Financials",
    # Energy
    "XOM": "Energy",
    # Industrials
    "CAT": "Industrials", "BA": "Industrials", "HON": "Industrials",
    # Materials
    "LIN": "Materials",
}


def get_sector(ticker: str) -> str:
    return SECTORS.get(ticker, "Other")


def sector_count(tickers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tickers:
        s = get_sector(t)
        out[s] = out.get(s, 0) + 1
    return out

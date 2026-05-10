"""GICS sector mapping for the liquid-50 universe.

Used for sector-neutral selection and concentration limits. Hand-curated; in
production this would come from a real fundamentals data provider.
"""

SECTORS = {
    # === Technology ===
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AVGO": "Tech", "AMD": "Tech",
    "INTC": "Tech", "ORCL": "Tech", "CSCO": "Tech", "ADBE": "Tech", "CRM": "Tech",
    "ACN": "Tech", "QCOM": "Tech", "TXN": "Tech",
    # v6.0.x universe expansion — adding cap-weighted top names per sector
    "IBM": "Tech", "AMAT": "Tech", "MU": "Tech", "ADI": "Tech", "LRCX": "Tech",
    "KLAC": "Tech", "PANW": "Tech", "CDNS": "Tech", "SNPS": "Tech", "INTU": "Tech",
    "NOW": "Tech", "ANET": "Tech", "FTNT": "Tech", "MRVL": "Tech",
    # === Communication ===
    "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
    "DIS": "Communication", "T": "Communication", "VZ": "Communication",
    "CMCSA": "Communication", "TMUS": "Communication", "GOOG": "Communication",
    # === Consumer Discretionary ===
    "AMZN": "ConsumerDisc", "TSLA": "ConsumerDisc", "HD": "ConsumerDisc",
    "MCD": "ConsumerDisc", "NKE": "ConsumerDisc",
    "LOW": "ConsumerDisc", "SBUX": "ConsumerDisc", "TJX": "ConsumerDisc",
    "BKNG": "ConsumerDisc", "F": "ConsumerDisc", "GM": "ConsumerDisc",
    "ABNB": "ConsumerDisc", "CMG": "ConsumerDisc", "MAR": "ConsumerDisc",
    # === Consumer Staples ===
    "WMT": "ConsumerStap", "PG": "ConsumerStap", "KO": "ConsumerStap",
    "PEP": "ConsumerStap", "COST": "ConsumerStap",
    "MDLZ": "ConsumerStap", "CL": "ConsumerStap", "MO": "ConsumerStap",
    "PM": "ConsumerStap", "TGT": "ConsumerStap", "KMB": "ConsumerStap",
    # === Healthcare ===
    "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare",
    "ABT": "Healthcare", "TMO": "Healthcare", "DHR": "Healthcare",
    "LLY": "Healthcare", "ABBV": "Healthcare", "BMY": "Healthcare", "GILD": "Healthcare",
    "AMGN": "Healthcare", "VRTX": "Healthcare", "REGN": "Healthcare",
    "ISRG": "Healthcare", "ELV": "Healthcare", "CVS": "Healthcare", "MDT": "Healthcare",
    # === Financials ===
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "WFC": "Financials", "MS": "Financials", "GS": "Financials", "BLK": "Financials",
    "BRK-B": "Financials",
    "C": "Financials", "AXP": "Financials", "SCHW": "Financials", "USB": "Financials",
    "PNC": "Financials", "TFC": "Financials", "COF": "Financials", "PYPL": "Financials",
    "SPGI": "Financials", "ICE": "Financials", "CME": "Financials",
    # === Energy ===
    "XOM": "Energy",
    "CVX": "Energy", "COP": "Energy", "EOG": "Energy", "SLB": "Energy",
    "PSX": "Energy", "MPC": "Energy", "VLO": "Energy", "OXY": "Energy",
    # === Industrials ===
    "CAT": "Industrials", "BA": "Industrials", "HON": "Industrials",
    "GE": "Industrials", "UNP": "Industrials", "RTX": "Industrials", "DE": "Industrials",
    "UPS": "Industrials", "LMT": "Industrials", "ETN": "Industrials", "MMM": "Industrials",
    "ADP": "Industrials", "CSX": "Industrials", "NSC": "Industrials",
    # === Materials ===
    "LIN": "Materials",
    "APD": "Materials", "SHW": "Materials", "FCX": "Materials", "ECL": "Materials",
    "NEM": "Materials",
    # === Utilities (new sector for v6 — defensive) ===
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities", "SRE": "Utilities",
    "AEP": "Utilities",
    # === Real Estate (new sector for v6 — yield/defensive) ===
    "PLD": "RealEstate", "AMT": "RealEstate", "EQIX": "RealEstate",
    "CCI": "RealEstate", "SPG": "RealEstate",
}


def get_sector(ticker: str) -> str:
    return SECTORS.get(ticker, "Other")


def sector_count(tickers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tickers:
        s = get_sector(t)
        out[s] = out.get(s, 0) + 1
    return out

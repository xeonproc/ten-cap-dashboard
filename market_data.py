"""Market data: a bulk snapshot of every NYSE / NASDAQ / AMEX listing, and exchange rates."""

import re
import sys
from datetime import datetime, timezone

import requests

SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
FX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{currency}USD=X"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Warrants, units, preferreds and debt trade under their own symbols; keep ordinary equity.
NOT_COMMON_STOCK = re.compile(r"warrant|\bunits?\b|\brights?\b|preferred|notes|debenture", re.IGNORECASE)
# Foreign companies usually list through depositary shares (ADS / ADR).
DEPOSITARY = re.compile(r"deposit[ao]ry|\bADSs?\b|\bADRs?\b", re.IGNORECASE)
US_COUNTRIES = {"", "United States"}


def _number(text):
    try:
        return float(str(text).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def fetch_listings():
    """Return one dict per listed equity: ticker, name, price, market cap, sector, industry,
    country, and `foreign` (domiciled outside the US, or listed as depositary shares)."""
    resp = requests.get(
        SCREENER_URL, headers=HEADERS, params={"tableonly": "true", "download": "true"}, timeout=60
    )
    resp.raise_for_status()
    listings = []
    for row in resp.json()["data"]["rows"]:
        symbol = row["symbol"].strip()
        if "^" in symbol or NOT_COMMON_STOCK.search(row["name"]):
            continue
        country = (row.get("country") or "").strip()
        listings.append(
            {
                "ticker": symbol.replace("/", "-"),  # BRK/B -> BRK-B, matching SEC's format
                "name": row["name"],
                "price": _number(row["lastsale"]),
                "market_cap": _number(row["marketCap"]),
                "sector": row.get("sector") or None,
                "industry": row.get("industry") or None,
                "country": country or None,
                "foreign": country not in US_COUNTRIES or bool(DEPOSITARY.search(row["name"])),
            }
        )
    return listings


def fetch_exchange_rates(currency):
    """Monthly US-dollar value of one unit of `currency`, as {"YYYY-MM": rate}, going back
    about 15 years. Returns {} if the rate cannot be fetched."""
    try:
        resp = requests.get(
            FX_URL.format(currency=currency),
            headers=HEADERS,
            params={"range": "15y", "interval": "1mo"},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        rates = {}
        for stamp, close in zip(result["timestamp"], closes):
            if close:
                month = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m")
                rates[month] = close
        return rates
    except Exception as exc:
        print(f"  ! exchange rate lookup failed for {currency}: {exc}", file=sys.stderr)
        return {}

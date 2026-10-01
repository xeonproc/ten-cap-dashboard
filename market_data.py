"""Bulk market snapshot: one request returns every NYSE / NASDAQ / AMEX listing."""

import re

import requests

SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Warrants, units, preferreds, ADRs and debt trade under their own symbols; keep common stock only.
NOT_COMMON_STOCK = re.compile(
    r"warrant|\bunits?\b|\brights?\b|preferred|depositary|notes|debenture", re.IGNORECASE
)


def _number(text):
    try:
        return float(str(text).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def fetch_listings():
    """Return [{ticker, name, price, market_cap, sector, industry}] for listed common stocks."""
    resp = requests.get(
        SCREENER_URL, headers=HEADERS, params={"tableonly": "true", "download": "true"}, timeout=60
    )
    resp.raise_for_status()
    listings = []
    for row in resp.json()["data"]["rows"]:
        symbol = row["symbol"].strip()
        if "^" in symbol or NOT_COMMON_STOCK.search(row["name"]):
            continue
        listings.append(
            {
                "ticker": symbol.replace("/", "-"),  # BRK/B -> BRK-B, matching SEC's format
                "name": row["name"],
                "price": _number(row["lastsale"]),
                "market_cap": _number(row["marketCap"]),
                "sector": row.get("sector") or None,
                "industry": row.get("industry") or None,
            }
        )
    return listings

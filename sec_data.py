"""SEC EDGAR XBRL "company facts" client and fact extractors."""

import os
import time
from datetime import date

import requests

# SEC requires a descriptive User-Agent with contact info. Override via env in CI.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "FinancialApp admin@myproject.com")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC fair-access limit is 10 requests/second; stay well under it.
REQUEST_INTERVAL = 0.15

_session = requests.Session()
_session.headers.update(HEADERS)


def _get_json(url, retries=4):
    for attempt in range(retries):
        time.sleep(REQUEST_INTERVAL)
        resp = _session.get(url, timeout=30)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()


def load_cik_map():
    """Return {TICKER: {"cik": int, "name": str}} for every SEC registrant."""
    raw = _get_json(TICKER_MAP_URL)
    return {
        row["ticker"].upper(): {"cik": int(row["cik_str"]), "name": row["title"]}
        for row in raw.values()
    }


def fetch_company_facts(cik):
    return _get_json(FACTS_URL.format(cik=int(cik)))


def _facts(facts, tag, unit):
    return facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])


def _latest_filed_per_end(rows):
    """Collapse duplicate facts for the same period end, keeping the most recently filed
    (later filings carry restated / split-adjusted figures)."""
    best = {}
    for row in rows:
        end = row["end"]
        if end not in best or row.get("filed", "") > best[end].get("filed", ""):
            best[end] = row
    return best


def annual_series(facts, tag, unit, years=5):
    """Last `years` full-fiscal-year values of a duration fact, oldest first."""
    annual = []
    for row in _facts(facts, tag, unit):
        if "start" not in row or row.get("fp") != "FY":
            continue
        days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
        if 350 <= days <= 380:  # covers 52/53-week fiscal years, excludes quarters
            annual.append(row)
    best = _latest_filed_per_end(annual)
    ends = sorted(best)[-years:]
    return [{"fiscal_year": int(end[:4]), "end": end, "value": best[end]["val"]} for end in ends]


def _instants(facts, tag, unit="USD"):
    """{period_end: value} for a point-in-time (balance sheet) fact."""
    rows = [r for r in _facts(facts, tag, unit) if "start" not in r]
    return {end: row["val"] for end, row in _latest_filed_per_end(rows).items()}


def eps_history(facts, years=5):
    return annual_series(facts, "EarningsPerShareDiluted", "USD/shares", years)


def balance_sheet(facts):
    """Latest balance sheet: assets, liabilities, goodwill, intangibles (all as of one date)."""
    assets = _instants(facts, "Assets")
    if not assets:
        return None
    end = max(assets)

    liabilities = _instants(facts, "Liabilities").get(end)
    if liabilities is None:
        # Many filers never tag total Liabilities; derive it from the accounting identity.
        total = _instants(facts, "LiabilitiesAndStockholdersEquity").get(end)
        equity = _instants(
            facts, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        ).get(end)
        if equity is None:
            equity = _instants(facts, "StockholdersEquity").get(end)
        if total is not None and equity is not None:
            liabilities = total - equity

    # A tag missing at `end` means the company reports none of that item.
    goodwill = _instants(facts, "Goodwill").get(end, 0)
    # "us-gaap:Intangibles" is not a real taxonomy element; these are the tags filers use.
    intangibles = _instants(facts, "IntangibleAssetsNetExcludingGoodwill").get(end)
    if intangibles is None:
        intangibles = _instants(facts, "FiniteLivedIntangibleAssetsNet").get(end, 0) + _instants(
            facts, "IndefiniteLivedIntangibleAssetsExcludingGoodwill"
        ).get(end, 0)

    return {
        "date": end,
        "assets": assets[end],
        "liabilities": liabilities,
        "goodwill": goodwill,
        "intangibles": intangibles,
    }


def diluted_shares(facts):
    """Most recently reported weighted-average diluted share count."""
    rows = _facts(facts, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares")
    if not rows:
        return None
    return max(rows, key=lambda r: (r["end"], r.get("filed", "")))["val"]

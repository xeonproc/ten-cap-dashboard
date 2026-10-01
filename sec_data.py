"""SEC EDGAR client: registrant universe, bulk XBRL "frames", and per-company fact extractors."""

import os
import time
from datetime import date, timedelta

import requests

# SEC requires a descriptive User-Agent with contact info. Override via env in CI.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "FinancialApp admin@myproject.com")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Same registry as company_tickers.json, plus each ticker's exchange.
UNIVERSE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD-per-shares/CY{year}.json"

# Diluted is preferred; a minority of filers only tag basic EPS.
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
EPS_UNIT = "USD/shares"

# SEC fair-access limit is 10 requests/second; stay under it.
REQUEST_INTERVAL = 0.12

_session = requests.Session()
_session.headers.update(HEADERS)


def _get_json(url, retries=4, missing_ok=False):
    for attempt in range(retries):
        time.sleep(REQUEST_INTERVAL)
        resp = _session.get(url, timeout=60)
        if resp.status_code == 404 and missing_ok:
            return None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()


def load_universe():
    """Return {TICKER: {"cik": int, "name": str, "exchange": str | None}} for every SEC registrant."""
    raw = _get_json(UNIVERSE_URL)
    fields = raw["fields"]
    universe = {}
    for values in raw["data"]:
        row = dict(zip(fields, values))
        universe[row["ticker"].upper()] = {
            "cik": int(row["cik"]),
            "name": row["name"],
            "exchange": row["exchange"],
        }
    return universe


def latest_annual_eps_by_cik(lookback_years=3):
    """Most recent full-year EPS for every filer, from a handful of bulk "frames" requests.

    Returns {cik: {"end": "YYYY-MM-DD", "eps": float}}.
    """
    latest = {}
    this_year = date.today().year
    for tag in reversed(EPS_TAGS):  # diluted last so it wins ties
        for year in range(this_year - lookback_years + 1, this_year + 1):
            frame = _get_json(FRAME_URL.format(tag=tag, year=year), missing_ok=True)
            for row in (frame or {}).get("data", []):
                current = latest.get(row["cik"])
                if current is None or row["end"] >= current["end"]:
                    latest[row["cik"]] = {"end": row["end"], "eps": row["val"]}
    return latest


def fetch_company_facts(cik):
    return _get_json(FACTS_URL.format(cik=int(cik)))


def _facts(facts, tag, unit):
    return facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])


def _days(row):
    return (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days


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
    annual = [
        row
        for row in _facts(facts, tag, unit)
        # 350-380 days covers 52/53-week fiscal years and excludes quarters
        if "start" in row and row.get("fp") == "FY" and 350 <= _days(row) <= 380
    ]
    best = _latest_filed_per_end(annual)
    ends = sorted(best)[-years:]
    return [{"fiscal_year": int(end[:4]), "end": end, "value": best[end]["val"]} for end in ends]


def _instants(facts, tag, unit="USD"):
    """{period_end: value} for a point-in-time (balance sheet) fact."""
    rows = [r for r in _facts(facts, tag, unit) if "start" not in r]
    return {end: row["val"] for end, row in _latest_filed_per_end(rows).items()}


def eps_tag(facts):
    """The EPS tag this company reports annually, or None."""
    for tag in EPS_TAGS:
        if annual_series(facts, tag, EPS_UNIT, 1):
            return tag
    return None


def eps_history(facts, tag, years=5):
    return annual_series(facts, tag, EPS_UNIT, years)


def ttm_eps(facts, tag):
    """Trailing-twelve-month EPS: last fiscal year + current year-to-date - prior-year YTD.

    Falls back to the last fiscal year when no later quarterly report exists.
    """
    annual = annual_series(facts, tag, EPS_UNIT, 1)
    if not annual:
        return None
    fy = annual[-1]

    # Year-to-date periods of the fiscal year in progress (they start after the last FY ended).
    partial = [
        row
        for row in _facts(facts, tag, EPS_UNIT)
        if "start" in row and row["start"] > fy["end"] and _days(row) < 350
    ]
    if not partial:
        return fy["value"]
    latest_end = max(row["end"] for row in partial)
    # Of the periods ending on the latest date, the longest one is the year-to-date figure.
    ytd = max((r for r in partial if r["end"] == latest_end), key=lambda r: (_days(r), r.get("filed", "")))

    # The same YTD period one year earlier, as reported in the comparative columns.
    target_end = date.fromisoformat(ytd["end"]) - timedelta(days=365)
    prior = [
        row
        for row in _facts(facts, tag, EPS_UNIT)
        if "start" in row
        and abs((date.fromisoformat(row["end"]) - target_end).days) <= 10
        and abs(_days(row) - _days(ytd)) <= 10
    ]
    if not prior:
        return fy["value"]
    prior_ytd = max(prior, key=lambda r: r.get("filed", ""))
    return fy["value"] + ytd["val"] - prior_ytd["val"]


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

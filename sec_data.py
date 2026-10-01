"""SEC EDGAR client: registrant universe, bulk XBRL "frames", and per-company fact extractors.

Extractors take a company's `facts` as saved by fetch_data.py: the SEC "company facts"
structure plus two extra keys, "taxonomy" (us-gaap or ifrs-full) and "currency" (the
currency the company reports in). Tags are looked up by meaning via sec_tags.TAXONOMIES, and
the unit "USD" below stands for "the company's reporting currency".
"""

import os
import time
from datetime import date, timedelta

import requests

from sec_tags import KEEP_TAGS, TAXONOMIES

# SEC requires a descriptive User-Agent with contact info. Override via env in CI.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "FinancialApp admin@myproject.com")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Same registry as company_tickers.json, plus each ticker's exchange.
UNIVERSE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD-per-shares/CY{year}.json"

EPS_UNIT = "USD/shares"

# SEC fair-access limit is 10 requests/second; stay under it.
REQUEST_INTERVAL = 0.12

_session = requests.Session()
_session.headers.update(HEADERS)


# ------------------------------------------------------------------ network


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
    """Most recent full-year EPS for every US-GAAP, US-dollar filer, from a handful of bulk
    "frames" requests. Returns {cik: {"end": "YYYY-MM-DD", "eps": float}}.
    """
    latest = {}
    this_year = date.today().year
    for tag in reversed(TAXONOMIES["us-gaap"]["eps"]):  # diluted last so it wins ties
        for year in range(this_year - lookback_years + 1, this_year + 1):
            frame = _get_json(FRAME_URL.format(tag=tag, year=year), missing_ok=True)
            for row in (frame or {}).get("data", []):
                current = latest.get(row["cik"])
                if current is None or row["end"] >= current["end"]:
                    latest[row["cik"]] = {"end": row["end"], "eps": row["val"]}
    return latest


def fetch_company_facts(cik):
    return _get_json(FACTS_URL.format(cik=int(cik)))


# ------------------------------------------------------------------ reading facts


def _days(row):
    return (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days


def _is_fiscal_year(row):
    # 350-380 days covers 52/53-week fiscal years and excludes quarters
    return "start" in row and row.get("fp") == "FY" and 350 <= _days(row) <= 380


def detect_context(raw_facts):
    """Which accounting standard and currency a company reports in.

    Many foreign filers tag figures twice: in their own currency and as a "convenience
    translation" into dollars for the latest years only. The currency with the most recent,
    and then the most, annual profit figures is the real one. Returns (taxonomy, currency)
    or None when the filings contain no annual profit figure.
    """
    best = None
    for taxonomy, groups in TAXONOMIES.items():
        gaap = raw_facts.get("facts", {}).get(taxonomy, {})
        for tag in groups["net_income"]:
            for unit, rows in gaap.get(tag, {}).get("units", {}).items():
                annual = [row for row in rows if _is_fiscal_year(row)]
                if not annual or len(unit) != 3:
                    continue
                score = (max(row["end"] for row in annual), len(annual))
                if best is None or score > best[0]:
                    best = (score, taxonomy, unit)
    return (best[1], best[2]) if best else None


def files_quarterly(facts, since):
    """True if the company has filed quarterly EPS figures for a period ending after `since`
    (an ISO date). US-style filers (10-K / 10-Q) do; foreign-style filers (20-F / 40-F) do not."""
    for tag in tags(facts, "eps"):
        for row in _facts(facts, tag, EPS_UNIT):
            if row.get("fp") in ("Q1", "Q2", "Q3") and row["end"] >= since:
                return True
    return False


def tags(facts, group):
    """The tags for a concept, in this company's accounting standard."""
    return TAXONOMIES[facts.get("taxonomy") or "us-gaap"][group]


def _facts(facts, tag, unit):
    taxonomy = facts.get("taxonomy") or "us-gaap"
    if tag not in KEEP_TAGS[taxonomy]:
        raise KeyError(f"{tag} is not listed in sec_tags.py, so it is not in the raw-data cache")
    currency = facts.get("currency") or "USD"
    unit = {"USD": currency, "USD/shares": f"{currency}/shares"}.get(unit, unit)
    return facts.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit, [])


def _latest_filed_per_end(rows):
    """Collapse duplicate facts for the same period end, keeping the most recently filed
    (later filings carry restated / split-adjusted figures)."""
    best = {}
    for row in rows:
        end = row["end"]
        if end not in best or row.get("filed", "") > best[end].get("filed", ""):
            best[end] = row
    return best


def _instants(facts, tag, unit="USD"):
    """{period_end: value} for a point-in-time (balance sheet) fact."""
    rows = [r for r in _facts(facts, tag, unit) if "start" not in r]
    return {end: row["val"] for end, row in _latest_filed_per_end(rows).items()}


def _first_at(facts, end, group):
    """Value at balance-sheet date `end` from the first tag in `group` that reports one."""
    for tag in tags(facts, group):
        value = _instants(facts, tag).get(end)
        if value is not None:
            return value
    return None


def _shares_tag(facts):
    """The share-count tag this company uses."""
    candidates = tags(facts, "shares")
    for tag in candidates:
        if _facts(facts, tag, "shares"):
            return tag
    return candidates[0]


# ------------------------------------------------------------------ stock splits


def _by_period(rows):
    """{(start, end): [rows sorted by filing date]} for duration facts."""
    periods = {}
    for row in rows:
        if "start" in row and row.get("filed"):
            periods.setdefault((row["start"], row["end"]), []).append(row)
    for group in periods.values():
        group.sort(key=lambda r: r["filed"])
    return periods


def _merge_split_candidates(candidates):
    """Combine sightings of the same split. Each candidate is [before, after, factor, count]:
    the split happened between filing dates `before` and `after`."""
    events = []
    for cand in sorted(candidates):
        for event in events:
            if cand[0] < event[1] and event[0] < cand[1] and 0.9 < cand[2] / event[2] < 1.1:
                event[0] = max(event[0], cand[0])
                event[1] = min(event[1], cand[1])
                event[2] = (event[2] * event[3] + cand[2]) / (event[3] + 1)
                event[3] += 1
                break
        else:
            events.append(list(cand))
    return events


def _is_clean_split_ratio(ratio):
    """True for ratios that look like a real split (2, 3, 1.5, 1/10, ...) rather than an
    accounting restatement or a filing error."""
    if not 0.02 <= ratio <= 50:  # beyond this it is usually a units error (cents vs dollars)
        return False
    multiple = ratio if ratio >= 1 else 1 / ratio
    nearest = round(multiple * 2) / 2 if multiple < 3 else round(multiple)
    return nearest >= 1.5 and abs(multiple - nearest) / nearest <= 0.015


def detect_splits(facts, tag):
    """Find stock splits from the filings themselves.

    After a split, later filings restate earlier periods: the same period shows a new share
    count and EPS. Comparing a period's value across filings reveals the split factor and
    when it happened. Returns [(first_post_split_filing_date, factor)]; factor > 1 is a
    forward split, < 1 a reverse split.
    """
    shares = _by_period(_facts(facts, _shares_tag(facts), "shares"))
    strong, weak = [], []
    for period, rows in _by_period(_facts(facts, tag, EPS_UNIT)).items():
        shares_at = {r["filed"]: r["val"] for r in shares.get(period, [])}
        for old, new in zip(rows, rows[1:]):
            if old["filed"] == new["filed"] or not old["val"] or not new["val"]:
                continue
            eps_ratio = old["val"] / new["val"]
            if eps_ratio <= 0:
                continue
            old_shares, new_shares = shares_at.get(old["filed"]), shares_at.get(new["filed"])
            if old_shares and new_shares and old_shares > 0 and new_shares > 0:
                # Restated share count gives the exact factor; EPS must have moved to match.
                factor = new_shares / old_shares
                if (
                    (0.01 <= factor <= 0.87 or 1.15 <= factor <= 100)
                    and 0.7 * factor <= eps_ratio <= 1.3 * factor
                ):
                    strong.append([old["filed"], new["filed"], factor, 1])
            elif _is_clean_split_ratio(eps_ratio):
                weak.append([old["filed"], new["filed"], eps_ratio, 1])

    events = _merge_split_candidates(strong)
    # Without share counts, only trust an EPS jump seen consistently across several periods
    # (an accounting restatement would not rescale every period by the same factor).
    for event in _merge_split_candidates(weak):
        overlaps = any(event[0] < e[1] and e[0] < event[1] for e in events)
        if event[3] >= 3 and not overlaps:
            events.append(event)
    return sorted((after, factor) for _, after, factor, _ in events)


def _split_adjusted(row, splits, share_count=False):
    """A per-share value (or, with share_count, a number of shares) restated to today's
    share count."""
    value = row["val"]
    for first_post_split_filing, factor in splits:
        if row.get("filed", "") < first_post_split_filing:
            value = value * factor if share_count else value / factor
    return value


# ------------------------------------------------------------------ income and cash flow


def annual_series(facts, tag, unit, years=5, splits=(), share_count=False):
    """Last `years` full-fiscal-year values of a duration fact, oldest first."""
    best = _latest_filed_per_end([row for row in _facts(facts, tag, unit) if _is_fiscal_year(row)])
    ends = sorted(best)[-years:]
    return [
        {
            "fiscal_year": int(end[:4]),
            "end": end,
            "value": _split_adjusted(best[end], splits, share_count),
        }
        for end in ends
    ]


def annual_by_end(facts, group, years=10):
    """{fiscal_year_end: value} in the reporting currency, taking each year from the first
    tag in `group` that reports it (companies switch between equivalent tags over time)."""
    merged = {}
    for tag in tags(facts, group):
        for row in annual_series(facts, tag, "USD", years + 2):
            merged.setdefault(row["end"], row["value"])
    return merged


def eps_tag(facts):
    """The EPS tag this company reports annually, or None."""
    for tag in tags(facts, "eps"):
        if annual_series(facts, tag, EPS_UNIT, 1):
            return tag
    return None


def annual_history(facts, tag, splits, years=10):
    """Per-fiscal-year EPS, free cash flow per share, revenue, operating cash flow and capex.

    Free cash flow is operating cash flow minus all capital spending (maintenance capex is
    not disclosed separately, so this is the conservative reading of owner earnings). It is
    left empty for any year in which the company did not report capex.
    """
    cash_flow = annual_by_end(facts, "operating_cash_flow", years)
    capex = annual_by_end(facts, "capex", years)
    revenue = annual_by_end(facts, "revenue", years)
    shares = {
        row["end"]: row["value"]
        for row in annual_series(facts, _shares_tag(facts), "shares", years + 2, splits, share_count=True)
    }
    history = []
    for row in annual_series(facts, tag, EPS_UNIT, years, splits):
        end = row["end"]
        ocf, spent = cash_flow.get(end), capex.get(end)
        fcf_ps = None
        if ocf is not None and spent is not None and shares.get(end):
            fcf_ps = (ocf - spent) / shares[end]
        history.append(
            {
                "fiscal_year": row["fiscal_year"],
                "end": end,
                "eps": row["value"],
                "fcf_ps": fcf_ps,
                "revenue": revenue.get(end),
                "ocf": ocf,
                "capex": spent,
            }
        )
    return history


def ttm_value(facts, tag, unit="USD", splits=()):
    """Trailing-twelve-month value of a duration fact: last fiscal year + current
    year-to-date - prior-year YTD.

    Falls back to the last fiscal year when no later quarterly report exists.
    """
    annual = annual_series(facts, tag, unit, 1, splits)
    if not annual:
        return None
    fy = annual[-1]

    # Year-to-date periods of the fiscal year in progress (they start after the last FY ended).
    partial = [
        row
        for row in _facts(facts, tag, unit)
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
        for row in _facts(facts, tag, unit)
        if "start" in row
        and abs((date.fromisoformat(row["end"]) - target_end).days) <= 10
        and abs(_days(row) - _days(ytd)) <= 10
    ]
    if not prior:
        return fy["value"]
    prior_ytd = max(prior, key=lambda r: r.get("filed", ""))
    return fy["value"] + _split_adjusted(ytd, splits) - _split_adjusted(prior_ytd, splits)


def ttm_eps(facts, tag, splits=()):
    return ttm_value(facts, tag, EPS_UNIT, splits)


def _ttm_of_freshest(facts, group):
    """TTM value of whichever tag in `group` was reported for the most recent fiscal year."""
    latest = {}
    for tag in tags(facts, group):
        annual = annual_series(facts, tag, "USD", 1)
        if annual:
            latest[tag] = annual[-1]["end"]
    if not latest:
        return None
    return ttm_value(facts, max(latest, key=latest.get))


# ------------------------------------------------------------------ balance sheet


def total_debt(facts, end):
    """Approximate interest-bearing debt at `end`. Filers tag debt inconsistently, so this
    tries the common combinations; a company that tags none of them is treated as debt-free."""
    combined = _first_at(facts, end, "debt_total")
    if combined is not None:
        return combined
    long_term = _first_at(facts, end, "debt_long_total")
    if long_term is None:
        long_term = (_first_at(facts, end, "debt_long_noncurrent") or 0) + (
            _first_at(facts, end, "debt_long_current") or 0
        )
    return long_term + (_first_at(facts, end, "debt_short") or 0)


def quality_inputs(facts, end):
    """Raw figures behind the quality ratios, as of balance-sheet date `end`."""
    return {
        "equity": _first_at(facts, end, "equity"),
        "current_assets": _first_at(facts, end, "current_assets"),
        "current_liabilities": _first_at(facts, end, "current_liabilities"),
        "total_debt": total_debt(facts, end),
        "net_income_ttm": _ttm_of_freshest(facts, "net_income"),
        "operating_income_ttm": _ttm_of_freshest(facts, "operating_income"),
        "revenue_ttm": _ttm_of_freshest(facts, "revenue"),
    }


def balance_sheet(facts):
    """Latest balance sheet: assets, liabilities, goodwill, intangibles (all as of one date)."""
    assets = {}
    for tag in tags(facts, "assets"):
        assets = _instants(facts, tag)
        if assets:
            break
    if not assets:
        return None
    end = max(assets)

    liabilities = _first_at(facts, end, "liabilities")
    if liabilities is None:
        # Many filers never tag total liabilities; derive it from the accounting identity.
        total = _first_at(facts, end, "liabilities_and_equity")
        equity = _first_at(facts, end, "equity_with_minorities")
        if total is not None and equity is not None:
            liabilities = total - equity

    # A tag missing at `end` means the company reports none of that item.
    goodwill = _first_at(facts, end, "goodwill")
    intangibles = _first_at(facts, end, "intangibles")
    if intangibles is None:
        intangibles = sum(_instants(facts, tag).get(end, 0) for tag in tags(facts, "intangibles_parts"))
    if goodwill is None and not intangibles:
        # Some IFRS filers report one combined figure.
        intangibles = _first_at(facts, end, "intangibles_with_goodwill") or 0

    return {
        "date": end,
        "assets": assets[end],
        "liabilities": liabilities,
        "goodwill": goodwill or 0,
        "intangibles": intangibles,
    }


def diluted_shares(facts):
    """Most recently reported weighted-average diluted share count."""
    rows = _facts(facts, _shares_tag(facts), "shares")
    if not rows:
        return None
    return max(rows, key=lambda r: (r["end"], r.get("filed", "")))["val"]

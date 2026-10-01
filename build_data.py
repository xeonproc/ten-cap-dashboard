"""Screen the whole U.S. market, value the survivors, and write dist/data.json.

Phase 1 (a handful of bulk requests) narrows every listed stock to profitable candidates.
Phase 2 (one SEC request per candidate) pulls full fundamentals and computes valuations.

Runs in GitHub Actions so the browser never has to call the data sources (no CORS issues).
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import market_data
import sec_data
import valuation

OUTPUT = Path(__file__).parent / "dist" / "data.json"

# Phase 1 thresholds
MAJOR_EXCHANGES = {"NYSE", "Nasdaq"}  # SEC labels NYSE American (AMEX) listings as NYSE
MIN_MARKET_CAP = 100_000_000
MAX_EPS_AGE_DAYS = 550  # ignore companies whose latest annual report is ~18+ months old

# Phase 2 thresholds
EPS_YEARS = 10  # fiscal years of EPS history to keep (long enough to span a business cycle)
MIN_EPS_YEARS = 3  # need at least this many fiscal years to normalize earnings
MIN_PLAUSIBLE_PE = 1  # below this the EPS is almost certainly wrong (share classes, bad filings)

# Optional cap on candidates, for quick test builds.
CANDIDATE_LIMIT = int(os.environ.get("CANDIDATE_LIMIT") or 0)


def select_candidates():
    """Phase 1: return (candidates, funnel) where funnel counts survivors of each filter."""
    universe = sec_data.load_universe()
    listings = market_data.fetch_listings()
    annual_eps = sec_data.latest_annual_eps_by_cik()
    oldest_eps = (date.today() - timedelta(days=MAX_EPS_AGE_DAYS)).isoformat()

    funnel = {"sec_registrant_tickers": len(universe), "listed_common_stocks": len(listings)}
    on_exchange, sized, profitable = [], {}, []

    for listing in listings:
        info = universe.get(listing["ticker"].upper())
        if info and info["exchange"] in MAJOR_EXCHANGES:
            on_exchange.append({**listing, **info, "name": info["name"]})
    funnel["on_major_exchange_with_sec_filings"] = len(on_exchange)

    for stock in on_exchange:
        if not (stock["price"] and stock["price"] > 0):
            continue
        if not (stock["market_cap"] and stock["market_cap"] > MIN_MARKET_CAP):
            continue
        # One row per company: keep the largest share class.
        kept = sized.get(stock["cik"])
        if kept is None or stock["market_cap"] > kept["market_cap"]:
            sized[stock["cik"]] = stock
    funnel["price_and_market_cap_ok"] = len(sized)

    for stock in sized.values():
        eps = annual_eps.get(stock["cik"])
        if eps and eps["eps"] > 0 and eps["end"] >= oldest_eps:
            profitable.append(stock)
    funnel["positive_latest_annual_eps"] = len(profitable)

    profitable.sort(key=lambda s: s["market_cap"], reverse=True)
    if CANDIDATE_LIMIT:
        profitable = profitable[:CANDIDATE_LIMIT]
    return profitable, funnel


def build_company(stock):
    """Phase 2: full fundamentals and valuation for one candidate."""
    facts = sec_data.fetch_company_facts(stock["cik"])
    tag = sec_data.eps_tag(facts)
    if tag is None:
        raise ValueError("no annual EPS reported")
    splits = sec_data.detect_splits(facts, tag)
    eps = sec_data.eps_history(facts, tag, EPS_YEARS, splits)
    if len(eps) < MIN_EPS_YEARS:
        raise ValueError(f"only {len(eps)} fiscal years of EPS")
    ttm = sec_data.ttm_eps(facts, tag, splits)
    if ttm is None or ttm <= 0:
        raise ValueError("TTM EPS is not positive")
    if stock["price"] / ttm < MIN_PLAUSIBLE_PE:
        raise ValueError("implausible EPS relative to price (multiple share classes?)")

    values = [row["value"] for row in eps]
    norm_eps = valuation.normalized_eps(values)
    norm_eps_5y = valuation.normalized_eps(values[-5:])
    for norm in (norm_eps, norm_eps_5y):
        if norm > 0 and stock["price"] / norm < MIN_PLAUSIBLE_PE:
            raise ValueError("implausible historical EPS relative to price (bad filing data?)")

    bs = sec_data.balance_sheet(facts) or {}
    shares = sec_data.diluted_shares(facts)
    q = sec_data.quality_inputs(facts, bs["date"]) if bs else {}
    if (q.get("net_income_ttm") or 0) < 0:
        raise ValueError("EPS is positive but net income is negative (mis-signed filing data)")

    iv = valuation.intrinsic_value(norm_eps)
    pe = stock["price"] / ttm
    growth = valuation.eps_growth(values[-5:])
    return {
        "normalized_eps_5y": norm_eps_5y,
        "splits": [{"before": when, "factor": round(factor, 4)} for when, factor in splits],
        "pe": pe,
        "roe": valuation.ratio(q.get("net_income_ttm"), q.get("equity")),
        "current_ratio": valuation.ratio(q.get("current_assets"), q.get("current_liabilities")),
        "debt_to_equity": valuation.ratio(q.get("total_debt"), q.get("equity")),
        "operating_margin": valuation.ratio(q.get("operating_income_ttm"), q.get("revenue_ttm")),
        "eps_growth": growth,
        "peg": valuation.peg(pe, growth),
        "ticker": stock["ticker"],
        "name": stock["name"],
        "cik": stock["cik"],
        "exchange": stock["exchange"],
        "sector": stock["sector"],
        "industry": stock["industry"],
        "price": stock["price"],
        "market_cap": stock["market_cap"],
        "eps_basis": "diluted" if tag == sec_data.EPS_TAGS[0] else "basic",
        "eps_history": [{"fiscal_year": r["fiscal_year"], "eps": r["value"]} for r in eps],
        "ttm_eps": ttm,
        "normalized_eps": norm_eps,
        "intrinsic_value": iv,
        "discount_pct": valuation.discount_pct(stock["price"], iv),
        "tbv_per_share": valuation.tbv_per_share(
            bs.get("assets"), bs.get("liabilities"), bs.get("goodwill"), bs.get("intangibles"), shares
        ),
        "balance_sheet_date": bs.get("date"),
        "assets": bs.get("assets"),
        "liabilities": bs.get("liabilities"),
        "goodwill": bs.get("goodwill"),
        "intangibles": bs.get("intangibles"),
        "shares_outstanding": shares,
    }


def main():
    candidates, funnel = select_candidates()
    for step, count in funnel.items():
        print(f"{step}: {count}")
    print(f"Phase 2: analysing {len(candidates)} candidates")

    companies, errors = [], []
    for i, stock in enumerate(candidates, 1):
        try:
            companies.append(build_company(stock))
        except Exception as exc:
            errors.append({"ticker": stock["ticker"], "error": str(exc)})
        if i % 100 == 0:
            print(f"  {i}/{len(candidates)} ({len(companies)} valued, {len(errors)} dropped)")
    funnel["valued"] = len(companies)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "defaults": {
                    "hurdle_rate": valuation.DEFAULT_HURDLE_RATE,
                    "growth_rate": valuation.DEFAULT_GROWTH_RATE,
                },
                "funnel": funnel,
                "companies": companies,
                "errors": errors,
            }
        )
    )
    print(f"Wrote {OUTPUT} ({len(companies)} companies, {len(errors)} dropped)")
    if not companies:
        sys.exit("No companies were built; failing so stale data is not replaced with nothing.")


if __name__ == "__main__":
    main()

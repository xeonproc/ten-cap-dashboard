"""Fast step, run on every build: compute valuations from the raw-data cache.

Reads cache/raw.jsonl.gz (written by fetch_data.py) and writes dist/data.json, which the
dashboard loads. No network access, so it takes seconds.
"""

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import sec_data
import valuation
from fetch_data import CACHE

OUTPUT = Path(__file__).parent / "dist" / "data.json"

HISTORY_YEARS = 10  # fiscal years to keep (long enough to span a business cycle)
MIN_EPS_YEARS = 3  # need at least this many fiscal years to normalize earnings
MIN_FCF_YEARS = 3  # ...and this many to use free cash flow as an earnings basis
MIN_PLAUSIBLE_PE = 1  # below this the EPS is almost certainly wrong (share classes, bad filings)

# Operating cash flow is not a measure of owner earnings for lenders and insurers.
NO_FCF_SECTORS = {"Finance"}


def build_company(stock, facts):
    """Fundamentals, quality metrics and default valuation for one candidate."""
    tag = sec_data.eps_tag(facts)
    if tag is None:
        raise ValueError("no annual EPS reported")
    splits = sec_data.detect_splits(facts, tag)
    history = sec_data.annual_history(facts, tag, splits, HISTORY_YEARS)
    if len(history) < MIN_EPS_YEARS:
        raise ValueError(f"only {len(history)} fiscal years of EPS")
    ttm = sec_data.ttm_eps(facts, tag, splits)
    if ttm is None or ttm <= 0:
        raise ValueError("TTM EPS is not positive")
    price = stock["price"]
    if price / ttm < MIN_PLAUSIBLE_PE:
        raise ValueError("implausible EPS relative to price (multiple share classes?)")

    eps = [year["eps"] for year in history]
    avg_eps = valuation.average(eps)
    for avg in (avg_eps, valuation.average(eps[-5:])):
        if avg > 0 and price / avg < MIN_PLAUSIBLE_PE:
            raise ValueError("implausible historical EPS relative to price (bad filing data?)")

    bs = sec_data.balance_sheet(facts) or {}
    shares = sec_data.diluted_shares(facts)
    q = sec_data.quality_inputs(facts, bs["date"]) if bs else {}
    if (q.get("net_income_ttm") or 0) < 0:
        raise ValueError("EPS is positive but net income is negative (mis-signed filing data)")

    if stock["sector"] in NO_FCF_SECTORS:
        for year in history:
            year["fcf_ps"] = None
    avg_fcf = valuation.average([year["fcf_ps"] for year in history], MIN_FCF_YEARS)
    iv = valuation.intrinsic_value(valuation.owner_earnings(avg_eps, avg_fcf))

    cash_years = [y for y in history if y["ocf"] is not None and y["capex"] is not None]
    tbv = valuation.tbv_per_share(
        bs.get("assets"), bs.get("liabilities"), bs.get("goodwill"), bs.get("intangibles"), shares
    )
    pe = price / ttm
    growth = valuation.eps_growth(eps[-5:])
    return {
        "ticker": stock["ticker"],
        "name": stock["name"],
        "cik": stock["cik"],
        "exchange": stock["exchange"],
        "sector": stock["sector"],
        "industry": stock["industry"],
        "price": price,
        "market_cap": stock["market_cap"],
        "eps_basis": "diluted" if tag == sec_data.EPS_TAGS[0] else "basic",
        "history": [
            {
                "fiscal_year": y["fiscal_year"],
                "eps": y["eps"],
                "fcf_ps": None if y["fcf_ps"] is None else round(y["fcf_ps"], 4),
            }
            for y in history
        ],
        "splits": [{"before": when, "factor": round(factor, 4)} for when, factor in splits],
        "ttm_eps": ttm,
        "intrinsic_value": iv,
        "discount_pct": valuation.discount_pct(price, iv),
        # stability
        "loss_years": sum(1 for v in eps if v < 0),
        "eps_volatility": valuation.volatility(eps),
        "ttm_to_avg": valuation.ratio(ttm, avg_eps),
        "revenue_growth": valuation.cagr([y["revenue"] for y in history]),
        "capex_to_ocf": valuation.ratio(
            sum(y["capex"] for y in cash_years), sum(y["ocf"] for y in cash_years)
        )
        if cash_years
        else None,
        # quality
        "pe": pe,
        "peg": valuation.peg(pe, growth),
        "eps_growth": growth,
        "roe": valuation.ratio(q.get("net_income_ttm"), q.get("equity")),
        "current_ratio": valuation.ratio(q.get("current_assets"), q.get("current_liabilities")),
        "debt_to_equity": valuation.ratio(q.get("total_debt"), q.get("equity")),
        "operating_margin": valuation.ratio(q.get("operating_income_ttm"), q.get("revenue_ttm")),
        # balance sheet
        "tbv_per_share": tbv,
        "price_to_tbv": valuation.ratio(price, tbv),
        "balance_sheet_date": bs.get("date"),
        "assets": bs.get("assets"),
        "liabilities": bs.get("liabilities"),
        "goodwill": bs.get("goodwill"),
        "intangibles": bs.get("intangibles"),
        "shares_outstanding": shares,
    }


def main():
    if not CACHE.exists():
        sys.exit(f"{CACHE} not found. Run fetch_data.py first.")

    companies, errors = [], []
    with gzip.open(CACHE, "rt", encoding="utf-8") as lines:
        header = json.loads(next(lines))
        for line in lines:
            record = json.loads(line)
            try:
                companies.append(build_company(record["stock"], record["facts"]))
            except Exception as exc:
                errors.append({"ticker": record["stock"]["ticker"], "error": str(exc) or repr(exc)})

    funnel = header["funnel"]
    funnel["valued"] = len(companies)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "generated_at": header["generated_at"],
                "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
    reasons = {}
    for error in errors:
        reasons[error["error"]] = reasons.get(error["error"], 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {count:5d}  {reason}")
    if not companies:
        sys.exit("No companies were built; failing so stale data is not replaced with nothing.")


if __name__ == "__main__":
    main()

"""Fast step, run on every build: compute valuations from the raw-data cache.

Reads cache/raw.jsonl.gz (written by fetch_data.py) and writes dist/data.json, which the
dashboard loads. No network access, so it takes seconds.

Two valuation paths produce the same output fields:
  - US companies: per-share figures straight from the filings, adjusted for stock splits.
  - Foreign companies: whole-company figures converted to US dollars, then divided by the
    number of US-listed shares (market cap / price). This avoids needing to know how many
    ordinary shares each depositary share represents.
"""

import gzip
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sec_data
import valuation
from fetch_data import CACHE, MAX_EPS_AGE_DAYS

OUTPUT = Path(__file__).parent / "dist" / "data.json"

HISTORY_YEARS = 10  # fiscal years to keep (long enough to span a business cycle)
MIN_EPS_YEARS = 3  # need at least this many fiscal years to normalize earnings
MIN_FCF_YEARS = 3  # ...and this many to use free cash flow as an earnings basis
MIN_PLAUSIBLE_PE = 1  # below this the EPS is almost certainly wrong (share classes, bad filings)

# Operating cash flow is not a measure of owner earnings for lenders and insurers.
NO_FCF_SECTORS = {"Finance"}


def finalize_history(stock, history):
    """Decide whether free cash flow is usable for this company, and blank it if not."""
    usable = (
        stock["sector"] not in NO_FCF_SECTORS
        # A missing latest year means the company stopped reporting capex in a form we read;
        # an average of only the older years would be misleading.
        and history[-1]["fcf_ps"] is not None
    )
    if not usable:
        for year in history:
            year["fcf_ps"] = None


def common_fields(stock, history, ttm, avg_eps, tbv):
    """Fields computed the same way for US and foreign companies."""
    price = stock["price"]
    eps = [year["eps"] for year in history]
    avg_fcf = valuation.average([year["fcf_ps"] for year in history], MIN_FCF_YEARS)
    iv = valuation.intrinsic_value(valuation.owner_earnings(avg_eps, avg_fcf))
    cash_years = [y for y in history if y["ocf"] is not None and y["capex"] is not None]
    pe = price / ttm
    growth = valuation.eps_growth(eps[-5:])
    return {
        "ticker": stock["ticker"],
        "name": stock["name"],
        "cik": stock["cik"],
        "exchange": stock["exchange"],
        "sector": stock["sector"],
        "industry": stock["industry"],
        "country": stock.get("country"),
        "price": price,
        "market_cap": stock["market_cap"],
        "history": [
            {
                "fiscal_year": y["fiscal_year"],
                "eps": round(y["eps"], 4),
                "fcf_ps": None if y["fcf_ps"] is None else round(y["fcf_ps"], 4),
            }
            for y in history
        ],
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
        # quality (the rest are added by the caller)
        "pe": pe,
        "peg": valuation.peg(pe, growth),
        "eps_growth": growth,
        "tbv_per_share": tbv,
        "price_to_tbv": valuation.ratio(price, tbv),
    }


def check_plausible(price, ttm, eps):
    if ttm is None or ttm <= 0:
        raise ValueError("latest earnings are not positive")
    if price / ttm < MIN_PLAUSIBLE_PE:
        raise ValueError("implausible EPS relative to price (multiple share classes?)")
    for avg in (valuation.average(eps), valuation.average(eps[-5:])):
        if avg > 0 and price / avg < MIN_PLAUSIBLE_PE:
            raise ValueError("implausible historical EPS relative to price (bad filing data?)")


def build_us_company(stock, facts):
    """US company: per-share figures from the filings, adjusted for splits."""
    tag = sec_data.eps_tag(facts)
    if tag is None:
        raise ValueError("no annual EPS reported")
    splits = sec_data.detect_splits(facts, tag)
    history = sec_data.annual_history(facts, tag, splits, HISTORY_YEARS)
    if len(history) < MIN_EPS_YEARS:
        raise ValueError(f"only {len(history)} fiscal years of EPS")
    ttm = sec_data.ttm_eps(facts, tag, splits)
    eps = [year["eps"] for year in history]
    check_plausible(stock["price"], ttm, eps)

    bs = sec_data.balance_sheet(facts) or {}
    shares = sec_data.diluted_shares(facts)
    q = sec_data.quality_inputs(facts, bs["date"]) if bs else {}
    if (q.get("net_income_ttm") or 0) < 0:
        raise ValueError("EPS is positive but net income is negative (mis-signed filing data)")

    finalize_history(stock, history)
    tbv = valuation.tbv_per_share(
        bs.get("assets"), bs.get("liabilities"), bs.get("goodwill"), bs.get("intangibles"), shares
    )
    return {
        **common_fields(stock, history, ttm, valuation.average(eps), tbv),
        "foreign": False,
        "currency": "USD",
        "eps_basis": "diluted" if tag == sec_data.tags(facts, "eps")[0] else "basic",
        "splits": [{"before": when, "factor": round(factor, 4)} for when, factor in splits],
        "roe": valuation.ratio(q.get("net_income_ttm"), q.get("equity")),
        "current_ratio": valuation.ratio(q.get("current_assets"), q.get("current_liabilities")),
        "debt_to_equity": valuation.ratio(q.get("total_debt"), q.get("equity")),
        "operating_margin": valuation.ratio(q.get("operating_income_ttm"), q.get("revenue_ttm")),
        "balance_sheet_date": bs.get("date"),
        "assets": bs.get("assets"),
        "liabilities": bs.get("liabilities"),
        "goodwill": bs.get("goodwill"),
        "intangibles": bs.get("intangibles"),
        "shares_outstanding": shares,
    }


class Converter:
    """Turns amounts in a company's reporting currency into US dollars."""

    def __init__(self, currency, exchange_rates):
        self.currency = currency
        self.monthly = None if currency == "USD" else exchange_rates.get(currency)
        if currency != "USD" and not self.monthly:
            raise ValueError(f"no exchange rate history for {currency}")

    def average_rate(self, end):
        """Average rate over the 12 months ending at `end` (for a year's income or cash flow)."""
        if self.monthly is None:
            return 1.0
        year, month = int(end[:4]), int(end[5:7])
        months = [f"{year + (month - i - 1) // 12:04d}-{(month - i - 1) % 12 + 1:02d}" for i in range(12)]
        rates = [self.monthly[m] for m in months if m in self.monthly]
        return sum(rates) / len(rates) if len(rates) >= 6 else None

    def spot_rate(self, end):
        """Rate at `end` (for balance-sheet figures), or the nearest earlier month."""
        if self.monthly is None:
            return 1.0
        earlier = [m for m in self.monthly if m <= end[:7]]
        return self.monthly[max(earlier)] if earlier else None


def build_foreign_company(stock, facts, exchange_rates):
    """Foreign company: whole-company figures in dollars per US-listed share."""
    if not facts or not facts.get("currency"):
        raise ValueError("no machine-readable annual figures in SEC filings")
    fx = Converter(facts["currency"], exchange_rates)
    price = stock["price"]
    listed_shares = stock["market_cap"] / price  # US-listed shares or depositary shares

    income = sec_data.annual_by_end(facts, "net_income", HISTORY_YEARS)
    cash_flow = sec_data.annual_by_end(facts, "operating_cash_flow", HISTORY_YEARS)
    capex = sec_data.annual_by_end(facts, "capex", HISTORY_YEARS)
    revenue = sec_data.annual_by_end(facts, "revenue", HISTORY_YEARS)
    operating_income = sec_data.annual_by_end(facts, "operating_income", HISTORY_YEARS)

    history = []
    for end in sorted(income)[-HISTORY_YEARS:]:
        rate = fx.average_rate(end)
        if rate is None:
            continue  # older than the exchange-rate history
        ocf, spent = cash_flow.get(end), capex.get(end)
        usd = lambda amount: None if amount is None else amount * rate  # noqa: E731
        history.append(
            {
                "fiscal_year": int(end[:4]),
                "end": end,
                "eps": income[end] * rate / listed_shares,
                "fcf_ps": (ocf - spent) * rate / listed_shares if ocf is not None and spent is not None else None,
                "revenue": usd(revenue.get(end)),
                "ocf": usd(ocf),
                "capex": usd(spent),
            }
        )
    if len(history) < MIN_EPS_YEARS:
        raise ValueError(f"only {len(history)} fiscal years of profit figures")
    latest = history[-1]
    if latest["end"] < (date.today() - timedelta(days=MAX_EPS_AGE_DAYS)).isoformat():
        raise ValueError(f"latest annual figures are stale ({latest['end']})")

    # Foreign filers rarely provide machine-readable quarterly figures, so the latest
    # fiscal year stands in for the trailing twelve months.
    ttm = latest["eps"]
    eps = [year["eps"] for year in history]
    check_plausible(price, ttm, eps)

    bs = sec_data.balance_sheet(facts) or {}
    end = bs.get("date")
    spot = fx.spot_rate(end) if end else None
    to_usd = lambda amount: None if amount is None or spot is None else amount * spot  # noqa: E731
    equity = sec_data._first_at(facts, end, "equity") if end else None
    latest_income = income[latest["end"]]

    # How many ordinary shares one US-listed share represents, implied by the filings:
    # (profit / reported EPS) gives ordinary shares; compare with US-listed shares.
    eps_tag = sec_data.eps_tag(facts)
    reported_eps = sec_data.annual_series(facts, eps_tag, sec_data.EPS_UNIT, 1) if eps_tag else []
    shares_per_listed = None
    if reported_eps and reported_eps[-1]["end"] == latest["end"] and reported_eps[-1]["value"] > 0:
        shares_per_listed = latest_income / reported_eps[-1]["value"] / listed_shares

    finalize_history(stock, history)
    tbv = valuation.tbv_per_share(
        to_usd(bs.get("assets")),
        to_usd(bs.get("liabilities")),
        to_usd(bs.get("goodwill")),
        to_usd(bs.get("intangibles")),
        listed_shares if spot is not None else None,
    )
    return {
        **common_fields(stock, history, ttm, valuation.average(eps), tbv),
        "foreign": True,
        "currency": facts["currency"],
        "accounting": "IFRS" if facts["taxonomy"] == "ifrs-full" else "US GAAP",
        "fiscal_year_end": latest["end"],
        "shares_per_listed_share": None if shares_per_listed is None else round(shares_per_listed, 3),
        "eps_basis": "profit / listed shares",
        "splits": [],
        # Ratios within one balance sheet need no currency conversion.
        "roe": valuation.ratio(latest_income, equity),
        "current_ratio": valuation.ratio(
            sec_data._first_at(facts, end, "current_assets") if end else None,
            sec_data._first_at(facts, end, "current_liabilities") if end else None,
        ),
        "debt_to_equity": valuation.ratio(sec_data.total_debt(facts, end) if end else None, equity),
        "operating_margin": valuation.ratio(operating_income.get(latest["end"]), revenue.get(latest["end"])),
        "balance_sheet_date": end,
        "assets": to_usd(bs.get("assets")),
        "liabilities": to_usd(bs.get("liabilities")),
        "goodwill": to_usd(bs.get("goodwill")),
        "intangibles": to_usd(bs.get("intangibles")),
        "shares_outstanding": listed_shares,
    }


def is_foreign(stock, facts):
    """Foreign path for anything domiciled abroad, listed as depositary shares, or not
    reporting in US dollars under US GAAP."""
    if stock.get("foreign"):
        return True
    if not facts:
        return False
    return (facts.get("taxonomy") or "us-gaap") != "us-gaap" or (facts.get("currency") or "USD") != "USD"


def read_cache():
    """Return (header, exchange_rates, iterator of company records)."""
    exchange_rates = {}
    with gzip.open(CACHE, "rt", encoding="utf-8") as lines:
        header = json.loads(next(lines))
        for line in lines:
            if line.startswith('{"exchange_rates"'):
                exchange_rates = json.loads(line)["exchange_rates"]

    def records():
        with gzip.open(CACHE, "rt", encoding="utf-8") as lines:
            next(lines)
            for line in lines:
                if not line.startswith('{"exchange_rates"'):
                    yield json.loads(line)

    return header, exchange_rates, records()


def main():
    if not CACHE.exists():
        sys.exit(f"{CACHE} not found. Run fetch_data.py first.")

    header, exchange_rates, records = read_cache()
    companies, errors = [], []
    for record in records:
        stock, facts = record["stock"], record.get("facts")
        foreign = is_foreign(stock, facts)
        try:
            if facts is None and not foreign:
                raise ValueError(f"SEC download failed: {record.get('error')}")
            if foreign:
                companies.append(build_foreign_company(stock, facts, exchange_rates))
            else:
                companies.append(build_us_company(stock, facts))
        except Exception as exc:
            errors.append({"ticker": stock["ticker"], "foreign": foreign, "error": str(exc) or repr(exc)})

    funnel = header["funnel"]
    funnel["valued_us"] = sum(1 for c in companies if not c["foreign"])
    funnel["valued_foreign"] = sum(1 for c in companies if c["foreign"])
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
    print(
        f"Wrote {OUTPUT} ({funnel['valued_us']} US and {funnel['valued_foreign']} foreign "
        f"companies, {len(errors)} dropped)"
    )
    reasons = {}
    for error in errors:
        key = ("foreign" if error["foreign"] else "US", error["error"])
        reasons[key] = reasons.get(key, 0) + 1
    for (market, reason), count in sorted(reasons.items(), key=lambda item: -item[1])[:25]:
        print(f"  {count:5d}  {market:7s}  {reason}")
    if not funnel["valued_us"]:
        sys.exit("No US companies were built; failing so stale data is not replaced with nothing.")


if __name__ == "__main__":
    main()

"""Slow step, run once a day: screen the listed market and download raw SEC figures.

Phase 1 (a handful of bulk requests) picks the candidates:
  - US companies: on a major exchange, over $100M, profitable in the latest fiscal year.
  - Foreign companies listed in the US: on a major exchange and over $100M. (There is no
    bulk profit figure for them, so profitability is checked later.)
Phase 2 (one SEC request per candidate) downloads each candidate's company facts and saves a
trimmed copy to cache/raw.jsonl.gz, followed by exchange rates for every currency seen.
build_data.py then computes everything from that file, so changes to the calculations do not
need another download.
"""

import gzip
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import market_data
import sec_data
from sec_tags import KEEP_TAGS

CACHE = Path(__file__).parent / "cache" / "raw.jsonl.gz"

MAJOR_EXCHANGES = {"NYSE", "Nasdaq"}  # SEC labels NYSE American (AMEX) listings as NYSE
MIN_MARKET_CAP = 100_000_000
MAX_EPS_AGE_DAYS = 550  # ignore companies whose latest annual report is ~18+ months old

# How much history to keep: enough for a 10-year window plus restated comparatives.
DURATION_YEARS = 13
BALANCE_SHEET_YEARS = 3
ROW_FIELDS = ("start", "end", "val", "filed", "fp")

# Optional cap on candidates, for quick test builds.
CANDIDATE_LIMIT = int(os.environ.get("CANDIDATE_LIMIT") or 0)


def select_candidates():
    """Phase 1: return (candidates, funnel) where funnel counts survivors of each filter."""
    universe = sec_data.load_universe()
    listings = market_data.fetch_listings()
    annual_eps = sec_data.latest_annual_eps_by_cik()
    oldest_eps = (date.today() - timedelta(days=MAX_EPS_AGE_DAYS)).isoformat()

    funnel = {"sec_registrant_tickers": len(universe), "listed_common_stocks": len(listings)}
    on_exchange, sized, candidates = [], {}, []

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
    funnel["us_over_100m"] = sum(1 for s in sized.values() if not s["foreign"])
    funnel["foreign_over_100m"] = sum(1 for s in sized.values() if s["foreign"])

    for stock in sized.values():
        if stock["foreign"]:
            candidates.append(stock)
            continue
        eps = annual_eps.get(stock["cik"])
        if eps and eps["eps"] > 0 and eps["end"] >= oldest_eps:
            candidates.append(stock)
    funnel["us_profitable_latest_year"] = sum(1 for s in candidates if not s["foreign"])

    candidates.sort(key=lambda s: s["market_cap"], reverse=True)
    if CANDIDATE_LIMIT:
        candidates = candidates[:CANDIDATE_LIMIT]
    return candidates, funnel


def trim(raw_facts):
    """Keep only the taxonomy, currency, tags, fields and years the calculations use."""
    context = sec_data.detect_context(raw_facts)
    if context is None:
        return {"taxonomy": None, "currency": None, "facts": {}}
    taxonomy, currency = context
    units_wanted = {currency, f"{currency}/shares", "shares"}

    today = date.today()
    duration_cutoff = date(today.year - DURATION_YEARS, today.month, 1).isoformat()
    instant_cutoff = date(today.year - BALANCE_SHEET_YEARS, today.month, 1).isoformat()
    source = raw_facts.get("facts", {}).get(taxonomy, {})
    kept_tags = {}
    for tag in KEEP_TAGS[taxonomy]:
        kept_units = {}
        for unit, rows in source.get(tag, {}).get("units", {}).items():
            if unit not in units_wanted:
                continue
            slim = [
                {field: row[field] for field in ROW_FIELDS if field in row}
                for row in rows
                if row["end"] >= (duration_cutoff if "start" in row else instant_cutoff)
            ]
            if slim:
                kept_units[unit] = slim
        if kept_units:
            kept_tags[tag] = {"units": kept_units}
    return {"taxonomy": taxonomy, "currency": currency, "facts": {taxonomy: kept_tags}}


def main():
    candidates, funnel = select_candidates()
    for step, count in funnel.items():
        print(f"{step}: {count}")
    print(f"Downloading company facts for {len(candidates)} candidates")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    saved = failed = 0
    currencies = set()
    with gzip.open(CACHE, "wt", encoding="utf-8") as out:
        header = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "funnel": funnel,
        }
        out.write(json.dumps(header) + "\n")
        for i, stock in enumerate(candidates, 1):
            try:
                facts = trim(sec_data.fetch_company_facts(stock["cik"]))
                out.write(json.dumps({"stock": stock, "facts": facts}) + "\n")
                saved += 1
                if facts["currency"]:
                    currencies.add(facts["currency"])
            except Exception as exc:
                failed += 1
                # Foreign companies that file no machine-readable figures still get a row,
                # so the dashboard can say why they are missing.
                out.write(json.dumps({"stock": stock, "facts": None, "error": str(exc)}) + "\n")
                print(f"  ! {stock['ticker']}: {exc}", file=sys.stderr)
            if i % 100 == 0:
                print(f"  {i}/{len(candidates)}")

        currencies.discard("USD")
        print(f"Fetching exchange rates for {len(currencies)} currencies: {sorted(currencies)}")
        rates = {currency: market_data.fetch_exchange_rates(currency) for currency in sorted(currencies)}
        out.write(json.dumps({"exchange_rates": rates}) + "\n")

    print(f"Wrote {CACHE} ({saved} companies, {failed} failed, {CACHE.stat().st_size / 1e6:.1f} MB)")
    if not saved:
        CACHE.unlink()
        sys.exit("No company facts were downloaded.")


if __name__ == "__main__":
    main()

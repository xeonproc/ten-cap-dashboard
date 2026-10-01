"""Pre-compute valuation data for every ticker in tickers.txt and write dist/data.json.

Runs in GitHub Actions so the browser never has to call SEC or Yahoo (no CORS issues).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

import sec_data
import valuation

ROOT = Path(__file__).parent
TICKERS_FILE = ROOT / "tickers.txt"
OUTPUT = ROOT / "dist" / "data.json"

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}


def load_tickers():
    lines = TICKERS_FILE.read_text().splitlines()
    return [ln.split("#")[0].strip().upper() for ln in lines if ln.split("#")[0].strip()]


def fetch_price(ticker):
    """Latest market price from Yahoo Finance, or None if unavailable."""
    try:
        resp = requests.get(
            YAHOO_CHART_URL.format(ticker=ticker.replace(".", "-")),
            headers=YAHOO_HEADERS,
            params={"range": "1d", "interval": "1d"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except Exception as exc:
        print(f"  ! price lookup failed for {ticker}: {exc}", file=sys.stderr)
        return None


def build_company(ticker, info):
    facts = sec_data.fetch_company_facts(info["cik"])
    eps = sec_data.eps_history(facts)
    if not eps:
        raise ValueError("no annual diluted EPS reported")
    bs = sec_data.balance_sheet(facts) or {}
    shares = sec_data.diluted_shares(facts)
    price = fetch_price(ticker)

    norm_eps = valuation.normalized_eps([row["value"] for row in eps])
    iv = valuation.intrinsic_value(norm_eps)
    return {
        "ticker": ticker,
        "name": info["name"],
        "cik": info["cik"],
        "price": price,
        "eps_history": [{"fiscal_year": r["fiscal_year"], "eps": r["value"]} for r in eps],
        "normalized_eps": norm_eps,
        "intrinsic_value": iv,
        "discount_pct": valuation.discount_pct(price, iv),
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
    tickers = load_tickers()
    cik_map = sec_data.load_cik_map()
    companies, errors = [], []

    for ticker in tickers:
        print(f"{ticker} ...")
        info = cik_map.get(ticker)
        if info is None:
            errors.append({"ticker": ticker, "error": "ticker not found in SEC registry"})
            continue
        try:
            companies.append(build_company(ticker, info))
        except Exception as exc:
            print(f"  ! {ticker}: {exc}", file=sys.stderr)
            errors.append({"ticker": ticker, "error": str(exc)})

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "defaults": {
                    "hurdle_rate": valuation.DEFAULT_HURDLE_RATE,
                    "growth_rate": valuation.DEFAULT_GROWTH_RATE,
                },
                "companies": companies,
                "errors": errors,
            },
            indent=2,
        )
    )
    print(f"Wrote {OUTPUT} ({len(companies)} companies, {len(errors)} errors)")
    if not companies:
        sys.exit("No companies were built; failing so stale data is not replaced with nothing.")


if __name__ == "__main__":
    main()

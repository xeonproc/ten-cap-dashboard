# 10-Cap Value Investing Dashboard

A serverless dashboard on GitHub Pages. GitHub Actions pulls SEC EDGAR fundamentals and
market prices, computes intrinsic values, and publishes a static `data.json` that a
Streamlit app (running in the browser via [stlite](https://github.com/whitphx/stlite)) reads.

| File | Role |
| --- | --- |
| `fetch_data.py` | Slow step: screens the market and downloads raw SEC figures to `cache/raw.jsonl.gz` |
| `build_data.py` | Fast step: computes everything from the cache and writes `dist/data.json` |
| `sec_tags.py` | The list of SEC fields that are downloaded and may be read |
| `sec_data.py` | SEC EDGAR client and extractors (EPS, cash flow, balance sheet, split detection) |
| `market_data.py` | Bulk price / market-cap snapshot of all NYSE, NASDAQ and AMEX listings; exchange rates |
| `valuation.py` | Formulas, shared by the build and the browser app |
| `app.py` / `index.html` | Streamlit dashboard and its stlite host page |
| `.github/workflows/deploy.yml` | Builds and deploys to Pages |

## How builds work

- **Daily (weekdays 22:30 UTC):** downloads fresh SEC data and prices (about 15 minutes) and
  saves the raw figures in the GitHub Actions cache.
- **On every push:** recomputes from the cached raw figures and redeploys (about a minute).
- A fresh download also happens automatically when `fetch_data.py`, `market_data.py` or
  `sec_tags.py` change, since those decide what is downloaded.
- From the Actions tab, "Run workflow" offers a forced refresh and a small test build
  (the N largest candidates only).

## Screening pipeline

1. **Pre-filter (bulk, a few requests):** every listed common stock is matched to its SEC
   registration and kept only if it is on NYSE/NASDAQ/AMEX, has a positive price, a market
   cap above $100M, and positive EPS in its latest fiscal year.
2. **Analysis (one SEC request per candidate):** candidates need positive trailing-twelve-month
   EPS and at least 3 fiscal years of history. Companies with obviously broken filing data
   are dropped (see "candidates dropped" on the dashboard).
3. **Dashboard:** intrinsic value and discount are recomputed in the browser from the
   sidebar settings, then filtered.

## US and foreign modes

The dashboard has a market switch.

- **US companies** are valued from per-share figures in their filings (US GAAP, dollars),
  adjusted for stock splits, with trailing-twelve-month earnings from quarterly reports.
- **Foreign companies listed in the US** (usually as depositary shares) report in their own
  currency, often under IFRS. Their whole-company figures are converted to dollars at each
  fiscal year's average exchange rate and divided by the number of US-listed shares
  (market cap / price), which avoids needing the depositary-share ratio. They are not
  pre-filtered for profit, have annual figures only, and may lag by up to about two years.

## Valuation

- **Owner earnings** = the lower of average EPS and average free cash flow per share
  (switchable to either alone), averaged over 10 or 5 fiscal years. Free cash flow is
  operating cash flow minus all capital spending. Financial companies always use EPS.
- **Intrinsic value** = owner earnings × (1 + g) / (r − g), defaults r = 10%, g = 3%.
  With g = 0 this is a strict 10-cap: 10 × owner earnings.
- **Discount %** = (1 − price / intrinsic value) × 100.
- **TBV/share** = (assets − liabilities − goodwill − intangibles) / diluted shares.
- Per-share history is adjusted for stock splits, detected from restated figures in later filings.

## Filters

- **Stability:** years of history, loss years, EPS volatility (standard deviation ÷ mean),
  current EPS relative to the long-run average, capex as a share of operating cash flow,
  and revenue growth.
- **Quality (Buffett screen):** P/E, PEG, 5-year EPS growth, return on equity, current
  ratio, debt/equity, operating margin. Growth and PEG use historical growth, not forecasts.

## Deploy

1. Push to a GitHub repository on `main`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Optional: Settings → Variables → add `SEC_USER_AGENT` (`Your Name you@example.com`).

## Run locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python fetch_data.py   # slow; CANDIDATE_LIMIT=100 for a quick sample
python build_data.py
streamlit run app.py
```

## Known limitations

- A stock split is only detected once the company's next quarterly or annual filing restates
  earlier periods, so a split in the last few months may not be adjusted yet.
- Maintenance capex is not disclosed, so free cash flow subtracts all capex. This understates
  owner earnings for companies investing heavily in growth.
- Foreign figures depend on the listing source's market cap being for the whole company and on
  monthly exchange rates from Yahoo Finance; currency moves appear as earnings volatility.
- Some foreign companies file no machine-readable figures with the SEC and cannot be valued.
- Companies tag debt and revenue inconsistently; debt/equity and operating margin are approximate.
- Prices and market caps come from Nasdaq's unofficial screener endpoint; if it fails the
  build fails and the previous deployment stays up.
- Companies with several share classes that report EPS per one class (e.g. Berkshire) are dropped.

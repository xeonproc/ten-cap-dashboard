# 10-Cap Value Investing Dashboard

**Live:** https://xeonproc.github.io/ten-cap-dashboard/

A serverless value-investing screener on GitHub Pages. Once a day GitHub Actions scans every
stock on the major US exchanges, downloads each candidate's financial figures from SEC EDGAR,
and estimates what each business is worth per share. A Streamlit app, running entirely in
the browser via [stlite](https://github.com/whitphx/stlite), lets you adjust the assumptions
and filters. Nothing runs on a server and the browser never calls the data sources.

It is a screening tool that produces a shortlist to research. It is not investment advice.

## What the dashboard does

- **Two markets**, switched at the top of the sidebar: US-style reporters and foreign
  reporters listed in the US. Each loads its own default settings.
- **Bargain screener:** a table of companies passing the filters, ranked by discount to
  intrinsic value, with CSV download. It opens as a watchlist (target discount 0%).
- **Stock drill-down:** price, intrinsic value and tangible book value, stability figures,
  and a chart of EPS and free cash flow per share by year.
- **Formulas:** every calculation explained in plain language with a worked example using a
  real company and the current sidebar settings.
- **Reset to defaults** restores every setting for the current market.
- Green-screen terminal theme with optional effects (the `FX` switch, bottom right).

## How intrinsic value is calculated

1. For each of the last 10 fiscal years, take **EPS** (reported profit per share) and
   **free cash flow per share** (operating cash flow minus all capital spending).
2. Adjust older years for **stock splits**, detected from restated figures in later filings.
3. **Average** each over 10 years (or 5).
4. **Owner earnings** = the lower of the two averages. Financial companies, and companies
   that did not report capex in their latest year, use EPS only.
5. **Intrinsic value** = owner earnings × (1 + g) / (r − g). The defaults, r = 10% and
   g = 3%, give 14.7 × owner earnings. With g = 0 it is a strict 10-cap: 10 × owner earnings.
6. **Discount %** = (1 − price / intrinsic value) × 100.

Tangible book value per share, (assets − liabilities − goodwill − intangibles) / shares, is
shown alongside as an asset-based anchor.

## Filters and defaults

| | US | Foreign |
| --- | --- | --- |
| Target discount | 0% | 0% |
| **Stability** | | |
| Years of history, at least | 10 | 5 |
| Loss years, at most | 1 | 0 |
| EPS volatility (std. dev. ÷ mean) under | 0.75 | 1.0 |
| Current EPS ÷ long-run average under | 2.5 | 2.5 |
| Capex as % of operating cash flow under | 60% | 60% |
| Revenue growth per year over | 0% | 0% |
| **Quality** | | |
| P/E under | 20 | 15 |
| Return on equity over | 15% | 15% |
| Debt / equity under | 1.0 | 1.0 |

Four more quality filters are available but off by default: PEG under 2, 5-year EPS growth
over 5%, current ratio over 1.5, operating margin over 15%. With all seven on this is the
full "Buffett screen". Growth and PEG use historical EPS growth, not analyst forecasts.

Foreign mode also has **Hide unreliable rows** (on by default), which removes companies whose
implied share ratio is not a whole number, whose latest annual report is more than about 18
months old, or which report under hyperinflation accounting (Argentina).

## US and foreign modes

The split is by how a company reports, not where its head office is.

- **US-style reporters:** US GAAP, US dollars, quarterly filings, and a listed share that is
  an ordinary share. This includes companies based abroad that report this way (lululemon,
  Accenture, Check Point). They are valued from per-share figures in the filings, with
  trailing-twelve-month earnings from quarterly reports.
- **Foreign reporters:** annual reports only, often in another currency or under IFRS,
  usually listed as depositary shares (ADS/ADR). Whole-company figures are converted to
  dollars at each fiscal year's average exchange rate and divided by the number of
  US-listed shares (market cap / price), which avoids needing the depositary-share ratio.
  The latest fiscal year stands in for trailing twelve months, and data can lag by up to
  about two years.

## Screening pipeline

1. **Pre-filter (a few bulk requests):** every listed common stock is matched to its SEC
   registration and kept if it is on NYSE/NASDAQ/AMEX with a market cap above $100M. US
   companies must also have positive EPS in their latest fiscal year; foreign companies are
   checked for profit later, since no bulk figure exists for them.
2. **Download (one SEC request per candidate):** each candidate's company facts are trimmed
   to the fields in `sec_tags.py` and saved, along with exchange rates for every currency seen.
3. **Compute:** companies need positive latest earnings and at least 3 fiscal years of
   history. Companies with obviously broken filing data are dropped; the dashboard lists
   them, with the reason, under "candidates dropped during analysis".
4. **Dashboard:** intrinsic value and discount are recomputed in the browser from the
   sidebar settings, then filtered.

The sidebar's "How the universe was screened" shows the count at each step.

## How builds work

- **Daily (weekdays 22:30 UTC):** downloads fresh SEC data, prices and exchange rates
  (about 25 minutes) and saves the raw figures in the GitHub Actions cache.
- **On every push:** recomputes from the cached raw figures and redeploys (under a minute).
- A fresh download also happens automatically when `fetch_data.py`, `market_data.py` or
  `sec_tags.py` change, since those decide what is downloaded.
- From the Actions tab, "Run workflow" offers a forced refresh and a small test build
  (the N largest candidates only, not cached).
- The build fails, leaving the previous deployment up, if the dashboard code does not
  compile or if no US companies could be valued.

## Files

| File | Role |
| --- | --- |
| `fetch_data.py` | Slow step: screens the market and downloads raw SEC figures to `cache/raw.jsonl.gz` |
| `build_data.py` | Fast step: computes everything from the cache and writes `dist/data.json` |
| `sec_tags.py` | The SEC fields that are downloaded and may be read, for US GAAP and IFRS |
| `sec_data.py` | SEC EDGAR client and extractors (EPS, cash flow, balance sheet, split detection) |
| `market_data.py` | Listing snapshot (price, market cap, sector, country) and exchange rates |
| `valuation.py` | Formulas, shared by the build and the browser app |
| `app.py` | The Streamlit dashboard |
| `index.html` | stlite host page, theme and visual effects |
| `.streamlit/config.toml` | The same theme for local runs |
| `.github/workflows/deploy.yml` | Builds and deploys to Pages |

## Data sources

- **SEC EDGAR** (`data.sec.gov`): company registry, bulk EPS "frames", and per-company
  XBRL facts. Requests identify themselves with the `SEC_USER_AGENT` value.
- **Nasdaq screener** (unofficial): price, market cap, sector and country for every listing.
- **Yahoo Finance** (unofficial): monthly exchange rates for foreign reporters.

## Deploy your own

1. Push to a GitHub repository on `main`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Variables → add `SEC_USER_AGENT` (`Your Name you@example.com`). The SEC asks
   for real contact details; the built-in default is a placeholder.

## Run locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python fetch_data.py   # slow; CANDIDATE_LIMIT=100 for a quick sample
python build_data.py
streamlit run app.py
```

## Known limitations

- The valuation assumes the next ten years resemble the last ten. It cannot tell a bargain
  from a business in decline, and it penalises companies that have grown steadily.
- A stock split is only detected once the company's next quarterly or annual filing restates
  earlier periods, so a split in the last few months may not be adjusted yet.
- Maintenance capex is not disclosed, so free cash flow subtracts all capex. This understates
  owner earnings for companies investing heavily in growth.
- Companies tag debt, revenue and capex inconsistently; debt/equity, operating margin and
  free cash flow are approximate, and filing errors occasionally slip through the checks.
- Foreign figures depend on the listing source's market cap covering the whole company, and
  currency moves appear as earnings volatility. Hyperinflation accounting is not handled.
- Some foreign companies file no machine-readable figures with the SEC, or their latest
  ones are years old, and cannot be valued.
- Companies with several share classes that report EPS per one class (e.g. Berkshire) are dropped.
- Prices and market caps come from an unofficial endpoint; if it fails, the daily build
  fails and the previous deployment stays up.

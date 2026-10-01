# 10-Cap Value Investing Dashboard

A serverless dashboard on GitHub Pages. GitHub Actions pulls SEC EDGAR fundamentals and
market prices, computes intrinsic values, and publishes a static `data.json` that a
Streamlit app (running in the browser via [stlite](https://github.com/whitphx/stlite)) reads.

| File | Role |
| --- | --- |
| `market_data.py` | Bulk price / market-cap snapshot of all NYSE, NASDAQ and AMEX listings |
| `sec_data.py` | SEC EDGAR client: registrant universe, bulk EPS frames, per-company facts |
| `valuation.py` | Formulas, shared by the build and the browser app |
| `build_data.py` | CI entry point, writes `dist/data.json` |
| `app.py` / `index.html` | Streamlit dashboard and its stlite host page |
| `.github/workflows/deploy.yml` | Builds on push, on weekdays, and on demand; deploys to Pages |

## Screening pipeline

1. **Phase 1 (bulk, a few requests):** every listed common stock is matched to its SEC
   registration and kept only if it is on NYSE/NASDAQ/AMEX, has a positive price, a market
   cap above $100M, and positive EPS in its latest fiscal year.
2. **Phase 2 (one SEC request per candidate):** full company facts are pulled; candidates
   need positive trailing-twelve-month EPS and at least 3 fiscal years of EPS history.
3. **Dashboard:** intrinsic value and discount are recomputed in the browser from the
   sliders, then filtered by target discount and sector.

Thresholds are constants at the top of `build_data.py`. The counts at each step are
written to `data.json` and shown in the dashboard sidebar.

## Formulas

- Normalized EPS = average of the last 5 fiscal years of diluted EPS
- Intrinsic value = Normalized EPS × (1 + g) / (r − g), defaults r = 10%, g = 3%
- TBV/share = (Assets − Liabilities − Goodwill − Intangibles) / diluted shares
- Discount % = (1 − Price / Intrinsic value) × 100

## Deploy

1. Push to a GitHub repository on `main`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Optional: Settings → Variables → add `SEC_USER_AGENT` (`Your Name you@example.com`).

## Run locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python build_data.py
streamlit run app.py
```

## Known limitations

- EPS older than the comparative periods in recent 10-Ks is not split-adjusted, so a
  recent stock split can distort the 5-year average.
- Companies filing under IFRS (most foreign issuers) have no `us-gaap` facts and are skipped.
- Banks and insurers often lack a tagged total for liabilities; it is derived from
  total liabilities and equity minus equity.
- Prices and market caps come from Nasdaq's unofficial screener endpoint; if it fails the
  build fails and the previous deployment stays up.
- Companies with several share classes that report EPS per one class (e.g. Berkshire) are dropped.

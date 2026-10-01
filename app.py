"""10-Cap value investing dashboard. Runs in the browser via stlite, or locally with
`streamlit run app.py` (after `python build_data.py && cp dist/data.json .`)."""

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import valuation

st.set_page_config(page_title="10-Cap Value Dashboard", page_icon="📉", layout="wide")


@st.cache_data
def load_data():
    for path in (Path("data.json"), Path("dist/data.json")):
        if path.exists():
            return json.loads(path.read_text())
    return None


data = load_data()
if data is None:
    st.error("data.json not found. Run `python build_data.py` first.")
    st.stop()

companies = data["companies"]
defaults = data.get("defaults", {})

# ---------------------------------------------------------------- sidebar
st.sidebar.header("Assumptions")
r = st.sidebar.slider(
    "Hurdle rate (r) %", 5.0, 20.0, defaults.get("hurdle_rate", 0.10) * 100, 0.5
) / 100
g = st.sidebar.slider(
    "Growth rate (g) %", 0.0, 8.0, defaults.get("growth_rate", 0.03) * 100, 0.25
) / 100
target = st.sidebar.slider("Target discount (margin of safety) %", 0, 90, 30, 5)
sectors = sorted({c.get("sector") for c in companies if c.get("sector")})
chosen_sectors = st.sidebar.multiselect("Sectors", sectors, placeholder="All sectors")

st.sidebar.header("Quality filters")
use_quality = st.sidebar.toggle("Apply Buffett screen", value=True)
with st.sidebar.expander("Thresholds", expanded=False):
    max_pe = st.number_input("P/E under", value=25.0, step=1.0)
    max_peg = st.number_input("PEG under (past EPS growth)", value=2.0, step=0.25)
    min_growth = st.number_input("EPS growth, past years, over %", value=5.0, step=1.0)
    min_roe = st.number_input("Return on equity over %", value=15.0, step=1.0)
    min_current = st.number_input("Current ratio over", value=1.5, step=0.1)
    max_de = st.number_input("Debt / equity under", value=0.5, step=0.1)
    min_margin = st.number_input("Operating margin over %", value=15.0, step=1.0)
    st.caption(
        "Growth and PEG use historical EPS growth, not analyst forecasts. "
        "A company missing a metric fails that filter."
    )
st.sidebar.caption(f"Data built {data.get('generated_at', 'unknown')} from SEC EDGAR filings.")

FUNNEL_LABELS = {
    "sec_registrant_tickers": "Tickers registered with the SEC",
    "listed_common_stocks": "Common stocks on NYSE / NASDAQ / AMEX",
    "on_major_exchange_with_sec_filings": "…matched to SEC filings",
    "price_and_market_cap_ok": "…with market cap > $100M",
    "positive_latest_annual_eps": "…profitable in latest fiscal year",
    "valued": "…valued (positive TTM EPS, 3+ years of data)",
}
if data.get("funnel"):
    with st.sidebar.expander("How the universe was screened", expanded=True):
        for key, count in data["funnel"].items():
            st.write(f"{FUNNEL_LABELS.get(key, key)}: **{count:,}**")

if r <= g:
    st.error("Hurdle rate must be greater than growth rate.")
    st.stop()


def pct(value):
    return None if value is None else value * 100


def revalue(company):
    """Re-run the valuation with the sidebar's r and g."""
    iv = valuation.intrinsic_value(company["normalized_eps"], r, g)
    return {
        "Ticker": company["ticker"],
        "Company": company["name"],
        "Sector": company.get("sector"),
        "Mkt Cap ($M)": (company.get("market_cap") or 0) / 1e6 or None,
        "Price": company["price"],
        "TTM EPS": company.get("ttm_eps"),
        "Normalized EPS": company["normalized_eps"],
        "Intrinsic Value": iv,
        "TBV / Share": company["tbv_per_share"],
        "Discount %": valuation.discount_pct(company["price"], iv),
        "P/E": company.get("pe"),
        "PEG": company.get("peg"),
        "EPS Growth %": pct(company.get("eps_growth")),
        "ROE %": pct(company.get("roe")),
        "Current Ratio": company.get("current_ratio"),
        "Debt/Equity": company.get("debt_to_equity"),
        "Op Margin %": pct(company.get("operating_margin")),
    }


table = pd.DataFrame([revalue(c) for c in companies])
total_loaded = len(table)
if chosen_sectors:
    table = table[table["Sector"].isin(chosen_sectors)]
if use_quality:
    # Comparisons against NaN are False, so companies missing a metric are excluded.
    table = table[
        (table["P/E"] < max_pe)
        & (table["PEG"] < max_peg)
        & (table["EPS Growth %"] > min_growth)
        & (table["ROE %"] > min_roe)
        & (table["Current Ratio"] > min_current)
        & (table["Debt/Equity"] < max_de)
        & (table["Op Margin %"] > min_margin)
    ]

st.title("10-Cap Value Investing Dashboard")
st.caption(
    f"Intrinsic value = normalized EPS × (1 + g) / (r − g), with r = {r:.1%} and g = {g:.2%}. "
    "Not investment advice."
)

screener_tab, detail_tab = st.tabs(["Bargain screener", "Stock drill-down"])

# ---------------------------------------------------------------- screener
with screener_tab:
    show_all = st.checkbox("Show all companies (ignore target discount)")
    view = table if show_all else table[table["Discount %"] >= target]
    view = view.sort_values("Discount %", ascending=False, na_position="last")

    st.subheader(f"{len(view)} of {len(table)} companies" + ("" if show_all else f" at ≥ {target}% discount"))
    if len(table) < total_loaded:
        st.caption(f"{len(table):,} of {total_loaded:,} loaded companies pass the sidebar filters.")

    if view.empty and table["Discount %"].notna().any():
        best = table.loc[table["Discount %"].idxmax()]
        st.info(
            f"{len(table)} companies pass the filters, but none trade at a ≥ {target}% discount. "
            f"The closest is {best['Ticker']} at {best['Discount %']:+.1f}%. "
            "Lower the target discount in the sidebar, or tick “Show all companies”."
        )

    def discount_color(value):
        if pd.isna(value):
            return ""
        return "color: #15803d; font-weight: 600" if value >= 0 else "color: #b91c1c; font-weight: 600"

    money = "${:,.2f}"
    styler = view.style.format(
        {
            "Price": money,
            "Mkt Cap ($M)": "{:,.0f}",
            "TTM EPS": money,
            "Normalized EPS": money,
            "Intrinsic Value": money,
            "TBV / Share": money,
            "Discount %": "{:+.1f}%",
            "P/E": "{:.1f}",
            "PEG": "{:.2f}",
            "EPS Growth %": "{:+.1f}%",
            "ROE %": "{:.1f}%",
            "Current Ratio": "{:.2f}",
            "Debt/Equity": "{:.2f}",
            "Op Margin %": "{:.1f}%",
        },
        na_rep="—",
    )
    # Styler.applymap was renamed to Styler.map in pandas 2.1
    color = styler.map if hasattr(styler, "map") else styler.applymap
    st.dataframe(color(discount_color, subset=["Discount %"]), hide_index=True, use_container_width=True)

    st.download_button(
        "Download CSV",
        view.to_csv(index=False).encode("utf-8"),
        file_name="ten_cap_screener.csv",
        mime="text/csv",
    )

    if data.get("errors"):
        with st.expander(f"{len(data['errors'])} candidates dropped during analysis"):
            st.dataframe(pd.DataFrame(data["errors"]), hide_index=True)

# ---------------------------------------------------------------- drill-down
with detail_tab:
    by_ticker = {c["ticker"]: c for c in companies}
    ticker = st.selectbox(
        "Company", list(by_ticker), format_func=lambda t: f"{t} — {by_ticker[t]['name']}"
    )
    company = by_ticker[ticker]
    row = revalue(company)

    def usd(value):
        return "—" if value is None or pd.isna(value) else f"${value:,.2f}"

    price_col, iv_col, tbv_col = st.columns(3)
    price_col.metric("Current price", usd(row["Price"]))
    discount = row["Discount %"]
    iv_col.metric(
        "Intrinsic value",
        usd(row["Intrinsic Value"]),
        None if discount is None else f"{discount:+.1f}% margin of safety",
    )
    tbv_col.metric("Tangible book value / share", usd(row["TBV / Share"]))

    if row["Intrinsic Value"] is None:
        st.warning("Normalized EPS is not positive, so no intrinsic value can be computed.")

    history = company["eps_history"]
    years = [str(h["fiscal_year"]) for h in history]
    eps = [h["eps"] for h in history]
    fig = go.Figure(
        go.Bar(
            x=years,
            y=eps,
            marker_color=["#15803d" if v >= 0 else "#b91c1c" for v in eps],
            text=[f"${v:,.2f}" for v in eps],
            textposition="outside",
            hovertemplate="FY %{x}: $%{y:,.2f}<extra></extra>",
        )
    )
    fig.add_hline(
        y=company["normalized_eps"],
        line_dash="dash",
        annotation_text=f"Normalized EPS ${company['normalized_eps']:,.2f}",
    )
    fig.update_layout(
        title=f"{ticker} diluted EPS by fiscal year",
        xaxis_title="Fiscal year",
        yaxis_title="Diluted EPS (USD)",
        xaxis_type="category",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander(f"Balance sheet inputs (as of {company.get('balance_sheet_date') or 'n/a'})"):
        st.dataframe(
            pd.DataFrame(
                {
                    "Item": ["Total assets", "Total liabilities", "Goodwill", "Other intangibles", "Diluted shares"],
                    "Value": [
                        company.get("assets"),
                        company.get("liabilities"),
                        company.get("goodwill"),
                        company.get("intangibles"),
                        company.get("shares_outstanding"),
                    ],
                }
            ).style.format({"Value": "{:,.0f}"}, na_rep="—"),
            hide_index=True,
        )

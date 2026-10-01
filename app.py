"""10-Cap value investing dashboard. Runs in the browser via stlite, or locally with
`streamlit run app.py` (after `python fetch_data.py && python build_data.py`)."""

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import valuation

st.set_page_config(page_title="10-Cap Value Dashboard", page_icon="📉", layout="wide")

MIN_FCF_YEARS = 3  # fewer years of cash-flow data than this and EPS is used instead


@st.cache_data
def load_data():
    for path in (Path("data.json"), Path("dist/data.json")):
        if path.exists():
            return json.loads(path.read_text())
    return None


data = load_data()
if data is None:
    st.error("data.json not found. Run `python fetch_data.py` then `python build_data.py`.")
    st.stop()

companies = data["companies"]
defaults = data.get("defaults", {})

# ---------------------------------------------------------------- sidebar
st.sidebar.header("Valuation")
r = st.sidebar.slider(
    "Hurdle rate (r) %", 5.0, 20.0, defaults.get("hurdle_rate", 0.10) * 100, 0.5
) / 100
g = st.sidebar.slider(
    "Growth rate (g) %",
    0.0,
    8.0,
    defaults.get("growth_rate", 0.03) * 100,
    0.25,
    help="Set to 0 for a strict 10-cap: at r = 10% the price ceiling is 10 × owner earnings.",
) / 100
target = st.sidebar.slider("Target discount (margin of safety) %", 0, 90, 30, 5)
BASES = {
    "lower": "Lower of EPS and free cash flow",
    "eps": "EPS only",
    "fcf": "Free cash flow only",
}
basis = st.sidebar.radio(
    "Owner earnings measured by",
    list(BASES),
    format_func=BASES.get,
    help="Free cash flow is operating cash flow minus all capital spending, per share. "
    "Taking the lower of the two avoids paying for accounting earnings that never become "
    "cash. Financial companies always use EPS.",
)
window = st.sidebar.radio(
    "Averaged over",
    [10, 5],
    format_func=lambda n: f"{n} years",
    horizontal=True,
    help="10 years spans a full business cycle, which matters for cyclical companies.",
)
sectors = sorted({c.get("sector") for c in companies if c.get("sector")})
chosen_sectors = st.sidebar.multiselect("Sectors", sectors, placeholder="All sectors")

st.sidebar.header("Stability filters")
use_stability = st.sidebar.toggle("Screen out boom-bust businesses", value=True)
with st.sidebar.expander("Thresholds", expanded=False):
    min_years = st.number_input("Years of history, at least", value=10, min_value=3, max_value=10, step=1)
    max_loss_years = st.number_input("Loss years, at most", value=1, min_value=0, step=1)
    max_volatility = st.number_input(
        "EPS volatility under",
        value=0.75,
        step=0.05,
        help="Standard deviation of annual EPS divided by its average. Steady earners are "
        "below about 0.5; boom-bust businesses are well above 1.",
    )
    max_peak = st.number_input(
        "Current EPS ÷ long-run average, under",
        value=2.5,
        step=0.25,
        help="A high value means today's earnings are far above the company's own history: "
        "either strong growth or a cyclical peak.",
    )
    max_capex = st.number_input(
        "Capex as % of operating cash flow, under",
        value=60.0,
        step=5.0,
        help="Capital-heavy businesses (shipping, mining, energy) must keep reinvesting just "
        "to stand still. Companies that do not report this pass.",
    )
    min_revenue_growth = st.number_input("Revenue growth per year, over %", value=0.0, step=1.0)

st.sidebar.header("Quality filters")
use_quality = st.sidebar.toggle("Apply Buffett screen", value=True)
with st.sidebar.expander("Thresholds", expanded=False):
    max_pe = st.number_input("P/E under", value=25.0, step=1.0)
    max_peg = st.number_input("PEG under (past EPS growth)", value=2.0, step=0.25)
    min_growth = st.number_input("EPS growth, past 5 years, over %", value=5.0, step=1.0)
    min_roe = st.number_input("Return on equity over %", value=15.0, step=1.0)
    min_current = st.number_input("Current ratio over", value=1.5, step=0.1)
    max_de = st.number_input("Debt / equity under", value=0.5, step=0.1)
    min_margin = st.number_input("Operating margin over %", value=15.0, step=1.0)
    st.caption(
        "Growth and PEG use historical EPS growth, not analyst forecasts. "
        "A company missing a metric fails that filter."
    )
st.sidebar.caption(f"SEC data downloaded {data.get('generated_at', 'unknown')}.")

FUNNEL_LABELS = {
    "sec_registrant_tickers": "Tickers registered with the SEC",
    "listed_common_stocks": "Common stocks on NYSE / NASDAQ / AMEX",
    "on_major_exchange_with_sec_filings": "…matched to SEC filings",
    "price_and_market_cap_ok": "…with market cap > $100M",
    "positive_latest_annual_eps": "…profitable in latest fiscal year",
    "valued": "…valued (positive TTM EPS, 3+ years of data)",
}
if data.get("funnel"):
    with st.sidebar.expander("How the universe was screened", expanded=False):
        for key, count in data["funnel"].items():
            st.write(f"{FUNNEL_LABELS.get(key, key)}: **{count:,}**")

if r <= g:
    st.error("Hurdle rate must be greater than growth rate.")
    st.stop()


def pct(value):
    return None if value is None else value * 100


def averages(company, years):
    """(average EPS, average free cash flow per share) over the last `years` fiscal years."""
    history = company["history"][-years:]
    return (
        valuation.average([h["eps"] for h in history]),
        valuation.average([h["fcf_ps"] for h in history], MIN_FCF_YEARS),
    )


def revalue(company):
    """Re-run the valuation with the sidebar's assumptions."""
    avg_eps, avg_fcf = averages(company, window)
    earnings = valuation.owner_earnings(avg_eps, avg_fcf, basis)
    iv = valuation.intrinsic_value(earnings, r, g)
    return {
        "Ticker": company["ticker"],
        "Company": company["name"],
        "Sector": company.get("sector"),
        "Mkt Cap ($M)": (company.get("market_cap") or 0) / 1e6 or None,
        "Price": company["price"],
        "Intrinsic Value": iv,
        "Discount %": valuation.discount_pct(company["price"], iv),
        "Owner Earnings": earnings,
        "Avg EPS": avg_eps,
        "Avg FCF/Sh": avg_fcf,
        "TTM EPS": company.get("ttm_eps"),
        "TBV / Share": company["tbv_per_share"],
        "Price/TBV": company.get("price_to_tbv"),
        "Years": len(company["history"]),
        "Loss Years": company.get("loss_years"),
        "EPS Volatility": company.get("eps_volatility"),
        "EPS vs Avg": company.get("ttm_to_avg"),
        "Capex/OCF %": pct(company.get("capex_to_ocf")),
        "Rev Growth %": pct(company.get("revenue_growth")),
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
# Comparisons against NaN are False, so a company missing a metric fails that filter.
if use_stability:
    table = table[
        (table["Years"] >= min_years)
        & (table["Loss Years"] <= max_loss_years)
        & (table["EPS Volatility"] < max_volatility)
        & (table["EPS vs Avg"] < max_peak)
        & ((table["Capex/OCF %"] < max_capex) | table["Capex/OCF %"].isna())
        & (table["Rev Growth %"] > min_revenue_growth)
    ]
if use_quality:
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
    f"Intrinsic value = {window}-year average owner earnings × (1 + g) / (r − g), "
    f"with r = {r:.1%} and g = {g:.2%} ({(1 + g) / (r - g):.1f}× earnings). Not investment advice."
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
    elif table.empty:
        st.info("No companies pass the sidebar filters. Loosen a threshold or switch a filter set off.")

    def discount_color(value):
        if pd.isna(value):
            return ""
        return "color: #15803d; font-weight: 600" if value >= 0 else "color: #b91c1c; font-weight: 600"

    money = "${:,.2f}"
    styler = view.style.format(
        {
            "Mkt Cap ($M)": "{:,.0f}",
            "Price": money,
            "Intrinsic Value": money,
            "Discount %": "{:+.1f}%",
            "Owner Earnings": money,
            "Avg EPS": money,
            "Avg FCF/Sh": money,
            "TTM EPS": money,
            "TBV / Share": money,
            "Price/TBV": "{:.1f}",
            "EPS Volatility": "{:.2f}",
            "EPS vs Avg": "{:.2f}",
            "Capex/OCF %": "{:.0f}%",
            "Rev Growth %": "{:+.1f}%",
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
    # Companies passing the filters first, best discount first; then everything else.
    passing = list(table.sort_values("Discount %", ascending=False, na_position="last")["Ticker"])
    passing_set = set(passing)
    options = passing + [t for t in by_ticker if t not in passing_set]
    ticker = st.selectbox(
        "Company",
        options,
        format_func=lambda t: f"{t} — {by_ticker[t]['name']}" + ("" if t in passing_set else "  (filtered out)"),
    )
    company = by_ticker[ticker]
    row = revalue(company)

    def usd(value):
        return "—" if value is None or pd.isna(value) else f"${value:,.2f}"

    def num(value, fmt="{:.2f}"):
        return "—" if value is None or pd.isna(value) else fmt.format(value)

    price_col, iv_col, tbv_col = st.columns(3)
    price_col.metric("Current price", usd(row["Price"]))
    discount = row["Discount %"]
    iv_col.metric(
        "Intrinsic value",
        usd(row["Intrinsic Value"]),
        None if discount is None else f"{discount:+.1f}% margin of safety",
    )
    tbv_col.metric(
        "Tangible book value / share",
        usd(row["TBV / Share"]),
        None if row["Price/TBV"] is None else f"price is {row['Price/TBV']:.1f}× book",
        delta_color="off",
    )

    if row["Intrinsic Value"] is None:
        st.warning("Average owner earnings are not positive, so no intrinsic value can be computed.")

    stats = st.columns(6)
    stats[0].metric(f"Avg EPS ({window}y)", usd(row["Avg EPS"]))
    stats[1].metric(f"Avg FCF / share ({window}y)", usd(row["Avg FCF/Sh"]))
    stats[2].metric("Loss years", num(row["Loss Years"], "{:.0f}") + f" of {row['Years']}")
    stats[3].metric("EPS volatility", num(row["EPS Volatility"]))
    stats[4].metric("Capex / cash flow", num(row["Capex/OCF %"], "{:.0f}%"))
    stats[5].metric("Revenue growth / yr", num(row["Rev Growth %"], "{:+.1f}%"))

    history = company["history"]
    years = [str(h["fiscal_year"]) for h in history]
    fig = go.Figure()
    fig.add_bar(
        name="Diluted EPS",
        x=years,
        y=[h["eps"] for h in history],
        marker_color="#2563eb",
        hovertemplate="FY %{x} EPS: $%{y:,.2f}<extra></extra>",
    )
    if any(h["fcf_ps"] is not None for h in history):
        fig.add_bar(
            name="Free cash flow / share",
            x=years,
            y=[h["fcf_ps"] for h in history],
            marker_color="#f59e0b",
            hovertemplate="FY %{x} FCF/share: $%{y:,.2f}<extra></extra>",
        )
    if row["Owner Earnings"] is not None:
        fig.add_hline(
            y=row["Owner Earnings"],
            line_dash="dash",
            annotation_text=f"Owner earnings used: ${row['Owner Earnings']:,.2f}",
            annotation_position="top left",
        )
    fig.update_layout(
        title=f"{ticker}: earnings and free cash flow per share by fiscal year",
        xaxis_title="Fiscal year",
        yaxis_title="USD per share",
        xaxis_type="category",
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
    if company.get("splits"):
        st.caption(
            "Per-share figures are adjusted for stock splits detected in the filings: "
            + ", ".join(
                f"{s['factor']:.3g}-for-1 before {s['before']}"
                if s["factor"] >= 1
                else f"1-for-{1 / s['factor']:.3g} reverse before {s['before']}"
                for s in company["splits"]
            )
            + "."
        )

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

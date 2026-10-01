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

# Green-screen terminal palette (base colours are set in index.html / .streamlit/config.toml)
GREEN, DIM_GREEN, AMBER, RED = "#00ff41", "#008f11", "#ffb000", "#ff5555"

st.markdown(
    f"""
    <style>
    h1, h2, h3 {{ text-transform: uppercase; letter-spacing: 0.04em; text-shadow: 0 0 6px {DIM_GREEN}; }}
    h1::before {{ content: "> "; }}
    [data-testid="stSidebar"] {{ border-right: 1px solid {DIM_GREEN}; }}
    [data-testid="stMetric"], [data-testid="stExpander"] details, [data-testid="stAlert"] {{
        border: 1px solid {DIM_GREEN}; border-radius: 0;
    }}
    [data-testid="stAlertContainer"] {{ background: #031403 !important; }}
    [data-testid="stAlert"] p {{ color: {GREEN} !important; }}
    [data-testid="stSidebar"] > div {{ overflow-x: hidden; }}
    [data-testid="stMetric"] {{ padding: 0.5rem 0.75rem; }}
    [data-testid="stMetricValue"] {{ text-shadow: 0 0 6px {DIM_GREEN}; }}
    button, [data-baseweb="select"] > div, [data-baseweb="input"] {{ border-radius: 0 !important; }}
    pre, code {{ border: 1px solid {DIM_GREEN}; border-radius: 0 !important; color: {GREEN} !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)


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

defaults = data.get("defaults", {})

# ---------------------------------------------------------------- sidebar
MODES = {"us": "US companies", "foreign": "Foreign companies (US-listed)"}
mode = st.sidebar.radio(
    "Market",
    list(MODES),
    format_func=MODES.get,
    help="Foreign mode covers companies based outside the US that trade on NYSE or NASDAQ, "
    "usually as depositary shares. Their accounts are converted to US dollars and are "
    "less precise than the US figures; see the Formulas tab.",
)
foreign_mode = mode == "foreign"
companies = [c for c in data["companies"] if bool(c.get("foreign")) == foreign_mode]
if not companies:
    st.warning("No companies are available for this market in the current data.")
    st.stop()

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
    "us_over_100m": "US companies",
    "foreign_over_100m": "Foreign companies",
    "us_profitable_latest_year": "US companies profitable in latest fiscal year",
    "valued_us": "US companies valued",
    "valued_foreign": "Foreign companies valued",
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
        **(
            {
                "Country": company.get("country"),
                "Reports In": company.get("currency"),
                "FY End": company.get("fiscal_year_end"),
            }
            if foreign_mode
            else {}
        ),
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
if foreign_mode:
    st.info(
        "FOREIGN MODE. Figures come from each company's annual report in its own currency, "
        "converted to US dollars at each year's average exchange rate and divided by the number "
        "of US-listed shares. 'TTM EPS' is the latest full fiscal year, since foreign companies "
        "rarely file machine-readable quarterly figures. Currency swings show up as earnings "
        "volatility. Treat these numbers as a first pass and check the company's own report."
    )

screener_tab, detail_tab, formulas_tab = st.tabs(["Bargain screener", "Stock drill-down", "Formulas"])

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
        return f"color: {GREEN}; font-weight: 700" if value >= 0 else f"color: {RED}; font-weight: 700"

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

    dropped = [
        {"ticker": e["ticker"], "reason": e["error"]}
        for e in data.get("errors", [])
        if bool(e.get("foreign")) == foreign_mode
    ]
    if dropped:
        with st.expander(f"{len(dropped)} candidates dropped during analysis"):
            st.dataframe(pd.DataFrame(dropped), hide_index=True)

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
        marker_color=GREEN,
        hovertemplate="FY %{x} EPS: $%{y:,.2f}<extra></extra>",
    )
    if any(h["fcf_ps"] is not None for h in history):
        fig.add_bar(
            name="Free cash flow / share",
            x=years,
            y=[h["fcf_ps"] for h in history],
            marker_color=AMBER,
            hovertemplate="FY %{x} FCF/share: $%{y:,.2f}<extra></extra>",
        )
    if row["Owner Earnings"] is not None:
        fig.add_hline(
            y=row["Owner Earnings"],
            line_dash="dash",
            line_color=GREEN,
            annotation_text=f"Owner earnings used: ${row['Owner Earnings']:,.2f}",
            annotation_position="top left",
        )
    fig.update_layout(
        title=f"{ticker}: earnings and free cash flow per share by fiscal year",
        xaxis_title="Fiscal year",
        yaxis_title="USD per share",
        xaxis_type="category",
        barmode="group",
        font=dict(family="IBM Plex Mono, Consolas, Courier New, monospace", color=GREEN),
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        xaxis=dict(gridcolor="#0a3d0a", linecolor=DIM_GREEN),
        yaxis=dict(gridcolor="#0a3d0a", zerolinecolor=DIM_GREEN),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True, theme=None)
    if company.get("foreign"):
        ratio = company.get("shares_per_listed_share")
        st.caption(
            f"{company.get('country') or 'Foreign'} company reporting in {company.get('currency')} "
            f"under {company.get('accounting')}; latest fiscal year ended {company.get('fiscal_year_end')}. "
            + (
                f"The filings imply one US-listed share represents about {ratio:g} ordinary share(s); "
                "if that is not a round number, the market cap or share data may be off."
                if ratio
                else "The share ratio could not be cross-checked from the filings."
            )
        )
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

# ---------------------------------------------------------------- formulas
with formulas_tab:
    st.subheader("How every number is calculated")
    st.write(
        "Each formula below is followed by a worked example using a real company from the "
        "data and your current sidebar settings. Change the company or the sliders and the "
        "examples update."
    )
    example_ticker = st.selectbox(
        "Example company",
        options,
        format_func=lambda t: f"{t} — {by_ticker[t]['name']}",
        key="formula_example",
    )
    ex = by_ticker[example_ticker]
    ex_row = revalue(ex)
    ex_history = ex["history"][-window:]

    def m(value):
        """Money, or a dash when missing."""
        return "n/a" if value is None or pd.isna(value) else f"${value:,.2f}"

    def n(value, fmt="{:,.2f}"):
        return "n/a" if value is None or pd.isna(value) else fmt.format(value)

    def big(value):
        return "n/a" if value is None else f"${value / 1e6:,.0f}M"

    def explain(title, formula, meaning, example):
        st.markdown(f"#### {title}")
        st.code(formula, language=None)
        st.write(meaning)
        st.caption(f"Example: {example_ticker}")
        st.code(example, language=None)

    if foreign_mode:
        st.markdown("### Part 0 — How foreign companies are converted")
        st.code(
            "per-share figure = company total in its own currency
"
            "                   x average USD exchange rate for that fiscal year
"
            "                   / number of US-listed shares
"
            "number of US-listed shares = market cap / share price",
            language=None,
        )
        st.write(
            "Foreign companies report whole-company totals in their home currency, often under "
            "IFRS (International Financial Reporting Standards) instead of US GAAP (the US "
            "rules). Their US listing is usually a depositary share (ADS or ADR): a US-traded "
            "certificate standing for some number of ordinary shares. Dividing dollar totals by "
            "the number of US-listed shares gives per-share figures without needing that ratio. "
            "'EPS' below therefore means profit attributable to shareholders per US-listed share. "
            "Balance-sheet figures use the exchange rate at the balance-sheet date."
        )
        st.caption(f"Example: {example_ticker}")
        st.code(
            f"reports in {ex.get('currency')} under {ex.get('accounting')}
"
            f"US-listed shares = {big(ex.get('market_cap'))} / {m(ex['price'])} = {n(ex.get('shares_outstanding'), '{:,.0f}')}
"
            f"implied ordinary shares per US-listed share = {n(ex.get('shares_per_listed_share'), '{:g}')}",
            language=None,
        )

    st.markdown("### Part 1 — Intrinsic value")

    explain(
        "1. EPS (earnings per share)",
        "EPS = net profit for the year / number of shares",
        "The company's reported accounting profit, per share. We use the 'diluted' figure, "
        "which counts shares that could be created by stock options. Older years are "
        "rescaled for stock splits so all years are comparable.",
        "\n".join(f"FY{h['fiscal_year']}  EPS {m(h['eps'])}" for h in ex_history),
    )

    explain(
        "2. Free cash flow per share (FCF/share)",
        "FCF/share = (operating cash flow - capital expenditure) / number of shares",
        "Cash the business actually produced, after paying for equipment and buildings "
        "(capital expenditure, or 'capex'). Profit is an accounting opinion; cash is a fact. "
        "Not used for financial companies.",
        "\n".join(f"FY{h['fiscal_year']}  FCF/share {m(h['fcf_ps'])}" for h in ex_history),
    )

    eps_values = [h["eps"] for h in ex_history]
    fcf_values = [h["fcf_ps"] for h in ex_history if h["fcf_ps"] is not None]
    explain(
        f"3. {window}-year averages",
        "average = sum of the yearly values / number of years",
        "One year can be unusually good or bad. Averaging over a full business cycle gives a "
        "steadier picture of what the company normally earns.",
        f"Avg EPS       = ({' + '.join(n(v) for v in eps_values)}) / {len(eps_values)} = {m(ex_row['Avg EPS'])}\n"
        + (
            f"Avg FCF/share = ({' + '.join(n(v) for v in fcf_values)}) / {len(fcf_values)} = {m(ex_row['Avg FCF/Sh'])}"
            if ex_row["Avg FCF/Sh"] is not None
            else "Avg FCF/share = n/a (not used for this company)"
        ),
    )

    explain(
        "4. Owner earnings",
        "owner earnings = the LOWER of average EPS and average FCF/share",
        "What an owner could realistically count on per share each year. Taking the lower "
        "figure means we never pay for profit that did not turn into cash. The sidebar can "
        f"switch this to EPS only or FCF only. Current setting: {BASES[basis]}.",
        f"Avg EPS {m(ex_row['Avg EPS'])}   Avg FCF/share {m(ex_row['Avg FCF/Sh'])}\n"
        f"Owner earnings = {m(ex_row['Owner Earnings'])}",
    )

    multiple = (1 + g) / (r - g)
    explain(
        "5. The multiple",
        "multiple = (1 + g) / (r - g)",
        "How many times owner earnings the business is worth. r is the hurdle rate: the "
        "yearly return you demand. g is the growth rate: how fast you assume earnings grow "
        "forever. A higher r lowers the multiple; a higher g raises it sharply. With g = 0 "
        "and r = 10% the multiple is exactly 10, the strict '10-cap'.",
        f"r = {r:.2%}   g = {g:.2%}\n"
        f"multiple = (1 + {g:.4f}) / ({r:.4f} - {g:.4f}) = {1 + g:.4f} / {r - g:.4f} = {multiple:.2f}",
    )

    explain(
        "6. Intrinsic value",
        "intrinsic value = owner earnings x multiple",
        "The estimate of what one share is worth, based on what the business earns. If owner "
        "earnings are zero or negative there is no intrinsic value.",
        f"{m(ex_row['Owner Earnings'])} x {multiple:.2f} = {m(ex_row['Intrinsic Value'])}",
    )

    explain(
        "7. Discount (margin of safety)",
        "discount % = (1 - price / intrinsic value) x 100",
        "Positive means the share costs less than the estimate. The margin of safety is the "
        "discount you insist on before buying, to allow for the estimate being wrong. The "
        f"screener currently shows companies at {target}% or more.",
        f"price {m(ex['price'])}   intrinsic value {m(ex_row['Intrinsic Value'])}\n"
        + (
            f"discount = (1 - {ex['price']:,.2f} / {ex_row['Intrinsic Value']:,.2f}) x 100 = {ex_row['Discount %']:+.1f}%\n"
            f"price needed for a {target}% discount = {m(ex_row['Intrinsic Value'] * (1 - target / 100))}"
            if ex_row["Intrinsic Value"]
            else "discount = n/a"
        ),
    )

    st.markdown("### Part 2 — Asset anchor")

    explain(
        "8. Tangible book value per share (TBV/share) and Price/TBV",
        "TBV/share = (assets - liabilities - goodwill - intangibles) / shares\n"
        "Price/TBV = price / TBV per share",
        "What the company owns minus what it owes, ignoring things you cannot sell separately "
        "such as brand value (goodwill and intangibles). A floor-style measure: useful for "
        "asset-heavy businesses, less so for software or brands.",
        f"assets {big(ex.get('assets'))} - liabilities {big(ex.get('liabilities'))} "
        f"- goodwill {big(ex.get('goodwill'))} - intangibles {big(ex.get('intangibles'))}\n"
        f"divided by {n(ex.get('shares_outstanding'), '{:,.0f}')} shares = {m(ex.get('tbv_per_share'))}\n"
        f"Price/TBV = {m(ex['price'])} / {m(ex.get('tbv_per_share'))} = {n(ex.get('price_to_tbv'), '{:.1f}')}",
    )

    st.markdown("### Part 3 — Stability filters")

    explain(
        "9. Loss years",
        "loss years = number of years with EPS below zero",
        "A business that loses money in bad years is cyclical or fragile.",
        f"{ex.get('loss_years')} of {len(ex['history'])} years",
    )
    explain(
        "10. EPS volatility",
        "volatility = standard deviation of yearly EPS / average EPS",
        "How much earnings jump around relative to their average. Steady earners are below "
        "about 0.5; boom-and-bust businesses are well above 1.",
        f"volatility = {n(ex.get('eps_volatility'))}",
    )
    explain(
        "11. Current EPS vs long-run average",
        "ratio = EPS over the last 12 months (TTM) / 10-year average EPS",
        "TTM means 'trailing twelve months'. A high ratio means today's earnings are far above "
        "the company's own history: either real growth or a cyclical peak.",
        f"{m(ex.get('ttm_eps'))} / {m(valuation.average([h['eps'] for h in ex['history']]))} = {n(ex.get('ttm_to_avg'))}",
    )
    explain(
        "12. Capex as a share of operating cash flow",
        "capex / OCF = total capital expenditure / total operating cash flow",
        "OCF is operating cash flow. A high share means the business must keep reinvesting "
        "heavily just to keep going (shipping, mining, energy).",
        f"capex / OCF = {n(pct(ex.get('capex_to_ocf')), '{:.0f}%')}",
    )
    explain(
        "13. Revenue growth",
        "growth per year = (last year's revenue / first year's revenue) ^ (1 / years between) - 1",
        "Compound annual growth in sales over the history. Negative means the business is shrinking.",
        f"revenue growth = {n(pct(ex.get('revenue_growth')), '{:+.1f}%')} per year",
    )

    st.markdown("### Part 4 — Quality filters (Buffett screen)")

    last5 = [h["eps"] for h in ex["history"][-5:]]
    explain(
        "14. P/E (price to earnings)",
        "P/E = price / EPS over the last 12 months",
        "How many years of current profit you pay for the share.",
        f"{m(ex['price'])} / {m(ex.get('ttm_eps'))} = {n(ex.get('pe'), '{:.1f}')}",
    )
    explain(
        "15. EPS growth, past 5 years",
        "growth per year = (latest EPS / EPS four years earlier) ^ (1 / 4) - 1",
        "Compound annual growth in EPS. Cannot be computed if either end is zero or negative. "
        "Note: Finviz uses analysts' forecasts of future growth; this uses actual past growth.",
        f"({m(last5[-1])} / {m(last5[0])}) ^ (1 / {len(last5) - 1}) - 1 = {n(pct(ex.get('eps_growth')), '{:+.1f}%')} per year",
    )
    explain(
        "16. PEG (price/earnings to growth)",
        "PEG = P/E / EPS growth in percent",
        "P/E adjusted for growth. A fast grower deserves a higher P/E; PEG under 1 to 2 is "
        "the usual range for 'reasonably priced for its growth'.",
        f"{n(ex.get('pe'), '{:.1f}')} / {n(pct(ex.get('eps_growth')), '{:.1f}')} = {n(ex.get('peg'))}",
    )
    explain(
        "17. ROE (return on equity)",
        "ROE = net profit over the last 12 months / shareholders' equity",
        "Profit as a percentage of the owners' money in the business. Consistently high ROE "
        "suggests a competitive advantage.",
        f"ROE = {n(pct(ex.get('roe')), '{:.1f}%')}",
    )
    explain(
        "18. Current ratio",
        "current ratio = current assets / current liabilities",
        "Short-term assets (cash, stock, money owed to it) against bills due within a year. "
        "Above 1.5 means bills are comfortably covered. Banks do not report this.",
        f"current ratio = {n(ex.get('current_ratio'))}",
    )
    explain(
        "19. Debt / equity",
        "debt / equity = total borrowings / shareholders' equity",
        "How much the company has borrowed relative to the owners' money. Lower is safer. "
        "Approximate: companies label debt inconsistently in their filings.",
        f"debt / equity = {n(ex.get('debt_to_equity'))}",
    )
    explain(
        "20. Operating margin",
        "operating margin = operating profit over the last 12 months / revenue",
        "Profit from the core business as a percentage of sales, before interest and tax. "
        "High margins suggest pricing power.",
        f"operating margin = {n(pct(ex.get('operating_margin')), '{:.1f}%')}",
    )

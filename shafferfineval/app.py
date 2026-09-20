"""
MARKET SIGNAL ENGINE -- Streamlit front end.

Run with:   streamlit run app.py

This file is presentation only. Every number shown here is produced by
`scoring.py` (pure math) from data supplied by `market_data.py` (Yahoo Finance).
Nothing is calculated inline.
"""

from __future__ import annotations

import datetime as _dt

import plotly.graph_objects as go
import streamlit as st

import scoring
from market_data import (
    EQUITY,
    SUPPORTED_INSTRUMENTS,
    SecurityData,
    TickerNotFound,
    build_sector_benchmark,
    fetch_peer_rows,
    get_security_data,
)
from scoring import (
    MODEL_DETAILS,
    build_equity_score,
    calculate_leverage_score,
    calculate_valuation_score,
    explain_score,
)

CACHE_TTL_SECONDS = 60 * 60  # fundamentals move slowly; one hour is plenty

BAND_COLORS = {
    scoring.BEARISH: "#c0392b",
    scoring.SEMI_BEARISH: "#c47a2c",
    scoring.SEMI_BULLISH: "#7aa63c",
    scoring.BULLISH: "#2e9e5b",
}

# --------------------------------------------------------------------------
# Cached data access
# --------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_security(ticker: str) -> SecurityData:
    return get_security_data(ticker)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_peer_rows(tickers: tuple[str, ...]) -> list[dict]:
    return fetch_peer_rows(list(tickers))


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_benchmark(ticker: str, sector: str | None, industry: str | None):
    stub = SecurityData(ticker=ticker, sector=sector, industry=industry)
    return build_sector_benchmark(
        stub, peer_fetcher=lambda tickers: load_peer_rows(tuple(tickers))
    )


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def fmt_pct(value, digits: int = 1) -> str:
    return "unavailable" if value is None else f"{value * 100:.{digits}f}%"


def fmt_x(value, digits: int = 1) -> str:
    return "unavailable" if value is None else f"{value:.{digits}f}x"


def fmt_score(value, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def fmt_money(value) -> str:
    if value is None:
        return "unavailable"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cutoff:
            return f"${value / cutoff:,.2f}{suffix}"
    return f"${value:,.2f}"


def fmt_price(value) -> str:
    return "unavailable" if value is None else f"${value:,.2f}"


def fmt_time(moment) -> str:
    if moment is None:
        return "unavailable"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------
# Visualisation
# --------------------------------------------------------------------------

def score_gauge(score: float, label: str) -> go.Figure:
    """Horizontal -100..+100 band gauge with a marker at the house score."""
    bands = [
        (-100, -40, scoring.BEARISH),
        (-40, 0, scoring.SEMI_BEARISH),
        (0, 40, scoring.SEMI_BULLISH),
        (40, 100, scoring.BULLISH),
    ]

    fig = go.Figure()
    for start, end, name in bands:
        fig.add_shape(
            type="rect",
            x0=start, x1=end, y0=0, y1=1,
            line=dict(width=0),
            fillcolor=BAND_COLORS[name],
            opacity=0.95 if name == label else 0.28,
            layer="below",
        )
        fig.add_annotation(
            x=(start + end) / 2, y=0.5, text=name, showarrow=False,
            font=dict(size=11, family="monospace", color="#ffffff"),
        )

    # Marker: a vertical rule plus a caret above it.
    fig.add_shape(
        type="line", x0=score, x1=score, y0=-0.12, y1=1.12,
        line=dict(color="#ffffff", width=3),
    )
    fig.add_trace(
        go.Scatter(
            x=[score], y=[1.12], mode="markers+text",
            marker=dict(symbol="triangle-down", size=16, color="#ffffff"),
            text=[f"{score:+.1f}"], textposition="top center",
            textfont=dict(size=15, family="monospace", color="#ffffff"),
            hoverinfo="x", showlegend=False,
        )
    )

    fig.update_xaxes(
        range=[-100, 100],
        tickvals=[-100, -40, 0, 40, 100],
        ticktext=["-100", "-40", "0", "+40", "+100"],
        tickfont=dict(family="monospace", size=11, color="#9aa4ad"),
        showgrid=False, zeroline=False,
    )
    fig.update_yaxes(range=[-0.2, 1.7], visible=False)
    fig.update_layout(
        height=150,
        margin=dict(l=10, r=10, t=34, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def factor_bar(factors: list[scoring.FactorResult]) -> go.Figure:
    """Individual factor contributions on the same -100..+100 scale."""
    usable = [f for f in factors if f.score is not None]
    names = [f.name.title() for f in usable][::-1]
    values = [f.score for f in usable][::-1]

    fig = go.Figure(
        go.Bar(
            x=values, y=names, orientation="h",
            marker=dict(
                color=[
                    BAND_COLORS[scoring.classify_score(v)] for v in values
                ]
            ),
            text=[f"{v:+.1f}" for v in values],
            textposition="outside",
            textfont=dict(family="monospace", size=13, color="#e6edf3"),
            hoverinfo="x", showlegend=False,
        )
    )
    fig.add_shape(
        type="line", x0=0, x1=0, y0=-0.5, y1=len(names) - 0.5,
        line=dict(color="#6b7681", width=1, dash="dot"),
    )
    fig.update_xaxes(
        range=[-115, 115],
        tickvals=[-100, -50, 0, 50, 100],
        tickfont=dict(family="monospace", size=11, color="#9aa4ad"),
        showgrid=False, zeroline=False,
    )
    fig.update_yaxes(tickfont=dict(family="monospace", size=13, color="#e6edf3"))
    fig.update_layout(
        height=60 + 44 * max(len(names), 1),
        margin=dict(l=10, r=10, t=6, b=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.45,
    )
    return fig


# --------------------------------------------------------------------------
# Page sections
# --------------------------------------------------------------------------

def render_header(company: SecurityData, result: scoring.EquityScore) -> None:
    st.markdown(f"## {company.ticker}")
    st.markdown(f"**{company.name}**")
    sector_line = " / ".join(x for x in (company.sector, company.industry) if x)
    if sector_line:
        st.caption(sector_line)

    st.markdown("#### HOUSE SCORE")
    if result.score is None:
        st.error("Unavailable -- no factor could be scored from the retrieved data.")
        return

    color = BAND_COLORS[result.label]
    st.markdown(
        f"<div style='font-size:3.4rem;line-height:1.05;font-weight:700;"
        f"font-family:monospace;color:{color};'>{result.score:+.1f}</div>"
        f"<div style='font-size:1.3rem;letter-spacing:.18em;font-family:monospace;"
        f"color:{color};'>{result.label}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(score_gauge(result.score, result.label), use_container_width=True)


def render_factors(result: scoring.EquityScore, benchmark) -> None:
    leverage = result.factors["leverage"]
    valuation = result.factors["valuation"]

    st.markdown("### LEVERAGE")
    left, right = st.columns(2)
    left.metric("Company Debt / Revenue", fmt_pct(leverage.company_value))
    right.metric(
        f"{benchmark.group_label} Median Debt / Revenue",
        fmt_pct(benchmark.debt_revenue_median),
    )
    st.markdown(f"**Leverage Score: {fmt_score(leverage.score)}**")
    for note in leverage.notes:
        st.caption(f"- {note}")

    st.markdown("### VALUATION")
    left, right = st.columns(2)
    left.metric("Company Forward P/E", fmt_x(valuation.company_value))
    right.metric(
        f"{benchmark.group_label} Median Forward P/E",
        fmt_x(benchmark.forward_pe_median),
    )
    st.markdown(f"**Valuation Score: {fmt_score(valuation.score)}**")
    for note in valuation.notes:
        st.caption(f"- {note}")

    st.markdown("### FACTOR SCORES")
    st.plotly_chart(
        factor_bar([leverage, valuation]), use_container_width=True
    )


def render_calculation(result: scoring.EquityScore) -> None:
    st.markdown("### FINAL CALCULATION")
    if result.calculation_text:
        st.code(result.calculation_text, language=None)
    for note in result.notes:
        st.caption(f"- {note}")


def render_data_used(company: SecurityData, benchmark) -> None:
    st.markdown("### DATA USED")
    rows = [
        ("Current price", fmt_price(company.price)),
        ("Market cap", fmt_money(company.market_cap)),
        ("Revenue (TTM)", fmt_money(company.revenue)),
        ("Total debt", fmt_money(company.total_debt)),
        ("Debt / Revenue", fmt_pct(company.debt_revenue)),
        ("Forward EPS (derived: price / fwd P/E)", fmt_price(company.forward_eps)),
        ("Forward P/E", fmt_x(company.forward_pe)),
        ("Trailing P/E", fmt_x(company.trailing_pe)),
        ("Sector", company.sector or "unavailable"),
        ("Industry", company.industry or "unavailable"),
        ("Benchmark group", f"{benchmark.group_label} ({benchmark.grouping})"),
        ("Sector Debt/Revenue median", fmt_pct(benchmark.debt_revenue_median)),
        ("Sector Forward P/E median", fmt_x(benchmark.forward_pe_median)),
        ("Peers used (Debt/Revenue)", str(benchmark.debt_revenue_n)),
        ("Peers used (Forward P/E)", str(benchmark.forward_pe_n)),
        ("Benchmark confidence", "LOW" if benchmark.low_confidence else "OK"),
        ("Revenue as of", company.revenue_as_of or "unavailable"),
        ("Total debt as of", company.debt_as_of or "unavailable"),
        ("Forward P/E as of", company.forward_pe_as_of or "unavailable"),
        ("Data retrieval time", fmt_time(company.retrieved_at)),
        ("Source", "Yahoo Finance (public query endpoints)"),
    ]
    st.table({"Field": [r[0] for r in rows], "Value": [r[1] for r in rows]})

    if company.missing_fields:
        st.warning(
            "Missing or unusable metrics: " + ", ".join(company.missing_fields)
        )
    for note in company.notes + list(benchmark.notes):
        st.caption(f"- {note}")


def render_peers(benchmark) -> None:
    st.markdown("### PEERS USED")
    if not benchmark.peers:
        st.info("No peer data was retrieved.")
        return
    st.caption(
        f"{len(benchmark.peers)} peers in the {benchmark.group_label} "
        f"{benchmark.grouping} group. Medians above are taken from these rows; "
        f"blanks were excluded."
    )
    st.dataframe(
        {
            "Ticker": [p["ticker"] for p in benchmark.peers],
            "Company": [p["name"] for p in benchmark.peers],
            "Debt / Revenue": [fmt_pct(p["debt_revenue"]) for p in benchmark.peers],
            "Forward P/E": [fmt_x(p["forward_pe"]) for p in benchmark.peers],
        },
        use_container_width=True,
        hide_index=True,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def analyze(ticker: str):
    """Fetch -> score. Returns (company, benchmark, EquityScore)."""
    company = load_security(ticker)
    benchmark = load_benchmark(company.ticker, company.sector, company.industry)
    leverage = calculate_leverage_score(
        company.debt_revenue, benchmark.debt_revenue_median
    )
    valuation = calculate_valuation_score(
        company.forward_pe, benchmark.forward_pe_median
    )
    return company, benchmark, build_equity_score([leverage, valuation])


def render_analysis(ticker: str) -> None:
    try:
        with st.spinner(f"Retrieving {ticker.upper()} and its sector peers..."):
            company, benchmark, result = analyze(ticker)
    except TickerNotFound:
        st.error("Ticker not found.")
        return
    except Exception as exc:  # never crash the page on a data-layer surprise
        st.error(f"Could not complete the analysis: {exc}")
        return

    if company.instrument_type not in SUPPORTED_INSTRUMENTS:
        st.warning(
            f"{company.ticker} is a {company.instrument_type or 'unknown'} "
            f"instrument. Version 1 scores equities only -- an "
            f"{company.instrument_type or 'unknown'} engine is not built yet."
        )
        return

    if not company.sector:
        st.warning(
            f"{company.ticker} resolved as an equity but Yahoo reports no sector, "
            "so no peer benchmark could be built."
        )

    render_header(company, result)
    st.divider()
    render_factors(result, benchmark)
    render_calculation(result)

    st.markdown("### EXPLANATION")
    st.write(explain_score(company.ticker, result, benchmark.group_label, benchmark))

    with st.expander("How is this calculated?"):
        st.markdown(MODEL_DETAILS)

    st.divider()
    render_data_used(company, benchmark)
    render_peers(benchmark)


def main() -> None:
    st.set_page_config(page_title="Market Signal Engine", layout="wide")
    st.markdown(
        """
        <style>
          html, body, [class*="css"] { font-family: ui-monospace, SFMono-Regular,
              "SF Mono", Menlo, Consolas, monospace; }
          .stMetric { border: 1px solid #2c333a; border-radius: 4px;
              padding: 10px 12px; }
          h1 { letter-spacing: .22em; font-size: 1.9rem !important; }
          h3 { letter-spacing: .12em; color: #9aa4ad; font-size: 1.0rem !important;
              margin-top: 1.6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("MARKET SIGNAL ENGINE")
    st.caption(
        "Quantitative house score for equities. Version 1: leverage and "
        "valuation, measured against sector peers."
    )

    with st.form("ticker_form"):
        left, right = st.columns([4, 1])
        ticker = left.text_input(
            "Ticker", value="", placeholder="NVDA", label_visibility="visible"
        )
        right.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        submitted = right.form_submit_button("ANALYZE", use_container_width=True)

    if submitted:
        if not ticker.strip():
            st.error("Enter a ticker.")
        else:
            st.session_state["ticker"] = ticker.strip().upper()

    if st.session_state.get("ticker"):
        render_analysis(st.session_state["ticker"])
    else:
        st.info("Enter a ticker (for example NVDA) and press ANALYZE.")


if __name__ == "__main__":
    main()

"""
ShafferFinEval -- Streamlit front end.

Run with:   streamlit run app.py

This file is presentation only. Every number shown here is produced by
`scoring.py` (pure math) from data supplied by `market_data.py` (Yahoo Finance).
Nothing is calculated inline.
"""

from __future__ import annotations

import datetime as _dt

import plotly.graph_objects as go
import streamlit as st

import company_scoring as comp
import scoring
import sector_scoring as sect
import universe as uni
from company_scoring import COMPANY_MODEL_DETAILS, build_company_score
from market_data import (
    EQUITY,
    MAX_UNIVERSE_COMPANIES,
    SUPPORTED_INSTRUMENTS,
    SecurityData,
    TickerNotFound,
    build_universe_records,
    fallback_universe_rows,
    fetch_company_record as md_fetch_company_record,
    get_security_data,
    get_vti_history,
)
from scoring import CLASSIFICATION_DETAILS, classify_score

CACHE_TTL_SECONDS = 60 * 60          # fundamentals move slowly; one hour is plenty
SECTOR_CACHE_TTL_SECONDS = 6 * 3600  # the whole-market sweep is expensive

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


@st.cache_data(ttl=SECTOR_CACHE_TTL_SECONDS, show_spinner=False)
def load_market():
    """One whole-universe sweep feeding BOTH engines.

    Sector percentile ranks are cross-sector and company percentile ranks are
    cross-industry, so both need the whole universe. Fetching it once and
    sharing it means the company model costs no extra requests.

    Returns (sector_scores, company_financials, universe_source, fetch_stats).
    """
    rows, source = uni.load_universe()
    if rows:
        universe_rows = [(r.ticker, r.sector) for r in rows]
    else:
        universe_rows = fallback_universe_rows()
        source.rows_kept = len(universe_rows)
        source.notes.append(
            f"Using the built-in fallback list of {len(universe_rows)} large-cap "
            "names so the engine can run. Sector scores are PROVISIONAL until "
            "the tradeable-asset workbook is supplied."
        )

    by_sector, financials, stats = build_universe_records(universe_rows)
    vti_prices = get_vti_history()
    stats["vti_points"] = len(vti_prices)
    scores = sect.build_all_sector_scores(by_sector, vti_prices)
    return scores, financials, source, stats


def load_sector_scores():
    """Sector-only view, kept so the sector section reads unchanged."""
    scores, _financials, source, stats = load_market()
    return scores, source, stats


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

def render_final_score(
    company: SecurityData, result: comp.CompanyScoreResult
) -> None:
    st.markdown("## FINAL SCORE")
    st.markdown(f"### {company.ticker}")
    st.markdown(f"**{company.name}**")
    st.caption(
        " / ".join(x for x in (company.sector, company.industry) if x)
        + f"  |  Price {fmt_price(company.price)}"
    )

    if not result.scored:
        st.error(
            "No company score could be produced. "
            + " ".join(result.notes)
        )
        return

    label = classify_score(result.final_score)
    color = BAND_COLORS[label]
    st.markdown(
        f"<div style='font-size:3.6rem;line-height:1.05;font-weight:700;"
        f"font-family:monospace;color:{color};'>{result.final_score:+.2f}</div>"
        f"<div style='font-size:1.3rem;letter-spacing:.18em;font-family:monospace;"
        f"color:{color};'>{label}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(score_gauge(result.final_score, label), use_container_width=True)

    left, middle, right = st.columns(3)
    left.metric("Company Score", f"{result.company_score:+.2f}", help="range -75..+75")
    middle.metric(
        "Sector Overlay",
        "unavailable" if result.sector_overlay is None
        else f"{result.sector_overlay:+.2f}",
        help="range -25..+25",
    )
    right.metric("Final Equity Score", f"{result.final_score:+.2f}", help="range -100..+100")
    st.caption(f"Benchmark Level: **{result.benchmark_level}**")
    for note in result.notes:
        st.caption(f"- {note}")


def render_valuation(detail: comp.ValuationDetail, factor: comp.FactorResult) -> None:
    st.markdown("### VALUATION")
    if detail.valuation_gap is not None:
        stance = "DISCOUNT (undervalued)" if detail.valuation_gap > 0 else "PREMIUM (overvalued)"
        st.markdown(f"**{fmt_signed_pct(detail.valuation_gap, 1)} {stance}** vs the benchmark cohort")

    st.table(
        {
            "Field": [
                "Current price", "Implied price", "Discount / Premium %",
                "Company EBITDA", "Benchmark EV/EBITDA", "Implied EV",
                "Debt", "Cash", "Implied equity value", "Shares outstanding",
                "Benchmark level", "Cohort companies used", "Valuation score",
            ],
            "Value": [
                fmt_price(detail.current_price),
                fmt_price(detail.implied_price),
                fmt_signed_pct(detail.valuation_gap, 2),
                fmt_money(detail.company_ebitda),
                fmt_x(detail.benchmark_ev_ebitda, 2),
                fmt_money(detail.implied_ev),
                fmt_money(detail.company_debt),
                fmt_money(detail.company_cash),
                fmt_money(detail.implied_equity_value),
                f"{detail.shares_outstanding:,.0f}"
                if detail.shares_outstanding else "unavailable",
                detail.benchmark_level or "unavailable",
                str(detail.n_valid_peers),
                fmt_score(factor.score, 1),
            ],
        }
    )
    for note in list(detail.notes) + list(factor.notes):
        st.caption(f"- {note}")

    st.markdown("**BENCHMARK PEERS (50th-75th percentile by EBITDA)**")
    if not detail.cohort:
        st.info("No benchmark cohort could be built.")
        return
    st.dataframe(
        {
            "Ticker": [r.ticker for r in detail.cohort],
            "Company": [r.name for r in detail.cohort],
            "EBITDA": [fmt_money(r.ebitda) for r in detail.cohort],
            "Market Cap": [fmt_money(r.market_cap) for r in detail.cohort],
            "Debt": [fmt_money(r.total_debt) for r in detail.cohort],
            "Cash": [fmt_money(r.cash) for r in detail.cohort],
            "Enterprise Value": [fmt_money(r.enterprise_value) for r in detail.cohort],
            "EV / EBITDA": [fmt_x(r.ev_ebitda, 2) for r in detail.cohort],
        },
        use_container_width=True,
        hide_index=True,
    )


def _component_rows(factor: comp.FactorResult, formatter) -> dict:
    names, raws, medians, percentiles, scores, weights, contributions = [], [], [], [], [], [], []
    for key in factor.components:
        part = factor.components[key]
        names.append(comp.COMPONENT_LABELS[key])
        raws.append(formatter(key, part.raw_value))
        medians.append(formatter(key, part.peer_median))
        percentiles.append(
            "n/a" if part.percentile is None else f"{part.percentile * 100:.0f}th"
        )
        scores.append(fmt_score(part.score, 1))
        weights.append(
            "dropped" if key not in factor.weights_used
            else f"{factor.weights_used[key] * 100:.1f}%"
        )
        contributions.append(
            "--" if key not in factor.contributions
            else f"{factor.contributions[key]:+.2f}"
        )
    return {
        "Component": names,
        "Company Value": raws,
        "Industry Median": medians,
        "Percentile": percentiles,
        "Score": scores,
        "Weight": weights,
        "Contribution": contributions,
    }


def render_component_factor(
    title: str, factor: comp.FactorResult, formatter
) -> None:
    st.markdown(f"### {title}")
    st.table(_component_rows(factor, formatter))
    st.markdown(f"**Combined {title.title()} Score: {fmt_score(factor.score, 1)}**")
    for note in factor.notes:
        st.caption(f"- {note}")
    for part in factor.components.values():
        for note in part.notes:
            st.caption(f"- {note}")


def _growth_fmt(key, value):
    return fmt_signed_pct(value, 2)


def _profit_fmt(key, value):
    return fmt_pct(value, 2)


def _debt_fmt(key, value):
    if value is None:
        return "unavailable"
    if key == "net_debt_ebitda":
        return f"{value:.2f}x"
    return fmt_pct(value, 2)


def render_contributions(result: comp.CompanyScoreResult) -> None:
    st.markdown("### SCORE CONTRIBUTIONS")
    names, scores, weights, contributions = [], [], [], []
    for key in comp.MAJOR_WEIGHTS:
        factor = result.factors.get(key)
        names.append(comp.FACTOR_LABELS[key])
        scores.append(fmt_score(factor.score if factor else None, 1))
        weights.append(
            "dropped" if key not in result.weights_used
            else f"{result.weights_used[key] * 100:.1f}%"
        )
        contributions.append(
            "--" if key not in result.contributions
            else f"{result.contributions[key]:+.2f}"
        )
    st.table(
        {
            "Factor": names,
            "Score": scores,
            "Weight": weights,
            "Contribution": contributions,
        }
    )
    st.code(
        f"Company Raw Score           {result.raw_score:+.2f}\n"
        f"x 0.75 scaling              {result.company_score:+.2f}   (clamped to -75..+75)\n"
        f"+ Sector Overlay            "
        f"{'unavailable' if result.sector_overlay is None else format(result.sector_overlay, '+.2f')}"
        f"   (clamped to -25..+25)\n"
        f"= Final Equity Score        {result.final_score:+.2f}   "
        f"{classify_score(result.final_score)}",
        language=None,
    )


def render_company_section(
    company: SecurityData, result: comp.CompanyScoreResult
) -> None:
    render_final_score(company, result)
    if not result.scored:
        return
    st.divider()
    render_valuation(result.valuation, result.factors["valuation"])
    render_component_factor("GROWTH", result.factors["growth"], _growth_fmt)
    render_component_factor("PROFITABILITY", result.factors["profitability"], _profit_fmt)
    render_component_factor("DEBT", result.factors["debt"], _debt_fmt)
    render_contributions(result)
    with st.expander("How is the company score calculated?"):
        st.markdown(COMPANY_MODEL_DETAILS)
        st.markdown(CLASSIFICATION_DETAILS)


# --------------------------------------------------------------------------
# Sector engine rendering
# --------------------------------------------------------------------------

def fmt_signed_pct(value, digits: int = 2) -> str:
    return "unavailable" if value is None else f"{value * 100:+.{digits}f}%"


def _factor_raw_display(name: str, value) -> str:
    """Every sector factor is a ratio, so all four render as percentages."""
    if value is None:
        return "unavailable"
    if name == "growth":
        return fmt_signed_pct(value)
    return fmt_pct(value, 2)


def render_sector_score(score: sect.SectorScore) -> None:
    st.markdown("### SECTOR SCORE")
    if not score.scored:
        st.warning(
            f"{score.sector} could not be scored. "
            + " ".join(score.notes)
        )
        return

    color = (
        BAND_COLORS[scoring.BULLISH] if score.raw_score >= 40
        else BAND_COLORS[scoring.SEMI_BULLISH] if score.raw_score >= 0
        else BAND_COLORS[scoring.SEMI_BEARISH] if score.raw_score >= -40
        else BAND_COLORS[scoring.BEARISH]
    )
    left, right = st.columns(2)
    left.markdown(
        f"<div style='font-size:.85rem;letter-spacing:.14em;color:#9aa4ad;'>"
        f"RAW SECTOR SCORE</div>"
        f"<div style='font-size:2.6rem;font-weight:700;font-family:monospace;"
        f"color:{color};line-height:1.1;'>{score.raw_score:+.2f}"
        f"<span style='font-size:1rem;color:#6b7681;'> / 100</span></div>",
        unsafe_allow_html=True,
    )
    right.markdown(
        f"<div style='font-size:.85rem;letter-spacing:.14em;color:#9aa4ad;'>"
        f"COMPANY OVERLAY</div>"
        f"<div style='font-size:2.6rem;font-weight:700;font-family:monospace;"
        f"color:{color};line-height:1.1;'>{score.overlay:+.2f}"
        f"<span style='font-size:1rem;color:#6b7681;'> / 25</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Confidence: {score.confidence} (informational only -- it does not "
        f"change the score). {score.n_eligible} eligible companies."
    )
    st.plotly_chart(
        score_gauge(score.raw_score, scoring.classify_score(score.raw_score)),
        use_container_width=True,
    )

    # --- factor table ---
    names, raws, norms, weights, contributions = [], [], [], [], []
    for key in sect.SECTOR_FACTOR_WEIGHTS:
        factor = score.factors.get(key)
        names.append(sect.FACTOR_LABELS[key])
        if factor is None or not factor.available:
            raws.append(_factor_raw_display(key, factor.raw_value if factor else None))
            norms.append("unavailable")
            weights.append("dropped")
            contributions.append("--")
            continue
        raws.append(_factor_raw_display(key, factor.raw_value))
        norms.append(f"{factor.score:+.1f}")
        weights.append(f"{score.weights_used.get(key, 0) * 100:.1f}%")
        contributions.append(f"{score.contributions.get(key, 0):+.2f}")

    st.table(
        {
            "Factor": names,
            "Raw Sector Value": raws,
            "Normalized Score": norms,
            "Final Weight": weights,
            "Contribution": contributions,
        }
    )
    for note in score.notes:
        st.caption(f"- {note}")


def render_growth_detail(score: sect.SectorScore) -> None:
    st.markdown("### GROWTH ACCELERATION DETAIL")
    growth = score.factors.get("growth")
    st.table(
        {
            "Measure": [
                "Recent sector return (63d)",
                "Previous sector return (prior 63d)",
                "Recent VTI return (63d)",
                "Previous VTI return (prior 63d)",
                "Sector acceleration",
                "VTI acceleration",
                "Acceleration differential (raw factor)",
            ],
            "Value": [
                fmt_signed_pct(score.sector_recent_return),
                fmt_signed_pct(score.sector_previous_return),
                fmt_signed_pct(score.vti_recent_return),
                fmt_signed_pct(score.vti_previous_return),
                fmt_signed_pct(score.sector_acceleration),
                fmt_signed_pct(score.vti_acceleration),
                fmt_signed_pct(growth.raw_value if growth else None),
            ],
        }
    )
    if growth is not None:
        for note in growth.notes:
            st.caption(f"- {note}")


def render_sector_coverage(score: sect.SectorScore) -> None:
    st.markdown("### SECTOR COVERAGE")
    labels, used, coverage = [], [], []
    for key in sect.SECTOR_FACTOR_WEIGHTS:
        factor = score.factors.get(key)
        labels.append(sect.FACTOR_LABELS[key])
        used.append(str(factor.n_used) if factor else "0")
        pct = factor.coverage if factor else None
        coverage.append("n/a" if pct is None else f"{pct * 100:.0f}%")
    st.table(
        {
            "Factor": ["Eligible companies"] + labels,
            "Companies used": [str(score.n_eligible)] + used,
            "Coverage": ["100%"] + coverage,
        }
    )


def render_all_sectors(scores: dict) -> None:
    st.markdown("### ALL SECTORS")
    rows = sect.sector_table_rows(scores)
    if not rows:
        st.info("No sector could be scored.")
        return
    st.dataframe(
        {
            "Sector": [r["sector"] for r in rows],
            "Growth Accel Raw": [fmt_signed_pct(r["growth_raw"]) for r in rows],
            "Growth": [fmt_score(r["growth_score"]) for r in rows],
            "Mean ROE": [fmt_pct(r["roe_raw"], 2) for r in rows],
            "ROE": [fmt_score(r["roe_score"]) for r in rows],
            "Mean ROA": [fmt_pct(r["roa_raw"], 2) for r in rows],
            "ROA": [fmt_score(r["roa_score"]) for r in rows],
            "Mean Debt/MktCap": [fmt_pct(r["debt_raw"], 2) for r in rows],
            "Debt": [fmt_score(r["debt_score"]) for r in rows],
            "Sector Raw": [fmt_score(r["raw_score"], 2) for r in rows],
            "Overlay": [fmt_score(r["overlay"], 2) for r in rows],
            "Confidence": [r["confidence"] for r in rows],
            "N": [str(r["n_eligible"]) for r in rows],
        },
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Sorted by Sector Raw Score, descending.")


def render_universe_provenance(source, stats: dict) -> None:
    with st.expander("Universe used for the sector model"):
        if source.is_fallback:
            st.warning(
                "No tradeable-asset workbook was found, so the built-in "
                "fallback list is in use. Sector scores are PROVISIONAL. Drop "
                "the workbook at `data/universe.xlsx` (or set "
                "$SHAFFERFINEVAL_UNIVERSE) and reload."
            )
        else:
            st.success(f"Universe workbook: {source.path}")
        st.table(
            {
                "Field": [
                    "Source", "Workbook path", "Sheet", "Rows read",
                    "Rows kept after pre-filter", "Symbols attempted",
                    "Resolved as operating equities",
                    "Dropped (not equity / no data)", "Truncated by cap",
                    "Sectors built", "VTI history points",
                ],
                "Value": [
                    source.kind, source.path or "--", source.sheet or "--",
                    str(source.rows_read), str(source.rows_kept),
                    str(stats.get("attempted", 0)), str(stats.get("resolved", 0)),
                    str(stats.get("dropped_not_equity_or_no_data", 0)),
                    str(stats.get("truncated", 0)), str(stats.get("sectors", 0)),
                    str(stats.get("vti_points", 0)),
                ],
            }
        )
        if source.excluded:
            st.caption("Pre-filter exclusions: " + ", ".join(
                f"{reason} x{count}" for reason, count in sorted(source.excluded.items())
            ))
        for note in source.notes:
            st.caption(f"- {note}")
        st.caption(
            f"Universe fetches are capped at {MAX_UNIVERSE_COMPANIES} symbols "
            "(MAX_UNIVERSE_COMPANIES in market_data.py)."
        )


def render_sector_section(company: SecurityData) -> None:
    """The sector block, shown above the company analysis."""
    try:
        with st.spinner("Building sector scores across the tradeable universe..."):
            scores, source, stats = load_sector_scores()
    except Exception as exc:
        st.error(f"Sector engine unavailable: {exc}")
        return

    st.markdown("## SECTOR")
    st.markdown(f"**{company.sector or 'unavailable'}**")

    score = sect.get_sector_score_for_ticker(company.ticker, company.sector, scores)
    if score is None:
        st.warning(
            f"No sector score for '{company.sector}'. The tradeable universe "
            "produced no scoreable companies in this sector."
        )
    else:
        render_sector_score(score)
        render_growth_detail(score)
        render_sector_coverage(score)

    with st.expander("How is the sector score calculated?"):
        st.markdown(sect.SECTOR_MODEL_DETAILS)

    render_all_sectors(scores)
    render_universe_provenance(source, stats)

    if score is not None and score.overlay is not None:
        st.info(
            f"Sector overlay for {company.ticker}: **{score.overlay:+.2f}**. "
            "This is exposed for the company model but is NOT yet applied -- "
            "the house score below is unchanged."
        )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def analyze(ticker: str):
    """Fetch -> score. Returns (company, CompanyScoreResult, SectorScore|None).

    All arithmetic lives in company_scoring / sector_scoring; this only wires
    the data layer to them.
    """
    company = load_security(ticker)
    sector_scores, financials, _source, _stats = load_market()

    sector_score = sect.get_sector_score_for_ticker(
        company.ticker, company.sector, sector_scores
    )
    overlay = sector_score.overlay if sector_score else None

    # The typed ticker may sit outside the tradeable universe; score it against
    # the universe anyway, using its own freshly fetched financials.
    target = next((c for c in financials if c.ticker == company.ticker), None)
    if target is None:
        record = md_fetch_company_record(company.ticker, company.sector)
        if record is None:
            raise TickerNotFound(
                f"{company.ticker} could not be resolved as an operating-company equity."
            )
        target = record[1]
        financials = list(financials) + [target]

    result = build_company_score(target, financials, sector_overlay=overlay)
    return company, result, sector_score


def render_analysis(ticker: str) -> None:
    # Resolve the symbol first, so a non-equity is reported as such rather
    # than failing later as an unscoreable company.
    try:
        with st.spinner(f"Resolving {ticker.upper()}..."):
            company = load_security(ticker)
    except TickerNotFound:
        st.error("Ticker not found.")
        return
    except Exception as exc:
        st.error(f"Could not complete the analysis: {exc}")
        return

    if company.instrument_type not in SUPPORTED_INSTRUMENTS:
        st.warning(
            f"{company.ticker} is a {company.instrument_type or 'unknown'} "
            f"instrument. ShafferFinEval scores equities only -- an "
            f"{company.instrument_type or 'unknown'} engine is not built yet."
        )
        return

    try:
        with st.spinner(
            f"Scoring {company.ticker} against its industry peers and the sector universe..."
        ):
            company, result, _sector_score = analyze(ticker)
    except TickerNotFound:
        st.error("Ticker not found.")
        return
    except Exception as exc:  # never crash the page on a data-layer surprise
        st.error(f"Could not complete the analysis: {exc}")
        return

    if not company.sector:
        st.warning(
            f"{company.ticker} resolved as an equity but Yahoo reports no sector, "
            "so no peer benchmark could be built."
        )

    render_company_section(company, result)
    st.divider()
    render_sector_section(company)
    st.divider()
    render_data_used(company)


def render_data_used(company: SecurityData) -> None:
    st.markdown("### DATA USED")
    rows = [
        ("Current price", fmt_price(company.price)),
        ("Market cap", fmt_money(company.market_cap)),
        ("Revenue (TTM)", fmt_money(company.revenue)),
        ("Total debt", fmt_money(company.total_debt)),
        ("Sector", company.sector or "unavailable"),
        ("Industry", company.industry or "unavailable"),
        ("Revenue as of", company.revenue_as_of or "unavailable"),
        ("Total debt as of", company.debt_as_of or "unavailable"),
        ("Data retrieval time", fmt_time(company.retrieved_at)),
        ("Source", "Yahoo Finance (public query endpoints)"),
    ]
    st.table({"Field": [r[0] for r in rows], "Value": [r[1] for r in rows]})
    if company.missing_fields:
        st.warning("Missing or unusable metrics: " + ", ".join(company.missing_fields))
    for note in company.notes:
        st.caption(f"- {note}")


def main() -> None:
    st.set_page_config(page_title="ShafferFinEval", layout="wide")
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

    st.title("SHAFFERFINEVAL")
    st.caption(
        "Quantitative scoring for equities. Sector engine (four factors, "
        "percentile-ranked across the market) plus the company house score."
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

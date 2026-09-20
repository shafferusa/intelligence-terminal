"""Page 3 -- Asset Detail.

Reads stored scores from the database and, for equities, re-renders the
existing company/sector engine breakdown. Nothing is recalculated with a
different formula -- the stored snapshot and the engines are the source of truth.
"""

from __future__ import annotations

import streamlit as st

import company_scoring as comp
import hedging as hedge
import routers
import scoring
import storage
from refresh import explain_score_change, score_delta
from views.common import (
    fmt_age, fmt_int, fmt_money, fmt_pct, fmt_price, fmt_score,
    fmt_signed_pct, fmt_x, score_badge,
)


def render(conn, symbol, catalog, market) -> None:
    asset = storage.get_asset(conn, symbol) if symbol else None
    if asset is None:
        st.info("Select an asset from the Market page.")
        return

    current = storage.get_current_score(conn, asset["asset_id"])
    history = storage.get_score_history(conn, asset["asset_id"])

    # ------------------------------------------------------------- header
    st.markdown(f"## {asset['symbol']}")
    st.markdown(f"**{asset['name'] or ''}**")
    bits = [asset["asset_class"]]
    if asset["subclass"] and asset["subclass"] != asset["asset_class"]:
        bits.append(asset["subclass"])
    if asset["sector"]:
        bits.append(asset["sector"])
    if asset["industry"]:
        bits.append(asset["industry"])
    st.caption(" / ".join(bits))

    if current is None:
        st.warning(
            "No score stored yet. Use **Refresh asset** in the sidebar, or run "
            "the daily refresh."
        )
        if asset["asset_class"] not in routers.SCORE_ENGINES:
            st.info(routers.MODEL_ROADMAP.get(
                asset["asset_class"], "No score model for this asset class yet."))
        return

    if current["model_status"] == routers.NOT_IMPLEMENTED:
        st.info(
            f"**Score model not yet implemented** for {asset['asset_class']}. "
            + routers.MODEL_ROADMAP.get(asset["asset_class"], "")
        )
        return

    previous = history[1] if len(history) > 1 else None
    delta = score_delta(current["shaffer_score"],
                        previous["shaffer_score"] if previous else None)

    left, right = st.columns([1, 2])
    with left:
        st.markdown("**SHAFFER SCORE**")
        score_badge(current["shaffer_score"], current["classification"])
    with right:
        cells = st.columns(4)
        cells[0].metric("Price", fmt_price(current["price"]))
        cells[1].metric("Company Score", fmt_score(current["company_score"]))
        cells[2].metric("Sector Overlay", fmt_score(current["sector_overlay"]))
        cells[3].metric("Score Δ", fmt_score(delta) if delta is not None else "--")
        st.caption(
            f"Shaffer Hedge: **{current['preferred_hedge'] or '--'}**  |  "
            f"Updated {fmt_age(current['updated_at'])}  |  "
            f"Model {current['model_version'] or '--'}"
        )

    tabs = st.tabs(["SCORE BREAKDOWN", "HEDGE", "WHAT CHANGED", "HISTORY"])

    # ------------------------------------------------- score breakdown
    with tabs[0]:
        detail = market.get("details", {}).get(asset["yahoo_symbol"])
        if detail is None:
            _render_stored_breakdown(current)
        else:
            _render_live_breakdown(detail)

    # ------------------------------------------------------------- hedge
    with tabs[1]:
        _render_hedge_tab(conn, asset, current, catalog)

    # ------------------------------------------------------ what changed
    with tabs[2]:
        _render_what_changed(conn, asset, current, previous, delta)

    # ----------------------------------------------------------- history
    with tabs[3]:
        if not history:
            st.info("No saved snapshots yet.")
        else:
            st.dataframe(
                {
                    "Date": [h["snapshot_date"] for h in history],
                    "Kind": [h["snapshot_kind"] for h in history],
                    "Price": [fmt_price(h["price"]) for h in history],
                    "Shaffer Score": [fmt_score(h["shaffer_score"]) for h in history],
                    "View": [h["classification"] or "--" for h in history],
                    "Company": [fmt_score(h["company_score"]) for h in history],
                    "Overlay": [fmt_score(h["sector_overlay"]) for h in history],
                    "Hedge": [h["preferred_hedge"] or "--" for h in history],
                    "Model": [h["model_version"] for h in history],
                },
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "Close snapshots are immutable: once written for a date they are "
                "never rewritten, so the score can be backtested honestly."
            )


def _render_stored_breakdown(current) -> None:
    factors = storage.loads(current["factor_scores_json"]) or {}
    raw = storage.loads(current["raw_inputs_json"]) or {}
    if not factors:
        st.info("No stored factor detail.")
        return
    st.markdown("### FACTOR SCORES (stored snapshot)")
    order = ["valuation", "growth", "profitability", "debt",
             "company_score", "sector_overlay"]
    keys = [k for k in order if k in factors] + [
        k for k in factors if k not in order]
    st.table({
        "Factor": keys,
        "Score": [fmt_score(factors.get(k)) for k in keys],
    })
    if raw:
        st.markdown("### RAW INPUTS (stored snapshot)")
        st.table({
            "Field": list(raw),
            "Value": [
                fmt_money(v) if isinstance(v, (int, float)) and abs(v) > 1e6
                else (f"{v:,.4f}" if isinstance(v, float) else str(v))
                for v in raw.values()
            ],
        })


def _render_live_breakdown(detail: comp.CompanyScoreResult) -> None:
    """The existing company + sector engine output, unchanged."""
    valuation = detail.valuation
    st.markdown("### VALUATION")
    st.table({
        "Field": ["Current price", "Implied share price", "Valuation gap",
                  "Company EBITDA", "Benchmark EV/EBITDA", "Implied EV",
                  "Debt", "Cash", "Implied equity value", "Shares outstanding",
                  "Benchmark level", "Cohort peers", "Valuation score"],
        "Value": [
            fmt_price(valuation.current_price), fmt_price(valuation.implied_price),
            fmt_signed_pct(valuation.valuation_gap), fmt_money(valuation.company_ebitda),
            fmt_x(valuation.benchmark_ev_ebitda), fmt_money(valuation.implied_ev),
            fmt_money(valuation.company_debt), fmt_money(valuation.company_cash),
            fmt_money(valuation.implied_equity_value),
            fmt_int(valuation.shares_outstanding),
            valuation.benchmark_level or "--", str(valuation.n_valid_peers),
            fmt_score(detail.factors["valuation"].score),
        ],
    })
    if valuation.cohort:
        with st.expander(f"Peer cohort ({len(valuation.cohort)})"):
            st.dataframe({
                "Ticker": [p.ticker for p in valuation.cohort],
                "Company": [p.name for p in valuation.cohort],
                "EBITDA": [fmt_money(p.ebitda) for p in valuation.cohort],
                "EV": [fmt_money(p.enterprise_value) for p in valuation.cohort],
                "EV/EBITDA": [fmt_x(p.ev_ebitda) for p in valuation.cohort],
            }, use_container_width=True, hide_index=True)

    for title, key, formatter in (
        ("GROWTH", "growth", lambda k, v: fmt_signed_pct(v)),
        ("PROFITABILITY", "profitability", lambda k, v: fmt_pct(v, 2)),
        ("DEBT", "debt", lambda k, v: (f"{v:.2f}x" if k == "net_debt_ebitda" and v is not None
                                       else fmt_pct(v, 2))),
    ):
        factor = detail.factors[key]
        st.markdown(f"### {title}")
        st.table({
            "Component": [comp.COMPONENT_LABELS[k] for k in factor.components],
            "Company": [formatter(k, p.raw_value) for k, p in factor.components.items()],
            "Industry median": [formatter(k, p.peer_median)
                                for k, p in factor.components.items()],
            "Percentile": [
                "--" if p.percentile is None else f"{p.percentile * 100:.0f}th"
                for p in factor.components.values()
            ],
            "Score": [fmt_score(p.score) for p in factor.components.values()],
        })
        st.markdown(f"**Combined {title.title()} Score: {fmt_score(factor.score)}**")

    st.markdown("### SECTOR")
    sector = getattr(detail, "_sector", None)
    st.table({
        "Field": ["Benchmark level", "Company score", "Sector overlay",
                  "Final Shaffer Score"],
        "Value": [detail.benchmark_level or "--", fmt_score(detail.company_score),
                  fmt_score(detail.sector_overlay), fmt_score(detail.final_score)],
    })


def _render_hedge_tab(conn, asset, current, catalog) -> None:
    from views.hedge_view import render_hedge_result

    if asset["asset_class"] not in routers.HEDGE_ENGINES:
        st.info(routers.hedge_engine_status(asset["asset_class"]))
        return
    if current["shaffer_score"] is None:
        st.info("No Shaffer Score stored, so no hedge can be produced.")
        return

    positions = [p for p in storage.list_positions(conn)
                 if p["asset_id"] == asset["asset_id"]]

    if positions:
        st.caption("You hold this asset, so the ticket below is sized to your position.")
        for row in positions:
            position = hedge.Position(
                ticker=row["symbol"], direction=row["direction"],
                shares=row["quantity"], price=current["price"],
                average_cost=row["average_cost"],
            )
            context = hedge.HedgeContext(
                equity_score=current["shaffer_score"], sector=asset["sector"],
                industry=asset["industry"],
            )
            result = routers.recommend_hedge_for(
                asset["asset_class"], position, context, catalog)
            if result:
                render_hedge_result(result, position)
        return

    st.info(
        "**No position held.** The strategies below are the preferred "
        "protective structures *if* you were exposed in that direction. "
        "No position-specific quantities are implied."
    )
    for direction, title in ((hedge.LONG, "PREFERRED STRATEGY FOR LONG EXPOSURE"),
                             (hedge.SHORT, "PREFERRED STRATEGY FOR SHORT EXPOSURE")):
        adverse = hedge.calculate_adverse_score(current["shaffer_score"], direction)
        st.markdown(f"### {title}")
        if adverse <= 0:
            st.success(
                f"A {direction} position would be aligned with a Shaffer Score of "
                f"{current['shaffer_score']:+.1f} — no hedge required."
            )
            continue
        # A nominal 100 shares only shapes the strike/ratio maths; quantities
        # are deliberately not surfaced for a position that does not exist.
        position = hedge.Position(ticker=asset["symbol"], direction=direction,
                                  shares=100.0, price=current["price"])
        context = hedge.HedgeContext(
            equity_score=current["shaffer_score"], sector=asset["sector"],
            industry=asset["industry"])
        result = routers.recommend_hedge_for(
            asset["asset_class"], position, context, catalog)
        if result and result.preferred:
            st.markdown(
                f"**{result.preferred.name}** — strategy score "
                f"{result.preferred.strategy_score:.0f}/100 "
                f"({result.preferred.confidence} confidence). "
                f"Recommended hedge {result.hedge_ratio:.0%} of exposure."
            )
            st.dataframe({
                "Rank": list(range(1, min(6, len(result.ranked)) + 1)),
                "Strategy": [e.name for e in result.ranked[:6]],
                "Score": [f"{e.strategy_score:.0f}" for e in result.ranked[:6]],
                "Confidence": [e.confidence for e in result.ranked[:6]],
            }, use_container_width=True, hide_index=True)
        else:
            st.caption("No eligible workbook strategy could be evaluated.")


def _render_what_changed(conn, asset, current, previous, delta) -> None:
    if previous is None:
        st.info("Only one snapshot so far — nothing to compare against yet.")
        return

    cells = st.columns(3)
    cells[0].metric("Previous", fmt_score(previous["shaffer_score"]),
                    help=f"Snapshot {previous['snapshot_date']}")
    cells[1].metric("Current", fmt_score(current["shaffer_score"]))
    cells[2].metric("Change", fmt_score(delta) if delta is not None else "--")

    drivers = explain_score_change(
        storage.loads(current["factor_scores_json"]),
        storage.loads(previous["factor_scores_json"]),
    )
    if drivers:
        st.markdown("### DRIVERS")
        st.table({
            "Factor": [d[0] for d in drivers],
            "Previous": [f"{d[1]:+.1f}" for d in drivers],
            "Current": [f"{d[2]:+.1f}" for d in drivers],
            "Change": [f"{d[3]:+.1f}" for d in drivers],
        })
    else:
        st.caption("No individual factor changed between these snapshots.")

    changes = storage.list_fundamental_changes(conn, asset["asset_id"])
    st.markdown("### FUNDAMENTAL CHANGES")
    if not changes:
        st.caption(
            "No fundamental change detected. Score moves so far are driven by "
            "price and by peer/sector repricing."
        )
        return
    for row in changes[:5]:
        fields = storage.loads(row["changed_fields_json"]) or {}
        st.markdown(f"**Detected {row['observed_at'][:10]}**")
        st.table({
            "Field": list(fields),
            "Previous": [fmt_money(v.get("previous")) for v in fields.values()],
            "New": [fmt_money(v.get("new")) for v in fields.values()],
        })
        st.caption("Reason: fundamental field changed in Yahoo data.")

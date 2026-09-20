"""Page 2 -- Watchlist / Positions.

Positions get exact hedge tickets from the equity hedge engine. Watchlist
entries without a position get the score and the preferred strategy only --
never a fabricated quantity.
"""

from __future__ import annotations

import streamlit as st

import hedging as hedge
import routers
import storage
import universe as uni
from refresh import score_delta
from views.common import (
    delta_text, fmt_age, fmt_money, fmt_price, fmt_score, open_asset,
)


def _position_risk(row) -> tuple[float, float]:
    """(adverse score, recommended hedge ratio) for one stored position."""
    adverse = hedge.calculate_adverse_score(row["shaffer_score"], row["direction"])
    return adverse, hedge.calculate_recommended_hedge_ratio(adverse)


def render(conn, catalog) -> None:
    st.markdown("## WATCHLIST & POSITIONS")
    tab_positions, tab_watch, tab_manage = st.tabs(
        ["POSITIONS", "WATCHLIST", "MANAGE"]
    )

    # ---------------------------------------------------------------- positions
    with tab_positions:
        positions = storage.list_positions(conn)
        if not positions:
            st.info("No positions stored. Add one in the MANAGE tab.")
        else:
            records = []
            for row in positions:
                adverse, ratio = _position_risk(row)
                price = row["price"]
                value = abs(row["quantity"]) * price if price else None
                records.append({
                    "row": row, "adverse": adverse, "ratio": ratio,
                    "value": value,
                    "delta": score_delta(row["shaffer_score"], row["prev_score"]),
                })
            # Most conflicted first: that is the point of the page.
            records.sort(key=lambda r: r["adverse"], reverse=True)

            st.dataframe(
                {
                    "Asset": [r["row"]["symbol"] for r in records],
                    "Dir": [r["row"]["direction"].upper() for r in records],
                    "Qty": [f"{r['row']['quantity']:,.0f}" for r in records],
                    "Market Value": [fmt_money(r["value"]) for r in records],
                    "Shaffer Score": [fmt_score(r["row"]["shaffer_score"]) for r in records],
                    "View": [r["row"]["classification"] or "--" for r in records],
                    "Score Δ": [delta_text(r["delta"]) for r in records],
                    "Adverse": [f"{r['adverse']:.0f}" for r in records],
                    "Hedge %": [f"{r['ratio']:.0%}" for r in records],
                    "Shaffer Hedge": [r["row"]["preferred_hedge"] or "--" for r in records],
                    "Urgency": [
                        "HIGH" if r["adverse"] >= 60 else
                        "MEDIUM" if r["adverse"] >= 30 else
                        "LOW" if r["adverse"] > 0 else "NONE"
                        for r in records
                    ],
                    "Updated": [fmt_age(r["row"]["updated_at"]) for r in records],
                },
                use_container_width=True, hide_index=True,
            )
            st.caption("Sorted by position conflict: the most adverse exposure first.")

            st.markdown("### EXACT HEDGE TICKET")
            labels = {
                f"{r['row']['symbol']} {r['row']['direction']} "
                f"{r['row']['quantity']:,.0f}": r for r in records
            }
            chosen = st.selectbox("Position", list(labels), label_visibility="collapsed")
            record = labels[chosen]
            _render_position_hedge(conn, record, catalog)

    # ---------------------------------------------------------------- watchlist
    with tab_watch:
        watched = storage.list_watchlist(conn)
        if not watched:
            st.info("Watchlist is empty. Add assets in the MANAGE tab.")
        else:
            st.dataframe(
                {
                    "Asset": [r["symbol"] for r in watched],
                    "Name": [(r["name"] or "")[:38] for r in watched],
                    "Type": [r["asset_class"] for r in watched],
                    "Sector": [r["sector"] or "--" for r in watched],
                    "Price": [fmt_price(r["price"]) for r in watched],
                    "Shaffer Score": [
                        fmt_score(r["shaffer_score"]) if r["shaffer_score"] is not None
                        else "model not implemented" for r in watched
                    ],
                    "View": [r["classification"] or "--" for r in watched],
                    "Score Δ": [
                        delta_text(score_delta(r["shaffer_score"], r["prev_score"]))
                        for r in watched
                    ],
                    "Shaffer Hedge": [r["preferred_hedge"] or "--" for r in watched],
                    "Updated": [fmt_age(r["updated_at"]) for r in watched],
                },
                use_container_width=True, hide_index=True,
            )
            picker = st.columns([3, 1])
            chosen = picker[0].selectbox(
                "Open", [r["symbol"] for r in watched], label_visibility="collapsed"
            )
            if picker[1].button("OPEN", use_container_width=True, key="watch_open"):
                open_asset(chosen)
                st.rerun()

    # ---------------------------------------------------------------- manage
    with tab_manage:
        st.markdown("### ADD A POSITION")
        with st.form("add_position"):
            cols = st.columns([2, 1, 1, 1, 2])
            symbol = cols[0].text_input("Symbol")
            direction = cols[1].selectbox("Direction", [hedge.LONG, hedge.SHORT])
            quantity = cols[2].number_input("Quantity", min_value=0.0, step=100.0, value=0.0)
            cost = cols[3].number_input("Avg cost", min_value=0.0, step=1.0, value=0.0)
            notes = cols[4].text_input("Notes")
            if st.form_submit_button("ADD POSITION"):
                asset = storage.get_asset(conn, symbol.strip().upper())
                if asset is None:
                    st.error(f"'{symbol}' is not in the tradeable universe.")
                elif quantity <= 0:
                    st.error("Quantity must be greater than zero.")
                else:
                    storage.add_position(
                        conn, asset["asset_id"], direction, quantity,
                        average_cost=cost or None, notes=notes or None,
                    )
                    st.success(f"Added {direction} {quantity:,.0f} {asset['symbol']}.")
                    st.rerun()

        positions = storage.list_positions(conn)
        if positions:
            cols = st.columns([3, 1])
            labels = {
                f"{p['symbol']} {p['direction']} {p['quantity']:,.0f} (#{p['position_id']})":
                p["position_id"] for p in positions
            }
            target = cols[0].selectbox("Remove position", list(labels))
            if cols[1].button("REMOVE", use_container_width=True):
                storage.remove_position(conn, labels[target])
                st.rerun()

        st.markdown("### WATCHLIST")
        cols = st.columns([3, 1, 1])
        watch_symbol = cols[0].text_input("Symbol", key="watch_add")
        if cols[1].button("ADD", use_container_width=True):
            asset = storage.get_asset(conn, watch_symbol.strip().upper())
            if asset is None:
                st.error(f"'{watch_symbol}' is not in the tradeable universe.")
            else:
                storage.add_to_watchlist(conn, asset["asset_id"])
                st.rerun()
        if cols[2].button("REMOVE", use_container_width=True, key="watch_rm"):
            asset = storage.get_asset(conn, watch_symbol.strip().upper())
            if asset is not None:
                storage.remove_from_watchlist(conn, asset["asset_id"])
                st.rerun()


def _render_position_hedge(conn, record, catalog) -> None:
    """Run the hedge engine live for one stored position."""
    row = record["row"]
    if row["asset_class"] not in routers.HEDGE_ENGINES:
        st.warning(
            f"No hedge engine for {row['asset_class']} yet. "
            "The equity engine is not applied to other asset classes."
        )
        return
    if row["shaffer_score"] is None:
        st.warning("No Shaffer Score stored for this asset yet; run a refresh.")
        return

    position = hedge.Position(
        ticker=row["symbol"], direction=row["direction"],
        shares=row["quantity"], price=row["price"],
        average_cost=row["average_cost"],
    )
    context = hedge.HedgeContext(
        equity_score=row["shaffer_score"], sector=row["sector"],
        industry=row["industry"], chain=st.session_state.get("chain_cache", {}).get(row["symbol"]),
    )
    result = routers.recommend_hedge_for(row["asset_class"], position, context, catalog)
    if result is None:
        st.warning("No hedge engine available.")
        return

    from views.hedge_view import render_hedge_result
    render_hedge_result(result, position)

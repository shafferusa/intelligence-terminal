"""Page 1 -- Market / Asset Universe.

Reads entirely from the local database. No market-data fetch happens here;
scoring is the refresh job's responsibility, which is what keeps this page fast
with the whole workbook universe loaded.
"""

from __future__ import annotations

import streamlit as st

import storage
import universe as uni
from refresh import score_delta
from views.common import (
    delta_text, fmt_age, fmt_price, fmt_score, open_asset,
)

ALL = "All"
SORTS = {
    "Highest Shaffer Score": ("shaffer_score", True),
    "Lowest Shaffer Score": ("shaffer_score", False),
    "Highest predicted 12M return": ("predicted_return", True),
    "Lowest predicted 12M return": ("predicted_return", False),
    "Largest positive score change": ("delta", True),
    "Largest negative score change": ("delta", False),
    "Price": ("price", True),
    "Asset class": ("asset_class", False),
    "Recently updated": ("updated_at", True),
    "Symbol": ("symbol", False),
}


def render(conn) -> None:
    st.markdown("## MARKET DB")

    rows = storage.market_rows(conn)
    if not rows:
        st.warning(
            "No assets loaded. Run a refresh from the sidebar to import the "
            "workbook universe."
        )
        return

    classes = [ALL] + sorted({r["asset_class"] for r in rows})
    top = st.columns([3, 2, 2, 1.4])
    query = top[0].text_input(
        "Search", key="market_search", placeholder="NVDA, NVIDIA, Semiconductors, Treasury, Oil...",
        label_visibility="collapsed",
    )
    asset_class = top[1].selectbox("Asset class", classes, label_visibility="collapsed")
    sort_choice = top[2].selectbox("Sort", list(SORTS), label_visibility="collapsed")
    scored_only = top[3].checkbox("Scored only", value=False)

    records = []
    for row in rows:
        delta = score_delta(row["shaffer_score"], row["prev_score"])
        records.append({
            "symbol": row["symbol"], "name": row["name"] or "",
            "asset_class": row["asset_class"], "subclass": row["subclass"] or "",
            "sector": row["sector"] or "", "industry": row["industry"] or "",
            "price": row["price"], "shaffer_score": row["shaffer_score"],
            "predicted_return": row["predicted_12m_return_pct"],
            "predicted_price": row["predicted_12m_price"],
            "classification": row["classification"],
            "preferred_hedge": row["preferred_hedge"],
            "delta": delta, "updated_at": row["updated_at"],
            "model_status": row["model_status"],
            "watch": bool(row["on_watchlist"]), "positions": row["position_count"],
        })

    if query:
        needle = query.lower()
        records = [
            r for r in records
            if needle in " ".join(str(r[k]).lower() for k in
                                  ("symbol", "name", "asset_class", "subclass",
                                   "sector", "industry"))
        ]
    if asset_class != ALL:
        records = [r for r in records if r["asset_class"] == asset_class]
    if scored_only:
        records = [r for r in records if r["shaffer_score"] is not None]

    key, reverse = SORTS[sort_choice]
    def sort_key(record):
        value = record.get(key)
        if value is None:
            return (1, 0) if reverse else (1, 0)
        if isinstance(value, str):
            return (0, value)
        return (0, -value if reverse else value)
    records.sort(key=sort_key)

    st.caption(
        "Click any column header to sort. "
        f"{len(records):,} of {len(rows):,} assets"
        + (f" matching '{query}'" if query else "")
        + f". {sum(1 for r in records if r['shaffer_score'] is not None):,} scored."
    )

    st.dataframe(
        {
            "Asset": [r["symbol"] for r in records],
            "Name": [r["name"][:38] for r in records],
            "Type": [r["asset_class"] for r in records],
            "Sector": [r["sector"] for r in records],
            "Price": [fmt_price(r["price"]) for r in records],
            "Shaffer Score": [
                fmt_score(r["shaffer_score"]) if r["shaffer_score"] is not None
                else "model not implemented" for r in records
            ],
            "Shaffer 12M Return": [
                "--" if r["predicted_return"] is None
                else f"{r['predicted_return']:+.1f}%" for r in records
            ],
            "Shaffer 12M Price": [fmt_price(r["predicted_price"]) for r in records],
            "View": [r["classification"] or "--" for r in records],
            "Shaffer Hedge": [r["preferred_hedge"] or "--" for r in records],
            "Score Δ": [delta_text(r["delta"]) for r in records],
            "Updated": [fmt_age(r["updated_at"]) for r in records],
            "W": ["*" if r["watch"] else "" for r in records],
            "Pos": [r["positions"] or "" for r in records],
        },
        use_container_width=True, hide_index=True, height=520,
    )

    st.markdown("### OPEN AN ASSET")
    picker = st.columns([3, 1])
    options = [r["symbol"] for r in records[:400]]
    if options:
        chosen = picker[0].selectbox(
            "Asset", options, label_visibility="collapsed",
            format_func=lambda s: next(
                (f"{r['symbol']} — {r['name'][:44]}" for r in records if r["symbol"] == s), s
            ),
        )
        if picker[1].button("OPEN", use_container_width=True):
            open_asset(chosen)
            st.rerun()
    else:
        st.info("No assets match the current filters.")

    unscored = {}
    for r in records:
        if r["shaffer_score"] is None and r["asset_class"] not in ("Equity",):
            unscored[r["asset_class"]] = unscored.get(r["asset_class"], 0) + 1
    if unscored:
        st.caption(
            "Awaiting a score model: "
            + ", ".join(f"{k} ({v:,})" for k, v in sorted(unscored.items()))
        )

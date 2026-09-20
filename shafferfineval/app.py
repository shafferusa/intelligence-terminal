"""
ShafferFinEval -- personal multi-asset research and risk terminal.

Run with:   streamlit run app.py

Three views: Market universe, Watchlist/Positions, Asset detail. Everything the
UI shows is read from the local SQLite database; market-data retrieval and
scoring happen in the refresh job (`refresh.py`), so pages stay fast with the
whole workbook universe loaded.

This file is the shell: navigation, search, and the refresh controls. All
calculation lives in the scoring and hedging engines.
"""

from __future__ import annotations

import datetime as _dt

import streamlit as st

import routers
import storage
import strategy_catalog as sc
import universe as uni
from refresh import SCHEDULES, next_scheduled_run, refresh_daily_scores
from views import asset_detail_page, market_page, watchlist_page
from views.common import TERMINAL_CSS, open_asset

VIEWS = ["Market", "Watchlist & Positions", "Asset Detail"]


@st.cache_resource
def get_connection():
    """One database connection for the session."""
    return storage.init_db()


@st.cache_resource
def get_catalog():
    """The approved workbook strategy catalog."""
    return sc.load_strategy_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def bootstrap_universe() -> dict:
    """Load the workbook into the database once per hour."""
    conn = get_connection()
    assets, source = uni.load_multi_asset_universe()
    if assets:
        storage.upsert_assets(conn, assets)
    return {
        "count": len(assets),
        "kind": source.kind,
        "path": source.path,
        "notes": source.notes,
    }


def sidebar(conn, catalog, catalog_notes) -> str:
    st.sidebar.markdown("# SHAFFERFINEVAL")
    st.sidebar.caption("Shaffer Score · Shaffer Hedge")

    view = st.sidebar.radio("View", VIEWS, key="view", label_visibility="collapsed")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**GLOBAL SEARCH**")
    query = st.sidebar.text_input(
        "Search", key="global_search", label_visibility="collapsed",
        placeholder="ticker, name, sector...",
    )
    if query:
        hits = storage.search_assets(conn, query, limit=12)
        if not hits:
            st.sidebar.caption("No match.")
        for row in hits:
            label = f"{row['symbol']} · {(row['name'] or '')[:22]}"
            if st.sidebar.button(label, key=f"go_{row['asset_id']}",
                                 use_container_width=True):
                open_asset(row["symbol"])
                st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("**REFRESH**")
    last = storage.last_refresh_run(conn)
    if last:
        st.sidebar.caption(
            f"Last run {(last['finished_at'] or last['started_at'] or '')[:16]} · "
            f"{last['succeeded'] or 0} scored, {last['failed'] or 0} failed "
            f"({last['snapshot_kind']})"
        )
    else:
        st.sidebar.caption("No refresh has run yet.")

    kind = st.sidebar.radio(
        "Snapshot", [storage.INTRADAY, storage.CLOSE],
        format_func=lambda k: ("Intraday (replaceable)" if k == storage.INTRADAY
                               else "Official close (immutable)"),
        key="snapshot_kind",
    )

    symbol = st.sidebar.text_input("Refresh one asset", key="refresh_one",
                                   placeholder="NVDA")
    if st.sidebar.button("REFRESH ASSET", use_container_width=True):
        _run_refresh(conn, catalog, [symbol.strip().upper()] if symbol.strip() else None, kind)
    if st.sidebar.button("REFRESH WATCHLIST", use_container_width=True):
        symbols = [r["yahoo_symbol"] for r in storage.list_watchlist(conn)
                   if r["yahoo_symbol"]]
        positions = [r["yahoo_symbol"] for r in storage.list_positions(conn)
                     if r["yahoo_symbol"]]
        targets = sorted(set(symbols) | set(positions))
        if targets:
            _run_refresh(conn, catalog, targets, kind)
        else:
            st.sidebar.warning("Watchlist and positions are both empty.")
    if st.sidebar.button("REFRESH ALL SUPPORTED", use_container_width=True):
        _run_refresh(conn, catalog, None, kind)

    st.sidebar.markdown("---")
    with st.sidebar.expander("Schedule"):
        for asset_class in SCHEDULES:
            st.caption(f"**{asset_class}** — {next_scheduled_run(asset_class)}")
        st.caption(
            "Run `python3 daily_job.py` from cron/launchd at the equity time "
            "to write the official close snapshot."
        )
    with st.sidebar.expander("Universe & catalog"):
        info = bootstrap_universe()
        st.caption(f"{info['count']:,} assets from the workbook ({info['kind']}).")
        if info["path"]:
            st.caption(info["path"])
        st.caption(f"{len(catalog)} approved strategies.")
        for note in catalog_notes:
            st.caption(note[:300])
        st.caption(
            "Score models live for: "
            + ", ".join(sorted(routers.SCORE_ENGINES)) + "."
        )

    return view


def _run_refresh(conn, catalog, symbols, kind) -> None:
    """Manual refresh with progress, then rerun so every view updates."""
    label = ", ".join(symbols) if symbols else "all supported assets"
    bar = st.sidebar.progress(0.0, text=f"Refreshing {label}...")

    def progress(symbol, index, total):
        bar.progress(index / max(total, 1), text=f"{symbol} ({index}/{total})")

    summary = refresh_daily_scores(
        conn, symbols=symbols, snapshot_kind=kind, progress=progress, catalog=catalog
    )
    bar.empty()
    message = (
        f"{summary.scored} scored, {summary.failed} failed, "
        f"{summary.snapshots_written} snapshots written"
        + (f", {summary.snapshots_existing} already existed"
           if summary.snapshots_existing else "")
        + (f", {summary.fundamental_changes} fundamental changes"
           if summary.fundamental_changes else "")
    )
    if summary.failed or summary.errors:
        st.sidebar.warning(message)
        for error in summary.errors[:3]:
            st.sidebar.caption(error[:160])
    else:
        st.sidebar.success(message)
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="ShafferFinEval", layout="wide")
    st.markdown(TERMINAL_CSS, unsafe_allow_html=True)

    conn = get_connection()
    catalog, catalog_notes = get_catalog()
    bootstrap_universe()

    view = sidebar(conn, catalog, catalog_notes)

    if view == "Market":
        market_page.render(conn)
    elif view == "Watchlist & Positions":
        watchlist_page.render(conn, catalog)
    else:
        asset_detail_page.render(
            conn, st.session_state.get("selected_asset"), catalog, {}
        )


if __name__ == "__main__":
    main()

"""Shared renderer for a HedgeResult -- used by positions and asset detail."""

from __future__ import annotations

import streamlit as st

import hedging as hedge
from views.common import (
    band_color, fmt_int, fmt_money, fmt_pct, fmt_price, fmt_score,
    fmt_signed_pct, fmt_x,
)

FACTOR_LABELS = {
    "effectiveness": "Effectiveness", "cost": "Cost", "upside": "Upside retained",
    "liquidity": "Liquidity", "capital": "Capital eff.", "tenor": "Tenor fit",
    "basis": "Basis quality",
}


def render_hedge_result(result: hedge.HedgeResult, position: hedge.Position) -> None:
    """The full hedge terminal for one position."""
    head = st.columns(4)
    head[0].metric("Shaffer Score", fmt_score(result.equity_score))
    head[1].metric("Adverse Score", f"{result.adverse_score:.0f}")
    head[2].metric("Recommended Hedge", f"{result.hedge_ratio:.0%}")
    head[3].metric("Position Value", fmt_money(result.position_market_value))

    if not result.hedge_required:
        st.success(
            f"**NO FUNDAMENTAL HEDGE REQUIRED.** Model-required hedge: 0%. "
            + " ".join(result.notes)
        )
        st.caption(result.explanation)
        st.caption(
            "Optional insurance structures can still be inspected, but the "
            "model is not asking for protection."
        )
        return

    st.markdown(
        f"Target protected shares: **{result.hedged_shares:,.0f}**  |  "
        f"Target hedge notional: **{fmt_money(result.hedge_notional)}**"
    )

    if not result.ranked:
        st.error(
            "No approved workbook strategy could be evaluated with the data "
            "available. " + " ".join(result.notes)
        )
        return

    preferred = result.preferred
    st.markdown("### PREFERRED STRATEGY")
    color = band_color(preferred.strategy_score / 2 if preferred.strategy_score else 0)
    st.markdown(
        f"<div style='font-size:1.5rem;font-weight:700;font-family:monospace;'>"
        f"{preferred.name.upper()}</div>"
        f"<div style='font-size:2.1rem;font-weight:700;font-family:monospace;"
        f"color:{color};'>{preferred.strategy_score:.0f}<span style='font-size:1rem;"
        f"color:#6b7681;'> / 100</span></div>"
        f"<div style='color:#9aa4ad;'>Confidence: {preferred.confidence}</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**SUGGESTED TRADE**")
    _render_legs(preferred, position)

    cover = st.columns(3)
    cover[0].metric("Protection-floor coverage", fmt_int(preferred.protected_shares),
                    help="Shares actually protected after contract rounding")
    cover[1].metric("Initial delta coverage",
                    fmt_int(preferred.delta_equivalent_shares),
                    help="Contracts x 100 x |delta| -- not the same as the protection floor")
    cover[2].metric("Effective hedge ratio",
                    fmt_pct(preferred.effective_hedge_ratio, 1))
    if preferred.total_premium is not None:
        st.caption(f"Estimated net premium: {fmt_money(preferred.total_premium)}")
    if preferred.missing:
        st.warning("Unavailable data: " + ", ".join(sorted(set(preferred.missing))))
    for note in preferred.notes:
        st.caption(f"- {note}")

    st.markdown("### STRATEGY RANKING")
    st.dataframe(
        {
            "Rank": list(range(1, len(result.ranked) + 1)),
            "Strategy": [e.name for e in result.ranked],
            "Score": [f"{e.strategy_score:.0f}" for e in result.ranked],
            **{
                label: [fmt_score(e.factors[key].score, 0)
                        if e.factors.get(key) and e.factors[key].available else "--"
                        for e in result.ranked]
                for key, label in FACTOR_LABELS.items()
            },
            "Conf": [e.confidence for e in result.ranked],
            "Trade": [e.summary[:70] for e in result.ranked],
        },
        use_container_width=True, hide_index=True,
    )

    st.markdown("### ALTERNATIVE STRATEGY DETAIL")
    for evaluation in result.ranked[1:]:
        with st.expander(
            f"{evaluation.name} — {evaluation.strategy_score:.0f}/100 "
            f"({evaluation.confidence})"
        ):
            _render_legs(evaluation, position)
            st.table({
                "Factor": [FACTOR_LABELS[k] for k in FACTOR_LABELS],
                "Score": [
                    fmt_score(evaluation.factors[k].score, 0)
                    if evaluation.factors.get(k) and evaluation.factors[k].available
                    else "unavailable" for k in FACTOR_LABELS
                ],
                "Basis": [
                    evaluation.factors[k].note if evaluation.factors.get(k) else ""
                    for k in FACTOR_LABELS
                ],
            })

    st.markdown("### WHY THIS STRATEGY")
    st.write(result.explanation)

    if result.excluded:
        with st.expander(f"Workbook strategies filtered out ({len(result.excluded)})"):
            st.dataframe(
                {
                    "Strategy": [k for k, _ in result.excluded],
                    "Reason": [r for _, r in result.excluded],
                },
                use_container_width=True, hide_index=True,
            )

    with st.expander("How is the hedge calculated?"):
        st.markdown(hedge.HEDGE_MODEL_DETAILS)


def _render_legs(evaluation, position) -> None:
    """Every leg of one ticket, typed by instrument."""
    option_legs = [l for l in evaluation.legs if l.instrument == "option"]
    other_legs = [l for l in evaluation.legs if l.instrument != "option"]

    if option_legs:
        st.table({
            "Action": [l.action for l in option_legs],
            "Right": ["PUT" if l.right == "P" else "CALL" for l in option_legs],
            "Contracts": [fmt_int(l.contracts) for l in option_legs],
            "Target strike": [fmt_price(l.target_strike) for l in option_legs],
            "Listed strike": [
                fmt_price(l.strike) if l.strike is not None else "no chain"
                for l in option_legs
            ],
            "vs spot": [fmt_signed_pct(l.strike_pct_from_spot, 1) for l in option_legs],
            "Expiry": [l.expiry.isoformat() if l.expiry else "--" for l in option_legs],
            "DTE": [fmt_int(l.dte) for l in option_legs],
            "Premium": [fmt_price(l.premium) for l in option_legs],
            "Delta": [fmt_x(l.delta, 3) if l.delta is not None else "--" for l in option_legs],
            "IV": [fmt_pct(l.implied_volatility, 1) for l in option_legs],
            "OI": [fmt_int(l.open_interest) for l in option_legs],
        })

    for leg in other_legs:
        if leg.instrument == "trs":
            st.markdown(
                f"**{leg.description}** — notional {fmt_money(leg.notional)}, "
                f"equivalent shares {fmt_int(leg.shares)}"
            )
        elif leg.instrument == "stock":
            st.markdown(
                f"**{leg.action} {fmt_int(leg.shares)} {position.ticker} shares** "
                f"({fmt_money(leg.notional)})"
            )
        elif leg.instrument == "future":
            st.markdown(
                f"**{leg.action} {fmt_int(leg.contracts)} contracts** — "
                f"{leg.description}; beta-adjusted target "
                f"{fmt_money(leg.notional)}"
            )
        if leg.missing:
            st.caption("Missing: " + ", ".join(leg.missing))

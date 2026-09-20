"""Shared formatting and small widgets for the terminal views."""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import streamlit as st

import scoring

BAND_COLORS = {
    scoring.BEARISH: "#c0392b",
    scoring.SEMI_BEARISH: "#c47a2c",
    scoring.SEMI_BULLISH: "#7aa63c",
    scoring.BULLISH: "#2e9e5b",
}
NEUTRAL = "#6b7681"


def band_color(score) -> str:
    label = scoring.classify_score(score)
    return BAND_COLORS.get(label, NEUTRAL)


def fmt_score(value, digits: int = 1) -> str:
    return "--" if value is None else f"{value:+.{digits}f}"


def fmt_price(value) -> str:
    return "--" if value is None else f"${value:,.2f}"


def fmt_money(value) -> str:
    if value is None:
        return "--"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= cutoff:
            return f"${value / cutoff:,.2f}{suffix}"
    return f"${value:,.2f}"


def fmt_pct(value, digits: int = 1) -> str:
    return "--" if value is None else f"{value * 100:.{digits}f}%"


def fmt_signed_pct(value, digits: int = 2) -> str:
    return "--" if value is None else f"{value * 100:+.{digits}f}%"


def fmt_x(value, digits: int = 2) -> str:
    return "--" if value is None else f"{value:.{digits}f}x"


def fmt_int(value) -> str:
    return "--" if value is None else f"{value:,.0f}"


def fmt_age(timestamp: Optional[str]) -> str:
    """'Today', 'Yesterday', or a date, from an ISO timestamp."""
    if not timestamp:
        return "never"
    try:
        moment = _dt.datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return str(timestamp)[:10]
    today = _dt.datetime.now(_dt.timezone.utc).date()
    day = moment.date()
    if day == today:
        return f"Today {moment.strftime('%H:%M')}"
    if (today - day).days == 1:
        return "Yesterday"
    return day.isoformat()


def score_badge(score, label: Optional[str] = None, size: str = "3.2rem") -> None:
    """Big coloured score with its classification underneath."""
    if score is None:
        st.markdown(
            "<div style='font-size:1.4rem;font-family:monospace;color:#6b7681;'>"
            "Score model not yet implemented</div>",
            unsafe_allow_html=True,
        )
        return
    text = label or scoring.classify_score(score)
    color = band_color(score)
    st.markdown(
        f"<div style='font-size:{size};line-height:1.05;font-weight:700;"
        f"font-family:monospace;color:{color};'>{score:+.1f}</div>"
        f"<div style='font-size:1.05rem;letter-spacing:.16em;font-family:monospace;"
        f"color:{color};'>{text}</div>",
        unsafe_allow_html=True,
    )


def delta_text(delta) -> str:
    return "--" if delta is None else f"{delta:+.1f}"


def open_asset(symbol: str) -> None:
    """Open the asset detail drill-down for a symbol."""
    st.session_state["selected_asset"] = symbol
    st.session_state["show_detail"] = True


TERMINAL_CSS = """
<style>
  html, body, [class*="css"] { font-family: ui-monospace, SFMono-Regular,
      "SF Mono", Menlo, Consolas, monospace; }
  .stMetric { border: 1px solid #2c333a; border-radius: 4px; padding: 8px 10px; }
  h1 { letter-spacing: .20em; font-size: 1.7rem !important; }
  h2 { letter-spacing: .10em; font-size: 1.25rem !important; }
  h3 { letter-spacing: .12em; color: #9aa4ad; font-size: .95rem !important;
       margin-top: 1.3rem; }
  section[data-testid="stSidebar"] { border-right: 1px solid #2c333a; }
  div[data-testid="stDataFrame"] { font-size: .86rem; }
</style>
"""

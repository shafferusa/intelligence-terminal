"""Cash and short-proceeds interest: how idle cash and the proceeds of short sales are remunerated.

Settings per portfolio (kv `cash_settings:{portfolio}`), defaults keep the conservative retail behaviour:
    cash_mode   none | bill | broker | custom      idle cash earns 0, the 3-month bill, the bill − broker_spread, or custom_rate
    short_mode  none | partial | institutional     short proceeds earn 0, short_share × the bill, or the bill (a rebate;
                                                   the borrow fee is charged separately)
The bill is FRED DGS3MO as known on each date. Interest accrues every session on the previous close's balances and is
booked as cash, so it flows into NAV, P&L, TWR and IRR; the long/short net scores use the same rates."""
from __future__ import annotations

from typing import Optional, Tuple

DEFAULTS = {"cash_mode": "none", "broker_spread": 0.005, "custom_rate": 0.0, "short_mode": "none", "short_share": 0.5}
CASH_MODES = ("none", "bill", "broker", "custom")
SHORT_MODES = ("none", "partial", "institutional")


def load(store, portfolio_id: str = "main") -> dict:
    try:
        v = store.kv_get(f"cash_settings:{portfolio_id}") or {}
    except Exception:
        v = {}
    return {**DEFAULTS, **{k: v[k] for k in DEFAULTS if k in v}}


def validate(s: dict) -> dict:
    out = {**DEFAULTS, **{k: s[k] for k in DEFAULTS if k in s}}
    if out["cash_mode"] not in CASH_MODES:
        raise ValueError(f"cash_mode must be one of {', '.join(CASH_MODES)}")
    if out["short_mode"] not in SHORT_MODES:
        raise ValueError(f"short_mode must be one of {', '.join(SHORT_MODES)}")
    for k, lo, hi in (("broker_spread", 0.0, 0.10), ("custom_rate", -0.05, 0.25), ("short_share", 0.0, 1.0)):
        out[k] = float(out[k])
        if not lo <= out[k] <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
    return out


def save(store, portfolio_id: str, settings: dict) -> dict:
    s = validate(settings)
    store.kv_set(f"cash_settings:{portfolio_id}", s)
    return s


def rates(settings: dict, bill: Optional[float]) -> Tuple[float, float]:
    """(idle-cash rate, short-proceeds rate), annual, for a bill rate (decimal) on the same date."""
    b = bill or 0.0
    m = settings.get("cash_mode", "none")
    rc = 0.0 if m == "none" else b if m == "bill" else max(0.0, b - settings.get("broker_spread", 0.0)) if m == "broker" else settings.get("custom_rate", 0.0)
    sm = settings.get("short_mode", "none")
    rs = 0.0 if sm == "none" else settings.get("short_share", 0.5) * b if sm == "partial" else b
    return rc, rs


def describe(settings: dict) -> str:
    m = settings["cash_mode"]
    c = {"none": "idle cash earns nothing", "bill": "idle cash earns the 3-month bill",
         "broker": f"idle cash earns the bill − {settings['broker_spread']:.2%}", "custom": f"idle cash earns {settings['custom_rate']:.2%}"}[m]
    s = {"none": "short proceeds earn nothing", "partial": f"short proceeds earn {settings['short_share']:.0%} of the bill",
         "institutional": "short proceeds earn the bill (rebate; borrow charged separately)"}[settings["short_mode"]]
    return f"{c}; {s}"

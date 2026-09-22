"""Path statistics for Shaffer Hedge research.

`pit_label` records where a position ENDED. That is the right target for Shaffer
Score, which ranks securities by forward return. It is the wrong target for
Shaffer Hedge, and no amount of machine learning recovers the difference:

    a stock that ends flat having fallen 40% mid-window NEEDED a hedge
    a stock that ends flat and never moved did NOT
    both have forward_return = 0.0

A hedge is bought against the PATH. So the question "which tool, what notional,
what tenor" is answerable only from what the price did between the anchor and
the horizon -- how far down it went, how fast it got there, and how long it
stayed. This module records that, once, alongside the label.

THE BARRIER LADDER IS THE POINT. A protective put struck K% below spot pays if
and only if the path breaches K before expiry, and the tenor is right if and
only if the breach happens before the option dies. So instead of leaving a
future researcher to re-derive that from a price series, the breach itself is
stored: for a ladder of strikes, did the path touch it, and on which session.
That turns "would a 6%-OTM put spread at 90 DTE have paid on this position"
from a backtest into a lookup -- which is what makes counterfactual hedge
research affordable across millions of position-days.

WHAT THIS MODULE DOES NOT DO. It does not price anything. Real option prices
before the archive start (2026-09-20) do not exist at any price of effort, so a
payoff computed here would be a model, not a market. The barrier facts are
observations; turning them into a P&L requires a pricing assumption that belongs
in a versioned hedge model, labelled as such.

Point-in-time status: these are OUTCOME statistics, measured after the fact, and
they carry the same contract as `pit_label` -- they describe the future of an
as-of date and may never be read as a feature at or before that date. The
availability rule binds the FEATURES; the outcome is allowed to know the future,
because knowing the future is what an outcome is for.
"""

from __future__ import annotations

import datetime as _dt
import math
import sqlite3
from typing import Any, Iterable, Optional, Sequence

import pit_store

#: Strike ladder, as fractional moves from the anchor price. Negative entries are
#: put strikes (the downside a long position hedges); positive ones are call
#: strikes (the upside a short position hedges, and the cost side of a collar).
#: Chosen to bracket the strikes the production hedge engine actually recommends
#: -- hedging.py's heuristics sit around 6% and 21% out of the money -- with
#: enough resolution either side to let research move them.
BARRIER_LADDER: tuple[float, ...] = (
    -0.03, -0.05, -0.06, -0.10, -0.15, -0.20, -0.21, -0.30, -0.50,
    +0.05, +0.10, +0.20, +0.30,
)

#: Trading days per horizon, matching pit_label's convention. Horizons are
#: session counts on pit_calendar, never calendar offsets: unscheduled closures
#: are real (Sandy closed 2012-10-29/30) and session counts run 250-253 a year.
HORIZON_SESSIONS: dict[str, int] = {
    "1D": 1, "5D": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252,
}

PATH_MODEL_VERSION = "pit_path_v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pit_path (
    listing_id            INTEGER NOT NULL,
    as_of_date            TEXT NOT NULL,
    horizon               TEXT NOT NULL,
    anchor_date           TEXT NOT NULL,
    anchor_price          REAL NOT NULL,
    end_date              TEXT,
    n_sessions            INTEGER NOT NULL,

    -- excursions: the worst and best the position ever was, and when
    max_adverse_excursion    REAL,     -- most negative (close/anchor - 1) in the window
    mae_date                 TEXT,
    sessions_to_mae          INTEGER,
    max_favourable_excursion REAL,
    mfe_date                 TEXT,
    sessions_to_mfe          INTEGER,

    -- drawdown measured along the path, not from the anchor
    max_drawdown             REAL,     -- worst peak-to-trough inside the window
    drawdown_peak_date       TEXT,
    drawdown_trough_date     TEXT,
    drawdown_sessions        INTEGER,

    -- dispersion, for sizing and for strike placement
    realized_vol             REAL,     -- annualised sd of daily log returns
    downside_vol             REAL,     -- annualised sd of negative daily returns only
    terminal_return          REAL,     -- redundant with pit_label by design: a cross-check

    -- provenance
    barriers_json            TEXT NOT NULL,   -- {strike: {"hit":bool,"date":..,"session":int}}
    path_quality             TEXT NOT NULL,   -- complete | partial | unusable
    quality_reason           TEXT,
    zero_volume_sessions     INTEGER NOT NULL DEFAULT 0,
    price_source             TEXT NOT NULL,
    price_ingest_id          INTEGER,
    model_version            TEXT NOT NULL,
    computed_at              TEXT NOT NULL,
    UNIQUE (listing_id, as_of_date, horizon, model_version)
);

CREATE INDEX IF NOT EXISTS idx_pit_path_lookup ON pit_path (as_of_date, horizon);
"""


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """Create pit_path if absent. Idempotent; returns the tables it owns."""
    conn.executescript(SCHEMA)
    conn.commit()
    return ["pit_path"]


# --------------------------------------------------------------------------
# Path statistics
# --------------------------------------------------------------------------

def _annualise(daily_sd: float, sessions_per_year: float = 252.0) -> float:
    return daily_sd * math.sqrt(sessions_per_year)


def compute_path(bars: Sequence[tuple[str, float, float]],
                 anchor_price: float) -> dict[str, Any]:
    """Path statistics over one window.

    `bars` is [(session_date, close, volume)] INCLUSIVE of the anchor session and
    ordered oldest first; `anchor_price` is the price the position was struck at.

    Uses ADJUSTED closes, like the return label: a corporate action inside the
    window rescales every point by a common factor, so ratios to the anchor are
    invariant to it. Using raw prices here would put a split into the drawdown.

    Zero-volume sessions are COUNTED but not dropped. A stale carried-forward
    print understates an excursion (First Republic printed $3.51 at zero volume
    for two sessions after the FDIC seizure; the next real trade was $0.3336),
    so the count is surfaced and the caller decides whether the path is usable
    rather than the statistic quietly absorbing it.
    """
    if not bars or anchor_price is None or anchor_price <= 0:
        return {"path_quality": "unusable", "quality_reason": "no_anchor_price",
                "n_sessions": 0}

    dates = [b[0] for b in bars]
    closes = [b[1] for b in bars]
    volumes = [b[2] if b[2] is not None else 0.0 for b in bars]

    usable = [(d, c, v) for d, c, v in zip(dates, closes, volumes)
              if c is not None and c > 0]
    if len(usable) < 2:
        return {"path_quality": "unusable", "quality_reason": "fewer_than_two_prices",
                "n_sessions": len(usable)}

    dates = [u[0] for u in usable]
    closes = [u[1] for u in usable]
    zero_vol = sum(1 for u in usable if not u[2])

    rel = [c / anchor_price - 1.0 for c in closes]

    mae_i = min(range(len(rel)), key=lambda i: rel[i])
    mfe_i = max(range(len(rel)), key=lambda i: rel[i])

    # Drawdown along the path: worst peak-to-trough, which is NOT the same as the
    # worst move from the anchor. A position that doubles then halves ends flat
    # against the anchor and has a 50% drawdown -- and it is the drawdown that
    # decides whether a stop or a collar would have been triggered.
    peak = closes[0]
    peak_i = 0
    best_peak_i = best_trough_i = 0
    max_dd = 0.0
    for i, c in enumerate(closes):
        if c > peak:
            peak, peak_i = c, i
        dd = c / peak - 1.0
        if dd < max_dd:
            max_dd, best_peak_i, best_trough_i = dd, peak_i, i

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        realized = _annualise(math.sqrt(var))
        downs = [r for r in rets if r < 0.0]
        if len(downs) >= 2:
            dmean = sum(downs) / len(downs)
            dvar = sum((r - dmean) ** 2 for r in downs) / (len(downs) - 1)
            downside = _annualise(math.sqrt(dvar))
        else:
            downside = None
    else:
        realized = downside = None

    barriers: dict[str, Any] = {}
    for strike in BARRIER_LADDER:
        hit_i = None
        for i, r in enumerate(rel):
            if (strike < 0 and r <= strike) or (strike > 0 and r >= strike):
                hit_i = i
                break
        barriers[f"{strike:+.2f}"] = {
            "hit": hit_i is not None,
            "date": dates[hit_i] if hit_i is not None else None,
            "session": hit_i if hit_i is not None else None,
        }

    quality = "complete"
    reason = None
    if zero_vol:
        quality = "partial"
        reason = f"{zero_vol}_zero_volume_sessions"

    return {
        "n_sessions": len(usable),
        "max_adverse_excursion": rel[mae_i],
        "mae_date": dates[mae_i],
        "sessions_to_mae": mae_i,
        "max_favourable_excursion": rel[mfe_i],
        "mfe_date": dates[mfe_i],
        "sessions_to_mfe": mfe_i,
        "max_drawdown": max_dd,
        "drawdown_peak_date": dates[best_peak_i],
        "drawdown_trough_date": dates[best_trough_i],
        "drawdown_sessions": best_trough_i - best_peak_i,
        "realized_vol": realized,
        "downside_vol": downside,
        "terminal_return": rel[-1],
        "barriers": barriers,
        "zero_volume_sessions": zero_vol,
        "path_quality": quality,
        "quality_reason": reason,
    }


def save_path(conn: sqlite3.Connection, listing_id: int, as_of_date: str,
              horizon: str, anchor_date: str, anchor_price: float,
              stats: dict[str, Any], **fields) -> str:
    """Upsert one path record. Mutable for the same reason labels are: it is an
    observation of the future, refined as more price history arrives."""
    with pit_store.transaction(conn):
        conn.execute(
            """INSERT INTO pit_path
                   (listing_id, as_of_date, horizon, anchor_date, anchor_price,
                    end_date, n_sessions, max_adverse_excursion, mae_date,
                    sessions_to_mae, max_favourable_excursion, mfe_date,
                    sessions_to_mfe, max_drawdown, drawdown_peak_date,
                    drawdown_trough_date, drawdown_sessions, realized_vol,
                    downside_vol, terminal_return, barriers_json, path_quality,
                    quality_reason, zero_volume_sessions, price_source,
                    price_ingest_id, model_version, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (listing_id, as_of_date, horizon, model_version)
               DO UPDATE SET
                   end_date=excluded.end_date, n_sessions=excluded.n_sessions,
                   max_adverse_excursion=excluded.max_adverse_excursion,
                   mae_date=excluded.mae_date, sessions_to_mae=excluded.sessions_to_mae,
                   max_favourable_excursion=excluded.max_favourable_excursion,
                   mfe_date=excluded.mfe_date, sessions_to_mfe=excluded.sessions_to_mfe,
                   max_drawdown=excluded.max_drawdown,
                   drawdown_peak_date=excluded.drawdown_peak_date,
                   drawdown_trough_date=excluded.drawdown_trough_date,
                   drawdown_sessions=excluded.drawdown_sessions,
                   realized_vol=excluded.realized_vol, downside_vol=excluded.downside_vol,
                   terminal_return=excluded.terminal_return,
                   barriers_json=excluded.barriers_json, path_quality=excluded.path_quality,
                   quality_reason=excluded.quality_reason,
                   zero_volume_sessions=excluded.zero_volume_sessions,
                   computed_at=excluded.computed_at""",
            (listing_id, as_of_date, horizon, anchor_date, anchor_price,
             fields.get("end_date"), stats.get("n_sessions", 0),
             stats.get("max_adverse_excursion"), stats.get("mae_date"),
             stats.get("sessions_to_mae"), stats.get("max_favourable_excursion"),
             stats.get("mfe_date"), stats.get("sessions_to_mfe"),
             stats.get("max_drawdown"), stats.get("drawdown_peak_date"),
             stats.get("drawdown_trough_date"), stats.get("drawdown_sessions"),
             stats.get("realized_vol"), stats.get("downside_vol"),
             stats.get("terminal_return"),
             pit_store.dumps(stats.get("barriers") or {}),
             stats.get("path_quality", "unusable"), stats.get("quality_reason"),
             int(stats.get("zero_volume_sessions", 0)),
             fields.get("price_source", "unknown"), fields.get("price_ingest_id"),
             PATH_MODEL_VERSION, pit_store._now()),
        )
    return "written"


def hedge_would_have_paid(conn: sqlite3.Connection, listing_id: int,
                          as_of_date: str, horizon: str,
                          strike_pct: float) -> Optional[dict[str, Any]]:
    """Did a protective put struck `strike_pct` below the anchor get breached?

    The lookup that makes counterfactual hedge research affordable: no price
    series is re-read and no option is priced. Returns None when no path record
    exists, which is different from 'the barrier was not hit' and must stay
    distinguishable.
    """
    row = conn.execute(
        """SELECT barriers_json, max_adverse_excursion, path_quality
             FROM pit_path
            WHERE listing_id=? AND as_of_date=? AND horizon=? AND model_version=?""",
        (listing_id, as_of_date, horizon, PATH_MODEL_VERSION),
    ).fetchone()
    if row is None:
        return None
    barriers = pit_store.loads(row["barriers_json"]) or {}
    key = f"{strike_pct:+.2f}"
    entry = barriers.get(key)
    if entry is None:
        # Not on the ladder: answer from the excursion, which is exact for the
        # 'was it ever breached' question even off-ladder.
        mae = row["max_adverse_excursion"]
        if mae is None:
            return None
        return {"hit": mae <= strike_pct, "date": None, "session": None,
                "off_ladder": True, "path_quality": row["path_quality"]}
    return {**entry, "off_ladder": False, "path_quality": row["path_quality"]}

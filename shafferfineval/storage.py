"""
ShafferFinEval -- SQLite persistence.

Holds the asset universe, the latest scores, an IMMUTABLE daily score history,
positions, the watchlist, hedge recommendations and a fundamental change log.

Immutability rule
-----------------
Once a daily CLOSE snapshot exists for (asset, date, model version) it is never
rewritten. If Yahoo later revises history, a new snapshot on a later date picks
that up; the old row keeps what was known at the time. That is what makes the
Shaffer Score honestly backtestable. `current_scores` is the mutable
latest-state table and may be refreshed as often as you like.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import datetime as _dt
from contextlib import contextmanager
from typing import Any, Iterable, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shafferfineval.db")

#: Model versions stamped onto every snapshot so old scores are never
#: reinterpreted under a newer formula.
EQUITY_MODEL_VERSION = "equity_model_v1"
SECTOR_MODEL_VERSION = "sector_model_v1"
HEDGE_MODEL_VERSION = "hedge_model_v1"
RETURN_CALIBRATION_VERSION = "return_calibration_v1_0.20"

#: ML model lifecycle. Only an explicit action moves a model to PRODUCTION.
RESEARCH = "RESEARCH"
CHALLENGER = "CHALLENGER"
VALIDATED_CHALLENGER = "VALIDATED_CHALLENGER"
PRODUCTION = "PRODUCTION"
RETIRED = "RETIRED"

CLOSE = "close"
INTRADAY = "intraday"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                TEXT NOT NULL,
    yahoo_symbol          TEXT,
    name                  TEXT,
    asset_class           TEXT NOT NULL,
    subclass              TEXT,
    sector                TEXT,
    industry              TEXT,
    currency              TEXT,
    country               TEXT,
    source_workbook_sheet TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    UNIQUE (symbol, asset_class)
);
CREATE INDEX IF NOT EXISTS idx_assets_class ON assets(asset_class);
CREATE INDEX IF NOT EXISTS idx_assets_symbol ON assets(symbol);

CREATE TABLE IF NOT EXISTS current_scores (
    asset_id         INTEGER PRIMARY KEY REFERENCES assets(asset_id),
    price            REAL,
    shaffer_score    REAL,
    classification   TEXT,
    preferred_hedge  TEXT,
    sector_score     REAL,
    sector_overlay   REAL,
    company_score    REAL,
    score_confidence TEXT,
    model_status     TEXT,
    factor_scores_json TEXT,
    raw_inputs_json    TEXT,
    model_version    TEXT,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_history (
    history_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id           INTEGER NOT NULL REFERENCES assets(asset_id),
    snapshot_date      TEXT NOT NULL,
    snapshot_kind      TEXT NOT NULL DEFAULT 'close',
    snapshot_timestamp TEXT NOT NULL,
    price              REAL,
    shaffer_score      REAL,
    classification     TEXT,
    sector_overlay     REAL,
    company_score      REAL,
    factor_scores_json TEXT,
    raw_inputs_json    TEXT,
    preferred_hedge    TEXT,
    model_version      TEXT NOT NULL,
    UNIQUE (asset_id, snapshot_date, snapshot_kind, model_version)
);
CREATE INDEX IF NOT EXISTS idx_history_asset ON score_history(asset_id, snapshot_date);

CREATE TABLE IF NOT EXISTS positions (
    position_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(asset_id),
    direction    TEXT NOT NULL,
    quantity     REAL NOT NULL,
    unit_type    TEXT NOT NULL DEFAULT 'shares',
    average_cost REAL,
    notes        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_asset ON positions(asset_id);

CREATE TABLE IF NOT EXISTS watchlist (
    watchlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL UNIQUE REFERENCES assets(asset_id),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hedge_recommendations (
    recommendation_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id                INTEGER NOT NULL REFERENCES assets(asset_id),
    position_id             INTEGER REFERENCES positions(position_id),
    snapshot_date           TEXT NOT NULL,
    strategy                TEXT,
    strategy_key            TEXT,
    strategy_score          REAL,
    hedge_ratio             REAL,
    adverse_score           REAL,
    ticket_json             TEXT,
    strategy_breakdown_json TEXT,
    confidence              TEXT,
    model_version           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    UNIQUE (asset_id, position_id, snapshot_date, model_version)
);

CREATE TABLE IF NOT EXISTS fundamental_history (
    fundamental_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id                INTEGER NOT NULL REFERENCES assets(asset_id),
    observed_at             TEXT NOT NULL,
    fundamental_values_json TEXT NOT NULL,
    changed_fields_json     TEXT,
    UNIQUE (asset_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_fundamental_asset ON fundamental_history(asset_id);

CREATE TABLE IF NOT EXISTS outcome_labels (
    label_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id        INTEGER NOT NULL REFERENCES assets(asset_id),
    snapshot_date   TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    base_price      REAL,
    future_date     TEXT,
    future_price    REAL,
    forward_return  REAL,
    vti_forward_return REAL,
    excess_return   REAL,
    computed_at     TEXT NOT NULL,
    UNIQUE (asset_id, snapshot_date, horizon)
);
CREATE INDEX IF NOT EXISTS idx_labels_asset ON outcome_labels(asset_id, horizon);
CREATE INDEX IF NOT EXISTS idx_labels_date ON outcome_labels(snapshot_date, horizon);

CREATE TABLE IF NOT EXISTS ml_models (
    model_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name           TEXT NOT NULL,
    model_family         TEXT NOT NULL,
    asset_class          TEXT NOT NULL,
    target               TEXT NOT NULL,
    horizon              TEXT NOT NULL,
    feature_list_json    TEXT,
    hyperparameters_json TEXT,
    training_start       TEXT,
    training_end         TEXT,
    validation_method    TEXT,
    training_observations INTEGER,
    test_observations    INTEGER,
    metrics_json         TEXT,
    importance_json      TEXT,
    coefficients_json    TEXT,
    sampling             TEXT,
    status               TEXT NOT NULL DEFAULT 'RESEARCH',
    model_version        TEXT NOT NULL,
    artifact_path        TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ml_status ON ml_models(status, horizon);

CREATE TABLE IF NOT EXISTS ml_predictions (
    prediction_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id       INTEGER NOT NULL REFERENCES ml_models(model_id),
    asset_id       INTEGER NOT NULL REFERENCES assets(asset_id),
    snapshot_date  TEXT NOT NULL,
    predicted_return_pct REAL,
    predicted_price      REAL,
    created_at     TEXT NOT NULL,
    UNIQUE (model_id, asset_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS political_events (
    event_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type               TEXT NOT NULL,
    description              TEXT,
    start_time               TEXT,
    last_updated             TEXT,
    end_time                 TEXT,
    severity_components_json TEXT,
    severity                 REAL,
    confidence               REAL,
    status                   TEXT NOT NULL DEFAULT 'active',
    model_version            TEXT NOT NULL DEFAULT 'gpi_v1',
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_status ON political_events(status);

CREATE TABLE IF NOT EXISTS asset_political_exposure (
    exposure_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id                 INTEGER NOT NULL REFERENCES assets(asset_id),
    event_id                 INTEGER NOT NULL REFERENCES political_events(event_id),
    exposure_components_json TEXT,
    net_exposure             REAL,
    impact                   REAL,
    decayed_severity         REAL,
    updated_at               TEXT NOT NULL,
    UNIQUE (asset_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_exposure_asset ON asset_political_exposure(asset_id);

CREATE TABLE IF NOT EXISTS trades (
    trade_row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT,
    order_id           TEXT,
    asset_id           INTEGER REFERENCES assets(asset_id),
    source_symbol      TEXT,
    asset_class        TEXT,
    trade_date         TEXT,
    settle_date        TEXT,
    side               TEXT,
    quantity           REAL,
    price              REAL,
    gross              REAL,
    accrued            REAL,
    commission         REAL,
    net_cash           REAL,
    realized_pnl       REAL,
    status             TEXT,
    strategy_tag       TEXT,
    trade_group_id     TEXT,
    hedge_relationship TEXT,
    parent_trade       TEXT,
    linked_hedge_trade TEXT,
    extra_json         TEXT,
    source             TEXT,
    imported_at        TEXT NOT NULL,
    UNIQUE (trade_id, source)
);
CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset_id);
CREATE INDEX IF NOT EXISTS idx_trades_group ON trades(trade_group_id);

CREATE TABLE IF NOT EXISTS trade_groups (
    group_row_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key     TEXT NOT NULL UNIQUE,
    primary_trade TEXT,
    relationship  TEXT,
    strategy_tag  TEXT,
    hedge_ratio   REAL,
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hedge_links (
    link_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key         TEXT NOT NULL,
    primary_trade_id  TEXT,
    hedge_trade_id    TEXT,
    hedge_strategy    TEXT,
    relationship_type TEXT,
    hedge_ratio       REAL,
    created_at        TEXT NOT NULL,
    closed_at         TEXT,
    UNIQUE (group_key, primary_trade_id, hedge_trade_id)
);

CREATE TABLE IF NOT EXISTS trade_snapshots (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id          TEXT,
    asset_id          INTEGER REFERENCES assets(asset_id),
    stage             TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    price             REAL,
    shaffer_score     REAL,
    factor_scores_json TEXT,
    raw_inputs_json   TEXT,
    gpi_json          TEXT,
    predicted_return  REAL,
    preferred_hedge   TEXT,
    hedge_json        TEXT,
    model_versions_json TEXT,
    UNIQUE (trade_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_trade_snap_asset ON trade_snapshots(asset_id);

CREATE TABLE IF NOT EXISTS hedge_counterfactuals (
    cf_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id       INTEGER REFERENCES assets(asset_id),
    group_key      TEXT,
    snapshot_date  TEXT NOT NULL,
    strategy_key   TEXT NOT NULL,
    strategy_name  TEXT,
    label          TEXT NOT NULL,
    context_json   TEXT,
    legs_json      TEXT,
    outcome_json   TEXT,
    net_pnl        REAL,
    hedge_efficiency REAL,
    model_version  TEXT NOT NULL DEFAULT 'hedge_model_v1',
    created_at     TEXT NOT NULL,
    UNIQUE (group_key, snapshot_date, strategy_key)
);
CREATE INDEX IF NOT EXISTS idx_cf_asset ON hedge_counterfactuals(asset_id, label);

CREATE TABLE IF NOT EXISTS refresh_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    scope        TEXT,
    attempted    INTEGER DEFAULT 0,
    succeeded    INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    snapshot_kind TEXT,
    summary_json TEXT
);
"""

_local = threading.local()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """A connection with row access by name and foreign keys on."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


#: Columns added after the first schema version. `init_db` adds any that are
#: missing, so an existing database upgrades in place without losing history.
MIGRATIONS = {
    "current_scores": [
        ("predicted_12m_return_pct", "REAL"),
        ("predicted_12m_price", "REAL"),
        ("prediction_model", "TEXT"),
        ("prediction_model_version", "TEXT"),
        ("prediction_timestamp", "TEXT"),
    ],
    "score_history": [
        ("predicted_12m_return_pct", "REAL"),
        ("predicted_12m_price", "REAL"),
        ("prediction_model", "TEXT"),
        ("prediction_model_version", "TEXT"),
        ("prediction_timestamp", "TEXT"),
    ],
}


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the schema if absent and return a connection. Idempotent."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str, sort_keys=True)


def _loads(text) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------

def upsert_assets(conn: sqlite3.Connection, assets: Iterable) -> dict[tuple, int]:
    """Insert or update universe assets. Returns {(symbol, asset_class): asset_id}.

    Keyed by BOTH fields because a handful of workbook symbols legitimately
    exist in two asset classes -- CL is Colgate and WTI crude, ZS is Zscaler and
    soybeans, NOK is Nokia and the Norwegian krone.
    """
    ids: dict[tuple, int] = {}
    with transaction(conn):
        for asset in assets:
            conn.execute(
                """INSERT INTO assets
                   (symbol, yahoo_symbol, name, asset_class, subclass, sector,
                    industry, currency, country, source_workbook_sheet, active, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(symbol, asset_class) DO UPDATE SET
                     yahoo_symbol=excluded.yahoo_symbol,
                     name=excluded.name,
                     subclass=excluded.subclass,
                     sector=COALESCE(excluded.sector, assets.sector),
                     industry=COALESCE(excluded.industry, assets.industry),
                     currency=excluded.currency,
                     country=excluded.country,
                     source_workbook_sheet=excluded.source_workbook_sheet,
                     active=1""",
                (asset.symbol, asset.yahoo_symbol, asset.name, asset.asset_class,
                 asset.subclass, asset.sector, asset.industry, asset.currency,
                 asset.country, asset.sheet, _now()),
            )
        for row in conn.execute("SELECT asset_id, symbol, asset_class FROM assets"):
            ids[(row["symbol"], row["asset_class"])] = row["asset_id"]
    return ids


def get_asset(conn, symbol: str, asset_class: Optional[str] = None):
    """Look up one asset.

    A few symbols exist in two asset classes (CL = Colgate and WTI crude). With
    no `asset_class` given, the scoreable equity wins, since that is what the
    terminal can actually score and hedge; pass `asset_class` to disambiguate
    explicitly.
    """
    if asset_class:
        return conn.execute(
            "SELECT * FROM assets WHERE symbol = ? COLLATE NOCASE AND asset_class = ?",
            (symbol, asset_class),
        ).fetchone()
    return conn.execute(
        """SELECT * FROM assets WHERE symbol = ? COLLATE NOCASE
           ORDER BY CASE asset_class WHEN 'Equity' THEN 0 ELSE 1 END, asset_id
           LIMIT 1""",
        (symbol,),
    ).fetchone()


def find_assets(conn, symbol: str) -> list:
    """Every asset class carrying this symbol."""
    return conn.execute(
        "SELECT * FROM assets WHERE symbol = ? COLLATE NOCASE ORDER BY asset_class",
        (symbol,),
    ).fetchall()


def get_asset_by_id(conn, asset_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()


def list_assets(conn, asset_class: Optional[str] = None) -> list[sqlite3.Row]:
    if asset_class:
        return conn.execute(
            "SELECT * FROM assets WHERE active=1 AND asset_class=? ORDER BY symbol",
            (asset_class,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM assets WHERE active=1 ORDER BY symbol"
    ).fetchall()


def search_assets(conn, query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Local search over symbol, name, class, sector and industry."""
    text = (query or "").strip()
    if not text:
        return []
    like = f"%{text}%"
    return conn.execute(
        """SELECT * FROM assets
           WHERE active=1 AND (
               symbol LIKE ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE
               OR asset_class LIKE ? COLLATE NOCASE OR subclass LIKE ? COLLATE NOCASE
               OR sector LIKE ? COLLATE NOCASE OR industry LIKE ? COLLATE NOCASE)
           ORDER BY
               CASE WHEN symbol = ? COLLATE NOCASE THEN 0
                    WHEN symbol LIKE ? COLLATE NOCASE THEN 1
                    ELSE 2 END,
               symbol
           LIMIT ?""",
        (like, like, like, like, like, like, text, f"{text}%", limit),
    ).fetchall()


# --------------------------------------------------------------------------
# Scores
# --------------------------------------------------------------------------

def save_current_score(conn, asset_id: int, **fields) -> None:
    """Mutable latest state. Safe to overwrite as often as you refresh."""
    with transaction(conn):
        conn.execute(
            """INSERT INTO current_scores
               (asset_id, price, shaffer_score, classification, preferred_hedge,
                sector_score, sector_overlay, company_score, score_confidence,
                model_status, factor_scores_json, raw_inputs_json, model_version,
                updated_at, predicted_12m_return_pct, predicted_12m_price,
                prediction_model, prediction_model_version, prediction_timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asset_id) DO UPDATE SET
                 price=excluded.price, shaffer_score=excluded.shaffer_score,
                 classification=excluded.classification,
                 preferred_hedge=excluded.preferred_hedge,
                 sector_score=excluded.sector_score,
                 sector_overlay=excluded.sector_overlay,
                 company_score=excluded.company_score,
                 score_confidence=excluded.score_confidence,
                 model_status=excluded.model_status,
                 factor_scores_json=excluded.factor_scores_json,
                 raw_inputs_json=excluded.raw_inputs_json,
                 model_version=excluded.model_version,
                 updated_at=excluded.updated_at,
                 predicted_12m_return_pct=excluded.predicted_12m_return_pct,
                 predicted_12m_price=excluded.predicted_12m_price,
                 prediction_model=excluded.prediction_model,
                 prediction_model_version=excluded.prediction_model_version,
                 prediction_timestamp=excluded.prediction_timestamp""",
            (asset_id, fields.get("price"), fields.get("shaffer_score"),
             fields.get("classification"), fields.get("preferred_hedge"),
             fields.get("sector_score"), fields.get("sector_overlay"),
             fields.get("company_score"), fields.get("score_confidence"),
             fields.get("model_status"), _dumps(fields.get("factor_scores")),
             _dumps(fields.get("raw_inputs")),
             fields.get("model_version", EQUITY_MODEL_VERSION), _now(),
             fields.get("predicted_12m_return_pct"),
             fields.get("predicted_12m_price"),
             fields.get("prediction_model"),
             fields.get("prediction_model_version"), _now()),
        )


def save_score_snapshot(
    conn, asset_id: int, snapshot_date: str, kind: str = CLOSE,
    model_version: str = EQUITY_MODEL_VERSION, **fields
) -> str:
    """Append an immutable history row.

    Returns "written" or "exists". A CLOSE snapshot that already exists is NEVER
    rewritten -- that is the guarantee the backtest rests on. Intraday rows are
    replaced, since they are explicitly a working state.
    """
    existing = conn.execute(
        """SELECT history_id FROM score_history
           WHERE asset_id=? AND snapshot_date=? AND snapshot_kind=? AND model_version=?""",
        (asset_id, snapshot_date, kind, model_version),
    ).fetchone()

    if existing and kind == CLOSE:
        return "exists"

    with transaction(conn):
        if existing:
            conn.execute("DELETE FROM score_history WHERE history_id=?",
                         (existing["history_id"],))
        conn.execute(
            """INSERT INTO score_history
               (asset_id, snapshot_date, snapshot_kind, snapshot_timestamp, price,
                shaffer_score, classification, sector_overlay, company_score,
                factor_scores_json, raw_inputs_json, preferred_hedge, model_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (asset_id, snapshot_date, kind, _now(), fields.get("price"),
             fields.get("shaffer_score"), fields.get("classification"),
             fields.get("sector_overlay"), fields.get("company_score"),
             _dumps(fields.get("factor_scores")), _dumps(fields.get("raw_inputs")),
             fields.get("preferred_hedge"), model_version),
        )
    return "written"


def get_current_score(conn, asset_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM current_scores WHERE asset_id=?", (asset_id,)
    ).fetchone()


def get_score_history(conn, asset_id: int, kind: str = CLOSE, limit: int = 400):
    return conn.execute(
        """SELECT * FROM score_history WHERE asset_id=? AND snapshot_kind=?
           ORDER BY snapshot_date DESC LIMIT ?""",
        (asset_id, kind, limit),
    ).fetchall()


def get_previous_snapshot(conn, asset_id: int, before_date: str, kind: str = CLOSE):
    return conn.execute(
        """SELECT * FROM score_history
           WHERE asset_id=? AND snapshot_kind=? AND snapshot_date < ?
           ORDER BY snapshot_date DESC LIMIT 1""",
        (asset_id, kind, before_date),
    ).fetchone()


def market_rows(conn) -> list[sqlite3.Row]:
    """Everything the market page needs, in one query, read from the database."""
    return conn.execute(
        """SELECT a.asset_id, a.symbol, a.yahoo_symbol, a.name, a.asset_class,
                  a.subclass, a.sector, a.industry,
                  c.price, c.shaffer_score, c.classification, c.preferred_hedge,
                  c.score_confidence, c.model_status, c.updated_at,
                  c.predicted_12m_return_pct, c.predicted_12m_price,
                  c.prediction_model_version,
                  (SELECT h.shaffer_score FROM score_history h
                    WHERE h.asset_id=a.asset_id AND h.snapshot_kind='close'
                    ORDER BY h.snapshot_date DESC LIMIT 1 OFFSET 1) AS prev_score,
                  EXISTS(SELECT 1 FROM watchlist w WHERE w.asset_id=a.asset_id) AS on_watchlist,
                  (SELECT COUNT(*) FROM positions p WHERE p.asset_id=a.asset_id) AS position_count
           FROM assets a LEFT JOIN current_scores c ON c.asset_id=a.asset_id
           WHERE a.active=1
           ORDER BY a.symbol"""
    ).fetchall()


# --------------------------------------------------------------------------
# Watchlist and positions
# --------------------------------------------------------------------------

def add_to_watchlist(conn, asset_id: int) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (asset_id, created_at) VALUES (?,?)",
            (asset_id, _now()),
        )


def remove_from_watchlist(conn, asset_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM watchlist WHERE asset_id=?", (asset_id,))


def is_watched(conn, asset_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM watchlist WHERE asset_id=?", (asset_id,)
    ).fetchone() is not None


def list_watchlist(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.*, c.price, c.shaffer_score, c.classification, c.preferred_hedge,
                  c.updated_at, c.model_status,
                  c.predicted_12m_return_pct, c.predicted_12m_price,
                  (SELECT h.shaffer_score FROM score_history h
                    WHERE h.asset_id=a.asset_id AND h.snapshot_kind='close'
                    ORDER BY h.snapshot_date DESC LIMIT 1 OFFSET 1) AS prev_score
           FROM watchlist w JOIN assets a ON a.asset_id=w.asset_id
           LEFT JOIN current_scores c ON c.asset_id=a.asset_id
           ORDER BY a.symbol"""
    ).fetchall()


def add_position(conn, asset_id: int, direction: str, quantity: float,
                 unit_type: str = "shares", average_cost: Optional[float] = None,
                 notes: Optional[str] = None) -> int:
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO positions
               (asset_id, direction, quantity, unit_type, average_cost, notes,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (asset_id, direction, abs(float(quantity)), unit_type, average_cost,
             notes, _now(), _now()),
        )
        return cursor.lastrowid


def remove_position(conn, position_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM positions WHERE position_id=?", (position_id,))


def list_positions(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT p.*, a.symbol, a.name, a.asset_class, a.sector, a.industry,
                  a.yahoo_symbol,
                  c.price, c.shaffer_score, c.classification, c.preferred_hedge,
                  c.updated_at, c.model_status,
                  c.predicted_12m_return_pct, c.predicted_12m_price,
                  (SELECT h.shaffer_score FROM score_history h
                    WHERE h.asset_id=a.asset_id AND h.snapshot_kind='close'
                    ORDER BY h.snapshot_date DESC LIMIT 1 OFFSET 1) AS prev_score
           FROM positions p JOIN assets a ON a.asset_id=p.asset_id
           LEFT JOIN current_scores c ON c.asset_id=a.asset_id
           ORDER BY a.symbol"""
    ).fetchall()


# --------------------------------------------------------------------------
# Hedge recommendations and fundamentals
# --------------------------------------------------------------------------

def save_hedge_recommendation(conn, asset_id: int, snapshot_date: str,
                              position_id: Optional[int] = None,
                              model_version: str = HEDGE_MODEL_VERSION,
                              **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO hedge_recommendations
               (asset_id, position_id, snapshot_date, strategy, strategy_key,
                strategy_score, hedge_ratio, adverse_score, ticket_json,
                strategy_breakdown_json, confidence, model_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asset_id, position_id, snapshot_date, model_version)
               DO UPDATE SET
                 strategy=excluded.strategy, strategy_key=excluded.strategy_key,
                 strategy_score=excluded.strategy_score,
                 hedge_ratio=excluded.hedge_ratio,
                 adverse_score=excluded.adverse_score,
                 ticket_json=excluded.ticket_json,
                 strategy_breakdown_json=excluded.strategy_breakdown_json,
                 confidence=excluded.confidence""",
            (asset_id, position_id, snapshot_date, fields.get("strategy"),
             fields.get("strategy_key"), fields.get("strategy_score"),
             fields.get("hedge_ratio"), fields.get("adverse_score"),
             _dumps(fields.get("ticket")), _dumps(fields.get("breakdown")),
             fields.get("confidence"), model_version, _now()),
        )


def get_hedge_recommendation(conn, asset_id: int, position_id: Optional[int] = None):
    if position_id is None:
        return conn.execute(
            """SELECT * FROM hedge_recommendations
               WHERE asset_id=? AND position_id IS NULL
               ORDER BY snapshot_date DESC LIMIT 1""", (asset_id,)).fetchone()
    return conn.execute(
        """SELECT * FROM hedge_recommendations
           WHERE asset_id=? AND position_id=?
           ORDER BY snapshot_date DESC LIMIT 1""", (asset_id, position_id)).fetchone()


def latest_fundamentals(conn, asset_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT fundamental_values_json FROM fundamental_history
           WHERE asset_id=? ORDER BY observed_at DESC LIMIT 1""", (asset_id,)).fetchone()
    return _loads(row["fundamental_values_json"]) if row else None


def save_fundamentals(conn, asset_id: int, values: dict,
                      changed: Optional[dict] = None) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO fundamental_history
               (asset_id, observed_at, fundamental_values_json, changed_fields_json)
               VALUES (?,?,?,?)""",
            (asset_id, _now(), _dumps(values), _dumps(changed)),
        )


def list_fundamental_changes(conn, asset_id: int, limit: int = 20):
    return conn.execute(
        """SELECT * FROM fundamental_history
           WHERE asset_id=? AND changed_fields_json IS NOT NULL
             AND changed_fields_json != 'null' AND changed_fields_json != '{}'
           ORDER BY observed_at DESC LIMIT ?""", (asset_id, limit)).fetchall()


def start_refresh_run(conn, scope: str, snapshot_kind: str) -> int:
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO refresh_runs (started_at, scope, snapshot_kind) VALUES (?,?,?)",
            (_now(), scope, snapshot_kind),
        )
        return cursor.lastrowid


def finish_refresh_run(conn, run_id: int, attempted: int, succeeded: int,
                       failed: int, summary: dict) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE refresh_runs SET finished_at=?, attempted=?, succeeded=?,
               failed=?, summary_json=? WHERE run_id=?""",
            (_now(), attempted, succeeded, failed, _dumps(summary), run_id),
        )


def last_refresh_run(conn):
    return conn.execute(
        "SELECT * FROM refresh_runs ORDER BY run_id DESC LIMIT 1").fetchone()


loads = _loads
dumps = _dumps


# --------------------------------------------------------------------------
# Predictions, outcome labels and the ML model registry
# --------------------------------------------------------------------------

def save_prediction_fields(conn, asset_id: int, snapshot_date: str,
                           kind: str = CLOSE, **fields) -> str:
    """Attach a prediction to an existing snapshot row.

    A CLOSE row that already carries a prediction is NEVER rewritten: a
    historical prediction must keep the model that produced it, so a later
    calibration cannot retroactively change what the terminal said that day.
    """
    row = conn.execute(
        """SELECT history_id, prediction_model_version FROM score_history
           WHERE asset_id=? AND snapshot_date=? AND snapshot_kind=?""",
        (asset_id, snapshot_date, kind),
    ).fetchone()
    if row is None:
        return "missing"
    if kind == CLOSE and row["prediction_model_version"]:
        return "exists"

    with transaction(conn):
        conn.execute(
            """UPDATE score_history SET predicted_12m_return_pct=?,
               predicted_12m_price=?, prediction_model=?,
               prediction_model_version=?, prediction_timestamp=?
               WHERE history_id=?""",
            (fields.get("predicted_return_pct"), fields.get("predicted_price"),
             fields.get("model"), fields.get("model_version"), _now(),
             row["history_id"]),
        )
    return "written"


def save_outcome_label(conn, asset_id: int, snapshot_date: str, horizon: str,
                       **fields) -> None:
    """Realised forward return for one snapshot. Replaceable: it is an
    observation of the future, refined as more price history arrives."""
    with transaction(conn):
        conn.execute(
            """INSERT INTO outcome_labels
               (asset_id, snapshot_date, horizon, base_price, future_date,
                future_price, forward_return, vti_forward_return, excess_return,
                computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asset_id, snapshot_date, horizon) DO UPDATE SET
                 base_price=excluded.base_price, future_date=excluded.future_date,
                 future_price=excluded.future_price,
                 forward_return=excluded.forward_return,
                 vti_forward_return=excluded.vti_forward_return,
                 excess_return=excluded.excess_return,
                 computed_at=excluded.computed_at""",
            (asset_id, snapshot_date, horizon, fields.get("base_price"),
             fields.get("future_date"), fields.get("future_price"),
             fields.get("forward_return"), fields.get("vti_forward_return"),
             fields.get("excess_return"), _now()),
        )


def label_counts(conn) -> dict:
    return {
        row["horizon"]: row["n"]
        for row in conn.execute(
            "SELECT horizon, COUNT(*) AS n FROM outcome_labels "
            "WHERE forward_return IS NOT NULL GROUP BY horizon")
    }


def dataset_rows(conn, horizon: str) -> list:
    """Point-in-time feature rows joined to their realised outcome.

    Only `score_history` is read for features, so every value is exactly what
    was known on that date. Nothing is recomputed from today's fundamentals.
    """
    return conn.execute(
        """SELECT h.asset_id, a.symbol, a.sector, a.industry,
                  h.snapshot_date, h.price, h.shaffer_score, h.company_score,
                  h.sector_overlay, h.factor_scores_json, h.raw_inputs_json,
                  h.model_version, h.predicted_12m_return_pct,
                  l.forward_return, l.vti_forward_return, l.excess_return,
                  l.future_date
           FROM score_history h
           JOIN assets a ON a.asset_id = h.asset_id
           JOIN outcome_labels l
             ON l.asset_id = h.asset_id AND l.snapshot_date = h.snapshot_date
            AND l.horizon = ?
           WHERE h.snapshot_kind = 'close' AND l.forward_return IS NOT NULL
           ORDER BY h.snapshot_date, a.symbol""",
        (horizon,),
    ).fetchall()


def snapshot_stats(conn) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS snapshots,
                  COUNT(DISTINCT asset_id) AS assets,
                  MIN(snapshot_date) AS earliest,
                  MAX(snapshot_date) AS latest,
                  COUNT(DISTINCT snapshot_date) AS dates
           FROM score_history WHERE snapshot_kind='close'"""
    ).fetchone()
    return dict(row) if row else {}


def register_model(conn, **fields) -> int:
    """Persist a trained model's definition and metrics. Status defaults to
    RESEARCH -- nothing reaches PRODUCTION without an explicit promotion."""
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO ml_models
               (model_name, model_family, asset_class, target, horizon,
                feature_list_json, hyperparameters_json, training_start,
                training_end, validation_method, training_observations,
                test_observations, metrics_json, importance_json,
                coefficients_json, sampling, status, model_version,
                artifact_path, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fields.get("model_name"), fields.get("model_family"),
             fields.get("asset_class", "Equity"), fields.get("target"),
             fields.get("horizon"), _dumps(fields.get("features")),
             _dumps(fields.get("hyperparameters")), fields.get("training_start"),
             fields.get("training_end"), fields.get("validation_method"),
             fields.get("training_observations"), fields.get("test_observations"),
             _dumps(fields.get("metrics")), _dumps(fields.get("importance")),
             _dumps(fields.get("coefficients")), fields.get("sampling"),
             fields.get("status", RESEARCH), fields.get("model_version", "v1"),
             fields.get("artifact_path"), _now()),
        )
        return cursor.lastrowid


def list_models(conn, horizon: Optional[str] = None) -> list:
    if horizon:
        return conn.execute(
            "SELECT * FROM ml_models WHERE horizon=? ORDER BY model_id DESC",
            (horizon,)).fetchall()
    return conn.execute("SELECT * FROM ml_models ORDER BY model_id DESC").fetchall()


def get_model(conn, model_id: int):
    return conn.execute("SELECT * FROM ml_models WHERE model_id=?",
                        (model_id,)).fetchone()


def set_model_status(conn, model_id: int, status: str) -> None:
    """Change a model's lifecycle status.

    Promotion to PRODUCTION is deliberately a separate, explicit call. Nothing
    in the training pipeline invokes it.
    """
    with transaction(conn):
        conn.execute("UPDATE ml_models SET status=? WHERE model_id=?",
                     (status, model_id))


def production_model(conn, horizon: str):
    """The ML model currently in production for a horizon, if any.

    Returns None in normal operation: the production score-to-return mapping is
    the human V1 calibration until a model is explicitly promoted.
    """
    return conn.execute(
        "SELECT * FROM ml_models WHERE horizon=? AND status=? ORDER BY model_id DESC LIMIT 1",
        (horizon, PRODUCTION)).fetchone()


# --------------------------------------------------------------------------
# Political events and exposures
# --------------------------------------------------------------------------

def save_political_event(conn, **fields) -> int:
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO political_events
               (event_type, description, start_time, last_updated, end_time,
                severity_components_json, severity, confidence, status,
                model_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (fields.get("event_type"), fields.get("description"),
             fields.get("start_time"), _now(), fields.get("end_time"),
             _dumps(fields.get("components")), fields.get("severity"),
             fields.get("confidence"), fields.get("status", "active"),
             fields.get("model_version", "gpi_v1"), _now()),
        )
        return cursor.lastrowid


def list_political_events(conn, status: Optional[str] = None):
    if status:
        return conn.execute(
            "SELECT * FROM political_events WHERE status=? ORDER BY start_time DESC",
            (status,)).fetchall()
    return conn.execute(
        "SELECT * FROM political_events ORDER BY start_time DESC").fetchall()


def save_political_exposure(conn, asset_id: int, event_id: int, **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO asset_political_exposure
               (asset_id, event_id, exposure_components_json, net_exposure,
                impact, decayed_severity, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(asset_id, event_id) DO UPDATE SET
                 exposure_components_json=excluded.exposure_components_json,
                 net_exposure=excluded.net_exposure, impact=excluded.impact,
                 decayed_severity=excluded.decayed_severity,
                 updated_at=excluded.updated_at""",
            (asset_id, event_id, _dumps(fields.get("components")),
             fields.get("net_exposure"), fields.get("impact"),
             fields.get("decayed_severity"), _now()),
        )


def asset_political_impacts(conn, asset_id: int):
    return conn.execute(
        """SELECT e.*, x.net_exposure, x.impact, x.exposure_components_json,
                  x.decayed_severity
           FROM asset_political_exposure x
           JOIN political_events e ON e.event_id = x.event_id
           WHERE x.asset_id = ? AND e.status = 'active'""", (asset_id,)).fetchall()


# --------------------------------------------------------------------------
# Trades, groups, hedge links, snapshots, counterfactuals
# --------------------------------------------------------------------------

def save_trades(conn, trades: Iterable, source: str) -> dict:
    """Persist imported blotter trades. Idempotent on (trade_id, source)."""
    stats = {"written": 0, "skipped": 0}
    with transaction(conn):
        for trade in trades:
            try:
                cursor = conn.execute(
                    """INSERT INTO trades
                       (trade_id, order_id, asset_id, source_symbol, asset_class,
                        trade_date, settle_date, side, quantity, price, gross,
                        accrued, commission, net_cash, realized_pnl, status,
                        strategy_tag, trade_group_id, hedge_relationship,
                        parent_trade, linked_hedge_trade, extra_json, source,
                        imported_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(trade_id, source) DO NOTHING""",
                    (trade.trade_id, trade.order_id, trade.mapped_asset_id,
                     trade.symbol, trade.asset_class, trade.trade_date,
                     trade.settle_date, trade.side, trade.quantity, trade.price,
                     trade.gross, trade.accrued, trade.commission, trade.net_cash,
                     trade.realized_pnl, trade.status, trade.strategy_tag,
                     trade.trade_group_id, trade.hedge_relationship,
                     trade.parent_trade, trade.linked_hedge_trade,
                     _dumps(trade.extra), source, _now()),
                )
                # ON CONFLICT DO NOTHING raises nothing, so rowcount is the
                # only honest signal that a row was actually inserted.
                if cursor.rowcount and cursor.rowcount > 0:
                    stats["written"] += 1
                else:
                    stats["skipped"] += 1
            except sqlite3.Error:
                stats["skipped"] += 1
    return stats


def list_trades(conn, asset_id: Optional[int] = None):
    if asset_id is not None:
        return conn.execute(
            """SELECT t.*, a.symbol AS universe_symbol FROM trades t
               LEFT JOIN assets a ON a.asset_id=t.asset_id
               WHERE t.asset_id=? ORDER BY t.trade_date DESC""",
            (asset_id,)).fetchall()
    return conn.execute(
        """SELECT t.*, a.symbol AS universe_symbol FROM trades t
           LEFT JOIN assets a ON a.asset_id=t.asset_id
           ORDER BY t.trade_date DESC""").fetchall()


def save_trade_group(conn, group_key: str, **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO trade_groups
               (group_key, primary_trade, relationship, strategy_tag,
                hedge_ratio, note, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(group_key) DO UPDATE SET
                 primary_trade=excluded.primary_trade,
                 relationship=excluded.relationship,
                 strategy_tag=excluded.strategy_tag,
                 hedge_ratio=excluded.hedge_ratio, note=excluded.note""",
            (group_key, fields.get("primary_trade"), fields.get("relationship"),
             fields.get("strategy_tag"), fields.get("hedge_ratio"),
             fields.get("note"), _now()),
        )


def save_hedge_link(conn, group_key: str, primary_trade_id, hedge_trade_id,
                    **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO hedge_links
               (group_key, primary_trade_id, hedge_trade_id, hedge_strategy,
                relationship_type, hedge_ratio, created_at, closed_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(group_key, primary_trade_id, hedge_trade_id)
               DO UPDATE SET hedge_strategy=excluded.hedge_strategy,
                 relationship_type=excluded.relationship_type,
                 hedge_ratio=excluded.hedge_ratio, closed_at=excluded.closed_at""",
            (group_key, primary_trade_id, hedge_trade_id,
             fields.get("hedge_strategy"), fields.get("relationship_type"),
             fields.get("hedge_ratio"), _now(), fields.get("closed_at")),
        )


def list_trade_groups(conn):
    return conn.execute("SELECT * FROM trade_groups ORDER BY group_key").fetchall()


def list_hedge_links(conn, group_key: Optional[str] = None):
    if group_key:
        return conn.execute(
            "SELECT * FROM hedge_links WHERE group_key=?", (group_key,)).fetchall()
    return conn.execute("SELECT * FROM hedge_links").fetchall()


def save_trade_snapshot(conn, trade_id: str, stage: str, **fields) -> str:
    """Contemporaneous Shaffer state at entry or exit.

    Immutable: a snapshot already captured for a (trade, stage) is never
    rewritten, so a decision can be judged on what was known at the time
    rather than on revised data.
    """
    existing = conn.execute(
        "SELECT snapshot_id FROM trade_snapshots WHERE trade_id=? AND stage=?",
        (trade_id, stage)).fetchone()
    if existing:
        return "exists"
    with transaction(conn):
        conn.execute(
            """INSERT INTO trade_snapshots
               (trade_id, asset_id, stage, captured_at, price, shaffer_score,
                factor_scores_json, raw_inputs_json, gpi_json, predicted_return,
                preferred_hedge, hedge_json, model_versions_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, fields.get("asset_id"), stage, _now(), fields.get("price"),
             fields.get("shaffer_score"), _dumps(fields.get("factor_scores")),
             _dumps(fields.get("raw_inputs")), _dumps(fields.get("gpi")),
             fields.get("predicted_return"), fields.get("preferred_hedge"),
             _dumps(fields.get("hedge")), _dumps(fields.get("model_versions"))),
        )
    return "written"


def get_trade_snapshot(conn, trade_id: str, stage: str = "entry"):
    return conn.execute(
        "SELECT * FROM trade_snapshots WHERE trade_id=? AND stage=?",
        (trade_id, stage)).fetchone()


def save_counterfactual(conn, group_key: str, snapshot_date: str,
                        strategy_key: str, **fields) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO hedge_counterfactuals
               (asset_id, group_key, snapshot_date, strategy_key, strategy_name,
                label, context_json, legs_json, outcome_json, net_pnl,
                hedge_efficiency, model_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(group_key, snapshot_date, strategy_key) DO UPDATE SET
                 outcome_json=excluded.outcome_json, net_pnl=excluded.net_pnl,
                 hedge_efficiency=excluded.hedge_efficiency,
                 label=excluded.label""",
            (fields.get("asset_id"), group_key, snapshot_date, strategy_key,
             fields.get("strategy_name"), fields.get("label", "SIMULATED COUNTERFACTUAL"),
             _dumps(fields.get("context")), _dumps(fields.get("legs")),
             _dumps(fields.get("outcome")), fields.get("net_pnl"),
             fields.get("hedge_efficiency"),
             fields.get("model_version", HEDGE_MODEL_VERSION), _now()),
        )


def list_counterfactuals(conn, label: Optional[str] = None):
    if label:
        return conn.execute(
            "SELECT * FROM hedge_counterfactuals WHERE label=? ORDER BY snapshot_date DESC",
            (label,)).fetchall()
    return conn.execute(
        "SELECT * FROM hedge_counterfactuals ORDER BY snapshot_date DESC").fetchall()

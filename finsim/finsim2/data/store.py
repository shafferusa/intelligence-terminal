"""SQLite research store (``~/.finsim2/research.db``).

* One connection per thread (``threading.local``), WAL journal, one process-wide write lock.
* Every method returns plain Python types; dates are ISO strings ("YYYY-MM-DD"); missing values are None.
* Tables follow the schema in ``finsim2/ARCHITECTURE.md``; one extra internal table ``store_meta`` holds the
  data-version counter behind :meth:`Store.data_version`.
* ``macro.published`` (nullable) is the date a value was first published (first-release vintages); databases
  created before it existed get the column added on open (:meth:`Store._migrate`). ``kv`` key
  ``macro_kind:{series}`` records whether a series holds "first_release" or "latest_vintage" values.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3
import threading
import uuid

from . import num
from .universe import UNIVERSE

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, name TEXT, asset_class TEXT, sector TEXT, country TEXT,
    currency TEXT, yahoo TEXT, cik INTEGER, duration REAL, convexity REAL, fred TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS prices(asset_id TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
    close REAL, adj_close REAL, volume REAL, PRIMARY KEY(asset_id, date));
CREATE TABLE IF NOT EXISTS macro(series TEXT NOT NULL, date TEXT NOT NULL, value REAL, published TEXT,
    PRIMARY KEY(series, date));
CREATE TABLE IF NOT EXISTS fundamentals(asset_id TEXT NOT NULL, concept TEXT NOT NULL, period_end TEXT NOT NULL,
    filed TEXT NOT NULL, value REAL, form TEXT, fp TEXT, PRIMARY KEY(asset_id, concept, period_end, filed));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS fetch_log(source TEXT, key TEXT, fetched_at TEXT, status TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS fetch_log_sk ON fetch_log(source, key, fetched_at);
CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, asset_id TEXT, horizon TEXT, model TEXT,
    model_version TEXT, made_on TEXT, target_date TEXT, predicted REAL, error_band REAL, confidence REAL,
    score REAL, realized REAL, error REAL, scored_on TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS predictions_ah ON predictions(asset_id, horizon);
CREATE TABLE IF NOT EXISTS model_runs(id INTEGER PRIMARY KEY, level TEXT, key TEXT, horizon TEXT, model TEXT,
    version TEXT, train_start TEXT, train_end TEXT, test_start TEXT, test_end TEXT, features TEXT, params TEXT,
    metrics TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS model_runs_kh ON model_runs(key, horizon);
CREATE TABLE IF NOT EXISTS backtests(id INTEGER PRIMARY KEY, spec TEXT, result TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS portfolios(id TEXT PRIMARY KEY, name TEXT, base_currency TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, portfolio_id TEXT, date TEXT, kind TEXT,
    asset_id TEXT, quantity REAL, price REAL, fee REAL, currency TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS transactions_p ON transactions(portfolio_id, date);
CREATE TABLE IF NOT EXISTS snapshots(portfolio_id TEXT, date TEXT, nav REAL, detail TEXT,
    PRIMARY KEY(portfolio_id, date));
CREATE TABLE IF NOT EXISTS watchlist(asset_id TEXT PRIMARY KEY, added TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS store_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS corporate_actions(asset_id TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL, value REAL,
    PRIMARY KEY(asset_id, date, kind));
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, at TEXT, action TEXT, entity TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS hedge_recommendations(id INTEGER PRIMARY KEY, created_at TEXT, made_on TEXT, source TEXT, status TEXT,
    portfolio_id TEXT, package_id TEXT, objective TEXT, risk_factor TEXT, exposure REAL, target REAL, horizon TEXT, horizon_days INTEGER,
    eval_date TEXT, candidates TEXT, selected TEXT, raw_ratio REAL, ml_adjustment REAL, final_ratio REAL, expected_cost REAL,
    expected_reduction REAL, expected_basis REAL, regime TEXT, score REAL, ml_confidence REAL, detail TEXT,
    realized_reduction REAL, hedge_pnl REAL, upside_sacrificed REAL, basis_error REAL, effectiveness REAL, graded_on TEXT);
CREATE TABLE IF NOT EXISTS option_quotes(asof TEXT, underlying TEXT, expiry TEXT, strike REAL, right TEXT, bid REAL, ask REAL,
    last REAL, iv REAL, delta REAL, gamma REAL, vega REAL, theta REAL, rho REAL, open_interest REAL, volume REAL, source TEXT,
    PRIMARY KEY(asof, underlying, expiry, strike, right));
CREATE INDEX IF NOT EXISTS option_quotes_u ON option_quotes(underlying, asof);
CREATE TABLE IF NOT EXISTS lab_records(asset_id TEXT NOT NULL, horizon TEXT NOT NULL, version TEXT NOT NULL, data_version TEXT,
    created TEXT, n INTEGER, data BLOB, PRIMARY KEY(asset_id, horizon, version));
"""
OPTION_COLS = ["asof", "underlying", "expiry", "strike", "right", "bid", "ask", "last", "iv", "delta", "gamma", "vega", "theta", "rho",
               "open_interest", "volume", "source"]
# columns added after the first release; `_migrate` adds them to older databases
MIGRATIONS = {"macro": [("published", "TEXT")],
              "transactions": [("basis_date", "TEXT"), ("created_at", "TEXT"), ("voided_at", "TEXT"), ("package_id", "TEXT")],
              "predictions": [("source", "TEXT"), ("raw", "REAL"), ("calibrated", "REAL"), ("range_lo", "REAL"), ("range_hi", "REAL"),
                              ("regime", "TEXT"), ("correct", "INTEGER"), ("created_at", "TEXT")]}

ASSET_COLS = ["id", "name", "asset_class", "sector", "country", "currency", "yahoo", "cik", "duration", "convexity",
              "fred", "meta"]
PRICE_COLS = ["date", "open", "high", "low", "close", "adj_close", "volume"]
PRED_COLS = ["asset_id", "horizon", "model", "model_version", "made_on", "target_date", "predicted", "error_band",
             "confidence", "score", "realized", "error", "scored_on", "detail", "source", "raw", "calibrated", "range_lo", "range_hi",
             "regime", "correct", "created_at"]
RUN_COLS = ["level", "key", "horizon", "model", "version", "train_start", "train_end", "test_start", "test_end",
            "features", "params", "metrics", "created_at"]
TX_COLS = ["portfolio_id", "date", "kind", "asset_id", "quantity", "price", "fee", "currency", "note", "basis_date", "created_at", "package_id"]
HEDGE_COLS = ["created_at", "made_on", "source", "status", "portfolio_id", "package_id", "objective", "risk_factor", "exposure", "target",
              "horizon", "horizon_days", "eval_date", "candidates", "selected", "raw_ratio", "ml_adjustment", "final_ratio", "expected_cost",
              "expected_reduction", "expected_basis", "regime", "score", "ml_confidence", "detail"]
HEDGE_GRADE_COLS = ["realized_reduction", "hedge_pnl", "upside_sacrificed", "basis_error", "effectiveness"]
MACRO_KINDS = ("first_release", "latest_vintage")


def default_path() -> str:
    """``$FINSIM2_RESEARCH_DB`` or ``$FINSIM2_HOME/research.db`` or ``~/.finsim2/research.db``."""
    explicit = os.environ.get("FINSIM2_RESEARCH_DB")
    if explicit:
        return explicit
    home = os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2")
    return os.path.join(home, "research.db")


def _d(x) -> str | None:
    """Normalise a date / datetime / ISO string to "YYYY-MM-DD"."""
    if x is None:
        return None
    if isinstance(x, _dt.datetime):
        return x.date().isoformat()
    if isinstance(x, _dt.date):
        return x.isoformat()
    s = str(x).strip()[:10]
    _dt.date.fromisoformat(s)  # validates; raises ValueError on junk
    return s


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _dumps(v) -> str:
    return json.dumps(v, default=str, separators=(",", ":"))


def _loads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


class Store:
    """The research store. Safe to share between threads (each thread gets its own connection)."""

    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        self._memory = self.path == ":memory:"
        if self._memory:  # a shared-cache in-memory DB so every thread's connection sees the same data
            self._uri = f"file:finsim2-{uuid.uuid4().hex}?mode=memory&cache=shared"
        else:
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
            self._uri = None
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._wlock = threading.RLock()
        self._closed = False
        conn = self._conn()  # keeps an in-memory DB alive for the Store's lifetime
        with self._wlock:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()
        self._seed_universe()

    @staticmethod
    def _migrate(conn):
        """Bring an older database up to the current schema (additive, idempotent, safe across processes)."""
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name in have:
                    continue
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                except sqlite3.OperationalError:          # another process added it first
                    if name not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                        raise
                    continue
                if table == "transactions" and name == "basis_date":
                    # rows entered before share-basis tracking used FinSim2's own (split-adjusted) prices, i.e. the
                    # share basis of the day they were entered; no split can have happened since, so today's date
                    conn.execute("UPDATE transactions SET basis_date = ? WHERE basis_date IS NULL", (_dt.date.today().isoformat(),))
        conn.commit()

    # ------------------------------------------------------------------ plumbing
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is not None:
            return c
        if self._closed:
            raise RuntimeError("Store is closed")
        if self._memory:
            c = sqlite3.connect(self._uri, uri=True, timeout=30, check_same_thread=False)
        else:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.row_factory = sqlite3.Row
        self._local.conn = c
        with self._conns_lock:
            self._conns.append(c)
        return c

    def _q(self, sql, args=()):
        return self._conn().execute(sql, args).fetchall()

    def _write(self, fn):
        """Run ``fn(conn)`` in one transaction under the write lock; returns (fn result, rows changed)."""
        conn = self._conn()
        with self._wlock:
            before = conn.total_changes
            try:
                result = fn(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return result, conn.total_changes - before

    def _bump(self, conn):
        conn.execute("INSERT INTO store_meta(key, value) VALUES('data_version', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)")

    def _changed(self, conn, sql, tuples) -> int:
        """executemany + bump the data version when any row really changed; returns rows changed."""
        before = conn.total_changes
        conn.executemany(sql, tuples)
        changed = conn.total_changes - before
        if changed:
            self._bump(conn)
        return changed

    def close(self):
        with self._conns_lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()
            self._closed = True
        self._local = threading.local()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ assets
    def _seed_universe(self):
        def fn(conn):
            for a in UNIVERSE:
                self._upsert_asset(conn, a)
        self._write(fn)

    @staticmethod
    def _upsert_asset(conn, a: dict):
        if not a.get("id"):
            raise ValueError("asset needs an id")
        row = conn.execute("SELECT meta FROM assets WHERE id = ?", (a["id"],)).fetchone()
        meta = _loads(row["meta"], {}) if row else {}
        if not isinstance(meta, dict):
            meta = {}
        meta.update(a.get("meta") or {})
        if a.get("benchmark"):
            meta["benchmark"] = a["benchmark"]
        cik = a.get("cik")
        vals = [a["id"], a.get("name"), a.get("asset_class"), a.get("sector"), a.get("country"), a.get("currency"),
                a.get("yahoo"), int(cik) if cik is not None else None, num(a.get("duration")),
                num(a.get("convexity")), a.get("fred"), _dumps(meta)]
        conn.execute(
            "INSERT INTO assets(" + ",".join(ASSET_COLS) + ") VALUES(" + ",".join("?" * len(ASSET_COLS)) + ") "
            "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in ASSET_COLS[1:]), vals)

    def upsert_asset(self, asset: dict):
        self._write(lambda conn: self._upsert_asset(conn, asset))

    @staticmethod
    def _asset_row(r) -> dict:
        d = {c: r[c] for c in ASSET_COLS}
        d["meta"] = _loads(d["meta"], {}) or {}
        d["benchmark"] = d["meta"].get("benchmark", "SPY")
        return d

    def asset(self, asset_id: str) -> dict | None:
        rows = self._q("SELECT * FROM assets WHERE id = ?", (asset_id,))
        return self._asset_row(rows[0]) if rows else None

    def assets(self, asset_class: str | None = None) -> list[dict]:
        if asset_class:
            rows = self._q("SELECT * FROM assets WHERE asset_class = ? ORDER BY rowid", (asset_class,))
        else:
            rows = self._q("SELECT * FROM assets ORDER BY rowid")
        return [self._asset_row(r) for r in rows]

    # ------------------------------------------------------------------ prices
    _PRICE_UPSERT = (
        "INSERT INTO prices(asset_id, date, open, high, low, close, adj_close, volume) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(asset_id, date) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, "
        "close=excluded.close, adj_close=excluded.adj_close, volume=excluded.volume "
        "WHERE prices.open IS NOT excluded.open OR prices.high IS NOT excluded.high "
        "OR prices.low IS NOT excluded.low OR prices.close IS NOT excluded.close "
        "OR prices.adj_close IS NOT excluded.adj_close OR prices.volume IS NOT excluded.volume")

    @staticmethod
    def _price_tuples(asset_id, rows):
        out = []
        for r in rows:
            close = num(r.get("close"))
            adj = num(r.get("adj_close"))
            if adj is None:
                adj = close
            out.append((asset_id, _d(r["date"]), num(r.get("open")), num(r.get("high")), num(r.get("low")), close,
                        adj, num(r.get("volume"))))
        return out

    def upsert_prices(self, asset_id: str, rows: list[dict]) -> int:
        """Insert or update daily bars. Returns the number of rows that actually changed."""
        tuples = self._price_tuples(asset_id, rows)

        return self._write(lambda conn: self._changed(conn, self._PRICE_UPSERT, tuples))[0]

    def replace_prices(self, asset_id: str, rows: list[dict]) -> int:
        """Delete every stored bar for ``asset_id`` and insert ``rows`` (used after splits / re-adjustments)."""
        tuples = self._price_tuples(asset_id, rows)

        def fn(conn):
            conn.execute("DELETE FROM prices WHERE asset_id = ?", (asset_id,))
            conn.executemany(self._PRICE_UPSERT, tuples)
            self._bump(conn)
        self._write(fn)
        return len(tuples)

    def prices(self, asset_id: str, start=None, end=None) -> list[dict]:
        sql, args = "SELECT " + ",".join(PRICE_COLS) + " FROM prices WHERE asset_id = ?", [asset_id]
        if start is not None:
            sql += " AND date >= ?"
            args.append(_d(start))
        if end is not None:
            sql += " AND date <= ?"
            args.append(_d(end))
        return [dict(r) for r in self._q(sql + " ORDER BY date", args)]

    def last_price_date(self, asset_id: str) -> str | None:
        return self._q("SELECT max(date) AS d FROM prices WHERE asset_id = ?", (asset_id,))[0]["d"]

    def first_price_date(self, asset_id: str) -> str | None:
        return self._q("SELECT min(date) AS d FROM prices WHERE asset_id = ?", (asset_id,))[0]["d"]

    def price_count(self, asset_id: str) -> int:
        return self._q("SELECT count(*) AS n FROM prices WHERE asset_id = ?", (asset_id,))[0]["n"]

    # ------------------------------------------------------------------ macro
    _MACRO_UPSERT = ("INSERT INTO macro(series, date, value, published) VALUES(?,?,?,?) ON CONFLICT(series, date) "
                     "DO UPDATE SET value=excluded.value, published=excluded.published "
                     "WHERE macro.value IS NOT excluded.value OR macro.published IS NOT excluded.published")

    @staticmethod
    def _macro_tuples(series, rows):
        """Rows are ``(date, value)`` or ``(date, value, published)``; published None = unknown."""
        out = []
        for r in rows:
            if len(r) == 2:
                d, v = r
                pub = None
            elif len(r) == 3:
                d, v, pub = r
            else:
                raise ValueError("macro rows are (date, value) or (date, value, published)")
            out.append((series, _d(d), num(v), _d(pub)))
        return out

    def upsert_macro(self, series: str, rows) -> int:
        """Insert or update observations; returns the number of rows that actually changed."""
        tuples = self._macro_tuples(series, rows)
        return self._write(lambda conn: self._changed(conn, self._MACRO_UPSERT, tuples))[0]

    def replace_macro(self, series: str, rows, kind: str | None = None) -> int:
        """Delete every stored observation of ``series`` and insert ``rows`` (used when the kind of data changes,
        so values of the old kind cannot linger); optionally records ``kind`` in the same transaction."""
        tuples = self._macro_tuples(series, rows)
        if kind is not None and kind not in MACRO_KINDS:
            raise ValueError(f"unknown macro kind {kind!r}")

        def fn(conn):
            conn.execute("DELETE FROM macro WHERE series = ?", (series,))
            conn.executemany(self._MACRO_UPSERT, tuples)
            if kind is not None:
                self._set_kind(conn, series, kind)
            self._bump(conn)
        self._write(fn)
        return len(tuples)

    def macro(self, series: str, start=None, end=None) -> list[tuple]:
        """``[(date, value)]`` ordered by date (see :meth:`macro_rows` for publication dates)."""
        return [(d, v) for d, v, _p in self.macro_rows(series, start, end)]

    def macro_rows(self, series: str, start=None, end=None) -> list[tuple]:
        """``[(date, value, published)]`` ordered by date; ``published`` is None when unknown (latest vintage)."""
        sql, args = "SELECT date, value, published FROM macro WHERE series = ?", [series]
        if start is not None:
            sql += " AND date >= ?"
            args.append(_d(start))
        if end is not None:
            sql += " AND date <= ?"
            args.append(_d(end))
        return [(r["date"], r["value"], r["published"]) for r in self._q(sql + " ORDER BY date", args)]

    def macro_kind(self, series: str) -> str | None:
        """"first_release", "latest_vintage", or None (unknown: stored before kinds were recorded)."""
        k = self.kv_get(f"macro_kind:{series}")
        return k if k in MACRO_KINDS else None

    def _set_kind(self, conn, series, kind) -> bool:
        key = f"macro_kind:{series}"
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        payload = _dumps(kind)
        if row is not None and row[0] == payload:
            return False
        conn.execute("INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                     "value=excluded.value, updated_at=excluded.updated_at", (key, payload, _now()))
        return True

    def set_macro_kind(self, series: str, kind: str):
        """Record which kind of values ``series`` holds; a change bumps the data version (cached results use it)."""
        if kind not in MACRO_KINDS:
            raise ValueError(f"unknown macro kind {kind!r}")

        def fn(conn):
            if self._set_kind(conn, series, kind):
                self._bump(conn)
        self._write(fn)

    def last_macro_date(self, series: str) -> str | None:
        return self._q("SELECT max(date) AS d FROM macro WHERE series = ?", (series,))[0]["d"]

    # ------------------------------------------------------------------ fundamentals
    def upsert_fundamentals(self, asset_id: str, rows: list[dict]) -> int:
        tuples = [(asset_id, r["concept"], _d(r["period_end"]), _d(r["filed"]), num(r.get("value")), r.get("form"),
                   r.get("fp")) for r in rows]

        sql = ("INSERT INTO fundamentals(asset_id, concept, period_end, filed, value, form, fp) "
               "VALUES(?,?,?,?,?,?,?) ON CONFLICT(asset_id, concept, period_end, filed) DO UPDATE SET "
               "value=excluded.value, form=excluded.form, fp=excluded.fp WHERE fundamentals.value IS NOT "
               "excluded.value OR fundamentals.form IS NOT excluded.form OR fundamentals.fp IS NOT excluded.fp")
        return self._write(lambda conn: self._changed(conn, sql, tuples))[0]

    def fundamentals(self, asset_id: str, concept: str | None = None) -> list[dict]:
        sql = "SELECT concept, period_end, filed, value, form, fp FROM fundamentals WHERE asset_id = ?"
        args = [asset_id]
        if concept:
            sql += " AND concept = ?"
            args.append(concept)
        return [dict(r) for r in self._q(sql + " ORDER BY concept, filed, period_end", args)]

    # ------------------------------------------------------------------ kv cache
    def kv_get(self, key: str, default=None):
        rows = self._q("SELECT value FROM kv WHERE key = ?", (key,))
        if not rows:
            return default
        v = _loads(rows[0]["value"], default)
        return v

    # ------------------------------------------------------------------ ML Lab research records
    def put_lab_records(self, asset_id: str, horizon: str, version: str, data_version: str, rows: list):
        """Matured point-in-time Shaffer records for one asset and horizon (columnar, zlib-compressed JSON)."""
        import zlib
        blob = zlib.compress(json.dumps(rows, separators=(",", ":")).encode(), 6)
        self._write(lambda conn: conn.execute(
            "INSERT OR REPLACE INTO lab_records(asset_id, horizon, version, data_version, created, n, data) VALUES(?,?,?,?,?,?,?)",
            (asset_id, horizon, version, data_version, _now(), len(rows), blob)))

    def lab_records(self, version: str, horizon: str | None = None) -> list[dict]:
        import zlib
        sql, args = "SELECT asset_id, horizon, data_version, created, n, data FROM lab_records WHERE version = ?", [version]
        if horizon:
            sql += " AND horizon = ?"; args.append(horizon)
        out = []
        for r in self._q(sql + " ORDER BY asset_id", args):
            d = dict(r)
            d["rows"] = json.loads(zlib.decompress(d.pop("data")).decode())
            out.append(d)
        return out

    def lab_record_summary(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT version, horizon, count(*) AS assets, sum(n) AS records, max(created) AS created "
                                         "FROM lab_records GROUP BY version, horizon ORDER BY version, horizon")]

    def kv_set(self, key: str, value):
        payload = _dumps(value)
        self._write(lambda conn: conn.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at", (key, payload, _now())))

    def kv_delete(self, key: str):
        self._write(lambda conn: conn.execute("DELETE FROM kv WHERE key = ?", (key,)))

    def kv_keys(self, prefix: str = "") -> list[str]:
        rows = self._q("SELECT key FROM kv WHERE substr(key, 1, ?) = ? ORDER BY key", (len(prefix), prefix))
        return [r["key"] for r in rows]

    # ------------------------------------------------------------------ fetch log
    def log_fetch(self, source: str, key: str, status: str, note: str = ""):
        note = (note or "")[:500]
        self._write(lambda conn: conn.execute(
            "INSERT INTO fetch_log(source, key, fetched_at, status, note) VALUES(?,?,?,?,?)",
            (source, key, _now(), status, note)))

    def fetch_log(self, limit: int = 100) -> list[dict]:
        rows = self._q("SELECT source, key, fetched_at, status, note FROM fetch_log ORDER BY rowid DESC LIMIT ?",
                       (int(limit),))
        return [dict(r) for r in rows]

    def last_fetch(self, source: str, key: str, status: str | None = "ok") -> str | None:
        """ISO timestamp of the latest fetch_log entry for (source, key) [with that status], or None."""
        sql, args = "SELECT max(fetched_at) AS t FROM fetch_log WHERE source = ? AND key = ?", [source, key]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        return self._q(sql, args)[0]["t"]

    # ------------------------------------------------------------------ predictions
    @staticmethod
    def _pred_row(r) -> dict:
        d = dict(r)
        d["detail"] = _loads(d.get("detail"), None)
        if d.get("regime"):
            d["regime"] = _loads(d["regime"], d["regime"])
        return d

    def add_prediction(self, p: dict) -> int:
        """Append a forecast. The ledger is append-only: a forecast is never edited except for grading once."""
        p = {**p, "created_at": p.get("created_at") or _now(), "source": p.get("source") or "live"}
        if isinstance(p.get("regime"), dict):
            p["regime"] = _dumps(p["regime"])
        vals = [p.get(c) for c in PRED_COLS]
        for i, c in enumerate(PRED_COLS):
            if c in ("made_on", "target_date", "scored_on") and vals[i] is not None:
                vals[i] = _d(vals[i])
        vals[PRED_COLS.index("detail")] = _dumps(p.get("detail")) if p.get("detail") is not None else None
        cur, _ = self._write(lambda conn: conn.execute(
            "INSERT INTO predictions(" + ",".join(PRED_COLS) + ") VALUES(" + ",".join("?" * len(PRED_COLS)) + ")",
            vals))
        return cur.lastrowid

    def predictions(self, asset_id=None, horizon=None, unscored_only=False) -> list[dict]:
        sql, args = "SELECT * FROM predictions WHERE 1=1", []
        if asset_id is not None:
            sql += " AND asset_id = ?"
            args.append(asset_id)
        if horizon is not None:
            sql += " AND horizon = ?"
            args.append(horizon)
        if unscored_only:
            sql += " AND scored_on IS NULL"
        return [self._pred_row(r) for r in self._q(sql + " ORDER BY made_on, id", args)]

    def score_prediction(self, pred_id: int, realized, error, scored_on, correct=None):
        """Grade a forecast once: only rows not graded yet are touched."""
        self._write(lambda conn: conn.execute(
            "UPDATE predictions SET realized = ?, error = ?, scored_on = ?, correct = ? WHERE id = ? AND realized IS NULL",
            (num(realized), num(error), _d(scored_on), None if correct is None else int(bool(correct)), int(pred_id))))

    # ------------------------------------------------------------------ model runs / backtests
    def add_model_run(self, run: dict) -> int:
        vals = []
        for c in RUN_COLS:
            v = run.get(c)
            if c in ("features", "params", "metrics"):
                v = _dumps(v) if v is not None else None
            elif c == "created_at" and v is None:
                v = _now()
            vals.append(v)
        cur, _ = self._write(lambda conn: conn.execute(
            "INSERT INTO model_runs(" + ",".join(RUN_COLS) + ") VALUES(" + ",".join("?" * len(RUN_COLS)) + ")",
            vals))
        return cur.lastrowid

    def model_runs(self, key=None, horizon=None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM model_runs WHERE 1=1", []
        if key is not None:
            sql += " AND key = ?"
            args.append(key)
        if horizon is not None:
            sql += " AND horizon = ?"
            args.append(horizon)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        out = []
        for r in self._q(sql, args):
            d = dict(r)
            for c in ("features", "params", "metrics"):
                d[c] = _loads(d[c], None)
            out.append(d)
        return out

    def add_backtest(self, spec, result) -> int:
        cur, _ = self._write(lambda conn: conn.execute(
            "INSERT INTO backtests(spec, result, created_at) VALUES(?,?,?)", (_dumps(spec), _dumps(result), _now())))
        return cur.lastrowid

    def backtests(self, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM backtests ORDER BY id DESC LIMIT ?", (int(limit),))
        return [{"id": r["id"], "spec": _loads(r["spec"]), "result": _loads(r["result"]), "created_at": r["created_at"]}
                for r in rows]

    # ------------------------------------------------------------------ portfolio
    def create_portfolio(self, portfolio_id: str, name: str, base_currency: str = "USD"):
        self._write(lambda conn: conn.execute(
            "INSERT INTO portfolios(id, name, base_currency, created) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name, base_currency=excluded.base_currency",
            (portfolio_id, name, base_currency, _now())))

    def portfolios(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, name, base_currency, created FROM portfolios ORDER BY created, id")]

    def delete_portfolio(self, portfolio_id: str):
        def fn(conn):
            for t in ("transactions", "snapshots"):
                conn.execute(f"DELETE FROM {t} WHERE portfolio_id = ?", (portfolio_id,))
            conn.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
        self._write(fn)

    def add_transaction(self, tx: dict) -> int:
        tx = {**tx, "created_at": tx.get("created_at") or _now()}
        vals = [tx.get(c) for c in TX_COLS]
        vals[1] = _d(vals[1])
        vals[9] = _d(vals[9]) if vals[9] else vals[1]
        for i in (4, 5, 6):  # quantity, price, fee
            vals[i] = num(vals[i])
        if vals[6] is None:
            vals[6] = 0.0
        cur, _ = self._write(lambda conn: conn.execute(
            "INSERT INTO transactions(" + ",".join(TX_COLS) + ") VALUES(" + ",".join("?" * len(TX_COLS)) + ")", vals))
        return cur.lastrowid

    def add_transactions(self, txs: list) -> list:
        """Insert several transactions in ONE database transaction (all or none); returns their ids."""
        rows = []
        for tx in txs:
            tx = {**tx, "created_at": tx.get("created_at") or _now()}
            vals = [tx.get(c) for c in TX_COLS]
            vals[1] = _d(vals[1])
            vals[9] = _d(vals[9]) if vals[9] else vals[1]
            for i in (4, 5, 6):
                vals[i] = num(vals[i])
            if vals[6] is None:
                vals[6] = 0.0
            rows.append(vals)

        def fn(conn):
            ids = []
            for vals in rows:
                cur = conn.execute("INSERT INTO transactions(" + ",".join(TX_COLS) + ") VALUES(" + ",".join("?" * len(TX_COLS)) + ")", vals)
                ids.append(cur.lastrowid)
            return ids
        ids, _ = self._write(fn)
        return ids

    # ------------------------------------------------------------------ option chains (when a source is available)
    def upsert_option_quotes(self, rows: list) -> int:
        vals = [[r.get(c) for c in OPTION_COLS] for r in rows]
        self._write(lambda conn: conn.executemany(
            "INSERT OR REPLACE INTO option_quotes(" + ",".join(OPTION_COLS) + ") VALUES(" + ",".join("?" * len(OPTION_COLS)) + ")", vals))
        return len(vals)

    def option_chain(self, underlying: str, asof: str, max_age_days: int = 7) -> list[dict]:
        """The most recent chain snapshot of `underlying` on or before `asof` (none older than max_age_days)."""
        import datetime as _d
        row = self._q("SELECT MAX(asof) AS a FROM option_quotes WHERE underlying = ? AND asof <= ?", [underlying, asof])
        a = row[0]["a"] if row else None
        if not a or (_d.date.fromisoformat(asof[:10]) - _d.date.fromisoformat(a[:10])).days > max_age_days:
            return []
        return [dict(r) for r in self._q("SELECT * FROM option_quotes WHERE underlying = ? AND asof = ? ORDER BY expiry, strike", [underlying, a])]

    def option_underlyings(self) -> list[str]:
        return [r["underlying"] for r in self._q("SELECT DISTINCT underlying FROM option_quotes", [])]

    # ------------------------------------------------------------------ the hedge ledger (append-only)
    def add_hedge(self, rec: dict) -> int:
        vals = []
        for c in HEDGE_COLS:
            v = rec.get(c)
            if c == "created_at":
                v = v or _now()
            if isinstance(v, (dict, list)):
                v = _dumps(v)
            vals.append(v)
        cur, _ = self._write(lambda conn: conn.execute(
            "INSERT INTO hedge_recommendations(" + ",".join(HEDGE_COLS) + ") VALUES(" + ",".join("?" * len(HEDGE_COLS)) + ")", vals))
        return cur.lastrowid

    def hedges(self, limit: int = 200, source: str | None = None, ungraded_before: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM hedge_recommendations WHERE 1=1", []
        if source:
            sql += " AND source = ?"; args.append(source)
        if ungraded_before:
            sql += " AND graded_on IS NULL AND eval_date <= ?"; args.append(ungraded_before)
        rows = [dict(r) for r in self._q(sql + " ORDER BY id DESC LIMIT ?", args + [int(limit)])]
        for r in rows:
            for k in ("candidates", "selected", "detail"):
                r[k] = _loads(r.get(k), None)
        return rows

    def grade_hedge(self, hedge_id: int, fields: dict) -> bool:
        """Record the realised outcome ONCE (only while ungraded); the recommendation itself is never rewritten."""
        sets = [c for c in HEDGE_GRADE_COLS if c in fields]
        if not sets:
            return False
        vals = [num(fields[c]) for c in sets] + [_now(), int(hedge_id)]
        _, n = self._write(lambda conn: conn.execute(
            "UPDATE hedge_recommendations SET " + ",".join(f"{c} = ?" for c in sets) + ", graded_on = ? WHERE id = ? AND graded_on IS NULL", vals))
        return n > 0

    def transactions(self, portfolio_id: str, include_voided: bool = False) -> list[dict]:
        sql = "SELECT * FROM transactions WHERE portfolio_id = ?" + ("" if include_voided else " AND voided_at IS NULL")
        return [dict(r) for r in self._q(sql + " ORDER BY date, id", (portfolio_id,))]

    def transaction(self, tx_id: int) -> dict | None:
        r = self._q("SELECT * FROM transactions WHERE id = ?", (int(tx_id),))
        return dict(r[0]) if r else None

    def void_transaction(self, tx_id: int):
        """Soft delete: the row stays (with the time it was voided) and the replay ignores it."""
        self._write(lambda conn: conn.execute("UPDATE transactions SET voided_at = ? WHERE id = ? AND voided_at IS NULL", (_now(), int(tx_id))))

    def delete_transaction(self, tx_id: int):
        self._write(lambda conn: conn.execute("DELETE FROM transactions WHERE id = ?", (int(tx_id),)))

    # ------------------------------------------------------------------ audit trail
    def audit(self, action: str, entity: str, detail=None):
        self._write(lambda conn: conn.execute("INSERT INTO audit_log(at, action, entity, detail) VALUES(?,?,?,?)",
                                              (_now(), str(action), str(entity), _dumps(detail))))

    def audit_log(self, limit: int = 200) -> list[dict]:
        return [{**dict(r), "detail": _loads(r["detail"])} for r in self._q("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),))]

    # ------------------------------------------------------------------ corporate actions (split ratios, cash dividends per share)
    def upsert_actions(self, asset_id: str, rows) -> int:
        """rows: [(date, kind, value)] with kind "SPLIT" (new shares per old share) or "DIVIDEND" (cash per share)."""
        tuples = [(asset_id, _d(d), str(k), num(v)) for d, k, v in rows if num(v) is not None]
        sql = ("INSERT INTO corporate_actions(asset_id, date, kind, value) VALUES(?,?,?,?) ON CONFLICT(asset_id, date, kind) "
               "DO UPDATE SET value=excluded.value WHERE corporate_actions.value IS NOT excluded.value")
        return self._write(lambda conn: self._changed(conn, sql, tuples))[0]

    def actions(self, asset_id: str, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT date, kind, value FROM corporate_actions WHERE asset_id = ?", [asset_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return [dict(r) for r in self._q(sql + " ORDER BY date", args)]

    def save_snapshot(self, portfolio_id: str, date, nav, detail=None):
        self._write(lambda conn: conn.execute(
            "INSERT INTO snapshots(portfolio_id, date, nav, detail) VALUES(?,?,?,?) ON CONFLICT(portfolio_id, date) "
            "DO UPDATE SET nav=excluded.nav, detail=excluded.detail",
            (portfolio_id, _d(date), num(nav), _dumps(detail))))

    def snapshots(self, portfolio_id: str) -> list[dict]:
        rows = self._q("SELECT * FROM snapshots WHERE portfolio_id = ? ORDER BY date", (portfolio_id,))
        return [{"portfolio_id": r["portfolio_id"], "date": r["date"], "nav": r["nav"],
                 "detail": _loads(r["detail"])} for r in rows]

    # ------------------------------------------------------------------ watchlist
    def watch(self, asset_id: str, note: str = ""):
        self._write(lambda conn: conn.execute(
            "INSERT INTO watchlist(asset_id, added, note) VALUES(?,?,?) ON CONFLICT(asset_id) DO UPDATE SET "
            "note=excluded.note", (asset_id, _now(), note or "")))

    def unwatch(self, asset_id: str):
        self._write(lambda conn: conn.execute("DELETE FROM watchlist WHERE asset_id = ?", (asset_id,)))

    def watchlist(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT asset_id, added, note FROM watchlist ORDER BY added, asset_id")]

    # ------------------------------------------------------------------ cache invalidation
    def data_version(self) -> str:
        """Opaque token that changes whenever prices, macro or fundamentals change (in any process)."""
        row = self._q("SELECT value FROM store_meta WHERE key = 'data_version'")
        counter = row[0]["value"] if row else "0"
        tail = self._q("SELECT (SELECT max(rowid) FROM prices) AS p, (SELECT max(rowid) FROM macro) AS m, "
                       "(SELECT max(rowid) FROM fundamentals) AS f")[0]
        raw = f"{counter}|{tail['p']}|{tail['m']}|{tail['f']}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


__all__ = ["Store", "default_path", "SCHEMA"]

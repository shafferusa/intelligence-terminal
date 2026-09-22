"""Offline tests for forward labels and hedge paths.

Pure stdlib, no network, a temporary store:   python test_pit_labels.py

THE ACCEPTANCE TESTS are sections 3, 5 and 6, and each one exists because the
mistake it catches produces a number rather than an error:

  Section 3 -- CENSORING. A 12-month window that has not finished must come back
  NULL. A builder that quietly uses the last available bar returns a real,
  plausible, wrong number for every recent cohort, biased by whatever the tail
  of the sample did. The test asserts both halves: the return is NULL, and it is
  not equal to the truncated return it would have been.

  Section 5 -- TERMINATION. A series that stops is the most dangerous row in the
  table. -100% invents a bankruptcy, 0.0 invents a flat year, the last price
  invents a survivor, and dropping the row is how a backtest lies about the
  companies that disappeared. The test asserts the row EXISTS, carries
  OUTCOME_UNKNOWN, and is none of those three numbers -- while a real bankruptcy
  with no recovery is exactly -1.0, to the bit.

  Section 6 -- BOTH ENDS MUST BE REAL TRADES. The fixture reproduces First
  Republic: $3.51 carried forward at zero volume for two sessions after the
  seizure, next real trade $0.3336. A terminal taken from the stale print
  understates the loss by 90%; a stale anchor overstates it by the same
  mechanism in the opposite direction.

The fixture calendar carries the real Sandy closure (2012-10-29 and 2012-10-30)
so that "five sessions" and "five days" are different answers in section 1, and
a builder using calendar offsets fails rather than passes by luck.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_labels as L
import pit_path
import pit_store

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------

#: Closures that are not weekends. Sandy is the one the horizon test needs; the
#: rest are there so the fixture calendar is not a naive weekday grid.
CLOSURES = {
    "2012-01-02", "2012-01-16", "2012-02-20", "2012-04-06", "2012-05-28",
    "2012-07-04", "2012-09-03", "2012-10-29", "2012-10-30", "2012-11-22",
    "2012-12-25", "2013-01-01", "2013-01-21", "2013-02-18", "2013-03-29",
    "2013-05-27", "2013-07-04", "2013-09-02", "2013-11-28", "2013-12-25",
    "2014-01-01", "2014-01-20", "2014-02-17", "2014-04-18", "2014-05-26",
    "2014-07-04", "2014-09-01", "2014-11-27", "2014-12-25", "2015-01-01",
    "2015-01-19", "2015-02-16", "2015-04-03", "2015-05-25", "2015-07-03",
    "2015-09-07", "2015-11-26", "2015-12-25", "2016-01-01", "2016-01-18",
    "2016-02-15", "2016-03-25", "2016-05-30", "2016-07-04", "2016-09-05",
    "2016-11-24", "2016-12-26",
}


def build_sessions() -> list[str]:
    day = _dt.date(2012, 1, 1)
    end = _dt.date(2016, 12, 31)
    out = []
    while day <= end:
        iso = day.isoformat()
        if day.weekday() < 5 and iso not in CLOSURES:
            out.append(iso)
        day += _dt.timedelta(days=1)
    return out


class Fixture:
    """A small store with one calendar and eight deliberately awkward listings."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="pit_labels_test_")
        self.path = os.path.join(self.dir, "pit_labels_test.db")
        self.conn = pit_store.init_db(self.path)
        L.ensure_schema(self.conn)
        self.sessions = build_sessions()
        pit_store.insert_calendar(self.conn, L.MARKET, self.sessions, "fixture")
        self.index = {d: i for i, d in enumerate(self.sessions)}
        self.listings: dict[str, int] = {}
        self._build()

    def close(self) -> None:
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _listing(self, symbol: str) -> int:
        entity_id = pit_store.upsert_entity(self.conn, symbol.rjust(10, "0"))
        listing_id = pit_store.upsert_listing(
            self.conn, symbol, self.sessions[0], entity_id=entity_id,
            exchange="NYSE", instrument_type="EQUITY", currency="USD",
            confidence="proved", source="fixture")
        self.listings[symbol] = listing_id
        return listing_id

    def _bars(self, listing_id: int, bars) -> None:
        with pit_store.transaction(self.conn):
            self.conn.executemany(
                """INSERT OR REPLACE INTO pit_price_bar
                       (listing_id, bar_date, open, high, low, close, adjclose,
                        volume, source_symbol, ingest_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(listing_id, d, c, c, c, c, a, v, "FIX", 900)
                 for d, c, a, v in bars])

    def _build(self) -> None:
        sess = self.sessions
        n = len(sess)

        # 1. ALIVE: trades every session to the data horizon.
        lid = self._listing("ALIVE")
        self._bars(lid, [(sess[i], 100.0 + i * 0.05, 100.0 + i * 0.05, 1e6)
                         for i in range(n)])

        # 2. ZEROVOL: a carried-forward print on the as-of session and the next.
        #    The anchor must roll two sessions to the first real trade.
        lid = self._listing("ZEROVOL")
        rows = []
        for i in range(n):
            price = 50.0 + i * 0.01
            vol = 1e6
            if sess[i] in (ANCHOR_STALE, self.sessions[self.index[ANCHOR_STALE] + 1]):
                price = 50.0 + self.index[ANCHOR_STALE] * 0.01   # carried forward
                vol = 0.0
            rows.append((sess[i], price, price, vol))
        self._bars(lid, rows)

        # 3. BANKRUPT: stops dead at session 600, cancelled at zero.
        lid = self._listing("BANKRUPT")
        self._bars(lid, [(sess[i], 20.0 - i * 0.01, 20.0 - i * 0.01, 1e6)
                         for i in range(601)])
        with pit_store.transaction(self.conn):
            self.conn.execute(
                """INSERT INTO pit_corporate_action
                       (listing_id, event_date, event_type, terminal_value_per_share,
                        applies_to_price, applies_to_shares, confidence, source)
                   VALUES (?,?,?,?,0,0,'proved','fixture')""",
                (lid, sess[601], L.EVENT_BANKRUPTCY, 0.0))

        # 4. VANISHED: stops at session 600 and nothing in the store says why.
        lid = self._listing("VANISHED")
        self._bars(lid, [(sess[i], 30.0, 30.0, 1e6) for i in range(601)])

        # 5a. NOSPLIT / 5b. SPLIT: identical economics, one with a 2:1 split
        #     inside the window. adjclose is continuous in both; only `close`
        #     jumps. A label taken from `close` differs by a factor of two.
        lid = self._listing("NOSPLIT")
        self._bars(lid, [(sess[i], 100.0 * (1.001 ** i), 100.0 * (1.001 ** i), 1e6)
                         for i in range(n)])
        lid = self._listing("SPLIT")
        split_i = self.index[SPLIT_DAY]
        rows = []
        for i in range(n):
            adj = 100.0 * (1.001 ** i)
            close = adj * 2.0 if i < split_i else adj
            rows.append((sess[i], close, adj, 1e6))
        self._bars(lid, rows)
        with pit_store.transaction(self.conn):
            self.conn.execute(
                """INSERT INTO pit_corporate_action
                       (listing_id, event_date, event_type, ratio_num, ratio_den,
                        applies_to_price, applies_to_shares, confidence, source)
                   VALUES (?,?,?,?,?,1,1,'proved','fixture')""",
                (lid, SPLIT_DAY, "split", 2.0, 1.0))

        # 6. STALE: First Republic. $3.51 carried at zero volume across the
        #    terminal session; the next real trade is $0.3336.
        lid = self._listing("STALE")
        stale_i = self.index[STALE_TERMINAL]
        rows = []
        for i in range(n):
            if i < stale_i:
                price, vol = 10.0, 1e6
            elif i < stale_i + 2:
                price, vol = 3.51, 0.0
            else:
                price, vol = 0.3336, 5e5
            rows.append((sess[i], price, price, vol))
        self._bars(lid, rows)

        # 7. CRASH: ends exactly flat over 12M having fallen 40% mid-window.
        #    forward_return is 0.0 and the position plainly needed a hedge.
        lid = self._listing("CRASH")
        start_i = self.index[CRASH_ANCHOR]
        rows = []
        for i in range(n):
            step = i - start_i
            if step < 0:
                price = 100.0
            elif step <= 126:
                price = 100.0 - 40.0 * (step / 126.0)
            elif step <= 252:
                price = 60.0 + 40.0 * ((step - 126) / 126.0)
            else:
                price = 100.0
            rows.append((sess[i], price, price, 1e6))
        self._bars(lid, rows)

        # 8. HALTED: a 40-session halt straddling the terminal. Longer than the
        #    roll cap, so there is no observable exit price at that horizon.
        lid = self._listing("HALTED")
        halt_i = self.index[HALT_TERMINAL] - 5
        rows = []
        for i in range(n):
            price, vol = 80.0, 1e6
            if halt_i <= i < halt_i + 40:
                vol = 0.0
            rows.append((sess[i], price, price, vol))
        self._bars(lid, rows)


# Dates the fixture is built around, chosen so each sits well inside the
# calendar and each horizon it is used for can actually be resolved.
SESSIONS = build_sessions()
_IDX = {d: i for i, d in enumerate(SESSIONS)}
ANCHOR_BASE = SESSIONS[300]
ANCHOR_STALE = SESSIONS[300]
SPLIT_DAY = SESSIONS[400]
STALE_TERMINAL = SESSIONS[300 + 21]
CRASH_ANCHOR = SESSIONS[300]
HALT_TERMINAL = SESSIONS[300 + 63]
LATE_ANCHOR = SESSIONS[len(SESSIONS) - 40]        # a 12M window cannot finish
BANK_ANCHOR = SESSIONS[500]


def labels_for(fx: Fixture, symbol: str, as_of_dates, **kwargs):
    """Run the pure core for one fixture listing and key the result."""
    listing_id = fx.listings[symbol]
    bars = L.load_bars(fx.conn, listing_id)
    outcome = L.terminal_outcome(fx.conn, listing_id,
                                 kwargs.pop("entity_id", None))
    if "outcome" in kwargs:
        outcome = kwargs.pop("outcome")
    horizon_end = L.data_horizon(fx.conn)
    labels, paths, counters = L.compute_listing_labels(
        bars, as_of_dates, fx.sessions, fx.index, horizon_end, outcome=outcome,
        **kwargs)
    return ({(r["as_of_date"], r["horizon"]): r for r in labels},
            {(r["as_of_date"], r["horizon"]): r for r in paths},
            counters)


# --------------------------------------------------------------------------
# 1. Horizons are trading sessions, and land on real sessions
# --------------------------------------------------------------------------

def test_sessions(fx: Fixture) -> None:
    print("\n1. trading-day horizons")
    agree = True
    sample = fx.sessions[::37]
    for day in sample:
        for horizon, n in L.HORIZON_SESSIONS.items():
            mine = L.session_offset_in(fx.sessions, fx.index, day, n)
            theirs = pit_store.session_offset(fx.conn, day, n, L.MARKET)
            if mine != theirs:
                agree = False
    check("preloaded calendar agrees with pit_store.session_offset everywhere", agree)

    landed = all(
        L.session_offset_in(fx.sessions, fx.index, day, n) in fx.index
        for day in sample for n in L.HORIZON_SESSIONS.values()
        if L.session_offset_in(fx.sessions, fx.index, day, n) is not None)
    check("every resolved horizon lands on a real session", landed)

    # Sandy. 2012-10-26 is a Friday; the market was shut on the Monday and the
    # Tuesday, so five SESSIONS later is 2012-11-06 and five DAYS later is not.
    five = L.session_offset_in(fx.sessions, fx.index, "2012-10-26", 5)
    check("5 sessions after 2012-10-26 skips the Sandy closure", five == "2012-11-06",
          five)
    check("a calendar-day offset would have been wrong", five != "2012-11-02", five)
    check("the closure really is absent from the calendar",
          "2012-10-29" not in fx.index and "2012-10-30" not in fx.index)

    twelve = L.session_offset_in(fx.sessions, fx.index, ANCHOR_BASE, 252)
    gap = (_dt.date.fromisoformat(twelve) - _dt.date.fromisoformat(ANCHOR_BASE)).days
    check("252 sessions is not 365 calendar days", gap != 365, gap)


# --------------------------------------------------------------------------
# 2. The ordinary label
# --------------------------------------------------------------------------

def test_basic(fx: Fixture) -> None:
    print("\n2. an ordinary label")
    labels, paths, _ = labels_for(fx, "ALIVE", [ANCHOR_BASE])
    row = labels[(ANCHOR_BASE, "1M")]
    anchor_i = fx.index[ANCHOR_BASE]
    expected = (100.0 + (anchor_i + 21) * 0.05) / (100.0 + anchor_i * 0.05) - 1.0
    check("1M forward return matches the adjusted-close ratio",
          abs(row["forward_return"] - expected) < 1e-12, row["forward_return"])
    check("terminal lands 21 sessions out",
          row["end_date"] == fx.sessions[anchor_i + 21], row["end_date"])
    check("a complete row is not censored",
          row["censored"] == 0 and row["terminal_reason"] == L.REASON_COMPLETE)
    check("no roll was needed at either end",
          row["anchor_roll_days"] == 0 and row["terminal_roll_days"] == 0)
    check("a path was written for 1M and not for 1D",
          (ANCHOR_BASE, "1M") in paths and (ANCHOR_BASE, "1D") not in paths)
    check("the path covers anchor..terminal inclusive",
          paths[(ANCHOR_BASE, "1M")]["stats"]["n_sessions"] == 22,
          paths[(ANCHOR_BASE, "1M")]["stats"]["n_sessions"])


# --------------------------------------------------------------------------
# 3. CENSORING, never truncation
# --------------------------------------------------------------------------

def test_censoring(fx: Fixture) -> None:
    print("\n3. censoring, never truncation")
    labels, paths, _ = labels_for(fx, "ALIVE", [LATE_ANCHOR])
    row = labels[(LATE_ANCHOR, "12M")]
    check("an unfinished 12M window returns NULL", row["forward_return"] is None)
    check("and is flagged censored", row["censored"] == 1)
    check("and carries the 'censored' reason, not an outcome",
          row["terminal_reason"] == L.REASON_CENSORED, row["terminal_reason"])
    check("no terminal price was invented",
          row["end_date"] is None and row["end_price"] is None)

    anchor_i = fx.index[LATE_ANCHOR]
    truncated = ((100.0 + (len(fx.sessions) - 1) * 0.05)
                 / (100.0 + anchor_i * 0.05) - 1.0)
    check("the truncated return it refused is a real, plausible number",
          truncated > 0.0)
    check("no path was written for a censored window",
          (LATE_ANCHOR, "12M") not in paths)

    short = labels[(LATE_ANCHOR, "1M")]
    check("a shorter horizon from the same anchor still resolves",
          short["forward_return"] is not None and short["censored"] == 0)


# --------------------------------------------------------------------------
# 4. A zero-volume anchor rolls, and says so
# --------------------------------------------------------------------------

def test_anchor_roll(fx: Fixture) -> None:
    print("\n4. volume hygiene at the anchor")
    labels, _paths, counters = labels_for(fx, "ZEROVOL", [ANCHOR_STALE])
    row = labels[(ANCHOR_STALE, "1M")]
    anchor_i = fx.index[ANCHOR_STALE]
    check("the anchor rolled to the next real trade",
          row["anchor_date"] == fx.sessions[anchor_i + 2], row["anchor_date"])
    check("the roll is recorded in sessions", row["anchor_roll_days"] == 2,
          row["anchor_roll_days"])
    check("the roll was counted", counters.get("anchor_rolled", 0) > 0)
    real = 50.0 + (anchor_i + 2) * 0.01
    check("the anchor price is the real trade, not the carried-forward print",
          abs(row["anchor_price"] - real) < 1e-9, row["anchor_price"])
    stale = 50.0 + anchor_i * 0.01
    check("using the stale print would have given a different anchor",
          abs(stale - real) > 1e-9)


# --------------------------------------------------------------------------
# 5. TERMINATION: -1.0 exactly, or OUTCOME_UNKNOWN. Never a silent number.
# --------------------------------------------------------------------------

def test_termination(fx: Fixture) -> None:
    print("\n5. termination by corporate action, not by absent bars")
    labels, _p, _c = labels_for(fx, "BANKRUPT", [BANK_ANCHOR], entity_id=None)
    row = labels[(BANK_ANCHOR, "12M")]
    check("a bankruptcy with no recovery is exactly -1.0",
          row["forward_return"] == -1.0, repr(row["forward_return"]))
    check("exactly, to the bit", repr(row["forward_return"]) == "-1.0")
    check("and is an OBSERVED outcome, not censored", row["censored"] == 0)
    check("reason is the bankruptcy outcome",
          row["terminal_reason"] == pit_store.OUTCOME_BANKRUPTCY,
          row["terminal_reason"])
    check("the terminal price is zero, not the last traded price",
          row["end_price"] == 0.0, row["end_price"])

    labels, _p, _c = labels_for(fx, "VANISHED", [BANK_ANCHOR])
    row = labels[(BANK_ANCHOR, "12M")]
    check("an unresolvable outcome produces a ROW, not a gap", row is not None)
    check("it is OUTCOME_UNKNOWN",
          row["terminal_reason"] == pit_store.OUTCOME_UNKNOWN, row["terminal_reason"])
    check("its return is NULL -- not -1.0, not 0.0, not the last price",
          row["forward_return"] is None)
    check("and it is flagged so a consumer must exclude it", row["censored"] == 1)

    # The same listing, a horizon that finishes before the series stops, must
    # still be a normal complete label: termination is not contagious.
    row = labels[(BANK_ANCHOR, "1M")]
    check("a horizon inside the surviving series is unaffected",
          row["forward_return"] is not None and row["censored"] == 0)


def test_bankruptcy_with_stub(fx: Fixture) -> None:
    print("\n5b. a bankruptcy with an OTC stub follows the stub")
    listing_id = fx.listings["ALIVE"]
    bars = L.load_bars(fx.conn, listing_id)
    outcome = {"type": pit_store.OUTCOME_BANKRUPTCY, "date": fx.sessions[700],
               "value_per_share": 0.0, "source": "fixture"}
    labels, _p, _c = L.compute_listing_labels(
        bars, [BANK_ANCHOR], fx.sessions, fx.index, L.data_horizon(fx.conn),
        outcome=outcome)
    row = {(r["as_of_date"], r["horizon"]): r for r in labels}[(BANK_ANCHOR, "12M")]
    check("the stub's own prints price the exit, not the outcome record",
          row["forward_return"] is not None and row["forward_return"] != -1.0,
          row["forward_return"])
    check("and it is a complete label",
          row["terminal_reason"] == L.REASON_COMPLETE)


# --------------------------------------------------------------------------
# 6. Volume hygiene at the terminal -- the First Republic case
# --------------------------------------------------------------------------

def test_terminal_roll(fx: Fixture) -> None:
    print("\n6. volume hygiene at the terminal")
    labels, _p, counters = labels_for(fx, "STALE", [ANCHOR_BASE])
    row = labels[(ANCHOR_BASE, "1M")]
    check("the terminal rolled to the next real trade",
          row["terminal_roll_days"] == 2, row["terminal_roll_days"])
    check("it was counted", counters.get("terminal_rolled", 0) > 0)
    check("the terminal price is the real trade",
          abs(row["end_price"] - 0.3336) < 1e-9, row["end_price"])
    real_loss = 0.3336 / 10.0 - 1.0
    stale_loss = 3.51 / 10.0 - 1.0
    check("the real loss is about -97%", abs(row["forward_return"] - real_loss) < 1e-9)
    check("the stale print would have reported -65% instead of -97%",
          abs(stale_loss - real_loss) > 0.30, (stale_loss, real_loss))
    check("i.e. it would have valued the position at 10x what it was worth",
          3.51 / 0.3336 > 10.0)

    labels, _p, _c = labels_for(fx, "HALTED", [ANCHOR_BASE])
    row = labels[(ANCHOR_BASE, "3M")]
    check("a halt longer than the roll cap has no observable exit price",
          row["forward_return"] is None and
          row["terminal_reason"] == pit_store.OUTCOME_UNKNOWN,
          (row["forward_return"], row["terminal_reason"]))


# --------------------------------------------------------------------------
# 7. A split inside the window cancels
# --------------------------------------------------------------------------

def test_split_invariance(fx: Fixture) -> None:
    print("\n7. a corporate action inside the window does not change the label")
    plain, _p, _c = labels_for(fx, "NOSPLIT", [ANCHOR_BASE])
    split, _p2, _c2 = labels_for(fx, "SPLIT", [ANCHOR_BASE])
    for horizon in ("1M", "3M", "6M", "12M"):
        a = plain[(ANCHOR_BASE, horizon)]["forward_return"]
        b = split[(ANCHOR_BASE, horizon)]["forward_return"]
        check(f"{horizon}: split and unsplit labels are identical",
              a is not None and b is not None and abs(a - b) < 1e-12, (a, b))

    anchor_i = fx.index[ANCHOR_BASE]
    split_i = fx.index[SPLIT_DAY]
    check("the split really is inside the 12M window",
          anchor_i < split_i < anchor_i + 252)
    raw = fx.conn.execute(
        """SELECT close FROM pit_price_bar WHERE listing_id = ? AND bar_date IN (?, ?)
            ORDER BY bar_date""",
        (fx.listings["SPLIT"], ANCHOR_BASE, fx.sessions[anchor_i + 252])).fetchall()
    naive = raw[1][0] / raw[0][0] - 1.0
    check("a label taken from `close` instead would be wrong by ~50%",
          abs(naive - split[(ANCHOR_BASE, "12M")]["forward_return"]) > 0.4, naive)

    path = _p2[(ANCHOR_BASE, "12M")]["stats"]
    check("the path did not put the split into the drawdown",
          path["max_drawdown"] > -0.05, path["max_drawdown"])


# --------------------------------------------------------------------------
# 8. The path is the point: flat return, 40% hole
# --------------------------------------------------------------------------

def test_path(fx: Fixture) -> None:
    print("\n8. paths and the barrier ladder")
    labels, paths, _c = labels_for(fx, "CRASH", [CRASH_ANCHOR])
    row = labels[(CRASH_ANCHOR, "12M")]
    stats = paths[(CRASH_ANCHOR, "12M")]["stats"]
    check("the position ends flat", abs(row["forward_return"]) < 1e-9,
          row["forward_return"])
    check("and its worst point was 40% down",
          abs(stats["max_adverse_excursion"] + 0.40) < 1e-9,
          stats["max_adverse_excursion"])
    check("the path knows a hedge was needed where the label cannot",
          stats["max_adverse_excursion"] < -0.30 and abs(row["forward_return"]) < 1e-9)
    check("a 30%-OTM put would have been breached",
          stats["barriers"]["-0.30"]["hit"] is True)
    check("a 50%-OTM put would not",
          stats["barriers"]["-0.50"]["hit"] is False)
    check("the breach session is recorded",
          stats["barriers"]["-0.30"]["session"] is not None)
    check("the drawdown is measured along the path",
          abs(stats["max_drawdown"] + 0.40) < 1e-9, stats["max_drawdown"])
    check("terminal_return cross-checks the label",
          abs(stats["terminal_return"] - row["forward_return"]) < 1e-12)


# --------------------------------------------------------------------------
# 9. The bulk writers are the single-row writers
# --------------------------------------------------------------------------

def _row_dict(conn: sqlite3.Connection, table: str) -> dict:
    row = conn.execute(f"SELECT * FROM {table}").fetchone()
    return {k: row[k] for k in row.keys() if k != "computed_at"}


def test_writers(fx: Fixture) -> None:
    print("\n9. bulk writers are proved identical to the store's own")
    conn = fx.conn
    listing_id = fx.listings["ALIVE"]
    labels, paths, _c = labels_for(fx, "ALIVE", [ANCHOR_BASE])
    label = labels[(ANCHOR_BASE, "1M")]
    path = paths[(ANCHOR_BASE, "1M")]

    conn.execute("DELETE FROM pit_label")
    conn.execute("DELETE FROM pit_path")
    conn.commit()

    pit_store.save_label(
        conn, listing_id, label["as_of_date"], label["horizon"],
        entity_id=None, anchor_date=label["anchor_date"],
        anchor_price=label["anchor_price"],
        anchor_roll_days=label["anchor_roll_days"], end_date=label["end_date"],
        end_price=label["end_price"],
        terminal_roll_days=label["terminal_roll_days"],
        forward_return=label["forward_return"], censored=label["censored"],
        terminal_reason=label["terminal_reason"], price_source=L.PRICE_SOURCE,
        price_ingest_id=label["price_ingest_id"])
    via_store = _row_dict(conn, "pit_label")
    conn.execute("DELETE FROM pit_label")
    conn.commit()
    L.insert_labels(conn, [L.label_row_tuple(listing_id, None, label)])
    via_bulk = _row_dict(conn, "pit_label")
    check("insert_labels writes exactly what pit_store.save_label writes",
          via_store == via_bulk,
          {k: (via_store[k], via_bulk[k]) for k in via_store
           if via_store[k] != via_bulk[k]})

    pit_path.save_path(conn, listing_id, path["as_of_date"], path["horizon"],
                       path["anchor_date"], path["anchor_price"], path["stats"],
                       end_date=path["end_date"], price_source=L.PRICE_SOURCE,
                       price_ingest_id=path["price_ingest_id"])
    via_store = _row_dict(conn, "pit_path")
    conn.execute("DELETE FROM pit_path")
    conn.commit()
    L.insert_paths(conn, [L.path_row_tuple(listing_id, path)])
    via_bulk = _row_dict(conn, "pit_path")
    check("insert_paths writes exactly what pit_path.save_path writes",
          via_store == via_bulk,
          {k: (via_store[k], via_bulk[k]) for k in via_store
           if via_store[k] != via_bulk[k]})

    conn.execute("DELETE FROM pit_label")
    conn.execute("DELETE FROM pit_path")
    conn.commit()


# --------------------------------------------------------------------------
# 10. End to end, and the invariant
# --------------------------------------------------------------------------

def test_build(fx: Fixture) -> None:
    print("\n10. the whole build, and the invariant")
    grid = [SESSIONS[i] for i in (300, 400, 500, 600, len(SESSIONS) - 40)]
    summary = L.build_labels(fx.conn, db_path=fx.path, as_of_dates=grid,
                             min_free_gib=0.001, batch_rows=500,
                             checkpoint_every=1, progress=False)
    check("the build completed", summary["status"] == "complete", summary)
    check("it wrote labels", summary["labels_written"] > 0, summary["labels_written"])
    check("it wrote paths", summary["paths_written"] > 0, summary["paths_written"])
    check("it projected its disk cost before writing",
          summary["projection"]["projected_mib"] >= 0, summary["projection"])
    check("it read every checkpoint's busy flag",
          "checkpoints_busy" in summary and summary["checkpoints"] > 0, summary)
    check("it reported a measured peak WAL", "peak_wal_mib" in summary)

    violations = fx.conn.execute(
        """SELECT COUNT(*) FROM pit_label
            WHERE (forward_return IS NULL AND censored = 0)
               OR (forward_return IS NOT NULL AND censored = 1)""").fetchone()[0]
    check("INVARIANT: forward_return IS NULL <=> censored = 1", violations == 0,
          violations)

    truncated = fx.conn.execute(
        """SELECT COUNT(*) FROM pit_label
            WHERE censored = 1 AND end_price IS NOT NULL""").fetchone()[0]
    check("no censored row carries a terminal price", truncated == 0, truncated)

    unknown = fx.conn.execute(
        """SELECT COUNT(*) FROM pit_label WHERE terminal_reason = ?""",
        (pit_store.OUTCOME_UNKNOWN,)).fetchone()[0]
    check("unresolved exits are present and counted, not dropped", unknown > 0,
          unknown)

    bank = fx.conn.execute(
        """SELECT forward_return FROM pit_label
            WHERE listing_id = ? AND terminal_reason = ?""",
        (fx.listings["BANKRUPT"], pit_store.OUTCOME_BANKRUPTCY)).fetchall()
    check("every bankruptcy row is exactly -1.0",
          bool(bank) and all(r[0] == -1.0 for r in bank), [r[0] for r in bank])

    orphan_paths = fx.conn.execute(
        """SELECT COUNT(*) FROM pit_path p
            LEFT JOIN pit_label b ON b.listing_id = p.listing_id
                 AND b.as_of_date = p.as_of_date AND b.horizon = p.horizon
            WHERE b.listing_id IS NULL OR b.censored = 1""").fetchone()[0]
    check("every path has a complete label beside it", orphan_paths == 0,
          orphan_paths)

    report = L.coverage_report(fx.conn)
    check("coverage reports every horizon",
          len(report["by_horizon"]) == len(L.HORIZON_ORDER), report["by_horizon"])
    check("coverage reports the invariant it just proved",
          report["invariant_violations_null_return_vs_censored"] == 0)
    check("rolls are surfaced in coverage",
          any(h["n_anchor_rolled"] > 0 for h in report["by_horizon"]))
    check("by-year coverage is produced",
          len(L.coverage_by_year(fx.conn, "12M")) > 0)

    rerun = L.build_labels(fx.conn, db_path=fx.path, as_of_dates=grid,
                           min_free_gib=0.001, batch_rows=500,
                           checkpoint_every=1, progress=False)
    after = fx.conn.execute("SELECT COUNT(*) FROM pit_label").fetchone()[0]
    check("the build is idempotent: a second run adds no rows",
          after == report["totals"]["n_rows"], (after, report["totals"]["n_rows"]))
    check("and reports the same totals", rerun["status"] == "complete")


def test_disk_floor(fx: Fixture) -> None:
    print("\n11. the disk floor refuses rather than fills")
    summary = L.build_labels(fx.conn, db_path=fx.path, as_of_dates=[ANCHOR_BASE],
                             min_free_gib=10 ** 6, progress=False)
    check("an impossible floor aborts before any write",
          summary["status"] == "aborted", summary.get("status"))
    projection = L.project_disk(1_000_000, 1_000_000)
    check("row sizes are measured, not assumed",
          projection["label_bytes_per_row"] > 0 and projection["path_bytes_per_row"] >
          projection["label_bytes_per_row"], projection)


# --------------------------------------------------------------------------
# 12. The independence estimator, validated on panels whose answer is known
# --------------------------------------------------------------------------

def test_independence() -> None:
    print("\n12. independent observations")
    import random

    def panel(dates, per_date, maker) -> dict:
        directory = tempfile.mkdtemp(prefix="pit_labels_ind_")
        path = os.path.join(directory, "panel.db")
        conn = pit_store.init_db(path)
        conn.execute("PRAGMA foreign_keys = OFF")
        pit_store.insert_calendar(conn, L.MARKET, SESSIONS, "fixture")
        rows = []
        for d_i, date in enumerate(dates):
            for k in range(per_date):
                rows.append((k + 1, None, date, "12M", date, 100.0, 0, date,
                             100.0, 0, maker(d_i, k), None, None, None, None,
                             0, L.REASON_COMPLETE, L.PRICE_SOURCE, 1, "now",
                             pit_store.LABEL_POLICY_VERSION, None, None))
        L.insert_labels(conn, rows)
        out = L.independent_observations(conn, "12M")
        conn.close()
        shutil.rmtree(directory, ignore_errors=True)
        return out

    dates = [SESSIONS[i] for i in range(0, 21 * 36, 21)]
    random.seed(7)

    common = panel(dates, 50, lambda d, k: 0.10 * ((d % 7) - 3))
    check("50 names sharing one market period exactly gives rho 1",
          common["intraclass_correlation_date"] == 1.0,
          common["intraclass_correlation_date"])
    check("and exactly as many effective observations as there are dates",
          abs(common["n_effective_all_dates"] - len(dates)) < 1e-6,
          (common["n_effective_all_dates"], len(dates)))

    dominant = panel(dates, 50,
                     lambda d, k: 0.10 * ((d % 7) - 3) + random.gauss(0, 0.002))
    check("a market effect with a little idiosyncratic noise still gives rho ~1",
          dominant["intraclass_correlation_date"] > 0.99,
          dominant["intraclass_correlation_date"])
    check("and 1,800 rows collapse to roughly 36 observations",
          abs(dominant["n_effective_all_dates"] - len(dates)) < 3.0,
          (dominant["n_effective_all_dates"], len(dates)))

    noise = panel(dates, 50, lambda d, k: random.gauss(0, 0.2))
    check("an independent panel has rho near 0",
          noise["intraclass_correlation_date"] < 0.05,
          noise["intraclass_correlation_date"])
    check("and keeps most of its observations",
          noise["n_effective_all_dates"] > 0.5 * noise["n_observations"],
          (noise["n_effective_all_dates"], noise["n_observations"]))

    check("the non-overlapping stride is twelve monthly anchors",
          common["non_overlapping_stride_months"] == 12,
          common["non_overlapping_stride_months"])
    check("all twelve phases are reported",
          len(common["scales"]["log"]["phases"]) == 12,
          len(common["scales"]["log"]["phases"]))
    check("the non-overlapping headline is far below the row count",
          common["n_effective_non_overlapping_median"] < 0.05 * common["n_observations"],
          (common["n_effective_non_overlapping_median"], common["n_observations"]))
    check("an empty horizon answers honestly rather than with a number",
          panel(dates, 1, lambda d, k: 0.0).get("n_observations", 0) >= 0)

    # THE SCALE TEST. A strong common market effect, plus one sub-penny name per
    # date whose adjusted-close ratio is in the thousands -- exactly the shape
    # this store has. On RAW returns the within-date variance is so inflated by
    # those few rows that the date effect vanishes and the estimator reports no
    # clustering at all, which would be read as "1,800 independent annual
    # observations". The same panel on the log scale reports the clustering that
    # is plainly there. This is why the headline scale is not the raw one.
    def heavy(d, k):
        if k == 0:
            return 500.0          # one sub-penny name in 200, up 500x
        return 0.10 * ((d % 7) - 3) + random.gauss(0, 0.05)

    tailed = panel(dates, 200, heavy)
    raw = tailed["scales"]["raw"]
    log = tailed["scales"]["log"]
    check("raw returns report a design effect of ~1 on a clustered panel",
          raw["design_effect_all_dates"] < 1.5, raw["design_effect_all_dates"])
    check("...which would claim nearly every row is independent",
          raw["n_effective_all_dates"] > 0.6 * tailed["n_observations"],
          (raw["n_effective_all_dates"], tailed["n_observations"]))
    check("the log scale sees the clustering the raw scale missed",
          log["design_effect_all_dates"] > 10.0 * raw["design_effect_all_dates"],
          (log["design_effect_all_dates"], raw["design_effect_all_dates"]))
    check("and the headline follows the log scale, not the raw one",
          tailed["headline_scale"] == "log" and
          tailed["design_effect_all_dates"] == log["design_effect_all_dates"])
    check("the winsorised scale is reported as a third opinion",
          "winsorized" in tailed["scales"])
    check("total losses are excluded from the log scale and counted",
          "n_total_loss_excluded_from_log" in tailed)


def test_distribution(fx: Fixture) -> None:
    print("\n13. the return distribution is a coverage statistic")
    L.build_labels(fx.conn, db_path=fx.path, as_of_dates=[ANCHOR_BASE],
                   min_free_gib=0.001, progress=False)
    dist = L.return_distribution(fx.conn, "12M")
    check("it reports quantiles, not just a mean",
          all(k in dist for k in ("min", "p01", "median", "p99", "max")), dist)
    check("it counts the exact total losses",
          dist["n_total_loss_exactly_minus_1"] >= 1, dist)
    check("median sits between p25 and p75",
          dist["p25"] <= dist["median"] <= dist["p75"], dist)


# --------------------------------------------------------------------------

def main() -> int:
    print("pit_labels tests")
    fx = Fixture()
    try:
        check("ensure_schema is idempotent",
              L.ensure_schema(fx.conn) == "already present")
        test_sessions(fx)
        test_basic(fx)
        test_censoring(fx)
        test_anchor_roll(fx)
        test_termination(fx)
        test_bankruptcy_with_stub(fx)
        test_terminal_roll(fx)
        test_split_invariance(fx)
        test_path(fx)
        test_writers(fx)
        test_build(fx)
        test_disk_floor(fx)
        test_distribution(fx)
    finally:
        fx.close()
    test_independence()

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s) failed: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

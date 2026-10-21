"""The quote contract every other package codes against (TERMINAL_DESIGN.md §2.11, §8.1, §8.2).

Conventions
- Quality ∈ {delayed, close, computed, private, stale, unavailable}; ``live`` is reserved and never produced.
- ``freshness()`` is the exact §8.2 rule; ``is_executable()`` adds the session guards: the print must belong to
  the current session (``quote_time ≥ session.opened_at``) and the provider's own session must not have ended
  (``now ≤ provider_session.end + 60``).  Options are executable only with ``bid > 0 and ask ≥ bid``.
- A ``private`` mark (method ``private_round``) is valuation only: never stale, never executable (sentence PRIVATE_MARK).
- Valuation never blocks: a stale/close record is still the last valid mark with its time; ``unavailable`` shows a
  dash and the dependent NAV is partial.
- ``MarkSet`` is an immutable snapshot used by one command or one read; ``FixedMarks`` is the test / offline
  implementation (also usable as the mark PROVIDER: ``snapshot()`` returns a frozen copy).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Protocol, Tuple, Union

from ..config import ERROR_AGE_S, FETCH_GRACE_S, STALE_GRACE_S
from ..units import qprice

QUALITY = ("live", "delayed", "close", "computed", "private", "stale", "unavailable")   # live: reserved, never produced
QUALITY_RANK = {"delayed": 0, "live": 0, "close": 1, "computed": 2, "private": 3, "stale": 4, "unavailable": 5}
COMPUTED_METHODS = ("curve", "cip", "model", "swap_model", "pc_model")
EXECUTABLE_QUALITIES = ("live", "delayed")


@dataclass
class Bar1m:
    t: int
    o: Decimal
    h: Decimal
    l: Decimal
    c: Decimal
    v: Optional[int]


@dataclass
class QuoteRecord:
    instrument_id: str
    provider: str
    source: str
    method: str                       # direct | chain | front_month_continuous | curve | cip | model | daily_close | private_round | swap_model | pc_model
    last: Optional[Decimal] = None
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    mid: Optional[Decimal] = None
    prev_close: Optional[Decimal] = None
    day_high: Optional[Decimal] = None
    day_low: Optional[Decimal] = None
    day_volume: Optional[int] = None
    quote_time: Optional[int] = None
    fetched_at: int = 0
    delay_s: int = 900
    delay_label: str = "up to 15 min delayed"
    currency: str = "USD"
    session_group: str = "US_EQUITY"
    exchange_tz: Optional[str] = None
    provider_session: Optional[Tuple[int, int]] = None
    bar: Optional[Bar1m] = None
    bars_tail: List[Bar1m] = field(default_factory=list)
    error: Optional[str] = None
    inputs: Optional[dict] = None
    quality: str = "unavailable"

    def price(self) -> Optional[Decimal]:
        """The valuation price: mid for chain records with a usable NBBO, else last."""
        if self.method == "chain" and self.mid is not None:
            return self.mid
        return self.last if self.last is not None else self.mid

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
            elif isinstance(v, Bar1m):
                d[k] = {"t": v.t, "o": str(v.o), "h": str(v.h), "l": str(v.l), "c": str(v.c), "v": v.v}
            elif k == "bars_tail":
                d[k] = [{"t": b.t, "o": str(b.o), "h": str(b.h), "l": str(b.l), "c": str(b.c), "v": b.v} for b in v]
            elif isinstance(v, tuple):
                d[k] = list(v)
            else:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "QuoteRecord":
        def dec(x):
            return None if x is None else Decimal(str(x))

        def bar(b):
            return None if not b else Bar1m(int(b["t"]), dec(b["o"]), dec(b["h"]), dec(b["l"]), dec(b["c"]), b.get("v"))
        kw = dict(d)
        for k in ("last", "bid", "ask", "mid", "prev_close", "day_high", "day_low"):
            kw[k] = dec(kw.get(k))
        kw["bar"] = bar(kw.get("bar"))
        kw["bars_tail"] = [bar(b) for b in (kw.get("bars_tail") or [])]
        ps = kw.get("provider_session")
        kw["provider_session"] = tuple(int(x) for x in ps) if ps else None
        kw = {k: v for k, v in kw.items() if k in cls.__dataclass_fields__}
        return cls(**kw)


def _input_qualities(inputs: Optional[dict]) -> List[str]:
    out: List[str] = []
    if not inputs:
        return out

    def walk(x):
        if isinstance(x, dict):
            q = x.get("quality")
            if isinstance(q, str):
                out.append(q)
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    for key in ("qualities", "input_qualities"):
        if isinstance(inputs.get(key), (list, tuple)):
            out.extend(str(q) for q in inputs[key])
    walk({k: v for k, v in inputs.items() if k not in ("qualities", "input_qualities")})
    return out


def freshness(rec: Optional[QuoteRecord], session, now: int) -> str:
    """§8.2, exactly."""
    if rec is None or (rec.last is None and rec.mid is None):
        return "unavailable"
    if rec.method == "private_round":
        return "private"
    if rec.method in COMPUTED_METHODS:
        if any(q in ("stale", "unavailable") for q in _input_qualities(rec.inputs)):
            return "stale"
        return "computed"
    if session is None or not session.open:
        return "close"
    if rec.quote_time is None:
        return "stale"
    if now - rec.quote_time > rec.delay_s + STALE_GRACE_S:
        return "stale"
    if now - rec.fetched_at > FETCH_GRACE_S:
        return "stale"
    if rec.error is not None and now - rec.fetched_at > ERROR_AGE_S:
        return "stale"
    if rec.delay_s == 0 and rec.method == "direct":
        return "live"
    return "delayed"


def is_executable(rec: Optional[QuoteRecord], session, now: int) -> Tuple[bool, str]:
    """``(ok, reason)``; reasons: NO_QUOTE, STALE, SESSION_CLOSED, COMPUTED_NOT_EXECUTABLE, QUOTE_FROM_PREVIOUS_SESSION,
    PROVIDER_SESSION_ENDED, NO_NBBO."""
    q = freshness(rec, session, now)
    if rec is None or q == "unavailable":
        return False, "NO_QUOTE"
    if q in ("computed", "private"):
        return False, "COMPUTED_NOT_EXECUTABLE"
    if session is None or not session.open:
        return False, "SESSION_CLOSED"
    # §8.2 / §16 example: a print from before this session's open (Wed 15:59 evaluated Thu 09:31) is reported as
    # QUOTE_FROM_PREVIOUS_SESSION even though it is also stale by age; the more specific reason wins.
    if session.opened_at is not None and rec.quote_time is not None and rec.quote_time < session.opened_at:
        return False, "QUOTE_FROM_PREVIOUS_SESSION"
    if q == "stale":
        return False, "STALE"
    if rec.provider_session is not None and now > int(rec.provider_session[1]) + 60:
        return False, "PROVIDER_SESSION_ENDED"
    if rec.method == "chain":
        if rec.bid is None or rec.ask is None or rec.bid <= 0 or rec.ask < rec.bid:
            return False, "NO_NBBO"
    return True, "OK"


@dataclass
class MarkView:
    instrument_id: str
    mark: Optional[Decimal]
    quality: str
    source: str
    method: str
    quote_time: Optional[int]
    fetched_at: Optional[int]
    delay_label: str
    inputs: Optional[dict]
    executable: bool
    note: str

    def to_dict(self) -> dict:
        return {"instrument_id": self.instrument_id, "value": None if self.mark is None else str(self.mark), "quality": self.quality,
                "source": self.source, "method": self.method, "as_of": self.quote_time, "fetched_at": self.fetched_at,
                "delay_label": self.delay_label, "inputs": self.inputs, "executable": self.executable, "note": self.note}


def unavailable(instrument_id: str, note: str = "no mark") -> MarkView:
    return MarkView(instrument_id, None, "unavailable", "", "", None, None, "", None, False, note)


class MarkSet(Protocol):
    """An immutable snapshot used by one command or one read."""

    def mark(self, instrument_id: str) -> MarkView: ...

    def fx(self, ccy: str) -> MarkView: ...          # USD per 1 unit of ccy; USD -> mark 1, quality "delayed", source "const"

    def quote(self, instrument_id: str) -> Optional[QuoteRecord]: ...

    def as_of(self) -> int: ...


MarkSpec = Union[str, Decimal, int, Tuple]


class FixedMarks:
    """Tests and ``--offline``: ``FixedMarks({"AAPL": ("339.50", "delayed", quote_time), "EURUSD": ("1.1238", "delayed")})``.
    A bare price string means quality ``delayed`` at ``as_of``.  ``set()`` changes a mark (not on a frozen snapshot);
    ``snapshot()`` returns a frozen copy, so the object serves as the mark PROVIDER in a test runtime."""

    def __init__(self, marks: Optional[Dict[str, MarkSpec]] = None, as_of: Optional[int] = None, frozen: bool = False):
        self._marks: Dict[str, Tuple[Optional[Decimal], str, Optional[int], str]] = {}
        self._quotes: Dict[str, QuoteRecord] = {}
        self._as_of = int(as_of or 0)
        self._frozen = frozen
        for k, v in (marks or {}).items():
            self._put(k, v)

    def _put(self, instrument_id: str, spec: MarkSpec) -> None:
        note = ""
        if isinstance(spec, tuple):
            price = spec[0]
            quality = spec[1] if len(spec) > 1 else "delayed"
            qt = spec[2] if len(spec) > 2 else self._as_of
            note = spec[3] if len(spec) > 3 else ""
        else:
            price, quality, qt = spec, "delayed", self._as_of
        mark = None if price is None else qprice(price)
        if mark is None:
            quality = "unavailable"
        self._marks[instrument_id] = (mark, quality, qt, note)

    # ---- provider side ----------------------------------------------------------------------------------------------
    def set(self, instrument_id: str, price: Optional[MarkSpec], quality: str = "delayed", quote_time: Optional[int] = None, note: str = "") -> None:
        if self._frozen:
            raise RuntimeError("a mark snapshot is immutable")
        if isinstance(price, tuple):
            self._put(instrument_id, price)
        else:
            self._put(instrument_id, (price, quality, quote_time if quote_time is not None else self._as_of, note))

    def unset(self, instrument_id: str) -> None:
        if self._frozen:
            raise RuntimeError("a mark snapshot is immutable")
        self._marks.pop(instrument_id, None)
        self._quotes.pop(instrument_id, None)

    def set_quote(self, rec: QuoteRecord) -> None:
        if self._frozen:
            raise RuntimeError("a mark snapshot is immutable")
        self._quotes[rec.instrument_id] = rec

    def set_as_of(self, as_of: int) -> None:
        if self._frozen:
            raise RuntimeError("a mark snapshot is immutable")
        self._as_of = int(as_of)

    def snapshot(self, ids=None) -> "FixedMarks":
        snap = FixedMarks({}, as_of=self._as_of, frozen=True)
        snap._marks = dict(self._marks)
        snap._quotes = {k: copy.deepcopy(v) for k, v in self._quotes.items()}
        return snap

    def mark_for(self, inst, at: Optional[int] = None) -> MarkView:
        return self.mark(getattr(inst, "instrument_id", inst))

    # ---- MarkSet ----------------------------------------------------------------------------------------------------
    def mark(self, instrument_id: str) -> MarkView:
        spec = self._marks.get(instrument_id)
        if spec is None:
            return unavailable(instrument_id, "no fixed mark")
        mark, quality, qt, note = spec
        method = "private_round" if quality == "private" else ("model" if quality == "computed" else "direct")
        return MarkView(instrument_id, mark, quality, "fixed (test)", method, qt, self._as_of, "test fixture", None,
                        quality in EXECUTABLE_QUALITIES, note)

    def fx(self, ccy: str) -> MarkView:
        if ccy == "USD":
            return MarkView("USD", Decimal(1), "delayed", "const", "const", self._as_of, self._as_of, "", None, True, "")
        direct = self._marks.get(f"{ccy}USD")
        if direct is not None and direct[0] is not None:
            mark, quality, qt, note = direct
            return MarkView(f"{ccy}USD", mark, quality, "fixed (test)", "direct", qt, self._as_of, "test fixture", None, True, note)
        inv = self._marks.get(f"USD{ccy}")
        if inv is not None and inv[0] is not None and inv[0] != 0:
            mark, quality, qt, note = inv
            return MarkView(f"USD{ccy}", qprice(Decimal(1) / mark), quality, "fixed (test)", "direct", qt, self._as_of, "test fixture",
                            {"inverted_from": str(mark)}, True, note)
        return unavailable(f"{ccy}USD", f"no USD rate for {ccy}")

    def quote(self, instrument_id: str) -> Optional[QuoteRecord]:
        return self._quotes.get(instrument_id)

    def as_of(self) -> int:
        return self._as_of

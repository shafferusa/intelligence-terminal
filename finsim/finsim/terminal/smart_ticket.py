"""The Smart Ticket parser: grammar ``smart-ticket/1``, the read-only resolver and ``POST /api/smart-ticket/parse`` (FinSim 1.0.7.2,
CHANGE_PLAN r1072 section 2; design FINSIM_TERMINAL_GAPS 3.4-3.6).

The owner types what to trade in words ("buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls and buy 50 WIDG at market in the
Fund book").  A FIXED grammar - a closed keyword table, no language model, no guessing - reads it into orders (legs) and the groups the
owner wrote together.  Every leg carries the exact body ``POST /api/orders/preview`` and ``POST /api/orders`` take, every field FinSim
filled in is flagged, and nothing is sent from here: the page fills one standard order ticket per order, and the owner previews and
sends each from that ticket (1.0.7.2 simplification, 2026-11-11: the Smart Ticket has no send of its own).

Conventions (sentence SMART_TICKET)
- PURITY.  The parse route writes nothing, fetches nothing, subscribes nothing, stores no preview and takes no mark.  It reads ONE pooled
  reader connection (books, lifetimes, accounts, positions, working orders), the in-memory instrument master, the cached option chains
  through ``ChainCache.peek`` (never ``get``, which reorders the cache, ``request`` or ``keep_on_screen``) and quote copies through
  ``QuoteCache.get``.  When only a fetch could answer (an unknown symbol, a chain not in memory), the leg carries a flag with an ACTION the
  page triggers through an existing route ("Look it up" = ``GET /api/instruments/resolve``, "Load the chain" = ``GET
  /api/instruments/{id}/chain``, the product's own ticket), then the page parses again.  No command, no schema, no setting.  A fault
  inside the parser is logged and answered 500 PARSE_FAILED by the route itself, never through the router's incident path (which writes
  an attention row), so even a failing parse writes nothing.  Registered NOT mutating: no request id is needed (one sent is ignored).
- DETERMINISM.  The same text with the same master, chains and positions gives the same answer: legs in text order, flags sorted by
  (severity, code, span), no clock in the answer except ``as_of``, no random id (the ticket id is the page's).  The pure layer
  (``normalise``, ``tokenize``, ``split_clauses``, ``parse_clause``, ``parse``, ``expand_strategy``) has no services and no clock.
- NORMALISATION.  NFKC, then curly quotes, dashes, non-breaking spaces and the multiplication sign to ASCII; any other non-ASCII
  character becomes "?" and an UNPARSED_TEXT span.  At most ``MAX_CHARS`` (2,000) characters after normalisation and ``MAX_LEGS`` (20)
  legs after expansion: beyond either the route answers 400 TEXT_TOO_LONG / TOO_MANY_LEGS.
- TOKENS (longest first): an OCC id typed whole; a futures id (root + month code + 2-digit year, the root in FUTURES_SPECS); an account
  id (``F1:US_EXCHANGE``); an ISO date; a US date ``m/d/yy`` or ``m/d/yyyy`` ALWAYS with a year (a bare ``12/18`` is a strike pair, never a
  date); a strike list ``n/n[/n...]``; a strike with its right (``240c``); ``'yy``; a number (thousands commas, decimals, a leading ``$``
  on prices, ``k`` / ``m`` on quantities, ``%`` on trails; a comma that is not a thousands group - "1,5k" - is ONE malformed number,
  QTY_NOT_WHOLE in the quantity slot, never a clause break; "1.000" as a quantity is TOKEN_AMBIGUOUS); a word (``$WORD`` forces a
  ticker); ``, ; : @ ( ) . /`` and newline.  Multi-word
  keywords are merged from one fixed phrase table (sides such as "sell to open", "at the bid", "good till cancelled", "call spread", "as a
  package", "us exchange" ...).
- CLAUSES.  ``;``, ``.`` and a newline always end a clause; ``,``, ``and``, ``then``, ``plus``, ``&`` end one only when the next token
  starts a clause (a side word, close, roll, a strategy verb, or a quantity followed by an instrument), so the comma of "Dec 18, 2026"
  never splits.  A clause that starts with a quantity takes the side of the previous clause (SIDE_CARRIED).  ``( ... )`` groups exactly the
  clauses inside; a group word at the end of a clause ("as a package", "as one order", "together", "all or none", "aon") groups the run of
  consecutive clauses on the same underlying that ends at it.
- THE SLOT DECIDES.  In the quantity or modifier slot a word is a keyword, in the instrument slot a ticker; a grammar keyword typed in
  lower case is never a ticker; one- and two-letter tickers need capitals or ``$`` (``$C``).  When two readings make a complete clause the
  leg is the blocker TOKEN_AMBIGUOUS with both readings as choices.  A 4-digit number 2000-2099 right after a month and day is always the
  year; a bare 2-digit number after a day is a strike.  "at <number>" is always a limit (TYPE_FROM_AT).
- DEFAULTS AND SCOPE.  No price -> MARKET (TYPE_DEFAULT); no time in force -> DAY (TIF_DEFAULT); "Dec 18" -> the first Dec 18 on or after
  today in New York (YEAR_INFERRED).  A book phrase inside a clause applies to that clause; a prefix scope ("in the Fund book: ...") to
  every clause naming none (BOOK_FROM_SCOPE information; a clause's own book overriding it says so: BOOK_CONFLICT check); a book phrase
  at the very end of the text - only the last order's own price, time in force, account or group word may follow it - to every leg naming
  none (SD-2, BOOK_FROM_SCOPE check on each; an account phrase inside it or right beside it goes with it); a clause that carries the side
  of the one before carries its book and account too (BOOK_FROM_SCOPE check; the account also without a book), unless it names its own;
  no book anywhere -> the selected book (BOOK_SELECTED); several books -> the clause once per book at the same
  quantity (MULTI_BOOK); a leg outside the selected book is allowed (OTHER_BOOK).  The account is the one account of the book's OPEN
  lifetime that trades the instrument (US Exchange first, the others listed: ACCOUNT_CHOICE, an explicit opening side included) - but
  when the side is read from the position (or closes it, or the quantity is 'all' / 'half') the account of the book that HOLDS the
  contract, whatever the quantity's wording; holdings split over accounts, or a named account that holds nothing while another one
  holds it, are an ACCOUNT_CHOICE blocker.  A named account must belong to the book (ACCOUNT_NOT_IN_BOOK); an account phrase in an
  opening or closing phrase is never dropped: with the phrase's book for the legs that take it, alone ("at pb ms: ...") for every leg
  that names no account, read in that leg's book.
- INSTRUMENTS.  Equities by an exact id / symbol / Yahoo symbol / alias match (several -> SYMBOL_AMBIGUOUS, none -> SYMBOL_UNKNOWN with
  "Look it up").  Options in this order: the cached chain row (the standard root first; two roots for one expiry, right and strike ->
  ROOT_CHOICE, never a silent pick), the master's option rows, else the OCC id built by ``occ_symbol`` and flagged OPTION_UNVERIFIED with
  "Load the chain" (the preview refuses NOT_LISTED until the chain lists it: nothing is invented).  An expiry or strike that is not
  listed is NEVER snapped: it is a blocker with the nearest listed values as choices.  Futures by id, root + month (+ year) on the listing
  rule, or front / active through ``api_products.active_months``.  FX, bonds, private names and swaps open their own ticket
  (KIND_OWN_TICKET with an action).
- POSITION-AWARE SIDES.  "buy" / "sell" read the leg account's position and working orders, each order on the account as it is (no
  count across the orders of one instruction): an option bought while short is BTC, sold while long STC, sold while not held STO (SD-3,
  SIDE_INFERRED); shares bought while short are covered; a sale beyond the free quantity (``orders_preview.cover_state``: net of working
  sells, of the shares covering calls sold or being sold - whole contracts, as the margin assigns them - and of lent shares) is
  WOULD_OVERSELL with "sell the free N" only (the order route refuses a short sale while shares are held); a quantity between is
  SIDE_SPLIT.  The explicit sides (short, cover, bto, sto, btc, stc and their long forms) are never changed; "short" or "sto" on shares
  held long is the blocker SHORT_WHILE_LONG (sell them first).  A position wholly in working closing orders is ALREADY_CLOSING (blocker;
  the explicit opening side offered), never silently an order that opens a new position.  "all" is the held quantity, "half"
  floor(held / 2) on the lot (QTY_FROM_HELD / QTY_ROUNDED check), and never on a side that would ADD to the position (ADDS_TO_POSITION
  blocker, "close" offered); "k" / "m" multiply by 1,000 / 1,000,000.
- GROUPS.  A strategy word, a roll, a covered call or collar that buys its shares, a collar on shares held, a group word or parentheses
  make a GROUP of the orders written together (``combos``: the same clauses 1.0.7.2 made combinations of; ``kind`` "SEPARATE",
  ``fill_mode`` and ``request`` null, ``order`` the sending order - purchases first, shares then options, then sales to close, sales to
  open, share sales and short sales).  Every order of a group is its own ticket that goes and fills on its own (SEPARATE_TICKETS check);
  one all-or-none order of options is built in the order ticket's Strategies tab (the action "Strategies tab" on an option strategy it
  has a template for).  Words asking for one order ("as a package", "as one order", "as one", "as a combo", "all or none", "aon",
  "together") are ONE_ORDER_ASKED (blocker, "as separate tickets" offered), never split silently; a price typed on a strategy, a roll, a
  collar or a covered call that buys its shares is a net price the tickets cannot carry (NET_PRICE, "without the price"), never dropped;
  a leg's own limit is that ticket's.  A group blocker is repeated, without its choices, on every order of the group, so none of them has
  a ``request``.  A vertical's debit or credit follows the side, never the typed strike order (bought = a debit); a bare "condor" is
  TOKEN_AMBIGUOUS (iron, call or put condor); a strategy takes only buy or sell, typed or carried (MISSING_SIDE otherwise; its legs then
  have no side); a butterfly without call / put is calls (RIGHT_DEFAULT check); a calendar's near expiry is told from the far one as a
  date (the year inferred, a month alone through the cached chain), never by its spelling, and refused when a date cannot be read.  A
  covered call or collar reads the signed holding and the shares free for NEW calls (``cover_state``'s ``free_for_calls``: also net of
  working short sales): on shares held free the options only, the calls sold as covered (``covered: true``: the order route places them
  only while those shares are free and keeps them for the calls until they are closed); on shares held but not free SIDE_SPLIT; on a
  short NOTHING_HELD (a buy write and a collar that buys its shares too); none held, or "buy write", the shares bought and the calls sold
  as covered, sent after the shares have filled; a collar's put strike below its call strike (STRIKE_ORDER otherwise).  One price typed
  for a close of several contracts is PRICE_AMBIGUOUS.
- CHOICES are clause texts (``{label, clause}``): the page puts the choice's text in place of the flag's span and parses again, so the
  grammar stays the one source of truth.  Every flag that offers choices carries the span it replaces: the leg's clause, the group's
  text, or for an instruction-level flag the word it names (an unknown or shared book word of the opening phrase).  Nothing is applied
  automatically; alternatives are listing facts only.  Third review: a choice changes only the order it is on - read again with the
  whole instruction (the pure parse), every other order keeps its side, book, account and grouping, and the order it is on its book and
  account (unless the choice is that book or account); where that needs it the choice also writes out what those orders inherited (a
  carried side word, "in the X book", "at <account>") and its span covers them, and a choice that cannot is not offered
  (``Resolver._keep_others``).  The opening part of a split names the account the order was read in.  Fourth review: every choice kept
  (one order too) is then RESOLVED as the page would re-read it (``Resolver._reads_alike``: positions, accounts, groups) and offered only
  when every other order reads as before - book, account, side, quantity, contract, its group, never newly sendable - and the order it
  is on stays in its book and account and joins no other order's group; a buy write and a side alternative that reads the account
  another way name the account the order was read in ("sto" on shares offers "short"); a covered call's or a collar's quantity in
  contracts or lots is UNIT_KIND_MISMATCH, never read as shares.  Sixth review: a book word that names no book leaves unread every order
  that takes its book (carried, closing or opening phrase), and a choice that settles it may give them the book it names; a choice on a
  group reads every copy outside that group as before (another book's copy of a clause too); a choice whose re-read reads a field twice
  (DUPLICATE_FIELD) is not offered, and the account of a book phrase is written once.  "as separate tickets" (ONE_ORDER_ASKED) removes
  the words asking for one order - a group word typed inside the group's parentheses too; the parentheses stay and only group the cards.
- A leg's ``request`` is exactly the order body (account_id, instrument_id, side, qty, order_type, tif, limit_px / stop_px / trail_amt /
  trail_pct / gtd_date when they apply, ``borrow: true`` for SELL_SHORT, ``covered: true`` for calls sold as covered: the order route
  places them only while their shares are free and keeps those shares for them until they are closed), null while the leg has a blocker
  (its group's included).  A group's ``request`` is always null: the Smart Ticket fills separate tickets.  Prices are copied as typed (a
  leading ``$`` and commas removed), never rounded: the preview judges the tick.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import config, tables
from .commands import RejectError
from .instruments import FUTURES_SPECS, MONTH_NAMES, futures_id, listed_months, occ_symbol, parse_futures_id, parse_occ

GRAMMAR = "smart-ticket/1"
MAX_CHARS = 2000
MAX_LEGS = 20
SEVERITY_RANK = {"blocker": 0, "check": 1, "info": 2}
ROUTE = r"/api/smart-ticket/parse"

tables.add_conventions({"SMART_TICKET": "Read from your words by a fixed grammar; every field FinSim filled in is flagged; each order goes from the order"
                                        " ticket after its own preview."})

#: every flag code and its severity (G 3.5.4; BOOK_FROM_SCOPE is information for a prefix scope, a check otherwise)
FLAG_SEVERITY = {
    "UNPARSED_TEXT": "blocker", "MISSING_SIDE": "blocker", "MISSING_QTY": "blocker", "MISSING_INSTRUMENT": "blocker", "MISSING_PRICE": "blocker",
    "DUPLICATE_FIELD": "blocker", "TOKEN_AMBIGUOUS": "blocker", "QTY_NOT_WHOLE": "blocker", "QTY_LOT": "blocker", "UNIT_KIND_MISMATCH": "blocker",
    "SYMBOL_UNKNOWN": "blocker", "SYMBOL_AMBIGUOUS": "blocker", "KIND_OWN_TICKET": "blocker", "NOT_TRADEABLE_HERE": "blocker",
    "EXPIRY_NOT_LISTED": "blocker", "EXPIRY_MONTH_ONLY": "blocker", "STRIKE_NOT_LISTED": "blocker", "ROOT_CHOICE": "blocker", "STRIKE_ORDER": "blocker",
    "SIDE_SPLIT": "blocker", "WOULD_OVERSELL": "blocker", "NOTHING_HELD": "blocker", "NOTHING_TO_ROLL": "blocker",
    "ACCOUNT_NOT_IN_BOOK": "blocker", "TOO_MANY_LEGS": "blocker",
    # 1.0.7.2 review: a closing side whose position is already in working closing orders; 'all' / 'half' read as an order that adds to
    # the position; one typed price for several contracts
    "ALREADY_CLOSING": "blocker", "ADDS_TO_POSITION": "blocker", "PRICE_AMBIGUOUS": "blocker",
    # 1.0.7.2 simplification: a price on a group as a whole (separate tickets cannot carry a net price); words asking for one order of a
    # group; a short sale of shares the account holds long (the order route refuses it)
    "NET_PRICE": "blocker", "ONE_ORDER_ASKED": "blocker", "SHORT_WHILE_LONG": "blocker",
    "SIDE_INFERRED": "check", "SIDE_CARRIED": "check", "YEAR_INFERRED": "check", "EXPIRY_FROM_MONTH": "check", "TYPE_DEFAULT": "check",
    "PRICE_FROM_QUOTE": "check", "QTY_ROUNDED": "check", "BOOK_FROM_SCOPE": "check", "OTHER_BOOK": "check", "MULTI_BOOK": "check",
    "ACCOUNT_CHOICE": "check", "OPTION_UNVERIFIED": "check", "CHAIN_STALE": "check", "COVER_HINT": "check", "DUPLICATE_LEG": "check",
    # 1.0.7.2 review: the held quantity 'all' / 'half' took; a butterfly's call / put filled in; an order whose own book overrides the
    # opening phrase's
    "QTY_FROM_HELD": "check", "RIGHT_DEFAULT": "check", "BOOK_CONFLICT": "check",
    # 1.0.7.2 simplification: the orders of a group are separate tickets
    "SEPARATE_TICKETS": "check",
    "TIF_DEFAULT": "info", "BOOK_SELECTED": "info", "TYPE_FROM_AT": "info", "STRATEGY_EXPANDED": "info", "STRATEGY_SIDE_CONVENTION": "info",
}

# ---- the vocabulary (G 3.4.1 item 4, 3.4.3) ------------------------------------------------------------------------------------------
MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
          "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
          "december": 12}
#: side words: "buy" / "sell" are position-aware; every other side is explicit and never changed
SIDE_WORDS = {"buy": "buy", "sell": "sell", "short": "SELL_SHORT", "cover": "BUY_TO_COVER", "bto": "BTO", "sto": "STO", "btc": "BTC", "stc": "STC",
              "buy-to-open": "BTO", "sell-to-open": "STO", "buy-to-close": "BTC", "sell-to-close": "STC", "buy-to-cover": "BUY_TO_COVER",
              "sell-short": "SELL_SHORT", "short-sell": "SELL_SHORT"}
VERB_WORDS = {"close": "close", "roll": "roll", "collar": "collar", "buy-write": "covered", "covered-call": "covered"}
UNIT_WORDS = {"shares": "shares", "share": "shares", "sh": "shares", "contracts": "contracts", "contract": "contracts", "cts": "contracts",
              "lots": "lots", "lot": "lots", "x": "x"}
RIGHT_WORDS = {"call": "C", "calls": "C", "c": "C", "put": "P", "puts": "P", "p": "P"}
#: "condor" alone is NOT an iron condor (1.0.7.2 review): a condor of calls or of puts is long the wings and short the body when bought,
#: an iron condor the reverse; a bare "condor" is TOKEN_AMBIGUOUS with the three readings as choices
STRAT_WORDS = {"vertical": ("vertical", None), "straddle": ("straddle", None), "strangle": ("strangle", None), "butterfly": ("butterfly", None),
               "fly": ("butterfly", None), "calendar": ("calendar", None), "condor": ("condor", None)}
CLASS_WORDS = {"options": "options", "option": "options", "shares": "shares", "stock": "shares"}
UNIT_AFTER = {"contracts": "contracts", "contract": "contracts", "cts": "contracts", "lots": "lots", "lot": "lots", "share": "shares"}
GROUP_WORDS = ("together", "aon")
FILLER_WORDS = ("and", "then", "plus")
FILLER_PUNCT = (",", "&", "+")
HARD_PUNCT = (";", ".")
PRICE_LIMIT_WORDS = ("limit", "lmt", "lim")
PCT_WORDS = ("pct", "percent")
PHRASES = {
    ("sell", "short"): ("side", "SELL_SHORT"), ("buy", "to", "cover"): ("side", "BUY_TO_COVER"), ("buy", "to", "open"): ("side", "BTO"),
    ("sell", "to", "open"): ("side", "STO"), ("buy", "to", "close"): ("side", "BTC"), ("sell", "to", "close"): ("side", "STC"),
    ("at", "market"): ("price", "MARKET"), ("at", "the", "market"): ("price", "MARKET"), ("@", "market"): ("price", "MARKET"),
    ("@", "the", "market"): ("price", "MARKET"), ("trailing", "stop"): ("price", "TRAIL"), ("stop", "limit"): ("price", "STOP_LIMIT"),
    ("at", "the", "bid"): ("price", "BID"), ("at", "the", "ask"): ("price", "ASK"), ("at", "the", "mid"): ("price", "MID"),
    ("at", "bid"): ("price", "BID"), ("at", "ask"): ("price", "ASK"), ("at", "mid"): ("price", "MID"),
    ("@", "the", "bid"): ("price", "BID"), ("@", "the", "ask"): ("price", "ASK"), ("@", "the", "mid"): ("price", "MID"),
    ("@", "bid"): ("price", "BID"), ("@", "ask"): ("price", "ASK"), ("@", "mid"): ("price", "MID"),
    ("good", "till", "cancelled"): ("tif", "GTC"), ("good", "till", "canceled"): ("tif", "GTC"), ("good", "til", "cancelled"): ("tif", "GTC"),
    ("good", "til", "canceled"): ("tif", "GTC"), ("good", "until", "cancelled"): ("tif", "GTC"), ("good", "until", "canceled"): ("tif", "GTC"),
    ("good", "till"): ("tif", "GTD"), ("good", "til"): ("tif", "GTD"), ("good", "until"): ("tif", "GTD"), ("next", "session"): ("tif", "NEXT_SESSION"),
    ("call", "spread"): ("strat", "call spread"), ("put", "spread"): ("strat", "put spread"), ("call", "vertical"): ("strat", "call spread"),
    ("put", "vertical"): ("strat", "put spread"), ("iron", "condor"): ("strat", "iron condor"), ("iron", "butterfly"): ("strat", "iron butterfly"),
    ("iron", "fly"): ("strat", "iron butterfly"), ("call", "butterfly"): ("strat", "call butterfly"), ("put", "butterfly"): ("strat", "put butterfly"),
    ("call", "fly"): ("strat", "call butterfly"), ("put", "fly"): ("strat", "put butterfly"), ("call", "calendar"): ("strat", "call calendar"),
    ("put", "calendar"): ("strat", "put calendar"), ("call", "condor"): ("strat", "call condor"), ("put", "condor"): ("strat", "put condor"),
    ("covered", "call"): ("verb", "covered"), ("buy", "write"): ("verb", "covered"), ("close", "out"): ("verb", "close"),
    ("as", "a", "package"): ("group", "as a package"), ("as", "one", "order"): ("group", "as one order"), ("all", "or", "none"): ("group", "all or none"),
    ("as", "a", "combo"): ("group", "as a combo"), ("as", "one"): ("group", "as one"),
    ("us", "exchange"): ("acct", "US_EXCHANGE"), ("prime", "broker"): ("acct", "PB"),
}
#: lower-case words that are never tickers unless typed in capitals or with "$" (the slot rule's keyword side)
BASE_KEYWORDS = frozenset(set(MONTHS) | set(SIDE_WORDS) | set(VERB_WORDS) | set(UNIT_WORDS) | set(RIGHT_WORDS) | set(STRAT_WORDS) | set(CLASS_WORDS)
                          | set(GROUP_WORDS) | set(FILLER_WORDS) | set(PRICE_LIMIT_WORDS) | set(PCT_WORDS)
                          | {"in", "for", "the", "book", "books", "at", "to", "out", "front", "active", "all", "half", "market", "mkt", "stop", "trail",
                             "trailing", "bid", "ask", "mid", "day", "gtc", "gtd", "good", "till", "til", "until", "next", "session", "cancelled",
                             "canceled", "exchange", "pb", "prime", "broker", "us", "as", "package", "one", "order", "or", "none", "spread", "iron",
                             "covered", "write", "of", "combo", "with", "each", "leg", "legs",
                             # G 3.4.5's collision words: English first, a ticker only in capitals or with "$"
                             "on", "now", "key", "so", "it", "please", "today"})
#: OCC root -> underlying instrument id (mirrors ``products.options.underlying_id_of_root``: SPXW is SPX)
ROOT_UNDERLYING = {"SPXW": "SPX"}
OPTION_ACCOUNT_KINDS = ("US_EXCHANGE", "PB")       # what ``InstrumentMaster.ensure_option`` gives a contract listed from a chain
EQUITY_KINDS = ("EQUITY", "ETF", "ADR", "REIT", "PREFERRED")
STRIKE_COUNT = {"call spread": 2, "put spread": 2, "vertical": 2, "straddle": 1, "strangle": 2, "iron condor": 4, "iron butterfly": 3,
                "butterfly": 3, "call butterfly": 3, "put butterfly": 3, "calendar": 1, "call calendar": 1, "put calendar": 1, "condor": 4}
#: strategies whose strikes are typed low to high (STRIKE_ORDER otherwise); a vertical's typed order does not matter (the side decides)
ORDERED_STRIKES = ("strangle", "iron condor", "iron butterfly", "butterfly", "condor")


class SmartTicketError(RejectError):
    """A 400 of the parse route (TEXT_TOO_LONG, TOO_MANY_LEGS, BAD_REQUEST)."""
    http = 400


class _UnknownBook(RejectError):
    http = 404


class _ParseFailed(RejectError):
    """A parser fault: logged and answered 500 PARSE_FAILED by the route itself - never through the router's incident path, which
    writes an attention row (the route stays pure even when it fails)."""
    http = 500


# ---- small helpers -------------------------------------------------------------------------------------------------------------------
def _flag(code: str, text: str, span=None, choices=None, action=None, severity: Optional[str] = None) -> dict:
    f = {"code": code, "severity": severity or FLAG_SEVERITY[code], "text": text}
    if span is not None:
        f["span"] = [int(span[0]), int(span[1])]
    if choices:
        # (a choice's ``_span``: the wider words it needs - fourth review - is consumed by ``_widen`` before the answer is built)
        f["choices"] = [dict({"label": str(c["label"]), "clause": str(c["clause"])}, **({"_span": [int(c["_span"][0]), int(c["_span"][1])]} if c.get("_span") else {}))
                        for c in choices]
    if action:
        f["action"] = action
    return f


def _flag_key(f: dict):
    sp = f.get("span") or [-1, -1]
    return (SEVERITY_RANK[f["severity"]], f["code"], sp[0], sp[1], f["text"])


def sort_flags(flags: Sequence[dict]) -> List[dict]:
    """Flags sorted by (severity rank, code, span), duplicates (the same code, span and text) dropped."""
    out, seen = [], set()
    for f in sorted(flags, key=_flag_key):
        k = (f["code"], tuple(f.get("span") or ()), f["text"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _kstr(x) -> str:
    """A strike or price in plain digits ("240", "242.5"), never exponent notation."""
    d = Decimal(str(x))
    s = format(d.normalize(), "f")
    return s if "." not in s else s.rstrip("0").rstrip(".")


def _dec(s) -> Optional[Decimal]:
    try:
        return Decimal(str(s))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _span(a, b=None) -> List[int]:
    if b is None:
        return [a.start, a.end]
    return [a.start, b.end]


def _edit(text: str, span: Sequence[int], edits: Sequence[Tuple[int, int, str]]) -> str:
    """The text of ``span`` with ``edits`` (absolute ``(start, end, replacement)``, non-overlapping) applied, spaces tidied."""
    s, e = int(span[0]), int(span[1])
    out = text[s:e]
    for a, b, rep in sorted(edits, key=lambda t: (t[0], t[1]), reverse=True):
        a2, b2 = max(0, a - s), max(0, b - s)
        out = out[:a2] + rep + out[b2:]
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = out.replace("( ", "(").replace(" )", ")").replace(" ,", ",")
    return out.strip()


def _month_words(m: int) -> str:
    return MONTH_NAMES[m - 1]


def _a(noun: str) -> str:
    return ("an " if str(noun)[:1].lower() in "aeiou" else "a ") + str(noun)


# ---- normalisation ---------------------------------------------------------------------------------------------------------------------
_ASCII_MAP = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u00b4": "'",
              "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
              "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-", "\ufe63": "-", "\uff0d": "-",
              "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ", "\u200a": " ", "\u2002": " ", "\u2003": " ", "\u3000": " ",
              "\u00d7": "x", "\u2715": "x", "\t": " "}


def normalise(text: str) -> Tuple[str, List[List[int]]]:
    """``(the ASCII text, the spans of characters that are not ASCII)``: NFKC; curly quotes, dashes, non-breaking spaces and the
    multiplication sign to ASCII; CR / CRLF to a newline, a tab to a space; any other non-ASCII or control character becomes "?" and its
    span is returned (each becomes an UNPARSED_TEXT span).  Idempotent."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    out: List[str] = []
    bad: List[List[int]] = []
    for ch in t:
        ch = _ASCII_MAP.get(ch, ch)
        for c in ch:
            if c == "\n" or 32 <= ord(c) < 127:
                out.append(c)
            else:
                pos = len(out)
                if bad and bad[-1][1] == pos:
                    bad[-1][1] = pos + 1
                else:
                    bad.append([pos, pos + 1])
                out.append("?")
    return "".join(out), bad


# ---- tokens ----------------------------------------------------------------------------------------------------------------------------
class Token:
    """One token: ``kind`` (occ, fut, acct, date, strikes, strike_right, year2, pair, forced, num, word, punct, nl, phrase, bad), the source
    ``text``, its ``lower`` form, ``[start, end)`` in the normalised text and a JSON-able ``value``."""
    __slots__ = ("kind", "text", "lower", "start", "end", "value")

    def __init__(self, kind: str, text: str, start: int, end: int, value: Any = None):
        self.kind, self.text, self.lower, self.start, self.end, self.value = kind, text, text.lower(), int(start), int(end), value

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "start": self.start, "end": self.end, "value": self.value}

    def __repr__(self) -> str:   # pragma: no cover - debugging aid
        return f"Token({self.kind}, {self.text!r}, {self.start}, {self.end})"


_RX = (
    ("occ", re.compile(r"[A-Za-z][A-Za-z0-9.\-]{0,5}\d{6}[CPcp]\d{8}(?![A-Za-z0-9])")),
    ("acct", re.compile(r"[A-Za-z]\d+:[A-Za-z_]+(?![A-Za-z0-9_])")),
    ("date", re.compile(r"\d{4}-\d{2}-\d{2}(?![\d\-])")),
    ("usdate", re.compile(r"\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})(?![\d/])")),
    ("strikes", re.compile(r"\d+(?:\.\d+)?(?:[ ]*/[ ]*\d+(?:\.\d+)?)+(?![\d.A-Za-z/])")),
    ("strike_right", re.compile(r"\d+(?:\.\d+)?[CcPp](?![A-Za-z0-9])")),
    ("year2", re.compile(r"'\d{2}(?!\d)")),
    ("pair", re.compile(r"[A-Za-z]{3}/[A-Za-z]{3}(?![A-Za-z0-9/])")),
    ("fut", re.compile(r"(?:6[EeJjBb]|[A-Za-z]{1,3})[FGHJKMNQUVXZfghjkmnquvxz]\d{2}(?![A-Za-z0-9])")),
    ("forced", re.compile(r"\$(?:[A-Za-z][A-Za-z0-9]*|\d+(?=[.\-][A-Za-z])|\d+[A-Za-z][A-Za-z0-9]*)(?:[.\-][A-Za-z0-9]+)*")),
    ("num", re.compile(r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:st|nd|rd|th|[kKmM%xX])?(?![A-Za-z0-9])|\$?\.\d+(?![A-Za-z0-9])")),
    ("word", re.compile(r"6[EeJjBb](?![A-Za-z0-9])|[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*")),
    ("punct", re.compile(r"[,;:@()./&+]")),
)
_JUNK = re.compile(r"[^\s,;:@()&+]+")
_NUM_PARTS = re.compile(r"^(\$?)([0-9,]*\.?[0-9]*)(st|nd|rd|th|[kKmM%xX])?$")
#: digits, a comma and digits typed together ("1,5k", "2,5", "10,25", "1,5000", "12,34567") that are not a thousands grouping
#: (``_THOUSANDS_OK``): ONE malformed number (QTY_NOT_WHOLE in the quantity slot), never a clause break that would leave "5k ACME" or
#: "5000 ACME" as an order of its own (1.0.7.2 review; second review: any number of digits after the comma); right after a month word a
#: day and a year stay a day and a year ("Dec 18,26", "Dec 18,2026": ``_DAY_YEAR``)
_BADNUM = re.compile(r"\$?\d+(?:,\d+)+(?:\.\d+)?[kKmMxX%]?(?![A-Za-z0-9])")
_THOUSANDS_OK = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?[kKmMxX%]?")
_DAY_YEAR = re.compile(r"\d{1,2},(?:\d{2}|\d{4})")
#: a quantity typed with a point before exactly three digits ("1.000"): a decimal point or a thousands point?  TOKEN_AMBIGUOUS
_THOUSANDS_POINT = re.compile(r"\d{1,3}\.\d{3}")


def _num_value(s: str) -> dict:
    m = _NUM_PARTS.match(s)
    dollar, body, suf = (m.group(1), m.group(2), m.group(3)) if m else ("", s, None)
    plain = body.replace(",", "")
    if plain.startswith("."):
        plain = "0" + plain
    sfx = None
    if suf:
        sfx = "ord" if suf.lower() in ("st", "nd", "rd", "th") else suf.lower()
    intpart = plain.split(".")[0]
    return {"num": plain, "dollar": bool(dollar), "suffix": sfx, "int": "." not in plain, "digits": len(intpart), "commas": "," in body}


def _make_token(kind: str, s: str, start: int, end: int) -> Optional[Token]:
    if kind == "occ":
        return Token(kind, s, start, end, s.upper()) if parse_occ(s) is not None else None
    if kind == "acct":
        up = s.upper()
        return Token(kind, s, start, end, up) if re.match(config.ACCOUNT_ID_RE, up) else None
    if kind == "date":
        try:
            return Token(kind, s, start, end, date.fromisoformat(s).isoformat())
        except ValueError:
            return None
    if kind == "usdate":
        mm, dd, yy = s.split("/")
        y = int(yy) + (2000 if len(yy) == 2 else 0)
        try:
            return Token("date", s, start, end, date(y, int(mm), int(dd)).isoformat())
        except ValueError:
            return None
    if kind == "strikes":
        parts = [p.strip() for p in s.split("/")]
        if any(_dec(p) is None or _dec(p) <= 0 for p in parts):
            return None
        return Token(kind, s, start, end, [_kstr(p) for p in parts])
    if kind == "strike_right":
        k, r = s[:-1], s[-1].upper()
        if _dec(k) is None or _dec(k) <= 0:
            return None
        return Token(kind, s, start, end, {"k": _kstr(k), "right": r})
    if kind == "year2":
        return Token(kind, s, start, end, 2000 + int(s[1:]))
    if kind == "pair":
        a, b = s.split("/")
        if a.lower() in MONTHS or b.lower() in MONTHS:
            return None
        return Token(kind, s, start, end, (a + b).upper())
    if kind == "fut":
        p = parse_futures_id(s.upper())
        if p is None:
            return None
        return Token(kind, s, start, end, {"id": s.upper(), "root": p[0], "year": int(p[1]), "month": int(p[2])})
    if kind == "forced":
        return Token(kind, s, start, end, s[1:].upper())
    if kind == "num":
        return Token(kind, s, start, end, _num_value(s))
    if kind == "word":
        return Token(kind, s, start, end, s.upper())
    return Token(kind, s, start, end, s)


def _merge_phrases(toks: List[Token]) -> List[Token]:
    out: List[Token] = []
    i = 0
    while i < len(toks):
        hit = None
        for n in (3, 2):
            part = toks[i:i + n]
            if len(part) < n or not all(t.kind == "word" or (t.kind == "punct" and t.text == "@") for t in part):
                continue
            key = tuple(t.lower for t in part)
            if key in PHRASES:
                hit = (n, PHRASES[key])
                break
        if hit is None:
            out.append(toks[i])
            i += 1
            continue
        n, (pk, pv) = hit
        a, b = toks[i], toks[i + n - 1]
        text = " ".join(t.text for t in toks[i:i + n])
        tok = Token("phrase", text, a.start, b.end, {"kind": pk, "value": pv})
        out.append(tok)
        i += n
    return out


def tokenize(text: str) -> List[Token]:
    """The tokens of a (normalised) text, longest match first, with the multi-word keywords merged into ``phrase`` tokens; a run of
    characters no pattern reads is one ``bad`` token (an UNPARSED_TEXT span)."""
    text = str(text or "")
    toks: List[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t":
            i += 1
            continue
        if ch == "\n":
            toks.append(Token("nl", "\n", i, i + 1, "\n"))
            i += 1
            continue
        bm = _BADNUM.match(text, i) if ch.isdigit() or ch == "$" else None
        prev = toks[-1] if toks else None
        if bm is not None and not _THOUSANDS_OK.fullmatch(bm.group(0)) \
                and not (prev is not None and prev.kind == "word" and prev.lower in MONTHS and _DAY_YEAR.fullmatch(bm.group(0))):
            tok = Token("badnum", bm.group(0), i, bm.end(), bm.group(0))
            toks.append(tok)
            i = tok.end
            continue
        tok = None
        for kind, rx in _RX:
            m = rx.match(text, i)
            if not m:
                continue
            tok = _make_token(kind, m.group(0), i, i + len(m.group(0)))
            if tok is not None:
                break
        if tok is None:
            m = _JUNK.match(text, i)
            s = m.group(0) if m and m.group(0) else text[i]
            tok = Token("bad", s, i, i + len(s), s)
        toks.append(tok)
        i = tok.end
    return _merge_phrases(toks)


# ---- the vocabulary that depends on the books table (passed in as data: the pure layer reads no database) ---------------------------------
DEFAULT_BOOKS = tuple((code, name, profile) for code, name, profile, _cap in config.BOOKS)


class Vocab:
    """The book words of one parse: a book code, the first word of a book's name and its profile word -> the book codes they name
    (two codes = ambiguous); the keyword set of the slot rule."""

    def __init__(self, books: Optional[Sequence[Sequence[str]]] = None):
        rows = [tuple(b[:3]) for b in (books if books is not None else DEFAULT_BOOKS)]
        self.books = [(str(c), str(n), str(p)) for c, n, p in rows]
        self.names = {c: n for c, n, _p in self.books}
        words: Dict[str, List[str]] = {}
        for c, n, p in self.books:
            first = (n.split() or [c])[0]
            for w in (c, first, p):
                key = str(w).lower()
                if key and c not in words.setdefault(key, []):
                    words[key].append(c)
        self.words = {k: sorted(v) for k, v in words.items()}
        self.keywords = frozenset(BASE_KEYWORDS | {k for k in self.words if len(k) > 1})

    def book_codes(self, tok: Optional[Token]) -> Optional[List[str]]:
        if tok is None or tok.kind != "word":
            return None
        return self.words.get(tok.lower)

    def label(self, code: str) -> str:
        return self.names.get(code, code)

    def first_word(self, code: str) -> str:
        return (self.names.get(code, code).split() or [code])[0]


def _ticker_of(tok: Optional[Token], vocab: Vocab) -> Optional[str]:
    """The ticker a token names in the instrument slot, or None: ``$WORD`` always; one- and two-letter words only in capitals; a grammar
    keyword only when typed in capitals."""
    if tok is None:
        return None
    if tok.kind == "forced":
        return tok.value
    if tok.kind != "word":
        return None
    w = tok.text
    if len(w) <= 2:
        return w.upper() if w.isupper() else None
    if tok.lower in vocab.keywords and not w.isupper():
        return None
    return w.upper()


def _underlying_key(sym: Optional[str]) -> Optional[str]:
    if not sym:
        return None
    s = str(sym).upper()
    return ROOT_UNDERLYING.get(s, s)


# ---- token predicates --------------------------------------------------------------------------------------------------------------------
def _is_punct(t: Optional[Token], *chars) -> bool:
    return t is not None and t.kind == "punct" and t.text in chars


def _is_word(t: Optional[Token], *lowers) -> bool:
    return t is not None and t.kind == "word" and t.lower in lowers


def _phrase(t: Optional[Token], kind: str) -> Optional[str]:
    if t is not None and t.kind == "phrase" and t.value["kind"] == kind:
        return t.value["value"]
    return None


def _side_of(t: Optional[Token]) -> Optional[str]:
    if t is None:
        return None
    if t.kind == "phrase" and t.value["kind"] == "side":
        return t.value["value"]
    if t.kind == "word":
        return SIDE_WORDS.get(t.lower)
    return None


def _verb_of(t: Optional[Token]) -> Optional[str]:
    if t is None:
        return None
    if t.kind == "phrase" and t.value["kind"] == "verb":
        return t.value["value"]
    if t.kind == "word":
        return VERB_WORDS.get(t.lower)
    return None


def _month_of(t: Optional[Token]) -> Optional[int]:
    if t is not None and t.kind == "word":
        return MONTHS.get(t.lower)
    return None


def _right_of(t: Optional[Token]) -> Optional[str]:
    if t is not None and t.kind == "word":
        return RIGHT_WORDS.get(t.lower)
    return None


def _strat_of(t: Optional[Token]) -> Optional[Tuple[str, Optional[str]]]:
    if t is None:
        return None
    if t.kind == "phrase" and t.value["kind"] == "strat":
        name = t.value["value"]
        right = "C" if name.startswith("call ") else ("P" if name.startswith("put ") else None)
        base = {"call butterfly": "butterfly", "put butterfly": "butterfly", "call calendar": "calendar", "put calendar": "calendar",
                "call condor": "condor", "put condor": "condor"}.get(name, name)
        return base, right
    if t.kind == "word" and t.lower in STRAT_WORDS:
        return STRAT_WORDS[t.lower]
    return None


def _num(t: Optional[Token]) -> Optional[dict]:
    return t.value if t is not None and t.kind == "num" else None


def _day_of(t: Optional[Token]) -> Optional[int]:
    v = _num(t)
    if v is None or v["dollar"] or v["commas"] or not v["int"] or v["suffix"] not in (None, "ord") or v["digits"] > 2:
        return None
    d = int(v["num"])
    return d if 1 <= d <= 31 else None


def _year4_of(t: Optional[Token]) -> Optional[int]:
    v = _num(t)
    if v is None or v["dollar"] or v["commas"] or not v["int"] or v["suffix"] is not None or v["digits"] != 4:
        return None
    y = int(v["num"])
    return y if 2000 <= y <= 2099 else None


def _strike_of(t: Optional[Token]) -> Optional[str]:
    v = _num(t)
    if v is None or v["dollar"] or v["suffix"] is not None:
        return None
    d = _dec(v["num"])
    return _kstr(d) if d is not None and d > 0 else None


def _price_num(t: Optional[Token], pct_ok: bool = False) -> Optional[str]:
    """A price as typed (a leading "$" and commas removed), or None."""
    v = _num(t)
    if v is None or v["suffix"] not in ((None, "%") if pct_ok else (None,)):
        return None
    return v["num"]


def _qty_token(t: Optional[Token], dollars: bool = False) -> bool:
    if t is None:
        return False
    if t.kind == "word" and t.lower in ("all", "half"):
        return True
    if t.kind == "badnum":                                  # "1,5k": one malformed quantity, refused in its slot (never a clause break)
        return dollars or not t.text.startswith("$")
    v = _num(t)
    return v is not None and (dollars or not v["dollar"]) and v["suffix"] in (None, "k", "m", "x")


def _instrumentish(t: Optional[Token], vocab: Vocab) -> bool:
    """Whether a token can open the instrument slot (used to decide whether "," / "and" end a clause): rights, months and units are
    excluded so "Dec 18, 26 C" never splits."""
    if t is None:
        return False
    if t.kind in ("occ", "fut", "pair", "forced"):
        return True
    if t.kind != "word":
        return False
    if t.lower in ("front", "active"):
        return True
    if t.lower in RIGHT_WORDS or t.lower in MONTHS or t.lower in UNIT_WORDS:
        return False
    return _ticker_of(t, vocab) is not None


def _starts_clause(toks: Sequence[Token], j: int, vocab: Vocab) -> bool:
    if j >= len(toks):
        return False
    t = toks[j]
    if _side_of(t) or _verb_of(t):
        return True
    if _qty_token(t):
        k = j + 1
        if k < len(toks) and toks[k].kind == "word" and toks[k].lower in UNIT_WORDS:
            return _instrumentish(toks[k + 1] if k + 1 < len(toks) else None, vocab) or _instrumentish(toks[k], vocab)
        return _instrumentish(toks[k] if k < len(toks) else None, vocab)
    return False


def _is_filler(t: Optional[Token]) -> bool:
    return t is not None and ((t.kind == "punct" and t.text in FILLER_PUNCT) or (t.kind == "word" and t.lower in FILLER_WORDS))


def _is_hard(t: Optional[Token]) -> bool:
    return t is not None and (t.kind == "nl" or (t.kind == "punct" and t.text in HARD_PUNCT))


# ---- clause splitting ---------------------------------------------------------------------------------------------------------------------
def split_clauses(tokens: Sequence[Token], vocab: Optional[Vocab] = None) -> Tuple[List[dict], List[dict], List[List[int]]]:
    """``(segments, groups, stray)``: each segment ``{tokens, group}`` (the index of its parenthesised group or None); each group
    ``{index, open, close, word}`` (spans); ``stray`` = the spans of unmatched parentheses.  ``;``, ``.`` and newline always end a clause;
    ``,`` / ``and`` / ``then`` / ``plus`` / ``&`` / ``+`` only when the next token starts a clause."""
    vocab = vocab or Vocab()
    toks = list(tokens)
    segs: List[dict] = []
    groups: List[dict] = []
    stray: List[List[int]] = []
    cur: List[Token] = []
    open_group: Optional[int] = None

    def flush():
        nonlocal cur
        # 1.0.7.2 fifth review: a connector alone between two groups or separators ("(...) then (...)") is a separator, never an order
        if cur and not all(_is_filler(t) for t in cur):
            segs.append({"tokens": cur, "group": open_group})
        cur = []

    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if _is_hard(t):
            flush()
            i += 1
            continue
        if _is_punct(t, "("):
            flush()
            if open_group is None:
                groups.append({"index": len(groups), "open": _span(t), "close": None, "word": None})
                open_group = len(groups) - 1
            else:
                stray.append(_span(t))
            i += 1
            continue
        if _is_punct(t, ")"):
            flush()
            if open_group is not None:
                g = groups[open_group]
                g["close"] = _span(t)
                open_group = None
                nxt = toks[i + 1] if i + 1 < n else None
                if nxt is not None and (_phrase(nxt, "group") or _is_word(nxt, *GROUP_WORDS)):
                    g["word"] = _span(nxt)
                    i += 2
                    continue
            else:
                stray.append(_span(t))
            i += 1
            continue
        if _is_filler(t):
            j = i
            while j < n and _is_filler(toks[j]):
                j += 1
            if _starts_clause(toks, j, vocab):
                flush()
                i = j
                continue
            cur.append(t)
            i += 1
            continue
        cur.append(t)
        i += 1
    flush()
    if open_group is not None:                              # an unclosed "(": its clauses stand alone, the parenthesis is unparsed
        g = groups[open_group]
        stray.append(g["open"])
        for s in segs:
            if s["group"] == open_group:
                s["group"] = None
        groups[open_group] = None
    keep = {}
    out_groups = []
    for g in groups:
        if g is None:
            continue
        keep[g["index"]] = len(out_groups)
        out_groups.append(g)
    for s in segs:
        if s["group"] is not None:
            s["group"] = keep.get(s["group"])
    return segs, out_groups, stray


# ---- modifiers ----------------------------------------------------------------------------------------------------------------------------
def _date_expr(toks: Sequence[Token], q: int) -> Optional[Tuple[dict, int]]:
    """A date for GTD / until: an ISO or US date token, month day [year] or day month [year]."""
    t = toks[q] if q < len(toks) else None
    if t is None:
        return None
    if t.kind == "date":
        return {"date": t.value, "span": _span(t)}, q + 1
    m = _month_of(t)
    if m and _day_of(toks[q + 1] if q + 1 < len(toks) else None):
        d = _day_of(toks[q + 1])
        y, q2 = _year_after(toks, q + 2)
        return {"m": m, "d": d, "y": y, "span": [t.start, toks[q2 - 1].end]}, q2
    d = _day_of(t)
    if d and _month_of(toks[q + 1] if q + 1 < len(toks) else None):
        m = _month_of(toks[q + 1])
        y, q2 = _year_after(toks, q + 2, allow_comma=False)
        return {"m": m, "d": d, "y": y, "span": [t.start, toks[q2 - 1].end]}, q2
    return None


def _year_after(toks: Sequence[Token], q: int, allow_comma: bool = True) -> Tuple[Optional[int], int]:
    t = toks[q] if q < len(toks) else None
    y = _year4_of(t)
    if y:
        return y, q + 1
    if t is not None and t.kind == "year2":
        return int(t.value), q + 1
    if allow_comma and _is_punct(t, ",") and q + 1 < len(toks):
        t2 = toks[q + 1]
        y2 = _year4_of(t2)
        if y2:
            return y2, q + 2
        v = _num(t2)
        if v and v["int"] and v["digits"] == 2 and not v["dollar"] and v["suffix"] is None:
            return 2000 + int(v["num"]), q + 2
    return None, q


def _price_at(toks: Sequence[Token], k: int) -> Optional[Tuple[dict, int, List[dict]]]:
    t = toks[k]
    n = len(toks)
    nxt = toks[k + 1] if k + 1 < n else None
    pv = _phrase(t, "price")
    flags: List[dict] = []
    if pv == "MARKET" or _is_word(t, "market", "mkt"):
        return {"type": "MARKET", "span": _span(t)}, k + 1, flags
    if pv in ("BID", "ASK", "MID"):
        return {"type": "LIMIT", "quote": pv.lower(), "span": _span(t)}, k + 1, flags
    if pv == "STOP_LIMIT":
        a, b = _price_num(nxt), _price_num(toks[k + 2] if k + 2 < n else None)
        end = k + 1 + (1 if a else 0) + (1 if a and b else 0)
        flags.append(_flag("MISSING_PRICE", "a stop limit needs both prices in order: 'stop 110 limit 109.50'", _span(t, toks[end - 1])))
        return {"type": "STOP_LIMIT", "stop": None, "limit": None, "span": _span(t, toks[end - 1]), "bad": True}, end, flags
    if pv == "TRAIL" or _is_word(t, "trail", "trailing"):
        q = k + 1
        if pv is None and _is_word(toks[q] if q < n else None, "stop"):
            q += 1
        if _is_word(toks[q] if q < n else None, "of"):
            q += 1
        v = _num(toks[q] if q < n else None)
        if v is None or v["suffix"] not in (None, "%"):
            flags.append(_flag("MISSING_PRICE", "a trailing stop needs its trail: 'trail 1.50' (price units) or 'trail 2%'", _span(t, toks[q - 1])))
            return {"type": "TRAILING_STOP", "span": _span(t, toks[q - 1]), "bad": True}, q, flags
        if v["suffix"] == "%":
            return {"type": "TRAILING_STOP", "trail_pct": v["num"], "span": _span(t, toks[q])}, q + 1, flags
        if _is_word(toks[q + 1] if q + 1 < n else None, *PCT_WORDS):
            return {"type": "TRAILING_STOP", "trail_pct": v["num"], "span": _span(t, toks[q + 1])}, q + 2, flags
        return {"type": "TRAILING_STOP", "trail_amt": v["num"], "span": _span(t, toks[q])}, q + 1, flags
    if _is_word(t, *PRICE_LIMIT_WORDS):
        q = k + 1
        if _is_word(nxt, "at") or _is_punct(nxt, "@"):
            q += 1
        t2 = toks[q] if q < n else None
        p = _price_num(t2)
        if p is not None:
            return {"type": "LIMIT", "limit": p, "span": _span(t, t2)}, q + 1, flags
        qv = _phrase(t2, "price")
        if qv in ("BID", "ASK", "MID"):
            return {"type": "LIMIT", "quote": qv.lower(), "span": _span(t, t2)}, q + 1, flags
        flags.append(_flag("MISSING_PRICE", f"'{t.text}' needs a price: 'limit 225'", _span(t)))
        return {"type": "LIMIT", "span": _span(t), "bad": True}, k + 1, flags
    if (_is_word(t, "at") or _is_punct(t, "@")) and _price_num(nxt) is not None:
        return {"type": "LIMIT", "limit": _price_num(nxt), "from_at": True, "span": _span(t, nxt)}, k + 2, flags
    if _is_word(t, "stop"):
        p = _price_num(nxt)
        if p is None:
            flags.append(_flag("MISSING_PRICE", "'stop' needs a price: 'stop 110.50'", _span(t)))
            return {"type": "STOP", "span": _span(t), "bad": True}, k + 1, flags
        q = k + 2
        t3 = toks[q] if q < n else None
        if _is_word(t3, *PRICE_LIMIT_WORDS):
            p2 = _price_num(toks[q + 1] if q + 1 < n else None)
            if p2 is not None:
                return {"type": "STOP_LIMIT", "stop": p, "limit": p2, "span": _span(t, toks[q + 1])}, q + 2, flags
            flags.append(_flag("MISSING_PRICE", "a stop limit needs its limit: 'stop 110 limit 109.50'", _span(t, t3)))
            return {"type": "STOP_LIMIT", "stop": p, "span": _span(t, t3), "bad": True}, q + 1, flags
        return {"type": "STOP", "stop": p, "span": _span(t, nxt)}, k + 2, flags
    if t.kind == "num" and _is_word(nxt, "limit", "lmt") and _price_num(t) is not None and not _price_follows(toks, k + 2):
        # "225 limit" - but when a price follows the word ("2026 limit 2.50") the number after it is the price (fifth review)
        return {"type": "LIMIT", "limit": _price_num(t), "span": _span(t, nxt)}, k + 2, flags
    return None


def _price_follows(toks: Sequence[Token], q: int) -> bool:
    """Whether a price is written at ``q`` (after a "limit" / "lmt" word): a number, "at" / "@" and a number, or the bid / ask / mid."""
    t = toks[q] if q < len(toks) else None
    if _is_word(t, "at") or _is_punct(t, "@"):
        t = toks[q + 1] if q + 1 < len(toks) else None
    return _price_num(t) is not None or _phrase(t, "price") in ("BID", "ASK", "MID")


def _tif_at(toks: Sequence[Token], k: int) -> Optional[Tuple[dict, int, List[dict]]]:
    t = toks[k]
    tv = _phrase(t, "tif")
    if _is_word(t, "day"):
        return {"tif": "DAY", "span": _span(t)}, k + 1, []
    if tv == "GTC" or _is_word(t, "gtc"):
        return {"tif": "GTC", "span": _span(t)}, k + 1, []
    if tv == "NEXT_SESSION":
        return {"tif": "NEXT_SESSION", "span": _span(t)}, k + 1, []
    if tv == "GTD" or _is_word(t, "gtd", "until"):
        got = _date_expr(toks, k + 1)
        if got is None:
            return {"tif": "GTD", "span": _span(t), "bad": True}, k + 1, [_flag("UNPARSED_TEXT", f"'{t.text}' needs a date: 'gtd 2026-12-18' or 'until Dec 18 2026'", _span(t))]
        d, q = got
        return {"tif": "GTD", "date": {k2: v for k2, v in d.items() if k2 != "span"}, "span": [t.start, toks[q - 1].end]}, q, []
    return None


def _account_words(toks: Sequence[Token], q: int) -> Tuple[Optional[dict], int]:
    t = toks[q] if q < len(toks) else None
    if t is None:
        return None, q
    if t.kind == "acct":
        return {"id": t.value, "span": _span(t), "text": t.text}, q + 1
    av = _phrase(t, "acct")
    if av == "US_EXCHANGE" or _is_word(t, "exchange"):
        return {"role": "US_EXCHANGE", "span": _span(t), "text": t.text}, q + 1
    if av == "PB" or _is_word(t, "pb"):
        nxt = toks[q + 1] if q + 1 < len(toks) else None
        if nxt is not None and nxt.kind == "word" and nxt.text.upper() in config.PRIME_BROKERS:
            return {"role": "PB_" + nxt.text.upper(), "span": _span(t, nxt), "text": f"{t.text} {nxt.text}"}, q + 2
        return {"role": "PB", "span": _span(t), "text": t.text}, q + 1
    return None, q


def _scope_at(toks: Sequence[Token], k: int, vocab: Vocab) -> Optional[Tuple[dict, int, List[dict]]]:
    """A book scope: [in | for] [the] book {(and | , | &) book} [book | books] [[at | in] account]; a bare book word needs "book(s)"."""
    n = len(toks)
    q = k
    marker = False
    if _is_word(toks[q], "in", "for"):
        q += 1
        marker = True
    if q < n and _is_word(toks[q], "the"):
        q += 1
    books: List[dict] = []
    q2 = q
    while q2 < n:
        codes = vocab.book_codes(toks[q2])
        if not codes:
            break
        books.append({"word": toks[q2].text, "codes": list(codes), "span": _span(toks[q2])})
        q2 += 1
        if q2 + 1 < n and (_is_word(toks[q2], "and") or _is_punct(toks[q2], ",", "&")) and vocab.book_codes(toks[q2 + 1]):
            q2 += 1
            continue
        break
    flags: List[dict] = []
    if not books:
        if marker and q + 1 < n and toks[q].kind == "word" and _is_word(toks[q + 1], "book", "books"):
            word = toks[q]
            choices = [{"label": f"the {vocab.label(c)}", "clause": vocab.first_word(c)} for c, _n, _p in vocab.books]
            names = ", ".join(f"{vocab.label(c)} ({c})" for c, _n, _p in vocab.books)
            flags.append(_flag("UNPARSED_TEXT", f"'{word.text}' is not a book: the books are {names}", _span(word), choices))
            # sixth review: ``word_span`` - the orders that take this phrase's book (carried with the side, under the closing or opening
            # phrase) are unread too, and their flag names these words
            return {"books": [], "unknown": word.text, "span": _span(toks[k], toks[q + 1]), "word_span": _span(word), "bad": True}, q + 2, flags
        return None
    has_book_word = q2 < n and _is_word(toks[q2], "book", "books")
    if has_book_word:
        q2 += 1
    if not marker and not has_book_word:
        return None
    # fifth review: ``book_span`` = the book words alone (a book choice replaces them and keeps an account named after them); an
    # account typed with "at" / "in" / "," has that word in its span (an account choice replaces the whole phrase: never "at at pb ms")
    scope = {"books": books, "span": _span(toks[k], toks[q2 - 1]), "book_span": _span(toks[k], toks[q2 - 1])}
    q3 = q2
    if q3 < n and (_is_word(toks[q3], "at", "in") or _is_punct(toks[q3], ",")):
        acct, q4 = _account_words(toks, q3 + 1)
        if acct is not None:
            scope["account"] = dict(acct, span=[toks[q3].start, toks[q4 - 1].end])
            q3 = q4
    else:
        acct, q4 = _account_words(toks, q3)
        if acct is not None:
            scope["account"] = acct
            q3 = q4
    scope["span"] = _span(toks[k], toks[q3 - 1])
    for b in books:
        if len(b["codes"]) > 1:
            choices = [{"label": f"the {vocab.label(c)}", "clause": c} for c in b["codes"]]
            flags.append(_flag("TOKEN_AMBIGUOUS", f"'{b['word']}' names {len(b['codes'])} books: " + ", ".join(vocab.label(c) for c in b["codes"]),
                               b["span"], choices))
    return scope, q3, flags


def _account_at(toks: Sequence[Token], k: int) -> Optional[Tuple[dict, int, List[dict]]]:
    q = k
    if _is_word(toks[q], "in", "at"):
        q += 1
        if q < len(toks) and _is_word(toks[q], "the"):
            q += 1
    acct, q2 = _account_words(toks, q)
    if acct is None:
        return None
    acct = dict(acct, span=[toks[k].start, toks[q2 - 1].end])
    return acct, q2, []


def _group_at(toks: Sequence[Token], k: int) -> Optional[Tuple[dict, int, List[dict]]]:
    t = toks[k]
    gv = _phrase(t, "group")
    if gv or _is_word(t, *GROUP_WORDS):
        return {"word": gv or t.lower, "span": _span(t)}, k + 1, []
    return None


MOD_NAMES = {"price": "price", "tif": "time in force", "scope": "book", "account": "account", "group": "group word"}


def _parse_mods(toks: Sequence[Token], k: int, vocab: Vocab) -> Tuple[dict, List[List[int]], List[dict]]:
    mods: Dict[str, Optional[dict]] = {"price": None, "tif": None, "scope": None, "account": None, "group": None}
    unparsed: List[List[int]] = []
    flags: List[dict] = []
    n = len(toks)

    def put(kind: str, val: dict) -> None:
        if mods[kind] is None:
            mods[kind] = val
            return
        flags.append(_flag("DUPLICATE_FIELD", f"two {MOD_NAMES[kind]}s in one order ('{_cut(toks, mods[kind]['span'])}' and '{_cut(toks, val['span'])}'): keep one",
                           val["span"]))

    while k < n:
        t = toks[k]
        if _is_filler(t):
            k += 1
            continue
        got = None
        for fn in (_price_at, _tif_at):
            got = fn(toks, k)
            if got is not None:
                val, k2, fl = got
                put("price" if fn is _price_at else "tif", val)
                flags.extend(fl)
                k = k2
                break
        if got is not None:
            continue
        got = _scope_at(toks, k, vocab)
        if got is not None:
            val, k2, fl = got
            acct = val.pop("account", None)
            put("scope", val)
            if acct is not None:
                put("account", acct)
            flags.extend(fl)
            k = k2
            continue
        for fn in (_account_at, _group_at):
            got = fn(toks, k)
            if got is not None:
                val, k2, fl = got
                put("account" if fn is _account_at else "group", val)
                flags.extend(fl)
                k = k2
                break
        if got is not None:
            continue
        unparsed.append(_span(t))
        k += 1
    return mods, unparsed, flags


def _cut(toks: Sequence[Token], span) -> str:
    return " ".join(t.text for t in toks if t.start >= span[0] and t.end <= span[1])


# ---- option terms ---------------------------------------------------------------------------------------------------------------------------
def _is_term_token(t: Token) -> bool:
    if t.kind in ("date", "strikes", "strike_right", "year2"):
        return True
    if t.kind == "num":
        v = t.value
        return not v["dollar"] and v["suffix"] in (None, "ord")
    if t.kind == "word":
        return t.lower in MONTHS or t.lower in RIGHT_WORDS or t.lower in STRAT_WORDS or t.lower in CLASS_WORDS or t.lower in UNIT_AFTER
    if t.kind == "phrase":
        return t.value["kind"] == "strat"
    return _is_punct(t, "/")


def _date_part(toks: Sequence[Token], e: int, k: int) -> bool:
    """Whether the number at ``e`` belongs to a date typed before it (``k`` = where the terms start): a day right after a month, a
    4-digit year 2000-2099 right after a month or a month and day ("Dec 18 2026", "18 Dec 2026", "Dec 2026", "Dec 18, 2026"), or a
    2-digit year after "month day," ("Dec 18, 26")."""
    t = toks[e]
    prev = [toks[x] if x >= k else None for x in (e - 1, e - 2, e - 3)]
    if _day_of(t) is not None and _month_of(prev[0]):
        return True
    if _year4_of(t) is not None:
        if _month_of(prev[0]) or (_day_of(prev[0]) is not None and _month_of(prev[1])):
            return True
        if _is_punct(prev[0], ",") and _day_of(prev[1]) is not None and _month_of(prev[2]):
            return True
    v = _num(t)
    if v and v["int"] and v["digits"] == 2 and not v["dollar"] and v["suffix"] is None and _is_punct(prev[0], ",") and _day_of(prev[1]) is not None \
            and _month_of(prev[2]):
        return True
    return False


def _after_month_day(toks: Sequence[Token], e: int, k: int) -> bool:
    """Sixth review: whether the number at ``e`` is a plain 1- or 2-digit number written right after "<month> <day>" with no comma ("Dec 18
    26", "December 18 26", "Jan 15 27") - trader shorthand for a year.  Before "limit" / "lmt" it is never the "<n> limit" price: it stays
    one of the contract's words (a bare "limit" then asks for a price, as "Dec 18 2026 limit" does)."""
    v = _num(toks[e])
    if not v or not v["int"] or v["digits"] > 2 or v["dollar"] or v["suffix"] is not None:
        return False
    p1 = toks[e - 1] if e - 1 >= k else None
    p2 = toks[e - 2] if e - 2 >= k else None
    return _day_of(p1) is not None and bool(_month_of(p2))


def _collect_terms(toks: Sequence[Token], k: int, roll_to: bool = False) -> int:
    """The end of the option terms that start at ``k``.  A number right before "limit" / "lmt" is the "225 limit" price only when it is
    not one of the contract's words (fifth review): never a day or a year typed after a month ("Dec 18 2026 limit"), never the strike
    of a roll's "to" part ("to Jan 15 2027 260 limit"), and never when a price follows the word ("... 260 limit 1.20")."""
    e, n = k, len(toks)
    strike_seen = False
    while e < n:
        t = toks[e]
        if t.kind == "num" and _is_word(toks[e + 1] if e + 1 < n else None, "limit", "lmt"):
            if not _date_part(toks, e, k) and not _after_month_day(toks, e, k) and not _price_follows(toks, e + 2) and not (roll_to and not strike_seen):
                break
        if _is_term_token(t):
            if t.kind in ("strikes", "strike_right") or (t.kind == "num" and not _date_part(toks, e, k)):
                strike_seen = True
            e += 1
            continue
        if _is_punct(t, ",") and e > k and e + 1 < n and toks[e + 1].kind == "num" and _day_of(toks[e - 1]) is not None:
            e += 1
            continue
        break
    return e


def _exp_item(m: int, d: Optional[int], y: Optional[int], a: Token, b: Token) -> dict:
    return {"t": "EXP", "exp": {"m": m, "d": d, "y": y}, "span": _span(a, b)}


def _term_items(T: Sequence[Token], p: int) -> List[Tuple[dict, int]]:
    t = T[p]
    out: List[Tuple[dict, int]] = []
    if t.kind == "date":
        out.append(({"t": "EXP", "exp": {"date": t.value}, "span": _span(t)}, p + 1))
    m = _month_of(t)
    if m:
        d = _day_of(T[p + 1]) if p + 1 < len(T) else None
        if d:
            y, q = _year_after(T, p + 2)
            if y:
                out.append((_exp_item(m, d, y, t, T[q - 1]), q))
            elif _year4_of(T[p + 2] if p + 2 < len(T) else None) is None:
                out.append((_exp_item(m, d, None, t, T[p + 1]), p + 2))
        y, q = _year_after(T, p + 1, allow_comma=False)
        if y:
            out.append((_exp_item(m, None, y, t, T[q - 1]), q))
    d = _day_of(t)
    if d and p + 1 < len(T) and _month_of(T[p + 1]):
        m2 = _month_of(T[p + 1])
        y, q = _year_after(T, p + 2, allow_comma=False)
        if y:
            out.append((_exp_item(m2, d, y, t, T[q - 1]), q))
        else:
            out.append((_exp_item(m2, d, None, t, T[p + 1]), p + 2))
    k = _strike_of(t)
    if k is not None and (t.value["suffix"] is None):
        out.append(({"t": "STRIKE", "k": k, "span": _span(t)}, p + 1))
    if t.kind == "strikes":
        out.append(({"t": "STRIKES", "ks": list(t.value), "span": _span(t)}, p + 1))
    if t.kind == "strike_right":
        out.append(({"t": "SR", "k": t.value["k"], "right": t.value["right"], "span": _span(t)}, p + 1))
    r = _right_of(t)
    if r:
        out.append(({"t": "RIGHT", "right": r, "span": _span(t), "word": t.lower}, p + 1))
    s = _strat_of(t)
    if s:
        out.append(({"t": "STRAT", "name": s[0], "right": s[1], "span": _span(t)}, p + 1))
    if _is_punct(t, "/"):
        out.append(({"t": "SLASH", "span": _span(t)}, p + 1))
    if t.kind == "word" and t.lower in CLASS_WORDS:
        out.append(({"t": "CLASS", "cls": CLASS_WORDS[t.lower], "span": _span(t)}, p + 1))
    if t.kind == "word" and t.lower in UNIT_AFTER:
        out.append(({"t": "UNIT", "unit": UNIT_AFTER[t.lower], "span": _span(t), "word": t.text}, p + 1))
    return out


def _segmentations(T: Sequence[Token], limit: int = 64) -> List[List[dict]]:
    out: List[List[dict]] = []

    def rec(p: int, acc: List[dict]) -> None:
        if len(out) >= limit:
            return
        if p == len(T):
            out.append(list(acc))
            return
        for item, q in _term_items(T, p):
            acc.append(item)
            rec(q, acc)
            acc.pop()
    rec(0, [])
    return out


OPTION_PATTERNS = {("EXP", "STRIKE", "RIGHT"), ("EXP", "RIGHT", "STRIKE"), ("STRIKE", "RIGHT", "EXP"), ("EXP", "SR"), ("SR", "EXP")}


def _interpret_terms(items: List[dict], context: str) -> Tuple[str, Optional[dict], List[str]]:
    """``(shape, payload, problems)`` of one segmentation of the term tokens in a clause context (order, strategy-capable order, close,
    roll, roll_to, covered, collar).  ``shape`` none / option / strategy / class / covered / collar / roll_to / invalid."""
    types = tuple(i["t"] for i in items)
    if not items:
        return "none", None, []
    if context in ("order", "close", "roll") and types in OPTION_PATTERNS:
        exp = next(i for i in items if i["t"] == "EXP")
        if "SR" in types:
            sr = next(i for i in items if i["t"] == "SR")
            k, right, k_span, r_span = sr["k"], sr["right"], sr["span"], sr["span"]
        else:
            st = next(i for i in items if i["t"] == "STRIKE")
            rt = next(i for i in items if i["t"] == "RIGHT")
            k, right, k_span, r_span = st["k"], rt["right"], st["span"], rt["span"]
        return "option", {"expiry": dict(exp["exp"]), "exp_span": exp["span"], "strike": k, "strike_span": k_span, "right": right, "right_span": r_span,
                          "span": [items[0]["span"][0], items[-1]["span"][1]], "sr": "SR" in types}, []
    if context == "order" and types == ("STRIKE",):
        return "bare_number", {"span": items[0]["span"], "k": items[0]["k"]}, []
    if context == "order" and types == ("CLASS",) and items[0]["cls"] == "shares":
        return "none", {"unit": "shares", "unit_span": items[0]["span"], "unit_word": "shares"}, []
    if context in ("order", "close") and types == ("UNIT",):
        return "none", {"unit": items[0]["unit"], "unit_span": items[0]["span"], "unit_word": items[0]["word"]}, []
    if context == "close" and types in (("RIGHT",), ("CLASS",)):
        it = items[0]
        cls = ("calls" if it["right"] == "C" else "puts") if it["t"] == "RIGHT" else it["cls"]
        return "class", {"cls": cls, "span": it["span"]}, []
    if context == "order" and "STRAT" in types:
        return _interpret_strategy(items)
    if context == "roll_to":
        if types == ("EXP",):
            return "roll_to", {"expiry": dict(items[0]["exp"]), "exp_span": items[0]["span"], "strike": None, "span": items[0]["span"]}, []
        if types == ("EXP", "STRIKE"):
            return "roll_to", {"expiry": dict(items[0]["exp"]), "exp_span": items[0]["span"], "strike": items[1]["k"], "strike_span": items[1]["span"],
                               "span": [items[0]["span"][0], items[1]["span"][1]]}, []
        if types == ("STRIKE",):
            return "roll_to", {"expiry": None, "strike": items[0]["k"], "strike_span": items[0]["span"], "span": items[0]["span"]}, []
        if types in (("EXP", "STRIKE", "RIGHT"), ("EXP", "SR")):
            k = items[1]["k"]
            r = items[2]["right"] if types[-1] == "RIGHT" else items[1]["right"]
            return "roll_to", {"expiry": dict(items[0]["exp"]), "exp_span": items[0]["span"], "strike": k, "strike_span": items[1]["span"], "right": r,
                               "span": [items[0]["span"][0], items[-1]["span"][1]]}, []
        return "invalid", None, ["a roll goes 'to' an expiry, a strike, or both: 'to Jan 15 2027 250'"]
    if context == "covered":
        if types in (("EXP", "STRIKE"), ("EXP", "STRIKE", "RIGHT"), ("EXP", "SR")):
            right = items[-1]["right"] if types[-1] in ("RIGHT", "SR") else "C"
            if right != "C":
                return "invalid", None, ["a covered call writes calls, not puts"]
            k = items[1]["k"]
            return "covered", {"expiry": dict(items[0]["exp"]), "exp_span": items[0]["span"], "strike": k, "strike_span": items[1]["span"], "right": "C",
                               "sr": types[-1] == "SR", "span": [items[0]["span"][0], items[-1]["span"][1]]}, []
        return "invalid", None, ["a covered call names its expiry and strike: 'covered call 100 ACME Dec 18 2026 240'"]
    if context == "collar":
        if types == ("EXP", "STRIKES") and len(items[1]["ks"]) == 2:
            return "collar", {"expiry": dict(items[0]["exp"]), "exp_span": items[0]["span"], "strikes": list(items[1]["ks"]), "strikes_span": items[1]["span"],
                              "span": [items[0]["span"][0], items[1]["span"][1]]}, []
        return "invalid", None, ["a collar names its expiry and the put / call strikes: 'collar 100 ACME Dec 18 2026 220/260'"]
    return "invalid", None, [_missing_terms(types, context)]


def _missing_terms(types: Tuple[str, ...], context: str) -> str:
    have = set(types)
    if types.count("STRIKE") + types.count("SR") > 1 and "STRAT" not in have:
        return "the terms hold two strikes: a year after the day is written 2026 or '26, and an option has one strike ('Dec 18 2026 240 call')"
    if "STRIKES" in have and "STRAT" not in have:
        return "n/n reads as strikes, never as a date: a date needs its year (12/18/26) and a strike pair needs a strategy word ('240/250 call spread')"
    if "STRAT" in have and context != "order":
        return "a strategy word belongs on a buy or sell order"
    need = []
    if "EXP" not in have:
        need.append("an expiry (Dec 18 2026)")
    if not have & {"STRIKE", "SR", "STRIKES"}:
        need.append("a strike")
    if not have & {"RIGHT", "SR", "STRAT"}:
        need.append("call or put")
    if need:
        return "an option needs " + ", ".join(need) + " (for example 'ACME Dec 18 2026 240 call')"
    return "the option terms do not read as one contract: write expiry, strike, then call or put ('Dec 18 2026 240 call')"


def _interpret_strategy(items: List[dict]) -> Tuple[str, Optional[dict], List[str]]:
    types = [i["t"] for i in items]
    if types.count("STRAT") != 1:
        return "invalid", None, ["one strategy word per order"]
    strat = next(i for i in items if i["t"] == "STRAT")
    rest = [i for i in items if i["t"] != "STRAT"]
    # the strategy word follows the strikes (and the right): expiry first, or the expiry after the strategy word
    si = items.index(strat)
    before, after = items[:si], items[si + 1:]
    exps: List[dict] = []
    if after and [i["t"] for i in after] == ["EXP"] and before and before[0]["t"] != "EXP":
        exps = [after[0]]
        body = before
    elif not after:
        if len(before) >= 3 and before[0]["t"] == "EXP" and before[1]["t"] == "SLASH" and before[2]["t"] == "EXP":
            exps = [before[0], before[2]]
            body = before[3:]
        elif before and before[0]["t"] == "EXP":
            exps = [before[0]]
            body = before[1:]
        else:
            return "invalid", None, ["a strategy names its expiry first: 'Dec 18 2026 240/250 call spread'"]
    else:
        return "invalid", None, ["the strategy word comes after the strikes: 'Dec 18 2026 240/250 call spread'"]
    btypes = [i["t"] for i in body]
    strikes: Optional[List[str]] = None
    right = strat["right"]
    right_span = strat["span"] if right else None
    if btypes in (["STRIKE"], ["STRIKES"], ["STRIKE", "RIGHT"], ["STRIKES", "RIGHT"], ["SR"]):
        b0 = body[0]
        strikes = [b0["k"]] if b0["t"] in ("STRIKE", "SR") else list(b0["ks"])
        strikes_span = b0["span"]
        typed_right = body[1]["right"] if len(body) == 2 else (b0["right"] if b0["t"] == "SR" else None)
        if typed_right:
            if right and typed_right != right:
                return "invalid", None, [f"'{strat['name']}' with {'calls' if typed_right == 'C' else 'puts'}: the right is said twice and differs"]
            right = typed_right
            right_span = body[-1]["span"]
    else:
        return "invalid", None, ["a strategy takes its strikes as K1/K2[/K3/K4], then the strategy word"]
    name = strat["name"]
    if name == "vertical":
        if not right:
            return "invalid", None, ["a vertical says call or put: '240/250 call vertical' or 'call spread'"]
        name = "call spread" if right == "C" else "put spread"
    if name in ("straddle", "strangle", "iron condor", "iron butterfly") and right and strat["right"] is None and right_span is not strat["span"]:
        return "invalid", None, [f"{_a(name)} takes no call / put word"]
    right_default = False
    if name == "butterfly" and not right:
        right, right_default = "C", True                    # calls, said as the check RIGHT_DEFAULT (1.0.7.2 review)
    if name == "calendar":
        if len(exps) != 2:
            return "invalid", None, ["a calendar names two expiries: 'Dec 18 2026/Jan 15 2027 240 call calendar'"]
        if not right:
            return "invalid", None, ["a calendar says call or put: '240 call calendar'"]
    elif len(exps) != 1:
        return "invalid", None, ["one expiry per strategy (a calendar takes two)"]
    want = STRIKE_COUNT.get(name)
    if want is not None and len(strikes) != want:
        return "invalid", None, [f"{_a(name)} takes {want} strike{'s' if want > 1 else ''}"
                                 + {1: " (K)", 2: " (K1/K2)", 3: " (K1/K2/K3)", 4: " (K1/K2/K3/K4)"}[want]]
    return "strategy", {"name": name, "strikes": strikes, "strikes_span": strikes_span, "right": right, "expiries": [dict(e["exp"]) for e in exps],
                        "exp_spans": [e["span"] for e in exps], "span": [items[0]["span"][0], items[-1]["span"][1]], "word_span": strat["span"],
                        "right_default": right_default}, []


# ---- the clause ---------------------------------------------------------------------------------------------------------------------------
def _qty_value(t: Token) -> dict:
    if t.kind == "word":
        return {"kind": t.lower, "value": None, "text": t.text, "span": _span(t)}
    if t.kind == "badnum":
        return {"kind": "num", "value": None, "text": t.text, "span": _span(t), "mult": None, "bad": True}
    v = t.value
    d = _dec(v["num"])
    mult = {"k": 1000, "m": 1000000}.get(v["suffix"], 1)
    total = d * mult if d is not None else None
    whole = total is not None and total == total.to_integral_value()
    out = {"kind": "num", "value": int(total) if whole and not v["dollar"] else None, "text": t.text, "span": _span(t),
           "mult": v["suffix"] if v["suffix"] in ("k", "m") else None}
    if v["dollar"]:
        out["dollar"] = True
        return out
    if v["suffix"] == "x":
        out["unit"] = "x"
    if v["suffix"] is None and not v["commas"] and _THOUSANDS_POINT.fullmatch(v["num"]):
        out["thousands_point"] = True                    # "1.000": 1 or 1,000?  never read silently
    if not whole:
        out["not_whole"] = True
    return out


def _instrument_alts(toks: Sequence[Token], j: int, vocab: Vocab) -> List[Tuple[dict, int]]:
    n = len(toks)
    t = toks[j] if j < n else None
    if t is None:
        return []
    alts: List[Tuple[dict, int]] = []
    if t.kind == "occ":
        return [({"kind": "occ", "id": t.value, "symbol": t.value, "span": _span(t)}, j + 1)]
    if t.kind == "fut":
        return [({"kind": "future", "id": t.value["id"], "root": t.value["root"], "year": t.value["year"], "month": t.value["month"], "span": _span(t),
                  "symbol": t.value["id"]}, j + 1)]
    if t.kind == "pair":
        return [({"kind": "pair", "symbol": t.value, "span": _span(t)}, j + 1)]
    if _is_word(t, "front", "active") and j + 1 < n and toks[j + 1].kind == "word" and toks[j + 1].text.upper() in FUTURES_SPECS:
        return [({"kind": "front", "root": toks[j + 1].text.upper(), "word": t.lower, "span": _span(t, toks[j + 1]), "symbol": toks[j + 1].text.upper()}, j + 2)]
    sym = _ticker_of(t, vocab)
    if sym is None:
        return []
    if t.kind == "word" and sym in FUTURES_SPECS and _month_of(toks[j + 1] if j + 1 < n else None):
        m = _month_of(toks[j + 1])
        y, q = _year_after(toks, j + 2)
        alts.append(({"kind": "future_month", "root": sym, "month": m, "year": y, "span": _span(t, toks[q - 1]), "symbol": sym}, q))
    alts.append(({"kind": "ticker", "symbol": sym, "forced": t.kind == "forced", "span": _span(t)}, j + 1))
    return alts


def _new_clause(span, text: str) -> dict:
    return {"span": list(span), "text": text[span[0]:span[1]], "kind": "empty", "verb": None, "side": None, "side_carried": False, "qty": None, "instrument": None,
            "option": None, "strategy": None, "close_class": None, "roll_to": None,
            "mods": {"price": None, "tif": None, "scope": None, "account": None, "group": None}, "flags": [], "unparsed": [], "underlying_key": None}


def _canonical(c: dict, text: str) -> str:
    """One reading of a clause in a canonical text (for TOKEN_AMBIGUOUS choices): the verb, the quantity, ``$TICKER`` / the id, the option
    terms as an ISO / month expiry, strike and right, then the modifiers as typed."""
    parts: List[str] = []
    v = c.get("verb")
    if v and v.get("word"):
        parts.append(v["word"])
    q = c.get("qty")
    if q:
        parts.append(q["text"])
        if q.get("unit"):
            parts.append(q["unit"])
    inst = c.get("instrument")
    if inst:
        if inst["kind"] == "ticker":
            parts.append("$" + inst["symbol"])
        elif inst["kind"] == "future_month":
            parts.append(inst["root"] + " " + _month_words(inst["month"]) + (f" {inst['year']}" if inst.get("year") else ""))
        elif inst["kind"] == "front":
            parts.append(f"{inst['word']} {inst['root']}")
        else:
            parts.append(inst.get("id") or inst.get("symbol") or "")
    o = c.get("option")
    if o:
        parts.append(_exp_text(o["expiry"]))
        parts.append(o["strike"] if c.get("kind") == "covered" else f"{o['strike']} {'call' if o.get('right') == 'C' else 'put'}")
    s = c.get("strategy")
    if s:
        exps = s.get("expiries") or ([s["expiry"]] if s.get("expiry") else [])
        parts.append("/".join(_exp_text(e) for e in exps))
        parts.append("/".join(s.get("strikes") or []))
        name = s.get("name")
        if name:
            parts.append(name if name not in ("butterfly", "calendar", "condor") or not s.get("right")
                         else f"{'call' if s.get('right') == 'C' else 'put'} {name}")
    if c.get("close_class"):
        parts.append(c["close_class"])
    rt = c.get("roll_to")
    if rt:
        parts.append("to")
        if rt.get("expiry"):
            parts.append(_exp_text(rt["expiry"]))
        if rt.get("strike"):
            parts.append(rt["strike"])
    parts.extend(_mod_texts(c, text, ("price", "tif", "scope", "account", "group")))
    return " ".join(p for p in parts if p)


def _mod_texts(c: dict, text: str, keys: Sequence[str]) -> List[str]:
    """The typed words of a clause's modifiers ``keys``, in that order, each written once.  Sixth review: an account named in the book
    phrase itself ("in the Institutional book at pb ms": the phrase's span covers it, and the clause also holds it as its account) is
    written with that phrase, never a second time on its own (which reads as two accounts: DUPLICATE_FIELD)."""
    ssp = (c["mods"].get("scope") or {}).get("span")
    out = []
    for key in keys:
        mv = c["mods"].get(key)
        if not mv or not mv.get("span"):
            continue
        if key == "account" and "scope" in keys and ssp and ssp[0] <= mv["span"][0] and mv["span"][1] <= ssp[1]:
            continue
        out.append(text[mv["span"][0]:mv["span"][1]])
    return out


def _exp_text(e: dict) -> str:
    if e.get("date"):
        return e["date"]
    if e.get("d") is None:
        return f"{_month_words(e['m'])} {e['y']}" if e.get("y") else _month_words(e["m"])
    if e.get("y"):
        try:
            return date(int(e["y"]), int(e["m"]), int(e["d"])).isoformat()
        except ValueError:
            return f"{_month_words(e['m'])} {e['d']} {e['y']}"
    return f"{_month_words(e['m'])} {e['d']}"


def parse_clause(tokens: Sequence[Token], text: str = "", vocab: Optional[Vocab] = None) -> dict:
    """One clause -> its AST (a JSON-able dict): kind (order, strategy, close, roll, covered, collar, scope, empty), verb / side, qty,
    instrument, option or strategy terms, close class, roll target, modifiers, the clause's own flags and its unparsed spans.  Every
    reading the grammar allows is tried; one complete reading is taken, two are TOKEN_AMBIGUOUS, none gives the best partial reading
    with the blockers that say what is missing."""
    vocab = vocab or Vocab()
    toks = list(tokens)
    if not toks:
        return _new_clause([0, 0], text)
    if not text:
        text = " " * toks[-1].end
    span = [toks[0].start, toks[-1].end]
    n = len(toks)
    i = 0
    side = _side_of(toks[0])
    verb_kind = _verb_of(toks[0])
    verb = None
    if side:
        verb = {"kind": "side", "side": side, "word": toks[0].text.lower(), "span": _span(toks[0])}
        i = 1
    elif verb_kind:
        verb = {"kind": verb_kind, "word": toks[0].text.lower(), "span": _span(toks[0])}
        i = 1
        if verb_kind == "close" and _is_word(toks[1] if n > 1 else None, "out"):
            verb["span"] = _span(toks[0], toks[1])
            verb["word"] = "close out"
            i = 2
    # quantity alternatives (a unit word that can also be a ticker in capitals is tried both ways)
    qalts: List[Tuple[Optional[dict], int]] = []
    if i < n and _qty_token(toks[i], dollars=True):
        q = _qty_value(toks[i])
        j = i + 1
        if j < n and toks[j].kind == "word" and toks[j].lower in UNIT_WORDS:
            qu = dict(q, unit=UNIT_WORDS[toks[j].lower], unit_word=toks[j].text, unit_span=_span(toks[j]))
            qalts.append((qu, j + 1))
            if _ticker_of(toks[j], vocab) is not None:
                qalts.append((q, j))
        else:
            qalts.append((q, j))
    else:
        qalts.append((None, i))
    context = {"side": "order", "close": "close", "roll": "roll", "covered": "covered", "collar": "collar", None: "order"}[verb["kind"] if verb else None]
    readings: List[dict] = []
    for alt_q, (qty, j) in enumerate(qalts):
        ialts = _instrument_alts(toks, j, vocab)
        if not ialts:
            ialts = [(None, j)]
        for alt_i, (inst, k) in enumerate(ialts):
            r = _new_clause(span, text)
            r["verb"], r["qty"], r["instrument"] = verb, qty, inst
            r["_order"] = (alt_q, alt_i)
            r["flags"] = []
            if inst is None:
                mods, unp, fl = _parse_mods(toks, k, vocab)
                r["mods"], r["unparsed"], r["flags"] = mods, unp, fl
                r["_shape"] = "none"
                r["_complete"] = False
                readings.append(r)
                continue
            if inst["kind"] in ("ticker", "occ"):
                e = _collect_terms(toks, k)
                term_toks = toks[k:e]
                roll_toks: List[Token] = []
                k2 = e
                if verb and verb["kind"] == "roll" and k2 < n and _is_word(toks[k2], "to"):
                    e2 = _collect_terms(toks, k2 + 1, roll_to=True)
                    roll_toks = toks[k2 + 1:e2]
                    r["_to_span"] = _span(toks[k2])
                    k2 = e2
                mods, unp, fl = _parse_mods(toks, k2, vocab)
                segs = _segmentations(term_toks) if term_toks else [[]]
                if not segs:
                    segs = [None]
                tctx = context if inst["kind"] == "ticker" else ("roll" if context == "roll" else "occ")
                for alt_s, seg in enumerate(segs):
                    r2 = dict(r, mods=mods, unparsed=list(unp), flags=list(fl))
                    r2["_order"] = (alt_q, alt_i, alt_s)
                    if seg is None:
                        r2["unparsed"] = r2["unparsed"] + [_span(term_toks[0], term_toks[-1])]
                        r2["flags"] = r2["flags"] + [_flag("MISSING_INSTRUMENT", _missing_terms((), tctx), _span(term_toks[0], term_toks[-1]))]
                        r2["_shape"] = "invalid"
                        readings.append(r2)
                        continue
                    if tctx == "occ":
                        shape, payload, problems = ("none", None, []) if not seg else ("invalid", None, ["an OCC id is the whole contract: nothing follows it"])
                    else:
                        shape, payload, problems = _interpret_terms(seg, tctx)
                    _apply_shape(r2, shape, payload, problems, term_toks)
                    if verb and verb["kind"] == "roll":
                        if roll_toks or r2.get("_to_span"):
                            rsegs = _segmentations(roll_toks) if roll_toks else [[]]
                            best = None
                            for rs in rsegs:
                                sh2, pl2, pr2 = _interpret_terms(rs, "roll_to")
                                if sh2 == "roll_to":
                                    best = pl2
                                    break
                            if best is None:
                                r2["flags"] = r2["flags"] + [_flag("MISSING_INSTRUMENT", "a roll goes 'to' an expiry, a strike, or both: 'to Jan 15 2027 250'",
                                                                   _span(toks[k2 - 1]) if k2 > 0 else span)]
                                r2["_shape"] = "invalid"
                            else:
                                r2["roll_to"] = best
                        else:
                            r2["flags"] = r2["flags"] + [_flag("MISSING_INSTRUMENT", "a roll says where to: 'roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250'",
                                                               span)]
                            r2["_shape"] = "invalid"
                    readings.append(r2)
                continue
            if k < n and toks[k].kind == "word" and toks[k].lower in UNIT_AFTER and qty is not None and not qty.get("unit"):
                r["qty"] = dict(qty, unit=UNIT_AFTER[toks[k].lower], unit_word=toks[k].text, unit_span=_span(toks[k]))
                k += 1
            mods, unp, fl = _parse_mods(toks, k, vocab)
            r2 = dict(r, mods=mods, unparsed=list(unp), flags=list(fl))
            r2["_shape"] = "none"
            readings.append(r2)
    for r in readings:
        _finish_reading(r, text, vocab, context)
    best = _choose(readings, text)
    best = {k: v for k, v in best.items() if not k.startswith("_")}
    inst = best.get("instrument") or {}
    if best["kind"] in ("order", "close") and inst.get("kind") == "ticker" and not inst.get("forced") and inst["symbol"] in FUTURES_SPECS             and not best.get("option") and not best.get("strategy"):
        sym, sp = inst["symbol"], inst["span"]               # a futures root alone: the shares of a ticker, or the future (no month named)?
        best["flags"] = list(best["flags"]) + [_flag("TOKEN_AMBIGUOUS", f"'{text[sp[0]:sp[1]]}' is a ticker and a futures root: '${sym}' for the shares,"
                                                     f" 'front {sym}' or '{sym} Dec' for the future", span,
                                                     [{"label": f"the shares ${sym}", "clause": _edit(text, span, [(sp[0], sp[1], "$" + sym)])},
                                                      {"label": f"the active {sym} future", "clause": _edit(text, span, [(sp[0], sp[1], "front " + sym)])}])]
    whole = []
    for f in best["flags"]:
        if f.get("choices") and f.get("span") and list(f["span"]) != list(span):
            f = dict(f, span=list(span), choices=[{"label": ch["label"], "clause": _edit(text, span, [(f["span"][0], f["span"][1], ch["clause"])])}
                                                  for ch in f["choices"]])
        whole.append(f)
    best["flags"] = whole
    return best


def _apply_shape(r: dict, shape: str, payload: Optional[dict], problems: List[str], term_toks: Sequence[Token]) -> None:
    r["_shape"] = shape
    if shape == "none" and payload and payload.get("unit"):
        q = r.get("qty")
        if q is not None and not q.get("unit"):
            r["qty"] = dict(q, unit=payload["unit"], unit_word=payload["unit_word"], unit_span=payload["unit_span"])
        elif q is not None:
            r["flags"] = r["flags"] + [_flag("DUPLICATE_FIELD", "the unit is said twice", payload["unit_span"])]
    if shape == "option":
        r["option"] = payload
    elif shape == "strategy":
        r["strategy"] = payload
    elif shape == "class":
        r["close_class"] = payload["cls"]
        r["_class_span"] = payload["span"]
    elif shape in ("covered", "collar"):
        r["option" if shape == "covered" else "strategy"] = payload
    elif shape == "bare_number":
        r["unparsed"] = list(r.get("unparsed") or []) + [payload["span"]]
        r["_shape"] = "none"
    elif shape == "invalid":
        sp = _span(term_toks[0], term_toks[-1]) if term_toks else r["span"]
        for p in problems:
            r["flags"] = r["flags"] + [_flag("MISSING_INSTRUMENT", p, sp)]


def _finish_reading(r: dict, text: str, vocab: Vocab, context: str) -> None:
    """The clause kind, the missing-part blockers and the completeness of one reading."""
    verb, qty, inst = r["verb"], r["qty"], r["instrument"]
    flags = list(r["flags"])
    shape = r.get("_shape", "none")
    kind = "order"
    if verb and verb["kind"] in ("close", "roll", "covered", "collar"):
        kind = verb["kind"]
    elif shape == "strategy":
        kind = "strategy"
    if verb is None and qty is None:
        only_scope = inst is None and (r["mods"].get("scope") or r["mods"].get("account")) and not r["unparsed"]
        kind = "scope" if only_scope else "empty"
    r["kind"] = kind
    if kind in ("order", "strategy") and verb is None and qty is not None:
        r["side_carried"] = True
    if kind == "strategy" and verb and verb["kind"] == "side" and verb["side"] not in ("buy", "sell", "BTO", "STO"):
        flags.append(_flag("MISSING_SIDE", f"a strategy is bought or sold: '{verb['word']}' does not apply to a {r['strategy']['name']}", verb["span"]))
    if kind in ("order", "strategy", "covered", "collar") and qty is None:
        flags.append(_flag("MISSING_QTY", "how many? (for example 'buy 100 ACME' or 'sell 2 ACME Dec 18 2026 240 calls')", verb["span"] if verb else r["span"]))
    if qty is not None and qty.get("dollar"):
        flags.append(_flag("MISSING_QTY", f"{qty['text']} is an amount, not a quantity: name the shares or contracts ('buy 100 ACME')", qty["span"]))
    if qty is not None and qty.get("not_whole"):
        flags.append(_flag("QTY_NOT_WHOLE", f"{qty['text']} is not a whole number of shares or contracts", qty["span"]))
    if qty is not None and qty.get("bad"):
        # "1,5k" / "2,5": a comma that is not a thousands group - one malformed number, never two orders (1.0.7.2 review)
        qs = qty["span"]
        raw = qty["text"]
        dec, plain = raw.replace(",", "."), raw.replace(",", "")
        flags.append(_flag("QTY_NOT_WHOLE", f"'{raw}' is not a quantity FinSim reads: a comma groups thousands (1,500) and a point is the decimal (1.5k)",
                           r["span"], [{"label": f"{dec} (the comma as a decimal point)", "clause": _edit(text, r["span"], [(qs[0], qs[1], dec)])},
                                       {"label": f"{plain} (without the comma)", "clause": _edit(text, r["span"], [(qs[0], qs[1], plain)])}]))
    if qty is not None and qty.get("thousands_point"):
        qs = qty["span"]
        raw = qty["text"]
        as_decimal = _kstr(Decimal(raw))
        as_thousands = raw.replace(".", "")
        flags.append(_flag("TOKEN_AMBIGUOUS", f"'{raw}': {as_decimal} (a decimal point) or {int(as_thousands):,} (a thousands point)? write it without the point",
                           r["span"], [{"label": f"{as_decimal}", "clause": _edit(text, r["span"], [(qs[0], qs[1], as_decimal)])},
                                       {"label": f"{int(as_thousands):,}", "clause": _edit(text, r["span"], [(qs[0], qs[1], as_thousands)])}]))
    if qty is not None and qty.get("kind") == "num" and qty.get("value") == 0:
        flags.append(_flag("MISSING_QTY", "the quantity must be above 0", qty["span"]))
    if qty is not None and qty.get("kind") in ("all", "half") and kind in ("strategy", "covered", "collar"):
        flags.append(_flag("MISSING_QTY", f"'{qty['text']}' is a held quantity: a {kind if kind != 'strategy' else 'strategy'} needs a number", qty["span"]))
    if kind in ("scope", "empty"):
        flags = [f for f in flags if f["code"] in ("UNPARSED_TEXT", "TOKEN_AMBIGUOUS", "DUPLICATE_FIELD")]
    if inst is None and kind not in ("scope", "empty"):
        flags.append(_missing_instrument_flag(r, text, vocab))
    if kind == "roll" and inst is not None and r.get("option") is None and inst["kind"] != "occ" and shape != "invalid":
        flags.append(_flag("MISSING_INSTRUMENT", "a roll names the option held: 'roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250'", inst["span"]))
    if kind in ("covered", "collar") and inst is not None and inst["kind"] != "ticker":
        flags.append(_flag("MISSING_INSTRUMENT", f"a {('covered call' if kind == 'covered' else 'collar')} names the ticker: '{verb['word']} 100 ACME ...'", inst["span"]))
    if kind in ("covered", "collar") and inst is not None and inst["kind"] == "ticker" and shape == "none":
        flags.append(_flag("MISSING_INSTRUMENT", "a covered call names its expiry and strike: 'covered call 100 ACME Dec 18 2026 240'" if kind == "covered"
                           else "a collar names its expiry and the put / call strikes: 'collar 100 ACME Dec 18 2026 220/260'", inst["span"]))
    if r.get("strategy") and kind == "strategy":
        s = r["strategy"]
        ks = [Decimal(k) for k in s["strikes"]]
        if s["name"] == "condor" and not s.get("right"):
            ws = s["word_span"]
            flags.append(_flag("TOKEN_AMBIGUOUS", "'condor' alone: an iron condor (puts below, calls above; bought = the body bought, the wings sold) or a"
                               " condor of calls or of puts (bought = the wings bought, the body sold)? say which", r["span"],
                               [{"label": lab, "clause": _edit(text, r["span"], [(ws[0], ws[1], rep)])}
                                for lab, rep in (("iron condor", "iron condor"), ("call condor", "call condor"), ("put condor", "put condor"))]))
        if s.get("right_default"):
            ws = s["word_span"]
            flags.append(_flag("RIGHT_DEFAULT", "no call / put word: the butterfly is read as calls", r["span"],
                               [{"label": lab, "clause": _edit(text, r["span"], [(ws[0], ws[1], lab)])} for lab in ("call butterfly", "put butterfly")]))
        if s["name"] in ORDERED_STRIKES and any(ks[x] >= ks[x + 1] for x in range(len(ks) - 1)):
            order = "/".join(_kstr(k) for k in sorted(set(ks)))
            choice = _edit(text, r["span"], [(s["strikes_span"][0], s["strikes_span"][1], order)]) if len(set(ks)) == len(ks) else None
            flags.append(_flag("STRIKE_ORDER", f"{_a(s['name'])} takes its strikes from low to high ({'/'.join(s['strikes'])} is not)", r["span"] if choice else s["strikes_span"],
                               [{"label": f"strikes {order}", "clause": choice}] if choice else None))
        if s["name"] == "calendar" and len(s["expiries"]) == 2 and _exp_text(s["expiries"][0]) == _exp_text(s["expiries"][1]):
            flags.append(_flag("MISSING_INSTRUMENT", "a calendar takes two different expiries", s["span"]))
    price = r["mods"].get("price")
    if price is not None and price.get("from_at"):
        flags.append(_flag("TYPE_FROM_AT", f"'{text[price['span'][0]:price['span'][1]]}' read as a limit {price['limit']} (never a stop)", price["span"]))
    if r["unparsed"]:
        flags.extend(_unparsed_flags(r, text, vocab))
    r["flags"] = flags
    blockers = [f for f in flags if f["severity"] == "blocker"]
    r["_complete"] = kind not in ("empty",) and shape != "invalid" and not r["unparsed"] and not blockers
    r["_score"] = (len(r["unparsed"]), len(blockers), r.get("_order", ()))
    inst = r["instrument"]
    if inst is not None:
        if inst["kind"] == "occ":
            p = parse_occ(inst["id"])
            r["underlying_key"] = _underlying_key(p[0]) if p else None
        elif inst["kind"] in ("ticker",):
            r["underlying_key"] = _underlying_key(inst["symbol"])
        else:
            r["underlying_key"] = inst.get("root") or inst.get("symbol")


def _missing_instrument_flag(r: dict, text: str, vocab: Vocab) -> dict:
    """MISSING_INSTRUMENT, with the "$" reading as a choice when the instrument slot holds a lower-case short word or a keyword."""
    verb, qty = r["verb"], r["qty"]
    after = (qty.get("unit_span") or qty["span"])[1] if qty else (verb["span"][1] if verb else r["span"][0])
    m = re.match(r"\s*([A-Za-z][A-Za-z0-9.\-]*)", text[after:r["span"][1]])
    if m:
        word = m.group(1)
        s = after + m.start(1)
        choice = _edit(text, r["span"], [(s, s + len(word), "$" + word.upper())])
        why = ("a one- or two-letter ticker needs capitals or $" if len(word) <= 2 else f"'{word}' is a grammar word here: a ticker needs capitals or $")
        return _flag("MISSING_INSTRUMENT", f"no instrument: {why} (type ${word.upper()})", r["span"], [{"label": f"the ticker {word.upper()}", "clause": choice}])
    return _flag("MISSING_INSTRUMENT", "no instrument: name a ticker, an option (ACME Dec 18 2026 240 call) or a futures contract", r["span"])


def _unparsed_flags(r: dict, text: str, vocab: Vocab) -> List[dict]:
    out = []
    for sp in r["unparsed"]:
        words = text[sp[0]:sp[1]]
        hint = ""
        choices = None
        num = re.fullmatch(r"\$?[0-9][0-9,]*\.?[0-9]*", words)
        inst = r["instrument"]
        # fifth review: a number among an option's words (a month, call / put) is a strike or a date the terms did not read, never a price
        optionish = re.search(r"(?i)\b(?:" + "|".join(sorted(set(MONTHS) | set(RIGHT_WORDS) | set(STRAT_WORDS), key=len, reverse=True)) + r")\b",
                              text[r["span"][0]:r["span"][1]]) is not None
        if num and inst is not None and inst["kind"] == "ticker" and r.get("option") is None and r.get("strategy") is None and r["mods"].get("price") is None \
                and r.get("qty") is not None and not optionish:
            hint = f": a price needs a word ('limit {words.lstrip('$')}' or 'at {words.lstrip('$')}')"
            choices = [{"label": f"limit {words.lstrip('$')}", "clause": _edit(text, r["span"], [(sp[0], sp[0], "limit ")])}]
        out.append(_flag("UNPARSED_TEXT", f"'{words}' is not understood" + hint, r["span"] if choices else sp, choices))
    return out


def _choose(readings: List[dict], text: str) -> dict:
    complete = [r for r in readings if r.get("_complete")]
    if complete:
        canon: Dict[str, dict] = {}
        for r in sorted(complete, key=lambda x: x["_score"]):
            canon.setdefault(_canonical(r, text), r)
        if len(canon) == 1:
            return next(iter(canon.values()))
        first = sorted(canon.values(), key=lambda x: x["_score"])[0]
        out = dict(first)
        choices = [{"label": f"read as: {c}", "clause": c} for c in sorted(canon)]
        out["flags"] = list(first["flags"]) + [_flag("TOKEN_AMBIGUOUS", f"two readings: {' | '.join(sorted(canon))}", first["span"], choices)]
        return out
    return sorted(readings, key=lambda x: x["_score"])[0]


# ---- strategy expansion (G 3.4.6) -------------------------------------------------------------------------------------------------------
def expand_strategy(c: dict) -> List[dict]:
    """The abstract legs of one clause (sides for buy; sell reverses every leg): ``{role, side, right?, strike?, expiry?, ratio}``.  An order
    is one leg with the typed side ("buy" / "sell" are resolved later from the position); a strategy, covered call, collar or roll expands
    into the legs of ONE combination."""
    kind = c.get("kind")
    if kind == "order":
        if c.get("option"):
            o = c["option"]
            return [{"role": "single", "option": True, "right": o["right"], "strike": o["strike"], "expiry": o["expiry"], "ratio": 1}]
        return [{"role": "single", "option": False, "ratio": 1}]
    if kind == "close":
        return [{"role": "close", "ratio": 1}]
    if kind == "covered":
        o = c.get("option") or {}
        return [{"role": "shares", "side": "BUY", "ratio": 1, "primary": True},
                {"role": "option", "side": "STO", "right": "C", "strike": o.get("strike"), "expiry": o.get("expiry"), "ratio": 1, "per_100": True}]
    if kind == "collar":
        s = c.get("strategy") or {}
        ks = s.get("strikes") or [None, None]
        return [{"role": "shares_if_not_held", "side": "BUY", "ratio": 1, "primary": True},
                {"role": "option", "side": "BTO", "right": "P", "strike": ks[0], "expiry": s.get("expiry"), "ratio": 1, "per_100": True},
                {"role": "option", "side": "STO", "right": "C", "strike": ks[1], "expiry": s.get("expiry"), "ratio": 1, "per_100": True}]
    if kind == "roll":
        o = c.get("option") or {}
        rt = c.get("roll_to") or {}
        return [{"role": "roll_old", "right": o.get("right"), "strike": o.get("strike"), "expiry": o.get("expiry"), "ratio": 1},
                {"role": "roll_new", "right": rt.get("right") or o.get("right"), "strike": rt.get("strike") or o.get("strike"),
                 "expiry": rt.get("expiry") or o.get("expiry"), "ratio": 1}]
    if kind != "strategy" or not c.get("strategy"):
        return []
    s = c["strategy"]
    side = (c.get("verb") or {}).get("side")
    # a strategy is bought or sold: any other side - none, or short / cover / btc / stc, typed or carried from the clause before - is a
    # MISSING_SIDE blocker, and its legs carry no side at all (second review: never expanded as a bought strategy by default)
    known = side in ("buy", "sell", "BTO", "STO")
    sell = side in ("sell", "STO")
    ks, r, exps = s["strikes"], s["right"], s["expiries"]
    e = exps[0] if exps else None

    def L(sd, right, k, ratio=1, expiry=None):
        if sell:
            sd = "STO" if sd == "BTO" else "BTO"
        return {"role": "option", "side": sd if known else None, "right": right, "strike": k, "expiry": expiry or e, "ratio": ratio}
    name = s["name"]
    if name in ("call spread", "put spread"):
        # 1.0.7.2 review: the SIDE decides debit or credit, never the typed strike order - a bought spread is a debit (the call of the
        # lower strike / the put of the higher strike bought), a sold one a credit; the legs are listed low strike first
        rr = "C" if name == "call spread" else "P"
        lo, hi = sorted(ks[:2], key=Decimal)
        return [L("BTO", rr, lo), L("STO", rr, hi)] if rr == "C" else [L("STO", rr, lo), L("BTO", rr, hi)]
    if name == "straddle":
        return [L("BTO", "C", ks[0]), L("BTO", "P", ks[0])]
    if name == "strangle":
        return [L("BTO", "P", ks[0]), L("BTO", "C", ks[1])]
    if name == "iron condor":
        return [L("STO", "P", ks[0]), L("BTO", "P", ks[1]), L("BTO", "C", ks[2]), L("STO", "C", ks[3])]
    if name == "iron butterfly":
        return [L("STO", "P", ks[0]), L("BTO", "P", ks[1]), L("BTO", "C", ks[1]), L("STO", "C", ks[2])]
    if name == "butterfly":
        return [L("BTO", r or "C", ks[0]), L("STO", r or "C", ks[1], 2), L("BTO", r or "C", ks[2])]
    if name == "condor":
        # a condor of one right (calls or puts): bought = the wings bought, the body sold (a debit); a bare "condor" (no right) is a
        # TOKEN_AMBIGUOUS blocker - its legs stay without a right, so nothing is resolved or sent
        return [L("BTO", r, ks[0]), L("STO", r, ks[1]), L("STO", r, ks[2]), L("BTO", r, ks[3])]
    if name == "calendar":
        a, b = exps[0], exps[1]
        # the near expiry is sold on a buy: compared as dates when both years are typed; otherwise the typed order is only a placeholder -
        # the resolver, which knows today and the cached chain, reads both dates and puts the legs right, or refuses the calendar, before
        # anything is resolved (Resolver._calendar_order); never by the typed spelling
        ka, kb = _exp_key(a), _exp_key(b)
        near, far = (b, a) if ka is not None and kb is not None and kb < ka else (a, b)
        return [L("STO", r, ks[0], expiry=near), L("BTO", r, ks[0], expiry=far)]
    return []


def _exp_key(e: dict) -> Optional[Tuple[int, int, int]]:
    """An expiry's (year, month, day) for ordering when its year is typed (a month alone counts day 0), else None: the pure layer has no
    clock, so a day typed without its year is never ordered against another expiry here (second review)."""
    if e.get("date"):
        y, m, d = (int(x) for x in str(e["date"]).split("-"))
        return y, m, d
    if not e.get("y"):
        return None
    return int(e["y"]), int(e.get("m") or 0), int(e.get("d") or 0)


def _clause_leg_count(c: dict) -> int:
    k = c.get("kind")
    if k == "collar":
        return 2
    return len(expand_strategy(c)) if k in ("order", "close", "covered", "roll", "strategy") else 0


# ---- the whole instruction --------------------------------------------------------------------------------------------------------------
def _take_prefix(toks: List[Token], vocab: Vocab) -> Tuple[Optional[dict], List[Token], List[dict]]:
    """A prefix scope ``scope ":"`` at the very start, or None."""
    for idx, t in enumerate(toks):
        if _is_punct(t, ":"):
            if idx == 0:
                return None, toks, []
            got = _scope_at(toks[:idx], 0, vocab) or _account_at(toks[:idx], 0)
            if got is None:
                return None, toks, []
            val, k2, fl = got
            if k2 != idx:
                return None, toks, []
            scope = val if "books" in val else {"books": [], "span": val["span"], "account": val}
            scope["span"] = [toks[0].start, t.end]
            return scope, toks[idx + 1:], fl
        if _side_of(t) or _verb_of(t) or _is_hard(t):
            return None, toks, []
    return None, toks, []


def parse(text: str, books: Optional[Sequence[Sequence[str]]] = None) -> dict:
    """The pure parse of one instruction (no services, no clock): ``{grammar, text, clauses, groups, prefix, suffix, unparsed, flags,
    legs}``.  ``books`` = ``[(code, name, profile), ...]`` (the books table; default ``config.BOOKS``).  Raises ``SmartTicketError``
    TEXT_TOO_LONG / TOO_MANY_LEGS (400)."""
    norm, bad = normalise(text)
    if len(norm) > MAX_CHARS:
        raise SmartTicketError("TEXT_TOO_LONG", f"the instruction is {len(norm):,} characters: at most {MAX_CHARS:,}", {"length": len(norm), "max": MAX_CHARS})
    vocab = Vocab(books)
    toks = tokenize(norm)
    prefix, body, pflags = _take_prefix(toks, vocab)
    if prefix is not None:
        prefix["flags"] = pflags
    segs, groups, stray = split_clauses(body, vocab)
    flags: List[dict] = []
    unparsed: List[List[int]] = [list(b) for b in bad]
    for sp in stray:
        flags.append(_flag("UNPARSED_TEXT", "a parenthesis without its pair", sp))
        unparsed.append(list(sp))
    clauses: List[dict] = []
    prev: Optional[dict] = None
    for seg in segs:
        c = parse_clause(seg["tokens"], norm, vocab)
        c["group"] = seg["group"]
        if c["kind"] in ("order", "strategy") and c["verb"] is None and c["qty"] is not None:
            if prev is not None and prev.get("verb") and prev["verb"]["kind"] == "side":
                c["verb"] = dict(prev["verb"], span=None, carried=True)
                c["side_carried"] = True
                # 1.0.7.2 review: the book (and account) the clause before named travel with its side ("buy 100 ACME in the Fund book and 50
                # WIDG" puts both in the Fund book); the leg says so (BOOK_FROM_SCOPE check).  Second review: its account travels too when it
                # named no book ("buy 100 ACME at pb ms and 50 WIDG": both at pb ms), unless this clause names its own book or account
                # sixth review: a book word that names no book travels the same way - the order that carries its side takes that unread
                # book (never the selected one)
                own_sc = c["mods"].get("scope") or {}
                own_book = bool(own_sc.get("books") or own_sc.get("unknown"))
                psc = prev["mods"].get("scope") or {}
                pscope = psc if (psc.get("books") or psc.get("unknown")) else prev.get("carried_scope")
                if not own_book and pscope:
                    c["carried_scope"] = pscope
                pacct = prev["mods"].get("account") or prev.get("carried_account")
                said = ""
                if not own_book and c["mods"].get("account") is None and pacct is not None:
                    c["carried_account"] = pacct
                    if not pscope:
                        said = f", with its account '{norm[pacct['span'][0]:pacct['span'][1]]}'" if pacct.get("span") else ", with its account"
                c["flags"].append(_flag("SIDE_CARRIED", f"no side: '{prev['verb']['word']}' carried from the clause before{said}", c["span"]))
                if c["kind"] == "strategy" and c["verb"].get("side") not in ("buy", "sell", "BTO", "STO"):
                    # second review: the strategy side rule applies to a carried side as to a typed one - a strategy is bought or sold
                    sname = (c.get("strategy") or {}).get("name") or "strategy"
                    c["flags"].append(_flag("MISSING_SIDE", f"a strategy is bought or sold: '{prev['verb']['word']}', carried from the clause before, does not apply"
                                            f" to {_a(sname)}", c["span"], [{"label": f"{w} ...", "clause": f"{w} " + c["text"]} for w in ("buy", "sell")]))
            else:
                c["side_carried"] = False
                c["flags"].append(_flag("MISSING_SIDE", "no side: start with buy, sell, short, cover, bto, sto, btc or stc", c["span"],
                                        [{"label": f"{w} ...", "clause": f"{w} " + c["text"]} for w in ("buy", "sell")]))
        if c["kind"] == "strategy" and c["strategy"]:
            c["flags"].append(_flag("STRATEGY_EXPANDED", f"{c['strategy']['name']}: " + _expansion_words(c), c["span"]))
            sd = (c.get("verb") or {}).get("side")
            sold = sd in ("sell", "STO")
            sname = c["strategy"]["name"]
            conv = None
            if sd not in ("buy", "sell", "BTO", "STO"):
                pass                                        # no side a strategy takes: MISSING_SIDE says so, and no convention applies
            elif sname in ("iron condor", "iron butterfly"):
                conv = "a credit: the wings bought, the body sold" if sold else "a debit: the body bought, the wings sold"
            elif sname == "condor" and c["strategy"].get("right"):
                conv = "a credit: the body bought, the wings sold" if sold else "a debit: the wings bought, the body sold"
                sname = f"{_call_put(c['strategy']['right'])} condor"
            elif sname == "call spread":
                conv = "a credit: the lower strike sold, the higher bought" if sold else "a debit: the lower strike bought, the higher sold"
            elif sname == "put spread":
                conv = "a credit: the higher strike sold, the lower bought" if sold else "a debit: the higher strike bought, the lower sold"
            if conv:
                c["flags"].append(_flag("STRATEGY_SIDE_CONVENTION", f"{'sell' if sold else 'buy'} {sname} = {conv}", c["span"]))
        clauses.append(c)
        prev = c if c.get("verb") else prev
        for sp in c["unparsed"]:
            unparsed.append(list(sp))
        if c["kind"] == "empty":
            flags.append(_flag("UNPARSED_TEXT", f"'{c['text']}' is not understood: start with buy, sell, short, cover, close or roll", c["span"]))
    # the suffix scope (SD-2): a book phrase at the very end of the text
    suffix = None
    if clauses and clauses[-1]["kind"] == "scope":
        last = clauses.pop()
        suffix = {"scope": last["mods"].get("scope") or {"books": [], "span": last["span"]}, "account": last["mods"].get("account"), "clause": None,
                  "flags": [f for f in last["flags"]]}
    elif clauses and clauses[-1]["mods"].get("scope") and (clauses[-1]["mods"]["scope"].get("books") or clauses[-1]["mods"]["scope"].get("unknown")):
        # sixth review: a closing phrase whose book word names no book closes the text too - the orders that name no book are unread
        last = clauses[-1]
        sc = last["mods"]["scope"]
        # 1.0.7.2 review: the book phrase still ends the text when only the last order's own modifiers follow it (a price, a time in
        # force, an account, a group word: "... in the Fund book at market"), so swapping two modifiers never moves an order to another book
        mod_spans = [m["span"] for m in (last["mods"].get(k) for k in ("price", "tif", "account", "group")) if m and m.get("span")]
        tail = [t for t in body if t.start >= sc["span"][1] and not _is_filler(t) and not _is_hard(t)
                and not any(s[0] <= t.start and t.end <= s[1] for s in mod_spans)]
        if not tail:                                        # nothing but separators and modifiers after the book phrase: it ends the text
            acct = last["mods"].get("account")
            asp = (acct or {}).get("span")
            # an account in the phrase itself ("in the Institutional book at pb ms") goes with the book to every leg that inherits it; so
            # does one right beside the phrase - just before it, or just after it past a comma ("at pb ms in the Institutional book", "in
            # the Institutional book, at pb ms"): swapping the two phrases or adding a comma never moves an order to another account
            inside = bool(asp) and sc["span"][0] <= asp[0] and asp[1] <= sc["span"][1]
            carried = acct if asp and (inside or _beside(norm, asp, sc["span"])) else None
            suffix = {"scope": sc, "account": carried, "clause": len(clauses) - 1, "flags": []}
    for c in clauses:
        if c["kind"] == "scope":
            c["kind"] = "empty"
            flags.append(_flag("UNPARSED_TEXT", f"'{c['text']}' names a book but no order", c["span"]))
    # groups: parentheses, then group words (the run of consecutive clauses on the same underlying that ends at the word)
    out_groups: List[dict] = []
    for g in groups:
        members = [i for i, c in enumerate(clauses) if c.get("group") == g["index"]]
        if not members:
            continue
        sp = [g["open"][0], (g["word"] or g["close"])[1]]
        out_groups.append({"id": None, "kind": "paren", "clauses": members, "span": sp, "word_span": g["word"], "open_span": g["open"], "close_span": g["close"]})
    in_group = {i for g in out_groups for i in g["clauses"]}
    for i, c in enumerate(clauses):
        gw = c["mods"].get("group")
        if gw is None or i in in_group:
            continue
        key = c.get("underlying_key")
        run = [i]
        j = i - 1
        while j >= 0 and j not in in_group and clauses[j]["kind"] in ("order", "strategy") and clauses[j].get("underlying_key") == key and key \
                and clauses[j]["mods"].get("group") is None:
            run.insert(0, j)
            j -= 1
        out_groups.append({"id": None, "kind": "word", "clauses": run, "span": [clauses[run[0]]["span"][0], c["span"][1]], "word_span": gw["span"]})
        in_group.update(run)
    out_groups.sort(key=lambda g: g["clauses"][0])
    for n_, g in enumerate(out_groups):
        g["id"] = f"G{n_ + 1}"
    for c in clauses:
        c["legs"] = expand_strategy(c)
        c.pop("group", None)
    # the leg count after expansion (books named in a scope repeat the clause; a close of a class counts one here)
    count = 0
    for i, c in enumerate(clauses):
        nb = _books_named(c["mods"].get("scope")) or _books_named(c.get("carried_scope"))
        if nb == 0 and prefix is not None:
            nb = _books_named(prefix)
        if nb == 0 and suffix is not None:
            nb = _books_named(suffix["scope"])
        count += _clause_leg_count(c) * max(1, nb)
    if count > MAX_LEGS:
        raise SmartTicketError("TOO_MANY_LEGS", f"the instruction makes {count} legs: at most {MAX_LEGS}", {"legs": count, "max": MAX_LEGS})
    unparsed = sorted({tuple(u) for u in unparsed})
    return {"grammar": GRAMMAR, "text": norm, "clauses": clauses, "groups": out_groups, "prefix": prefix, "suffix": suffix,
            "unparsed": [{"span": list(u), "text": norm[u[0]:u[1]]} for u in unparsed], "flags": sort_flags(flags), "legs": count}


def _beside(text: str, a: Sequence[int], b: Sequence[int]) -> bool:
    """Whether two spans of ``text`` touch, with only spaces or commas between them (second review: the account phrase of a closing
    book phrase)."""
    lo, hi = (a, b) if a[1] <= b[0] else (b, a)
    return lo[1] <= hi[0] and text[lo[1]:hi[0]].strip(" \t,") == ""


def _books_named(scope: Optional[dict]) -> int:
    if not scope:
        return 0
    return len(scope.get("books") or [])


def _expansion_words(c: dict, legs: Optional[List[dict]] = None) -> str:
    legs = expand_strategy(c) if legs is None else legs
    words = []
    for l in legs:
        k = l.get("strike")
        words.append(f"{l['side'] or 'buy or sell?'} {'2 x ' if l.get('ratio') == 2 else ''}{k} {'call' if l.get('right') == 'C' else ('put' if l.get('right') == 'P' else 'call or put?')}"
                     + (f" {_exp_text(l['expiry'])}" if c["strategy"]["name"] == "calendar" and l.get("expiry") else ""))
    return ", ".join(words) + " (separate tickets)"


# ======================================================================================================================================
# The resolver: FinSim's own lookups, read only (G 3.5).  One reader connection for the whole parse; never a write, a fetch, a
# subscription, a preview or a mark.
# ======================================================================================================================================
SIDE_WORD = {"BUY": "buy", "SELL": "sell", "SELL_SHORT": "short", "BUY_TO_COVER": "cover", "BTO": "bto", "STO": "sto", "BTC": "btc", "STC": "stc"}
SIDE_TEXT = {"BUY": "buy", "SELL": "sell", "SELL_SHORT": "sell short", "BUY_TO_COVER": "buy to cover", "BTO": "buy to open", "STO": "sell to open",
             "BTC": "buy to close", "STC": "sell to close"}
OPTION_SIDES = ("BTO", "STO", "BTC", "STC")
#: the side words of a choice that open a position: the account rule reads them without the position (R-ACCT-1)
OPENING_WORDS = ("bto", "sto", "short")
CASH_SIDES = ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER")
FUTURES_SIDES = ("BUY", "SELL")
LIVE_ORDER_STATUSES = ("WORKING", "PARTIAL", "SUSPENDED", "WAITING_PARENT")
#: the keys of a leg's ``position`` in the answer (unchanged since 1.0.7.2's first build: the unified app relays the answer)
POSITION_KEYS = ("qty", "working_sells", "working_covers", "reserved", "lent", "free")
KIND_WORDS = {"EQUITY": "equity", "ETF": "ETF", "ADR": "ADR", "REIT": "REIT", "PREFERRED": "preferred", "FUTURE": "future", "OPTION": "option", "FX": "FX pair",
              "BOND": "bond", "PRIVATE": "private company", "SWAP": "swap", "INDEX": "index", "YIELD": "yield"}
OWN_TICKET_LABEL = {"FX": "Open the FX desk", "BOND": "Open the bond ticket", "PRIVATE": "Open the private-markets ticket", "OTC": "Open the OTC desk"}
OWN_TICKET_ROLE = {"FX": "FOREIGN_FX", "BOND": "DEBT", "PRIVATE": "PRIVATE"}
UNITS_OK = {"option": ("contracts", "x"), "future": ("contracts", "lots", "x"), "shares": ("shares", "x")}
#: option strategies the order ticket's Strategies tab has a template for (call / put spreads, straddles, strangles, iron condor and its
#: reverse, iron butterfly, call butterfly, call / put calendars): their group offers the action "Strategies tab"
TEMPLATE_STRATEGIES = ("call spread", "put spread", "vertical", "straddle", "strangle", "iron condor", "iron butterfly", "butterfly", "call butterfly",
                       "calendar", "call calendar", "put calendar")


def _norm_root(r: Optional[str]) -> str:
    return str(r or "").upper().replace(".", "").replace("-", "").replace("_", "")


def _send_rank(leg: dict) -> int:
    """The sending order of a group's tickets: purchases first (shares, then options), then sales to close, then sales to open, then
    share sales and short sales; an order with no side yet last."""
    return {"BUY": 0, "BUY_TO_COVER": 0, "BTO": 1, "BTC": 1, "STC": 2, "STO": 3, "SELL": 4, "SELL_SHORT": 4}.get(str(leg.get("side") or ""), 5)


def _groupish(text: str) -> bool:
    """Whether words hold a parenthesis or a group word (a choice that writes one is checked on the whole instruction: its run may reach
    other orders)."""
    try:
        return any(_is_punct(t, "(", ")") or _phrase(t, "group") or _is_word(t, *GROUP_WORDS) for t in tokenize(normalise(text)[0]))
    except Exception:   # noqa: BLE001 - words that cannot be read are checked whole
        return True


def _clause_words(text: Optional[str]) -> str:
    """A clause's words without spaces, commas, semicolons and parentheses around them (fourth review: the guard's re-read matches the
    orders a choice does not touch by their words)."""
    return " ".join(re.sub(r"[,;()]", " ", str(text or "")).split()).lower()


def _widen(text: str, f: dict) -> None:
    """Fourth review: a flag one of whose choices needs more words than the flag's span (``_span``: "as separate orders" that also writes
    the next group with parentheses) gets one span over them all, every choice written over it (the page replaces the flag's span)."""
    chs = f.get("choices") or []
    spans = [ch["_span"] for ch in chs if ch.get("_span")]
    if not spans or not f.get("span"):
        for ch in chs:
            ch.pop("_span", None)
        return
    s0, s1 = int(f["span"][0]), int(f["span"][1])
    u0, u1 = min([s0] + [int(x[0]) for x in spans]), max([s1] + [int(x[1]) for x in spans])
    out = []
    for ch in chs:
        a, b = ch.pop("_span", None) or (s0, s1)
        out.append(dict(ch, clause=text[u0:a] + ch["clause"] + text[b:u1]))
    f["choices"] = out
    f["span"] = [u0, u1]


def _date_words(iso: str) -> str:
    """An ISO date in the words a clause reads anywhere ("2026-12-18" -> "Dec 18 2026"); the text itself when it is not a date."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{_month_words(d.month)} {d.day} {d.year}"


def _nearest(values: Sequence, x) -> List:
    """The nearest listed value below and the nearest above ``x`` (listing facts only, never applied)."""
    below = [v for v in values if v < x]
    above = [v for v in values if v > x]
    out = []
    if below:
        out.append(max(below))
    if above:
        out.append(min(above))
    return out


def _call_put(right: Optional[str]) -> str:
    return "call" if right == "C" else "put"


def _inst_words(inst) -> str:
    kw = KIND_WORDS.get(inst.kind, str(inst.kind).lower())
    if inst.kind == "FUTURE":
        return f"{inst.symbol} ({inst.name})"
    name = str(inst.name or "")
    return f"{inst.symbol} {kw}" + (f" ({name})" if name and name.upper() != inst.symbol.upper() else "")


def _account_words_of(a) -> str:
    role = str(a["role"])
    if role == "US_EXCHANGE":
        return "us exchange"
    if role.startswith("PB_"):
        return "pb " + role[3:].lower()
    return str(a["account_id"])


class Resolver:
    """Turns a pure parse tree into the staged legs and combinations of the 2.4 answer, reading FinSim's own state on ONE connection."""

    def __init__(self, services, conn, selected_book: Optional[str], default_account: Optional[str] = None, now: Optional[int] = None,
                 vocab: Optional[Vocab] = None):
        from . import books as _books
        from .clock import ny_of
        self.s = services
        self.conn = conn
        self.master = services.instruments
        self.now = int(now if now is not None else services.clock.epoch())
        self.today = ny_of(self.now).date()
        self._ny_of = ny_of
        self._books = _books
        self.book_rows = {b.code: b for b in _books.books(conn)}
        self.vocab = vocab or Vocab([(b.code, b.name, b.profile) for b in self.book_rows.values()])
        self.selected = selected_book
        self.default_account = str(default_account).strip().upper() if default_account else None
        self.text = ""
        self._tree: dict = {}
        self._lt: Dict[str, Any] = {}
        self._accts: Dict[str, List[dict]] = {}
        self._index: Optional[Dict[str, list]] = None
        self._opt_index: Dict[Tuple[str, str], list] = {}
        self._snaps: Dict[str, Any] = {}
        self._pos: Dict[Tuple[str, str], dict] = {}
        self._cover: Dict[Tuple[str, str], dict] = {}        # 1.0.7.2: ``orders_preview.cover_state`` per (account, name), read once per parse
        self._fresh: Dict[tuple, tuple] = {}                 # fifth review: a chain's freshness, judged once per parse (shared with re-reads)

    # ---- reads ---------------------------------------------------------------------------------------------------------------------
    def _lifetime(self, code: Optional[str]):
        if code is None:
            return None
        if code not in self._lt:
            self._lt[code] = self._books.open_lifetime(self.conn, code)
        return self._lt[code]

    def _accounts(self, lid: str) -> List[dict]:
        if lid not in self._accts:
            rows = self.conn.execute("SELECT * FROM accounts WHERE lifetime_id=? AND status='OPEN' ORDER BY sort_order, account_id", (lid,)).fetchall()
            self._accts[lid] = [dict(r) for r in rows]
        return self._accts[lid]

    def _build_index(self) -> None:
        idx: Dict[str, Dict[str, Any]] = {}
        opt: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for inst in self.master.all():
            if inst.kind == "OPTION":
                if inst.underlying_id and inst.expiry:
                    opt.setdefault((inst.underlying_id, inst.expiry), {})[inst.instrument_id] = inst
                continue
            keys = [inst.symbol.upper(), inst.instrument_id.upper()]
            if inst.yahoo_symbol:
                keys.append(inst.yahoo_symbol.upper())
            raw = (inst.spec or {}).get("aliases") if isinstance(inst.spec, dict) else None
            keys.extend(str(a).upper() for a in (raw if isinstance(raw, (list, tuple)) else ([raw] if raw else [])))
            for k in keys:
                idx.setdefault(k, {})[inst.instrument_id] = inst
        self._index = {k: [v[i] for i in sorted(v)] for k, v in idx.items()}
        self._opt_index = {k: [v[i] for i in sorted(v)] for k, v in opt.items()}

    def _lookup(self, sym: str) -> list:
        """Exact id / symbol / Yahoo symbol / alias hits (``InstrumentMaster.search`` rank 0), options excluded."""
        if self._index is None:
            self._build_index()
        return list(self._index.get(str(sym or "").upper(), []))

    def _master_options(self, uid: str, expiry: str) -> list:
        if self._index is None:
            self._build_index()
        return list(self._opt_index.get((uid, expiry), []))

    def _snap(self, uid: Optional[str]):
        """The cached chain of ``uid`` through ``ChainCache.peek`` only (never ``get``, which reorders the cache)."""
        if not uid:
            return None
        if uid not in self._snaps:
            ch = getattr(self.s, "chains", None)
            peek = getattr(ch, "peek", None) if ch is not None else None
            self._snaps[uid] = peek(uid) if callable(peek) else None
        return self._snaps[uid]

    @staticmethod
    def _rows(snap, expiry: str) -> list:
        if snap is None:
            return []
        fn = getattr(snap, "rows_for", None)
        return list(fn(expiry)) if callable(fn) else [r for r in getattr(snap, "contracts", []) if r.expiry == expiry]

    def _chain_info(self, under, snap, leg: dict, uid: str) -> dict:
        from .products.options import chain_freshness
        key = (uid, id(snap))
        got = self._fresh.get(key)
        if got is None:
            try:
                q, _age, reason = chain_freshness(self.s, snap, self.now, under)
            except Exception as e:   # noqa: BLE001 - a freshness that cannot be judged is said, never guessed
                q, reason = "unavailable", f"the chain's freshness could not be judged ({type(e).__name__})"
            got = self._fresh[key] = (q, reason)
        q, reason = got
        info = {"quality": q, "quote_time": getattr(snap, "quote_time", None), "fetched_at": getattr(snap, "fetched_at", None), "reason": reason}
        if q == "stale":
            leg["flags"].append(_flag("CHAIN_STALE", f"the {under.symbol} chain is stale: {reason}", leg["span"],
                                      action={"kind": "load_chain", "label": "Load the chain", "instrument_id": uid}))
        return info

    def _cover_of(self, aid: str, uid: str) -> dict:
        """1.0.7.2: the shares of ``uid`` held in ``aid`` and the calls they cover, read by the server's one function
        (``orders_preview.cover_state``, without the claims: the parse never reads ``commands``) once per parse, so the reading and the
        order route agree - ``free`` (what a sale may take: net of working sells, of the shares covering calls sold or being sold, in whole
        contracts as the margin assigns them, and of lent shares) and ``free_for_calls`` (what NEW calls may take as cover: also net of
        working short sales).  A SELECT on the parse's own connection: nothing is written."""
        key = (aid, uid)
        if key not in self._cover:
            from .orders_preview import cover_state
            self._cover[key] = cover_state(self.s, self.conn, aid, uid)
        return self._cover[key]

    def _position(self, aid: str, iid: str, equity_like: bool) -> dict:
        key = (aid, iid)
        if key not in self._pos:
            from .ledger import position as _ledger_position
            qty, _cost = _ledger_position(self.conn, aid, iid)
            ws = wc = 0
            for r in self.conn.execute("SELECT side, qty, filled_qty FROM orders WHERE account_id=? AND instrument_id=? AND status IN (?,?,?,?) ORDER BY order_id",
                                       (aid, iid) + LIVE_ORDER_STATUSES).fetchall():
                rem = int(r["qty"]) - int(r["filled_qty"])
                if r["side"] in ("SELL", "STC"):
                    ws += rem
                elif r["side"] in ("BUY_TO_COVER", "BTC"):
                    wc += rem
            qty = int(qty)
            out = {"qty": qty, "working_sells": ws, "working_covers": wc, "reserved": 0, "calls_working": 0, "lent": 0}
            if equity_like and qty > 0:
                st = self._cover_of(aid, iid)
                out.update({"working_sells": int(st["working_sells"]), "reserved": int(st["reserved_sold"]), "calls_working": int(st["reserved_working"]),
                            "lent": int(st["lent"]), "working_shorts": int(st["working_shorts"]), "free": int(st["free"]),
                            "free_for_calls": int(st["free_for_calls"])})
            else:
                out["free"] = max(0, qty - ws) if qty > 0 else (max(0, -qty - wc) if qty < 0 else 0)
            self._pos[key] = out
        return dict(self._pos[key])

    @staticmethod
    def _cover_free(pos: Optional[dict]) -> int:
        """The shares held free to cover NEW calls (a covered call, a collar): ``free_for_calls`` of the leg's position (``_position``), 0
        when none are held long.  Each order is read on the account as it is: another order sent first may take them, and the order
        route then refuses the calls as covered (NOT_COVERED)."""
        pos = pos or {}
        return int(pos.get("free_for_calls") or 0) if int(pos.get("qty") or 0) > 0 else 0

    def _holders(self, lid: Optional[str], iid: str) -> Dict[str, int]:
        if not lid:
            return {}
        ids = [a["account_id"] for a in self._accounts(lid)]
        if not ids:
            return {}
        rows = self.conn.execute(f"SELECT account_id, qty FROM v_positions WHERE instrument_id=? AND account_id IN ({','.join('?' * len(ids))})",
                                 (iid, *ids)).fetchall()
        return {r["account_id"]: int(r["qty"]) for r in rows if int(r["qty"] or 0) != 0}

    def _is_trading_day(self, d: date) -> bool:
        cal = getattr(self.s, "cal", None)
        try:
            return bool(cal.is_business_day(d)) if cal is not None else d.weekday() < 5
        except Exception:   # noqa: BLE001
            return d.weekday() < 5

    # ---- text edits for choices ---------------------------------------------------------------------------------------------------------
    def _clause_edit(self, c: dict, edits: Sequence[Tuple[int, int, str]]) -> str:
        return _edit(self.text, c["span"], edits)

    def _side_edit(self, c: dict, side_word: str, qty: Optional[int], leg: Optional[dict] = None, con: Optional[dict] = None) -> str:
        """The clause with its side word (inserted when it was carried) and its quantity replaced.  Third review: an OPENING side (bto,
        sto, short) written for a leg read in an account the opening-side rule would not pick (the account that holds the contract, read
        from the position) names that account, so the part re-reads where the leg was read; the book and account a carried clause
        inherits are written by ``_keep_others``."""
        edits: List[Tuple[int, int, str]] = []
        v = c.get("verb") or {}
        if v.get("span") and not v.get("carried"):
            edits.append((v["span"][0], v["span"][1], side_word))
        else:
            edits.append((c["span"][0], c["span"][0], side_word + " "))
        q = c.get("qty")
        if qty is not None and q and q.get("span"):
            edits.append((q["span"][0], q["span"][1], str(qty)))
        out = self._clause_edit(c, edits)
        if leg is not None and con is not None and side_word in OPENING_WORDS and leg.get("account_id") and not leg.get("_acct_named"):
            if self._opening_pick(leg, con) != leg["account_id"]:
                row = next((x for x in self._accounts(leg["lifetime_id"]) if x["account_id"] == leg["account_id"]), None) if leg.get("lifetime_id") else None
                out += " at " + (_account_words_of(row) if row is not None else str(leg["account_id"]))
        return out

    def _pin_account(self, clause: str, leg: dict) -> str:
        """Fourth review: a choice that changes how the account is read (an opening side reads R-ACCT-1, a side read from the position the
        account that holds the contract) names the account the leg was read in, so the order stays where it was read."""
        if leg.get("_acct_named") or not leg.get("account_id") or not leg.get("lifetime_id"):
            return clause
        row = next((x for x in self._accounts(leg["lifetime_id"]) if x["account_id"] == leg["account_id"]), None)
        return clause + " at " + (_account_words_of(row) if row is not None else str(leg["account_id"]))

    def _share_unit(self, c: dict, what: str) -> Optional[dict]:
        """Fourth review: a covered call's or a collar's quantity is in shares (100 a contract): typed in contracts or lots it is refused
        (UNIT_KIND_MISMATCH), never read as shares - with the two readings as choices: that many contracts (x 100 shares), or that many
        shares (the unit word dropped)."""
        qa = c.get("qty") or {}
        unit = qa.get("unit")
        if not unit or unit in UNITS_OK["shares"]:
            return None
        q = qa.get("value") if qa.get("kind") == "num" else None
        choices = []
        if qa.get("unit_span") and qa.get("span") and q:
            lo, hi = sorted((int(qa["span"][0]), int(qa["unit_span"][0])))
            hi = max(int(qa["span"][1]), int(qa["unit_span"][1]))
            choices.append({"label": f"{q:,} {qa.get('unit_word') or unit} = {q * 100:,} shares", "clause": self._clause_edit(c, [(lo, hi, str(q * 100))])})
            choices.append({"label": f"{q:,} shares (without the unit word)", "clause": self._clause_edit(c, [(qa["unit_span"][0], qa["unit_span"][1], "")])})
        return _flag("UNIT_KIND_MISMATCH", f"'{qa.get('unit_word') or unit}' with a {what}: its quantity is in shares (100 a contract), so"
                     f" {q:,} {qa.get('unit_word') or unit} is not read as {q:,} shares" if q else f"'{qa.get('unit_word') or unit}' with a {what}: its quantity is in"
                     " shares (100 a contract)", c["span"], choices or None)

    def _opening_pick(self, leg: dict, con: dict) -> Optional[str]:
        """The account an explicit opening side would be read in, with no account named (``_account`` with ``aware`` False): the page's
        default account when it trades the contract, else R-ACCT-1 (US Exchange first)."""
        lid = leg.get("lifetime_id")
        if not lid:
            return None
        accts = self._accounts(lid)
        dflt = next((x for x in accts if x["account_id"] == self.default_account), None) if self.default_account else None
        if dflt is not None and self._tradeable(con, dflt):
            return dflt["account_id"]
        cands = [x for x in accts if self._tradeable(con, x)]
        us = [x for x in cands if x["role"] == "US_EXCHANGE"]
        return (us or cands)[0]["account_id"] if cands else None

    def _editors(self, c: dict, role: str = "single", strike_index: Optional[int] = None, exp_index: int = 0) -> dict:
        """Callables that rewrite the clause for a choice: a new expiry, a new strike, a contract typed whole."""
        o = c.get("option") or {}
        st = c.get("strategy") or {}
        rt = c.get("roll_to") or {}
        inst = c.get("instrument") or {}

        def expiry(e: str) -> Optional[str]:
            if role == "roll_new" and rt.get("exp_span"):
                return self._clause_edit(c, [(rt["exp_span"][0], rt["exp_span"][1], e)])
            if role in ("single", "roll_old", "covered") and o.get("exp_span"):
                return self._clause_edit(c, [(o["exp_span"][0], o["exp_span"][1], e)])
            spans = st.get("exp_spans") or ([st["exp_span"]] if st.get("exp_span") else [])
            if spans:
                i = min(exp_index, len(spans) - 1)
                sp = spans[i]
                exps = st.get("expiries") or []
                if len(spans) > 1:
                    # fifth review: a calendar's expiries are typed "date/date"; a listed expiry goes in as words the reader takes after "/"
                    # ("Dec 18 2026", never "2026-12-18": "2026/2026" reads as strikes), and never as the other leg's own expiry
                    other = exps[1 - i] if len(exps) == 2 and i in (0, 1) else None
                    if other is not None and self._resolve_date(other, self._snap(self._calendar_uid(c))) == e:
                        return None
                    e = _date_words(e)
                return self._clause_edit(c, [(sp[0], sp[1], e)])
            return None

        def strike(k: str) -> Optional[str]:
            if role == "roll_new" and rt.get("strike_span"):
                return self._clause_edit(c, [(rt["strike_span"][0], rt["strike_span"][1], k)])
            if role in ("single", "roll_old", "covered") and o.get("strike_span"):
                rep = k + ("c" if o.get("right") == "C" else "p") if o.get("sr") else k
                return self._clause_edit(c, [(o["strike_span"][0], o["strike_span"][1], rep)])
            sp = st.get("strikes_span")
            ks = list(st.get("strikes") or [])
            if sp and strike_index is not None and strike_index < len(ks):
                ks[strike_index] = k
                return self._clause_edit(c, [(sp[0], sp[1], "/".join(ks))])
            return None

        def whole(occ: str) -> Optional[str]:
            if role == "single" and inst.get("span") and o.get("span"):
                return self._clause_edit(c, [(inst["span"][0], o["span"][1], occ)])
            return None
        return {"expiry": expiry, "strike": strike, "root": whole}

    # ---- the leg skeleton and its book ----------------------------------------------------------------------------------------------------
    def _leg_base(self, ci: int, c: dict, binfo: dict, typed_symbol: Optional[str] = None) -> dict:
        leg = {"n": None, "span": list(c["span"]), "clause": c["text"], "combo": None, "book": None, "book_name": None, "lifetime_id": None, "account_id": None,
               "account_name": None,
               "instrument": {"id": None, "symbol": typed_symbol, "kind": None, "lot": None, "tick": None, "underlying_id": None, "expiry": None, "strike": None,
                              "right": None, "listed": False, "words": typed_symbol or "", "chain": None},
               "position": None, "side": None, "qty": None, "order_type": None, "limit_px": None, "stop_px": None, "trail_amt": None, "trail_pct": None,
               "tif": None, "gtd_date": None, "flags": [dict(f) for f in c["flags"]], "inferred": [], "request": None,
               "_ci": ci, "_rep": binfo.get("rep", 0), "_src": binfo["src"], "_clause": c}
        flags = leg["flags"]
        code = binfo["code"]
        scope = binfo.get("scope")
        if code is None:
            if binfo.get("unread") and binfo["src"] != "own" and scope:
                # sixth review: an order that takes the book of a phrase whose book word names no book is unread too (no book, no account),
                # a blocker that names those words and offers every book for them (the same words as on the order that holds them)
                ws = scope.get("word_span") or scope["span"]
                where = {"carried": "in the order its side is carried from", "prefix": "at the start", "suffix": "at the end of the text"}.get(binfo["src"], "")
                names = ", ".join(f"{self.vocab.label(c2)} ({c2})" for c2, _n, _p in self.vocab.books)
                choices = [{"label": f"the {self.vocab.label(c2)}", "clause": self.vocab.first_word(c2)} for c2, _n, _p in self.vocab.books]
                flags.append(_flag("UNPARSED_TEXT", f"'{scope.get('unknown')}' ({where}) is not a book, so this order's book is unread: the books are {names}",
                                   ws, choices))
            elif binfo["src"] in ("prefix", "suffix"):
                flags.append(_flag("TOKEN_AMBIGUOUS", "the book phrase names more than one book: pick one there", scope["span"] if scope else None))
            return leg
        b = self.book_rows.get(code)
        name = b.name if b is not None else code
        leg["book"], leg["book_name"] = code, name
        lt = self._lifetime(code)
        if lt is None:
            flags.append(_flag("NOT_TRADEABLE_HERE", f"the {name} has no open lifetime", c["span"]))
        else:
            leg["lifetime_id"] = lt["lifetime_id"]
        src = binfo["src"]
        stext = self.text[scope["span"][0]:scope["span"][1]] if scope else ""
        if src == "selected":
            flags.append(_flag("BOOK_SELECTED", f"no book named: the selected {name}"))
            leg["inferred"].append(f"R-BOOK-4: the selected book {code}")
        elif src == "prefix":
            flags.append(_flag("BOOK_FROM_SCOPE", f"book from '{stext}' at the start", scope["span"], severity="info"))
            leg["inferred"].append(f"R-BOOK-2: book {code} from the opening phrase")
        elif src == "suffix":
            sacct = (self._tree.get("suffix") or {}).get("account") or {}
            asp = sacct.get("span")
            beside = (f" with the account '{self.text[asp[0]:asp[1]]}' beside it" if asp and scope and not (scope["span"][0] <= asp[0] and asp[1] <= scope["span"][1])
                      else "")
            flags.append(_flag("BOOK_FROM_SCOPE", f"book from '{stext}'{beside} at the end of the text (it applies to every leg that names no book)", scope["span"]))
            leg["inferred"].append(f"R-BOOK-3: book {code} applies to every leg without a book")
        elif src == "carried":
            flags.append(_flag("BOOK_FROM_SCOPE", f"book from '{stext}' in the order before: it travels with the side carried from there", scope["span"]))
            leg["inferred"].append(f"book {code} carried with the side from the order before")
        elif src == "own":
            pre = self._tree.get("prefix") or {}
            pcodes = [b["codes"][0] for b in pre.get("books") or [] if len(b["codes"]) == 1]
            if pcodes and code not in pcodes:
                ptext = self.text[pre["span"][0]:pre["span"][1]]
                flags.append(_flag("BOOK_CONFLICT", f"this order names its own book ({name}): the opening '{ptext}' does not apply to it", scope["span"]))
        if binfo.get("n", 1) > 1:
            names = ", ".join(self.book_rows[x].name if x in self.book_rows else x for x in binfo.get("codes", []))
            flags.append(_flag("MULTI_BOOK", f"the order is repeated in each book named ({names}) at the same quantity: no allocation",
                               scope["span"] if scope else None))
        if code != self.selected:
            sel = self.book_rows.get(self.selected)
            flags.append(_flag("OTHER_BOOK", f"this order goes to the {name}, not the selected {sel.name if sel is not None else self.selected}"))
        return leg

    def _books_for(self, ci: int, c: dict, tree: dict) -> List[dict]:
        own = c["mods"].get("scope")
        prefix, suffix = tree.get("prefix"), tree.get("suffix")
        scope, src = None, "selected"

        def unread(sc: dict, where: str) -> List[dict]:
            # fifth review: a book word that names no book leaves the order's book unread (no book, no account) - never the selected
            # book - so every book offered for the word is a choice that settles it, not one that moves it.  Sixth review: so does every
            # order that takes that phrase's book (its side carried from the order holding it, or under that closing or opening phrase)
            return [{"code": None, "src": where, "scope": sc, "n": 1, "rep": 0, "codes": [], "unread": True}]
        carried = c.get("carried_scope") or {}
        ssc = (suffix or {}).get("scope") or {}
        if own and own.get("books"):
            scope, src = own, "own"
        elif own and own.get("unknown"):
            return unread(own, "own")
        elif carried.get("books"):                               # the book of the order whose side this clause carries (1.0.7.2 review)
            scope, src = carried, "carried"
        elif carried.get("unknown"):
            return unread(carried, "carried")
        elif prefix and prefix.get("books"):
            scope, src = prefix, "prefix"
        elif prefix and prefix.get("unknown"):
            return unread(prefix, "prefix")
        elif suffix and ssc.get("books") and suffix.get("clause") != ci:
            scope, src = ssc, "suffix"
        elif suffix and ssc.get("unknown") and suffix.get("clause") != ci:
            return unread(ssc, "suffix")
        if scope is None:
            return [{"code": self.selected, "src": "selected", "scope": None, "n": 1, "rep": 0}]
        out: List[dict] = []
        codes: List[str] = []
        for b in scope["books"]:
            code = b["codes"][0] if len(b["codes"]) == 1 else None
            if code is not None and code in codes:
                continue
            if code is not None:
                codes.append(code)
            out.append({"code": code, "src": src, "scope": scope})
        for r, o in enumerate(out):
            o["n"], o["rep"], o["codes"] = len(out), r, list(codes)
        return out

    def _named_account(self, c: dict, binfo: dict, tree: dict) -> Optional[dict]:
        """The account a leg names: its own; the one carried with its side; an opening or closing phrase's account - with the phrase's
        book when the leg takes that book, and on its own ("at pb ms: ...", "...; at pb ms") for every leg that names no account, read
        in the leg's own book (ACCOUNT_NOT_IN_BOOK when that book lacks it).  Never dropped (1.0.7.2 review)."""
        a = c["mods"].get("account")
        if a is None:                                   # the account of the order whose side this clause carries (with its book or not)
            a = c.get("carried_account")
        prefix = tree.get("prefix") or {}
        if a is None and prefix.get("account") is not None and (binfo["src"] == "prefix" or not prefix.get("books")):
            a = prefix["account"]
        suffix = tree.get("suffix") or {}
        if a is None and suffix.get("account") is not None and (binfo["src"] == "suffix" or not (suffix.get("scope") or {}).get("books")):
            a = suffix["account"]
        return a

    # ---- instruments ---------------------------------------------------------------------------------------------------------------------
    def _set_instrument(self, leg: dict, contract: Optional[dict]) -> None:
        if contract is None:
            return
        leg["instrument"] = {k: contract.get(k) for k in ("id", "symbol", "kind", "lot", "tick", "underlying_id", "expiry", "strike", "right", "listed", "words", "chain")}
        leg["_contract"] = contract

    def _inst_contract(self, inst) -> dict:
        return {"id": inst.instrument_id, "symbol": inst.symbol, "kind": inst.kind, "lot": int(inst.lot), "tick": str(inst.tick), "underlying_id": inst.underlying_id,
                "expiry": inst.expiry, "strike": None if inst.strike is None else _kstr(inst.strike), "right": inst.right, "listed": True,
                "words": _inst_words(inst), "chain": None, "_inst": inst, "_kinds": tuple(inst.account_kinds)}

    @staticmethod
    def _own_ticket(inst) -> Optional[Tuple[str, str]]:
        from .orders_preview import OTC_TICKET_KINDS, OWN_TICKET
        if inst.kind in OWN_TICKET:
            return inst.kind, OWN_TICKET[inst.kind]
        if inst.kind in OTC_TICKET_KINDS:
            return "OTC", OWN_TICKET["SWAP"]
        return None

    def _symbol_contract(self, sym: str, leg: dict, span, forced: bool = False) -> Optional[dict]:
        hits = self._lookup(sym)
        if forced and len(hits) > 1:                       # "$ID" names the row whose id it is (the SYMBOL_AMBIGUOUS choices)
            exact = [h for h in hits if h.instrument_id.upper() == str(sym).upper()]
            hits = exact if len(exact) == 1 else hits
        flags = leg["flags"]
        if not hits:
            flags.append(_flag("SYMBOL_UNKNOWN", f"{sym} is not in the instrument master: look it up first", span,
                               action={"kind": "resolve", "label": "Look it up", "symbol": sym}))
            leg["instrument"]["words"] = f"{sym} (not in the master)"
            return None
        if len(hits) > 1:
            c = leg["_clause"]
            choices = [{"label": f"{h.instrument_id}: {_inst_words(h)}", "clause": self._clause_edit(c, [(span[0], span[1], "$" + h.instrument_id)])} for h in hits]
            flags.append(_flag("SYMBOL_AMBIGUOUS", f"{sym} names {len(hits)} instruments: " + ", ".join(h.instrument_id for h in hits), leg["span"], choices))
            return None
        inst = hits[0]
        con = self._inst_contract(inst)
        own = self._own_ticket(inst)
        if own is not None:
            con["_own"] = own
        elif inst.kind in ("INDEX", "YIELD"):
            flags.append(_flag("NOT_TRADEABLE_HERE", f"{inst.symbol} is an {KIND_WORDS.get(inst.kind, 'index')} (a reference): trade its options or futures", span))
            con["_blocked"] = True
        elif inst.status != "ACTIVE":
            flags.append(_flag("NOT_TRADEABLE_HERE", f"{inst.symbol} is {inst.status.lower()}", span))
            con["_blocked"] = True
        elif inst.support in ("UNSUPPORTED", "COMPUTED"):
            flags.append(_flag("NOT_TRADEABLE_HERE", f"{inst.symbol} is {inst.support.lower()} in this build: it does not trade", span))
            con["_blocked"] = True
        return con

    def _underlying(self, sym: str, leg: dict, span) -> Tuple[Any, Optional[str]]:
        s = str(sym or "").upper()
        root_filter = None
        if s in ROOT_UNDERLYING:
            root_filter, s = s, ROOT_UNDERLYING[s]
        hits = self._lookup(s)
        flags = leg["flags"]
        if not hits:
            flags.append(_flag("SYMBOL_UNKNOWN", f"{s} is not in the instrument master: look it up first", span,
                               action={"kind": "resolve", "label": "Look it up", "symbol": s}))
            leg["instrument"]["words"] = f"{s} (not in the master)"
            return None, None
        if len(hits) > 1:
            flags.append(_flag("SYMBOL_AMBIGUOUS", f"{s} names {len(hits)} instruments: " + ", ".join(h.instrument_id for h in hits), span))
            return None, None
        under = hits[0]
        if under.kind not in EQUITY_KINDS and under.kind != "INDEX":
            flags.append(_flag("NOT_TRADEABLE_HERE", f"{under.symbol} is {KIND_WORDS.get(under.kind, under.kind.lower())}: it has no listed options here", span))
            return None, None
        return under, root_filter

    def _expiry(self, exp: Optional[dict], snap, uid: str, usym: str, leg: dict, ed: dict) -> Optional[str]:
        flags = leg["flags"]
        if not exp:
            return None
        if exp.get("date"):
            d = exp["date"]
        elif exp.get("d") is None:
            m, y = int(exp["m"]), exp.get("y")
            if y is None:
                flags.append(_flag("EXPIRY_MONTH_ONLY", f"{_month_words(m)} without a day or a year: name the expiry date", leg["span"]))
                return None
            label = f"{_month_words(m)} {y}"
            if snap is None:
                flags.append(_flag("EXPIRY_MONTH_ONLY", f"{label} has no day: the listed {usym} dates need the chain", leg["span"],
                                   action={"kind": "load_chain", "label": "Load the chain", "instrument_id": uid}))
                return None
            listed = [e for e in snap.expiries() if e[:7] == f"{int(y):04d}-{m:02d}"]
            if len(listed) == 1:
                flags.append(_flag("EXPIRY_FROM_MONTH", f"{label} = {listed[0]}, the only {usym} expiry listed that month", leg["span"]))
                d = listed[0]
            else:
                alts = listed or _nearest(list(snap.expiries()), f"{int(y):04d}-{m:02d}-15")
                choices = [{"label": e, "clause": ed["expiry"](e)} for e in alts if ed["expiry"](e)]
                flags.append(_flag("EXPIRY_MONTH_ONLY", f"{usym} lists {len(listed)} expiries in {label}"
                                   + (": pick one" if listed else f" (nearest: {', '.join(alts) or 'none'})"), leg["span"], choices))
                return None
        else:
            m, dd, y = int(exp["m"]), int(exp["d"]), exp.get("y")
            if y is None:
                cand = None
                for yy in (self.today.year, self.today.year + 1):
                    try:
                        t = date(yy, m, dd)
                    except ValueError:
                        continue
                    if t >= self.today:
                        cand = t
                        break
                if cand is None:
                    flags.append(_flag("EXPIRY_NOT_LISTED", f"{_month_words(m)} {dd} is not a date", leg["span"]))
                    return None
                flags.append(_flag("YEAR_INFERRED", f"{_month_words(m)} {dd} read as {cand.isoformat()} (the first on or after today)", leg["span"]))
                d = cand.isoformat()
            else:
                try:
                    d = date(int(y), m, dd).isoformat()
                except ValueError:
                    flags.append(_flag("EXPIRY_NOT_LISTED", f"{_month_words(m)} {dd} {y} is not a date", leg["span"]))
                    return None
        if d < self.today.isoformat():
            flags.append(_flag("EXPIRY_NOT_LISTED", f"{d} has passed", leg["span"]))
            return None
        return d

    def _opt_out(self, under, occ: str, d: str, right: str, k: Decimal, root: str, std: str, listed: bool, inst, row, chain) -> dict:
        words = f"{under.symbol} {d} {_kstr(k)} {_call_put(right)}" + (f" (root {root})" if root and _norm_root(root) != _norm_root(std) else "")
        return {"id": occ, "symbol": occ, "kind": "OPTION", "lot": int(inst.lot) if inst is not None else 1, "tick": str(inst.tick) if inst is not None else "0.01",
                "underlying_id": under.instrument_id, "expiry": d, "strike": _kstr(k), "right": right, "listed": bool(listed), "words": words, "chain": chain,
                "_inst": inst, "_row": row, "_kinds": tuple(inst.account_kinds) if inst is not None else OPTION_ACCOUNT_KINDS, "_under": under}

    @staticmethod
    def _root(occ: str) -> str:
        p = parse_occ(occ)
        return p[0] if p else ""

    def _option_contract(self, sym: str, exp: Optional[dict], right: Optional[str], strike: Optional[str], leg: dict, ed: dict, span) -> Optional[dict]:
        flags = leg["flags"]
        under, root_filter = self._underlying(sym, leg, span)
        if under is None or right is None or strike is None:
            return None
        uid = under.instrument_id
        snap = self._snap(uid)
        d = self._expiry(exp, snap, uid, under.symbol, leg, ed)
        if d is None:
            return None
        k = Decimal(str(strike))
        std = (under.cboe_symbol or under.symbol or "").replace("_", "").upper()
        chain = self._chain_info(under, snap, leg, uid) if snap is not None else None
        rows = []
        if snap is not None:
            rows = [r for r in self._rows(snap, d) if r.right == right and r.strike == k]
            if root_filter:
                rows = [r for r in rows if self._root(r.occ) == root_filter]
        if rows:
            by_root: Dict[str, Any] = {}
            for r in rows:
                rt = self._root(r.occ)
                if rt not in by_root or (not by_root[rt].quoted and r.quoted):
                    by_root[rt] = r
            roots = sorted(by_root, key=lambda x: (0 if _norm_root(x) == _norm_root(std) else 1, x))
            row = by_root[roots[0]]
            pref = getattr(snap, "row", None)
            if len(roots) == 1 and callable(pref):
                try:
                    p = pref(d, right, k)
                except Exception:   # noqa: BLE001 - a snapshot without the 1.0.7.2 helper: the first match above stands
                    p = None
                if p is not None and self._root(p.occ) == roots[0]:
                    row = p
            if len(roots) > 1:
                choices = []
                for rt in roots:
                    txt = ed["root"](by_root[rt].occ)
                    if txt:
                        choices.append({"label": f"{by_root[rt].occ} (root {rt}{', the standard root' if _norm_root(rt) == _norm_root(std) else ''})", "clause": txt})
                flags.append(_flag("ROOT_CHOICE", f"the {under.symbol} chain lists {len(roots)} contracts for {d} {_kstr(k)} {_call_put(right)} (roots "
                                   f"{', '.join(roots)}): pick one", leg["span"], choices))
            m = self.master.get(row.occ, self.conn)
            return self._opt_out(under, row.occ, d, right, k, self._root(row.occ), std, True, m, row, chain)
        for m in self._master_options(uid, d):
            if m.right == right and m.strike is not None and Decimal(m.strike) == k and (not root_filter or self._root(m.instrument_id) == root_filter):
                return self._opt_out(under, m.instrument_id, d, right, k, self._root(m.instrument_id), std, True, m, None, chain)
        if snap is not None:
            exps = list(snap.expiries())
            if d not in exps:
                near = _nearest(exps, d)
                choices = [{"label": e, "clause": ed["expiry"](e)} for e in near if ed["expiry"](e)]
                flags.append(_flag("EXPIRY_NOT_LISTED", f"{under.symbol} {d} is not listed on the cached chain (nearest listed: {', '.join(near) or 'none'}): "
                                   "nothing is snapped", leg["span"], choices))
                return None
            ks = sorted({r.strike for r in self._rows(snap, d) if r.right == right and (not root_filter or self._root(r.occ) == root_filter)})
            near = _nearest(ks, k)
            choices = [{"label": f"{_kstr(x)} {_call_put(right)}", "clause": ed["strike"](_kstr(x))} for x in near if ed["strike"](_kstr(x))]
            flags.append(_flag("STRIKE_NOT_LISTED", f"no {under.symbol} {d} {_kstr(k)} {_call_put(right)} on the cached chain (nearest listed: "
                               f"{', '.join(_kstr(x) for x in near) or 'none'}): nothing is snapped", leg["span"], choices))
            return None
        dd = date.fromisoformat(d)
        if not self._is_trading_day(dd):
            flags.append(_flag("EXPIRY_NOT_LISTED", f"{d} is not a trading day: options expire on trading days", leg["span"],
                               action={"kind": "load_chain", "label": "Load the chain", "instrument_id": uid}))
            return None
        root = root_filter or std
        occ = occ_symbol(root, dd, right, k)
        flags.append(_flag("OPTION_UNVERIFIED", f"{occ} is built from its terms: the {under.symbol} chain is not loaded, so the contract is not confirmed "
                           "listed (the preview refuses it until the chain lists it)", leg["span"],
                           action={"kind": "load_chain", "label": "Load the chain", "instrument_id": uid}))
        return self._opt_out(under, occ, d, right, k, root, std, False, None, None, None)

    def _occ_contract(self, occ: str, leg: dict, span) -> Optional[dict]:
        flags = leg["flags"]
        p = parse_occ(occ)
        if p is None:
            return None
        root, dd, right, k = p
        under = None
        for cand in (ROOT_UNDERLYING.get(root, root), root.replace(".", "-"), root):
            under = self.master.get(cand, self.conn)
            if under is not None:
                break
        if under is None:
            flags.append(_flag("SYMBOL_UNKNOWN", f"{occ}: its underlying {root} is not in the instrument master", span,
                               action={"kind": "resolve", "label": "Look it up", "symbol": ROOT_UNDERLYING.get(root, root)}))
            return None
        d = dd.isoformat()
        std = (under.cboe_symbol or under.symbol or "").replace("_", "").upper()
        snap = self._snap(under.instrument_id)
        chain = self._chain_info(under, snap, leg, under.instrument_id) if snap is not None else None
        inst = self.master.get(occ, self.conn)
        if inst is not None:
            return self._opt_out(under, occ, d, right, k, root, std, True, inst, None, chain)
        row = next((r for r in self._rows(snap, d) if str(r.occ).upper() == occ), None)
        if row is not None:
            return self._opt_out(under, row.occ, d, right, k, root, std, True, None, row, chain)
        if d < self.today.isoformat():
            flags.append(_flag("EXPIRY_NOT_LISTED", f"{occ} expired on {d}", span))
            return None
        if snap is not None:
            flags.append(_flag("STRIKE_NOT_LISTED", f"{occ} is not on the cached {under.symbol} chain: nothing is snapped", span))
            return None
        flags.append(_flag("OPTION_UNVERIFIED", f"{occ} is not confirmed listed: the {under.symbol} chain is not loaded (the preview refuses it until the chain "
                           "lists it)", span, action={"kind": "load_chain", "label": "Load the chain", "instrument_id": under.instrument_id}))
        return self._opt_out(under, occ, d, right, k, root, std, False, None, None, None)

    def _futures_listed(self, root: str) -> List[Any]:
        if self._index is None:
            self._build_index()
        found: Dict[str, Any] = {}
        for lst in self._index.values():
            for inst in lst:
                if inst.kind != "FUTURE" or inst.status != "ACTIVE":
                    continue
                p = parse_futures_id(inst.instrument_id)
                if p and p[0] == root:
                    found[inst.instrument_id] = inst
        return sorted(found.values(), key=lambda i: (i.expiry or "9999-12-31", i.instrument_id))

    def _future_contract(self, c: dict, ia: dict, leg: dict) -> Optional[dict]:
        flags = leg["flags"]
        root = ia.get("root")
        span = ia["span"]
        listed = self._futures_listed(root) if root else []
        choices = [{"label": f"{i.instrument_id} ({i.name})", "clause": self._clause_edit(c, [(span[0], span[1], i.instrument_id)])} for i in listed]
        listed_words = ", ".join(i.instrument_id for i in listed) or "none"
        if ia["kind"] == "future":
            inst = self.master.get(ia["id"], self.conn)
            if inst is None or inst.status != "ACTIVE":
                flags.append(_flag("EXPIRY_NOT_LISTED", f"{ia['id']} is not a listed contract (listed {root}: {listed_words})", leg["span"], choices))
                return None
            return self._inst_contract(inst)
        if ia["kind"] == "future_month":
            m, y = int(ia["month"]), ia.get("year")
            if y is None:
                cal = getattr(self.s, "cal", None)
                months = listed_months(root, self.today, cal) if cal is not None else []
                pick = next(((yy, mm) for yy, mm in months if mm == m), None)
                if pick is None:
                    flags.append(_flag("EXPIRY_NOT_LISTED", f"no {_month_words(m)} {root} contract is listed (listed: {listed_words})", leg["span"], choices))
                    return None
                y = pick[0]
                flags.append(_flag("YEAR_INFERRED", f"{root} {_month_words(m)} read as {futures_id(root, int(y), m)} (the next listed {_month_words(m)})", leg["span"]))
            fid = futures_id(root, int(y), m)
            inst = self.master.get(fid, self.conn)
            if inst is None or inst.status != "ACTIVE":
                flags.append(_flag("EXPIRY_NOT_LISTED", f"{fid} is not a listed contract (listed {root}: {listed_words})", leg["span"], choices))
                return None
            return self._inst_contract(inst)
        if ia["kind"] == "front":
            from .api_products import active_months
            try:
                am = active_months(self.s, self.today).get(root)
            except Exception:   # noqa: BLE001 - the active month cannot be told: said, never guessed
                am = None
            if not am or am.get("active") is None:
                flags.append(_flag("EXPIRY_NOT_LISTED", f"no active {root} contract is listed (listed: {listed_words})", leg["span"], choices))
                return None
            inst = am["active"]
            flags.append(_flag("EXPIRY_FROM_MONTH", f"{ia['word']} {root} = {inst.instrument_id}: {am.get('why') or 'the active month'}", leg["span"], choices))
            return self._inst_contract(inst)
        return None

    def _contract_for(self, c: dict, leg: dict) -> Optional[dict]:
        ia = c.get("instrument")
        if ia is None:
            return None
        kind = ia["kind"]
        if kind == "occ":
            return self._occ_contract(ia["id"], leg, ia["span"])
        if kind in ("future", "future_month", "front"):
            return self._future_contract(c, ia, leg)
        if kind == "pair":
            return self._symbol_contract(ia["symbol"], leg, ia["span"])
        o = c.get("option")
        if o:
            return self._option_contract(ia["symbol"], o["expiry"], o["right"], o["strike"], leg, self._editors(c, "single"), ia["span"])
        return self._symbol_contract(ia["symbol"], leg, ia["span"], bool(ia.get("forced")))

    # ---- accounts --------------------------------------------------------------------------------------------------------------------------
    def _tradeable(self, con: Optional[dict], a: dict) -> bool:
        kind = self._books.account_kind_for(a)
        if kind == "TREASURY":
            return False
        if con is None:
            return True
        inst = con.get("_inst")
        if inst is not None:
            return bool(self.master.tradeable_in(inst, kind))
        return kind in tuple(con.get("_kinds") or OPTION_ACCOUNT_KINDS)

    def _held_words(self, holders: Dict[str, int], con: Optional[dict]) -> str:
        unit = "contracts" if (con or {}).get("kind") == "OPTION" else ("lots" if (con or {}).get("kind") == "FUTURE" else "shares")
        return ", ".join(f"{abs(q):,} {unit} {'long' if q > 0 else 'short'} in {aid}" for aid, q in sorted(holders.items()))

    def _account(self, c: dict, leg: dict, binfo: dict, tree: dict, con: Optional[dict], holders: Optional[Dict[str, int]] = None,
                 aware: bool = False) -> Optional[dict]:
        """R-ACCT-1 / R-ACCT-2, and where the book HOLDS the contract (1.0.7.2 review): with ``aware`` (a side read from the position, a
        closing side, or 'all' / 'half'), a leg goes to the one account of the book that holds the contract - holdings split over several
        accounts, or a named account that does not hold it while another one does, are an ACCOUNT_CHOICE blocker with one choice per
        holding account - so the same contract never resolves to another account by the wording of the quantity."""
        lid = leg["lifetime_id"]
        if lid is None:
            return None
        flags = leg["flags"]
        accts = self._accounts(lid)
        named = self._named_account(c, binfo, tree)
        book = leg["book_name"]
        words = (con or {}).get("words") or leg["instrument"].get("words") or ""
        held = {aid: q for aid, q in (holders or {}).items() if q}
        leg["_holders"] = held
        a = None
        if named is not None:
            leg["_acct_named"] = True
            if named.get("id"):
                a = next((x for x in accts if x["account_id"] == named["id"]), None)
                if a is None:
                    choices = []
                    try:
                        other = self._books.book_code_of(self._books.split_account_id(named["id"])[0])
                    except (ValueError, AttributeError):
                        other = None
                    if other and other in self.book_rows and other != leg["book"]:
                        # fifth review: an order that names its own book has that book phrase replaced (a second one is DUPLICATE_FIELD);
                        # one that names none gets the phrase at its end
                        rep = f"in the {self.vocab.first_word(other)} book"
                        sc = c["mods"].get("scope") or {}
                        bs = sc.get("book_span") if sc.get("books") else None
                        if bs and c["span"][0] <= bs[0] and bs[1] <= c["span"][1]:
                            clause = self._clause_edit(c, [(bs[0], bs[1], rep)])
                        else:
                            clause = self._clause_edit(c, [(c["span"][1], c["span"][1], " " + rep)])
                        choices.append({"label": f"in the {self.book_rows[other].name}", "clause": clause})
                    flags.append(_flag("ACCOUNT_NOT_IN_BOOK", f"account {named['id']} is not an open account of the {book} ({lid})", leg["span"], choices))
                    return None
            elif named.get("role") == "PB":
                pbs = [x for x in accts if x["kind"] == "PB"]
                if not pbs:
                    flags.append(_flag("ACCOUNT_NOT_IN_BOOK", f"the {book} has no prime-broker account", named.get("span")))
                    return None
                a = pbs[0]
                if len(pbs) > 1:
                    inside = bool(named.get("span")) and named["span"][0] >= c["span"][0] and named["span"][1] <= c["span"][1]
                    choices = [{"label": f"{x['name']} ({x['account_id']})",
                                "clause": self._clause_edit(c, [(named["span"][0], named["span"][1], " at " + _account_words_of(x))])} for x in pbs[1:]] if inside else []
                    flags.append(_flag("ACCOUNT_CHOICE", f"{a['name']} picked; the {book} has other prime-broker accounts: "
                                       + ", ".join(x["account_id"] for x in pbs[1:]), leg["span"], choices))
            else:
                a = next((x for x in accts if x["role"] == named["role"]), None)
                if a is None:
                    flags.append(_flag("ACCOUNT_NOT_IN_BOOK", f"the {book} has no {named.get('text') or named['role']} account", named.get("span")))
                    return None
            if con is not None and not self._tradeable(con, a):
                flags.append(_flag("NOT_TRADEABLE_HERE", f"{words} does not trade in {a['name']} ({a['account_id']})", named.get("span")))
            leg["inferred"].append(f"account {a['account_id']} as named")
            if aware and held and a["account_id"] not in held:
                hold_rows = [x for x in accts if x["account_id"] in held]
                # fifth review: the account phrase is replaced whole, its "at" / "in" with it (never "at at pb ms")
                choices = [{"label": f"{x['name']} ({x['account_id']})", "clause": self._clause_edit(c, [(named["span"][0], named["span"][1], " at " + _account_words_of(x))])}
                           for x in hold_rows] if named.get("span") and named["span"][0] >= c["span"][0] and named["span"][1] <= c["span"][1] else []
                flags.append(_flag("ACCOUNT_CHOICE", f"the {book} holds {words} in another account ({self._held_words(held, con)}), not in {a['account_id']}: "
                                   "name the account that holds it, or write an opening side (bto, sto, short) to open a new position here", leg["span"],
                                   choices or None, severity="blocker"))
        else:
            dflt = next((x for x in accts if x["account_id"] == self.default_account), None) if self.default_account else None
            if dflt is not None and self._tradeable(con, dflt) and not (aware and held and dflt["account_id"] not in held):
                a = dflt
                leg["inferred"].append(f"account {a['account_id']}: the page's default account")
            else:
                if con is None:
                    return None
                cands = [x for x in accts if self._tradeable(con, x)]
                if not cands:
                    flags.append(_flag("NOT_TRADEABLE_HERE", f"{words} does not trade in any open account of the {book}", leg["span"]))
                    return None
                hold = [x for x in cands if x["account_id"] in held]
                if len(hold) == 1 and aware:
                    a = hold[0]
                    leg["inferred"].append(f"R-ACCT-1: {a['account_id']}, the account that holds it")
                    others = [x for x in cands if x is not a]
                    if aware and others:
                        choices = [{"label": f"{x['name']} ({x['account_id']})",
                                    "clause": self._clause_edit(c, [(c["span"][1], c["span"][1], " at " + _account_words_of(x))])} for x in others]
                        flags.append(_flag("ACCOUNT_CHOICE", f"{a['name']} picked: it holds {words} ({self._held_words(held, con)}); the {book} also trades it in "
                                           + ", ".join(x["name"] for x in others), leg["span"], choices))
                elif len(hold) > 1 and aware:
                    a = hold[0]
                    choices = [{"label": f"{x['name']} ({x['account_id']})", "clause": self._clause_edit(c, [(c["span"][1], c["span"][1], " at " + _account_words_of(x))])}
                               for x in hold]
                    flags.append(_flag("ACCOUNT_CHOICE", f"the {book} holds {words} in {len(hold)} accounts ({self._held_words(held, con)}): name the account",
                                       leg["span"], choices, severity="blocker"))
                else:
                    # R-ACCT-1: US Exchange first.  Second review: an explicit opening side (bto, sto, short) opens where this rule puts it,
                    # never silently in the account that holds the contract - that account is offered in the choices, with what it holds
                    us = [x for x in cands if x["role"] == "US_EXCHANGE"]
                    a = (us or cands)[0]
                    leg["inferred"].append(f"R-ACCT-1: {a['account_id']}")
                    if len(cands) > 1:
                        others = [x for x in cands if x is not a]
                        choices = [{"label": f"{x['name']} ({x['account_id']})",
                                    "clause": self._clause_edit(c, [(c["span"][1], c["span"][1], " at " + _account_words_of(x))])} for x in others]
                        names = ", ".join(x["name"] + (f" (it holds {self._held_words({x['account_id']: held[x['account_id']]}, con)})" if x["account_id"] in held else "")
                                          for x in others)
                        flags.append(_flag("ACCOUNT_CHOICE", f"{a['name']} picked; {words} also trades in {names}", leg["span"], choices))
        leg["account_id"], leg["account_name"] = a["account_id"], a["name"]
        return a

    # ---- quantity, side, price ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _cls(con: dict) -> str:
        k = con.get("kind")
        return "option" if k == "OPTION" else ("future" if k == "FUTURE" else "shares")

    def _qty(self, c: dict, leg: dict, con: dict, pos: Optional[dict], ratio: int = 1) -> Optional[int]:
        flags = leg["flags"]
        qa = c.get("qty")
        if qa is None:
            return None
        lot = max(1, int(con.get("lot") or 1))
        if qa["kind"] in ("all", "half"):
            if pos is None:
                return None
            held = abs(int(pos["qty"]))
            if held == 0:
                flags.append(_flag("NOTHING_HELD", f"'{qa['text']}': no {con.get('words')} held in {leg['account_id']}", qa["span"]))
                return None
            what = "held" if int(pos["qty"]) > 0 else "short"
            if qa["kind"] == "all":
                leg["inferred"].append(f"all = the {held:,} held")
                q = held
                flags.append(_flag("QTY_FROM_HELD", f"all = the {held:,} {what} in {leg['account_id']}", qa["span"]))
            else:
                q = (held // 2) // lot * lot
                if Decimal(held) / 2 != q:
                    flags.append(_flag("QTY_ROUNDED", f"half of {held:,} = {q:,} (rounded down to the lot of {lot:,})", qa["span"]))
                else:
                    flags.append(_flag("QTY_FROM_HELD", f"half = {q:,} of the {held:,} {what} in {leg['account_id']}", qa["span"]))
                if q == 0:
                    flags.append(_flag("NOTHING_HELD", f"half of {held:,} is 0", qa["span"]))
                    return None
                leg["inferred"].append(f"half = {q:,} of the {held:,} held")
        else:
            q = qa.get("value")
            if q is None or q <= 0:
                return None
        unit = qa.get("unit")
        cls = self._cls(con)
        if unit and unit not in UNITS_OK[cls]:
            choice = _edit(self.text, c["span"], [(qa["unit_span"][0], qa["unit_span"][1], "")]) if qa.get("unit_span") else None
            flags.append(_flag("UNIT_KIND_MISMATCH", f"'{qa.get('unit_word') or unit}' with {KIND_WORDS.get(con.get('kind'), 'this kind')}: it trades in "
                               + {"option": "contracts", "future": "contracts", "shares": "shares"}[cls], leg["span"] if choice else qa["span"],
                               [{"label": "without the unit word", "clause": choice}] if choice else None))
        q = q * int(ratio)
        if q % lot:
            lo, hi = q // lot * lot, (q // lot + 1) * lot
            choices = [{"label": f"{x:,}", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(x))])} for x in (lo, hi) if x > 0 and ratio == 1]
            flags.append(_flag("QTY_LOT", f"{q:,} is not a multiple of the lot {lot:,}", leg["span"] if choices else qa["span"], choices))
            return None
        return q

    def _side(self, c: dict, leg: dict, con: dict, pos: Optional[dict], q: Optional[int]) -> Optional[str]:
        flags = leg["flags"]
        v = c.get("verb") or {}
        typed = v.get("side")
        cls = self._cls(con)
        words = con.get("words") or ""
        if typed is None:
            return None
        if typed not in ("buy", "sell"):
            allowed = OPTION_SIDES if cls == "option" else (FUTURES_SIDES if cls == "future" else CASH_SIDES)
            if cls == "shares" and typed in ("SELL_SHORT", "STO") and pos is not None and int(pos["qty"]) > 0:
                # 1.0.7.2 simplification (INV-4): the order route refuses a short sale while the account holds the shares long - they leave
                # only through a sale, which keeps the cover of calls; the sale of the free shares is offered
                held_l, n = int(pos["qty"]), min(int(q or 0), int(pos["free"]))
                ch = [{"label": f"sell {n:,} (sell the shares held first)", "clause": self._side_edit(c, "sell", n, leg, con)}] if n > 0 else None
                flags.append(_flag("SHORT_WHILE_LONG", f"'{v.get('word')}' {words}: {leg['account_id']} holds {held_l:,} long - a short sale while the shares are"
                                   " held is refused: sell them first (once they have sold, a short sale can follow)", leg["span"], ch))
                return typed if typed in allowed else None
            if typed in allowed:
                return typed
            # fourth review: "sto" (sell to open) on shares is a short sale; an alternative that reads the account another way than the typed
            # side (an opening side: R-ACCT-1; "buy" / "sell": the account that holds it) names the account the leg was read in, and says
            # when it would close a holding there
            alt = {("option", "SELL_SHORT"): "sto", ("option", "BUY_TO_COVER"): "btc", ("shares", "BTO"): "buy", ("shares", "BTC"): "buy",
                   ("shares", "STO"): "short", ("shares", "STC"): "sell", ("future", "SELL_SHORT"): "sell", ("future", "BUY_TO_COVER"): "buy",
                   ("future", "BTO"): "buy", ("future", "BTC"): "buy", ("future", "STO"): "sell", ("future", "STC"): "sell"}.get((cls, typed))
            choices = None
            if alt:
                clause = self._side_edit(c, alt, None, leg, con)
                if typed in ("BTO", "STO", "SELL_SHORT") and alt not in OPENING_WORDS:
                    clause = self._pin_account(clause, leg)
                label = alt
                held_here = int(pos["qty"]) if pos is not None and cls == "shares" else 0
                if cls == "shares" and typed == "STO":
                    label = "sell short (sto on shares opens a short)"
                elif alt == "buy" and held_here < 0:
                    label = f"buy (covers the {-held_here:,} short in {leg['account_id']})"
                elif alt == "sell" and held_here > 0:
                    label = f"sell (sells the {held_here:,} held in {leg['account_id']})"
                choices = [{"label": label, "clause": clause}]
            flags.append(_flag("MISSING_SIDE", f"'{v.get('word')}' is not a side for {KIND_WORDS.get(con.get('kind'), 'this kind')}: "
                               + {"option": "bto, sto, btc or stc", "future": "buy or sell", "shares": "buy, sell, short or cover"}[cls], leg["span"], choices))
            return None
        if cls == "future":
            return "BUY" if typed == "buy" else "SELL"
        if pos is None or q is None:
            return None
        held, free = int(pos["qty"]), int(pos["free"])
        acct = leg["account_id"]
        elsewhere = {aid: n for aid, n in (leg.get("_holders") or {}).items() if aid != acct}
        where = f"; the {leg['book_name']} holds {self._held_words(elsewhere, con)}" if elsewhere else ""

        def closing(word: str, alt: Optional[str]) -> None:
            # 1.0.7.2 review (R2): every unit of the position is already in working closing orders - "buy" / "sell" is never silently turned
            # into an order that opens a new position; an explicit opening side is offered instead
            what = "short" if held < 0 else "held"
            ch = [{"label": f"{alt} {q:,} (open a new position)", "clause": self._side_edit(c, alt, None, leg, con)}] if alt else None
            flags.append(_flag("ALREADY_CLOSING", f"{word} {q:,}: the {abs(held):,} {what} in {acct} are already in working closing orders (Pending Trades), so "
                               f"this would open a new position, not close it" + ("" if alt else ": change or cancel the working order first"), leg["span"], ch))
        if cls == "option":
            if typed == "buy":
                if held < 0 and free >= q:
                    flags.append(_flag("SIDE_INFERRED", f"buy read as buy to close: {-held:,} short held in {acct}", leg["span"]))
                    return "BTC"
                if held < 0 and free > 0:
                    flags.append(_flag("SIDE_SPLIT", f"buy {q:,} but {free:,} short held: close them and open the rest, or close only", leg["span"],
                                       [{"label": f"btc {free:,} + bto {q - free:,}",
                                         "clause": self._side_edit(c, "btc", free, leg, con) + " and " + self._side_edit(c, "bto", q - free, leg, con)},
                                        {"label": f"btc {free:,} only", "clause": self._side_edit(c, "btc", free, leg, con)}]))
                    return None
                if held < 0:
                    closing("buy", "bto")
                    return None
                return "BTO"
            if held > 0 and free >= q:
                flags.append(_flag("SIDE_INFERRED", f"sell read as sell to close: {held:,} held in {acct}", leg["span"]))
                return "STC"
            if held > 0 and free > 0:
                flags.append(_flag("SIDE_SPLIT", f"sell {q:,} but {free:,} held: close them and open the rest, or close only", leg["span"],
                                   [{"label": f"stc {free:,} + sto {q - free:,}",
                                     "clause": self._side_edit(c, "stc", free, leg, con) + " and " + self._side_edit(c, "sto", q - free, leg, con)},
                                    {"label": f"stc {free:,} only", "clause": self._side_edit(c, "stc", free, leg, con)}]))
                return None
            if held > 0:
                closing("sell", "sto")
                return None
            flags.append(_flag("SIDE_INFERRED", "sell read as sell to open (" + (f"none held in {acct}" if held == 0 else f"adds to the {-held:,} short in {acct}")
                               + where + "): opens a short option; its margin is in the preview", leg["span"]))
            return "STO"
        if typed == "buy":
            if held < 0 and free >= q:
                flags.append(_flag("SIDE_INFERRED", f"buy read as buy to cover: {-held:,} short held in {acct}", leg["span"]))
                return "BUY_TO_COVER"
            if held < 0 and free > 0:
                # fifth review: shares have no explicit side that opens a long ("buy" read here is a cover while the short is held), so a
                # split "cover N + buy M" would stage a second cover: the cover alone is offered, the rest is bought once it has filled
                flags.append(_flag("SIDE_SPLIT", f"buy {q:,} but {free:,} short held: cover them first; the other {q - free:,} can be bought once the cover"
                                   " has filled", leg["span"],
                                   [{"label": f"cover {free:,} only (buy the other {q - free:,} once the cover has filled)",
                                     "clause": self._side_edit(c, "cover", free, leg, con)}]))
                return None
            if held < 0:
                closing("buy", None)
                return None
            return "BUY"
        # a sale takes the free shares only: net of working sells, of the shares covering calls sold or being sold and of lent shares
        # (``_position`` reads ``cover_state``, as the order route does)
        if held > 0 and free >= q:
            return "SELL"
        free_long = free if held > 0 else 0
        choices = []
        if free_long > 0:
            choices.append({"label": f"sell {free_long:,}", "clause": self._side_edit(c, "sell", free_long, leg, con)})
        elif held <= 0:
            # none held long: the sale reads as a short sale (the order route refuses one only while shares are held long)
            choices.append({"label": f"short {q:,}", "clause": self._side_edit(c, "short", q, leg, con)})
        why = []
        if pos["working_sells"]:
            why.append(f"{pos['working_sells']:,} in working sells")
        if pos["reserved"]:
            why.append(f"{pos['reserved']:,} covering calls already sold")
        if pos.get("calls_working"):
            why.append(f"{int(pos['calls_working']):,} covering calls being sold (working orders)")
        if pos["lent"]:
            why.append(f"{pos['lent']:,} lent")
        flags.append(_flag("WOULD_OVERSELL", f"sell {q:,} {words} but the free quantity in {acct} is {free_long:,} (held {held:,}"
                           + ("; " + ", ".join(why) if why else "") + ")", leg["span"], choices or None))
        return None

    def _quote_price(self, which: str, leg: dict, con: dict) -> Optional[str]:
        flags = leg["flags"]
        if con.get("kind") == "OPTION":
            row = con.get("_row")
            if row is None:
                snap = self._snap(con.get("underlying_id"))
                row = next((r for r in self._rows(snap, con["expiry"]) if str(r.occ).upper() == con["id"]), None)
            px = getattr(row, which, None) if row is not None else None
            if px is None:
                flags.append(_flag("MISSING_PRICE", f"no {which} for {con['words']} on a cached chain: type a limit", leg["span"],
                                   action={"kind": "load_chain", "label": "Load the chain", "instrument_id": con.get("underlying_id")}))
                return None
            ch = con.get("chain") or {}
            when = self._ny_of(int(ch["quote_time"])).strftime("%H:%M:%S ET") if ch.get("quote_time") else "no provider time"
            flags.append(_flag("PRICE_FROM_QUOTE", f"limit = the chain's {which} {px} (Cboe, {ch.get('quality') or 'unknown quality'}, {when})", leg["span"]))
            return str(px)
        cache = getattr(self.s, "cache", None)
        getter = getattr(cache, "get", None) if cache is not None else None
        rec = getter(con["id"]) if callable(getter) else None
        px = getattr(rec, which, None) if rec is not None else None
        if px is None:
            flags.append(_flag("MISSING_PRICE", f"no {which} cached for {con.get('symbol')} yet: type a limit", leg["span"]))
            return None
        qt = getattr(rec, "quote_time", None)
        when = self._ny_of(int(qt)).strftime("%H:%M:%S ET") if qt else "no provider time"
        flags.append(_flag("PRICE_FROM_QUOTE", f"limit = the {which} {px} ({getattr(rec, 'provider', None) or 'quote'}, {getattr(rec, 'quality', None) or 'unknown'}, {when})",
                           leg["span"]))
        return str(px)

    def _price_tif(self, c: dict, leg: dict, con: Optional[dict], market_only: bool = False) -> None:
        flags = leg["flags"]
        p = None if market_only else c["mods"].get("price")
        if market_only and c["mods"].get("price") is not None:
            # the price of a strategy, roll, collar or buy write is never applied to a leg (its group refuses it: NET_PRICE), so the leg
            # never says it was "read as a limit" (1.0.7.2 review)
            leg["flags"] = flags = [f for f in flags if f["code"] != "TYPE_FROM_AT"]
        if p is None:
            leg["order_type"] = "MARKET"
            if not market_only and con is not None:
                side = leg.get("side") or {"buy": "BTO", "sell": "STO"}.get(str((c.get("verb") or {}).get("side")), "")
                what = (f"the next executable Cboe {'ask' if side in ('BTO', 'BTC') else 'bid'}" if self._cls(con) == "option" else "the next executable delayed print")
                flags.append(_flag("TYPE_DEFAULT", f"no price: a market order (fills at {what})", leg["span"]))
            leg["inferred"].append("R-TYPE: MARKET")
        else:
            t = p["type"]
            leg["order_type"] = t
            if not p.get("bad"):
                if t == "LIMIT":
                    px = p.get("limit")
                    if p.get("quote"):
                        px = self._quote_price(p["quote"], leg, con) if con is not None else None
                    leg["limit_px"] = px
                elif t == "STOP":
                    leg["stop_px"] = p.get("stop")
                elif t == "STOP_LIMIT":
                    leg["stop_px"], leg["limit_px"] = p.get("stop"), p.get("limit")
                elif t == "TRAILING_STOP":
                    leg["trail_amt"], leg["trail_pct"] = p.get("trail_amt"), p.get("trail_pct")
                for key in ("limit_px", "stop_px", "trail_amt", "trail_pct"):
                    v = leg[key]
                    if v is not None and (_dec(v) is None or _dec(v) <= 0):
                        flags.append(_flag("MISSING_PRICE", f"{key.replace('_px', '').replace('_', ' ')} {v} must be above 0", p["span"]))
                if t == "TRAILING_STOP" and leg["trail_pct"] is not None and (_dec(leg["trail_pct"]) or 0) >= 100:
                    flags.append(_flag("MISSING_PRICE", "a trail percent is below 100", p["span"]))
        tf = c["mods"].get("tif")
        if tf is None:
            leg["tif"] = "DAY"
            flags.append(_flag("TIF_DEFAULT", "time in force DAY (none given)"))
            leg["inferred"].append("R-TIF: DAY")
        else:
            leg["tif"] = tf["tif"]
            if tf["tif"] == "GTD" and not tf.get("bad"):
                leg["gtd_date"] = self._gtd(tf["date"], leg, tf["span"])

    def _gtd(self, d: dict, leg: dict, span) -> Optional[str]:
        if d.get("date"):
            return d["date"]
        m, dd, y = int(d["m"]), int(d["d"]), d.get("y")
        if y is not None:
            try:
                return date(int(y), m, dd).isoformat()
            except ValueError:
                leg["flags"].append(_flag("UNPARSED_TEXT", f"{_month_words(m)} {dd} {y} is not a date", span))
                return None
        for yy in (self.today.year, self.today.year + 1):
            try:
                t = date(yy, m, dd)
            except ValueError:
                continue
            if t >= self.today:
                leg["flags"].append(_flag("YEAR_INFERRED", f"until {_month_words(m)} {dd} read as {t.isoformat()} (the first on or after today)", span))
                return t.isoformat()
        leg["flags"].append(_flag("UNPARSED_TEXT", f"{_month_words(m)} {dd} is not a date", span))
        return None

    def _own_flag(self, c: dict, leg: dict, con: dict, q: Optional[int]) -> None:
        tkind, sentence = con["_own"]
        inst = con["_inst"]
        lid = leg["lifetime_id"]
        role = OWN_TICKET_ROLE.get(tkind)
        acct = None
        if lid:
            acct = next((a["account_id"] for a in self._accounts(lid) if (role and a["role"] == role) or (tkind == "OTC" and a["kind"] == "PB")), None)
        ticket: Dict[str, Any] = {"kind": tkind, "account_id": acct, "instrument_id": inst.instrument_id}
        typed = (c.get("verb") or {}).get("side")
        if tkind == "FX":
            spec = inst.spec if isinstance(inst.spec, dict) else {}
            base, quote = spec.get("base") or inst.instrument_id[:3], spec.get("quote") or inst.instrument_id[3:]
            buy_ccy, sell_ccy = (quote, base) if typed in ("sell", "SELL_SHORT") else (base, quote)
            ticket.update({"buy_ccy": buy_ccy, "sell_ccy": sell_ccy, "amount": None if q is None else str(q)})
        else:
            ticket.update({"side": "SELL" if typed in ("sell", "SELL_SHORT") else "BUY", "qty": q})
        leg["flags"].append(_flag("KIND_OWN_TICKET", f"{inst.name or inst.symbol} is {sentence}", leg["span"],
                                  action={"kind": "open_ticket", "label": OWN_TICKET_LABEL.get(tkind, "Open its ticket"), "ticket": ticket}))
        if acct:
            a = next(x for x in self._accounts(lid) if x["account_id"] == acct)
            leg["account_id"], leg["account_name"] = a["account_id"], a["name"]

    # ---- request bodies ---------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _blocked(flags: Sequence[dict]) -> bool:
        return any(f["severity"] == "blocker" for f in flags)

    def _request(self, leg: dict) -> Optional[dict]:
        if self._blocked(leg["flags"]):
            return None
        if not (leg["account_id"] and leg["instrument"].get("id") and leg["side"] and leg["qty"] and leg["order_type"] and leg["tif"]):
            return None
        req = {"account_id": leg["account_id"], "instrument_id": leg["instrument"]["id"], "side": leg["side"], "qty": int(leg["qty"]), "order_type": leg["order_type"],
               "tif": leg["tif"]}
        t = leg["order_type"]
        if t in ("LIMIT", "STOP_LIMIT"):
            if leg["limit_px"] is None:
                return None
            req["limit_px"] = leg["limit_px"]
        if t in ("STOP", "STOP_LIMIT"):
            if leg["stop_px"] is None:
                return None
            req["stop_px"] = leg["stop_px"]
        if t == "TRAILING_STOP":
            if leg["trail_amt"] is not None:
                req["trail_amt"] = leg["trail_amt"]
            elif leg["trail_pct"] is not None:
                req["trail_pct"] = leg["trail_pct"]
            else:
                return None
        if leg["tif"] == "GTD":
            if not leg["gtd_date"]:
                return None
            req["gtd_date"] = leg["gtd_date"]
        if leg["side"] == "SELL_SHORT":
            req["borrow"] = True
        if leg.get("_claim"):
            # seventh review: calls said to be covered by shares held - the order route places them only while those shares are still
            # free (409 NOT_COVERED, nothing placed), so shares sold between the reading and the send never leave them uncovered
            req["covered"] = True
        return req

    # ---- the clause kinds ----------------------------------------------------------------------------------------------------------------
    def _order_leg(self, ci: int, c: dict, binfo: dict, tree: dict) -> dict:
        ia = c.get("instrument") or {}
        leg = self._leg_base(ci, c, binfo, ia.get("symbol") or ia.get("id"))
        con = self._contract_for(c, leg) if leg["book"] is not None else None
        self._set_instrument(leg, con)
        qa = c.get("qty") or {}
        if con is not None and con.get("_own"):
            q = qa.get("value") if qa.get("kind") == "num" else None
            self._own_flag(c, leg, con, q)
            leg["qty"] = q
            return leg
        typed = (c.get("verb") or {}).get("side")
        # where the book holds the contract decides the account whatever the quantity's wording (1.0.7.2 review): read for every leg
        holders = self._holders(leg["lifetime_id"], con["id"]) if con is not None and con.get("id") and not con.get("_blocked") else None
        aware = typed not in ("BTO", "STO", "SELL_SHORT") or qa.get("kind") in ("all", "half")
        a = self._account(c, leg, binfo, tree, con, holders, aware) if not (con or {}).get("_blocked") else None
        pos = None
        if a is not None and con is not None and con.get("id"):
            pos = self._position(a["account_id"], con["id"], self._cls(con) == "shares")
            leg["position"] = pos
        if con is not None:
            q = self._qty(c, leg, con, pos)
            leg["qty"] = q
            leg["side"] = self._side(c, leg, con, pos, q)
            self._adds_check(c, leg, con, pos)
            self._price_tif(c, leg, con)
        else:
            self._price_tif(c, leg, None)
        return leg

    def _adds_check(self, c: dict, leg: dict, con: dict, pos: Optional[dict]) -> None:
        """'all' / 'half' take the HELD quantity to close it: a side that would ADD to the position (buy while long, sell / short / sto
        while short, bto while long) is the blocker ADDS_TO_POSITION with 'close' as the choice (1.0.7.2 review)."""
        qa = c.get("qty") or {}
        if qa.get("kind") not in ("all", "half") or pos is None or not leg.get("side"):
            return
        held, side = int(pos["qty"]), leg["side"]
        adds = (held > 0 and side in ("BUY", "BTO")) or (held < 0 and side in ("SELL", "SELL_SHORT", "STO"))
        if not adds:
            return
        v = c.get("verb") or {}
        if v.get("span") and not v.get("carried"):
            choice = self._clause_edit(c, [(v["span"][0], v["span"][1], "close")])
        else:
            choice = self._clause_edit(c, [(c["span"][0], c["span"][0], "close ")])
        leg["flags"].append(_flag("ADDS_TO_POSITION", f"'{qa['text']}' takes the {abs(held):,} {'held' if held > 0 else 'short'} in {leg['account_id']}: "
                                  f"{SIDE_TEXT.get(side, side)} {leg['qty'] or 0:,} would add to the position, not close it", leg["span"],
                                  [{"label": "close them", "clause": choice}]))

    def _resolve_date(self, exp: Optional[dict], snap=None) -> Optional[str]:
        """An expiry's date the way ``_expiry`` reads it, without its flags: a date; a day with its year, or the first on or after today
        without one; a month with its year through the cached chain ``snap`` when it lists exactly one date that month (second review).
        None when it cannot be known: a month without a year or a chain, or a day that is not a date."""
        if not exp:
            return None
        if exp.get("date"):
            return str(exp["date"])
        if exp.get("d") is None:
            y = exp.get("y")
            if y is None or snap is None or exp.get("m") is None:
                return None
            try:
                listed = [e for e in snap.expiries() if str(e)[:7] == f"{int(y):04d}-{int(exp['m']):02d}"]
            except Exception:   # noqa: BLE001 - a chain that cannot list its expiries gives no date (the calendar is then refused)
                return None
            return str(listed[0]) if len(listed) == 1 else None
        m, d, y = int(exp["m"]), int(exp["d"]), exp.get("y")
        for yy in ((int(y),) if y else (self.today.year, self.today.year + 1)):
            try:
                t = date(yy, m, d)
            except ValueError:
                continue
            if y or t >= self.today:
                return t.isoformat()
        return None

    def _calendar_order(self, c: dict) -> None:
        """A calendar sells the NEAR expiry on a buy: near and far are told apart once both dates are known (the year inferred from today
        the way each leg reads it), never by the typed spelling ('Dec 18/Jan 15 2027', 'Nov 20/Dec 18'): the legs, and the words of
        STRATEGY_EXPANDED, are put right before anything is resolved (1.0.7.2 review)."""
        s = c["strategy"]
        exps = s.get("expiries") or []
        if s.get("name") != "calendar" or len(exps) != 2 or len(c.get("legs") or []) != 2:
            return
        # second review: both dates are read as each leg reads its own (a month alone through the cached chain's one date that month);
        # when either cannot be known the calendar is refused (``_strategy_legs``), never left in the pure layer's typed order
        snap = self._snap(self._calendar_uid(c))
        da, db = self._resolve_date(exps[0], snap), self._resolve_date(exps[1], snap)
        if not da or not db:
            c["_calendar_unknown"] = True
            return
        if da == db:
            return
        near, far = (exps[0], exps[1]) if da < db else (exps[1], exps[0])
        if c["legs"][0].get("expiry") == near and c["legs"][1].get("expiry") == far:
            return
        c["legs"][0]["expiry"], c["legs"][1]["expiry"] = near, far
        old = f"{s['name']}: "
        c["flags"] = [_flag("STRATEGY_EXPANDED", f"{s['name']}: " + _expansion_words(c, c["legs"]), c["span"]) if f["code"] == "STRATEGY_EXPANDED"
                      and f["text"].startswith(old) else f for f in c["flags"]]

    def _calendar_uid(self, c: dict) -> Optional[str]:
        """The underlying id of a calendar typed on a ticker (read only: the master's exact match, as ``_underlying`` reads it)."""
        ia = c.get("instrument") or {}
        if ia.get("kind") != "ticker":
            return None
        sym = str(ia.get("symbol") or "").upper()
        hits = self._lookup(ROOT_UNDERLYING.get(sym, sym))
        return hits[0].instrument_id if len(hits) == 1 else None

    def _strategy_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        s = c["strategy"]
        ia = c.get("instrument") or {}
        out = []
        self._calendar_order(c)
        for al in c["legs"]:
            leg = self._leg_base(ci, c, binfo, ia.get("symbol"))
            idx = s["strikes"].index(al["strike"]) if al.get("strike") in s["strikes"] else None
            eidx = s["expiries"].index(al["expiry"]) if s["name"] == "calendar" and al.get("expiry") in s["expiries"] else 0
            ed = self._editors(c, "strategy", idx, eidx)
            con = None
            if ia.get("kind") == "ticker" and leg["book"] is not None:
                con = self._option_contract(ia["symbol"], al["expiry"], al["right"], al["strike"], leg, ed, ia["span"])
            self._set_instrument(leg, con)
            a = self._account(c, leg, binfo, tree, con)
            if a is not None and con is not None and con.get("id"):
                leg["position"] = self._position(a["account_id"], con["id"], False)
            if con is not None:
                leg["qty"] = self._qty(c, leg, con, leg["position"], al.get("ratio", 1))
            leg["side"] = al["side"]
            self._price_tif(c, leg, con, market_only=True)
            out.append(leg)
        if c.get("_calendar_unknown") and out and not any(self._blocked(l["flags"]) for l in out):
            for leg in out:
                leg["flags"].append(_flag("EXPIRY_MONTH_ONLY", "a calendar sells the near expiry and buys the far one: both expiries must be dates FinSim can read"
                                          " (a day, or a month the cached chain lists once) before the near one can be told from the far one", leg["span"]))
        return out

    def _covered_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        ia = c.get("instrument") or {}
        o = c.get("option") or {}
        qa = c.get("qty") or {}
        q = qa.get("value") if qa.get("kind") == "num" else None
        shares = self._leg_base(ci, c, binfo, ia.get("symbol"))
        con = self._symbol_contract(ia["symbol"], shares, ia["span"]) if ia.get("kind") == "ticker" and shares["book"] is not None else None
        if con is not None and con.get("_own"):
            self._own_flag(c, shares, con, q)
            con = None
        self._set_instrument(shares, con)
        # 1.0.7.2 review: "covered call" reads the shares held (as the collar does): calls written on shares the book holds are the calls
        # only; "buy write" is the explicit purchase of the shares (said as a check when shares are held)
        buy_write = str((c.get("verb") or {}).get("word") or "").replace("-", " ") == "buy write"
        holders = self._holders(shares["lifetime_id"], con["id"]) if con is not None else None
        a = self._account(c, shares, binfo, tree, con, holders, aware=not buy_write)
        free = held_qty = 0
        if a is not None and con is not None:
            shares["position"] = self._position(a["account_id"], con["id"], True)
            held_qty = int(shares["position"]["qty"])
            # free to cover NEW calls (``free_for_calls``): net of working sells and short sales, of the calls sold or being sold and of
            # lent shares - the account as it is; the order route checks it again at Send (NOT_COVERED)
            free = self._cover_free(shares["position"])
        shares["side"] = "BUY"
        sym = ia.get("symbol")
        uflag = self._share_unit(c, "buy write" if buy_write else "covered call")
        # second review: the SIGNED holding decides, never only the free long shares - a short holding or shares held but not free (in
        # working sells, reserved by calls, lent) are said, never read as "none held" (which buys the shares)
        covered_by_held = (uflag is None and not buy_write and q is not None and not q % 100 and con is not None and a is not None and held_qty > 0
                           and free >= q)
        if uflag is not None:
            shares["flags"].append(uflag)                 # fourth review: contracts / lots are never read as shares
        elif q is not None and q % 100:
            lo, hi = q // 100 * 100, (q // 100 + 1) * 100
            choices = [{"label": f"{x:,} shares", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(x))])} for x in (lo, hi) if x > 0]
            shares["flags"].append(_flag("QTY_LOT", f"a covered call covers 100 shares per contract: {q:,} is not a multiple of 100", c["span"], choices))
        elif con is not None and a is not None and q and held_qty < 0:
            # round 8: a buy write while the account is short the name buys shares that are not held long - its calls would be naked
            what = "buy write" if buy_write else "covered call"
            shares["flags"].append(_flag("NOTHING_HELD", f"{what} on {q:,} {sym}: the {shares['book_name']} is short {-held_qty:,} {sym} in {a['account_id']} - a {what}"
                                         " sells calls on shares held long" + (" (the shares it buys would not be held long): cover the short first" if buy_write else ""),
                                         c["span"]))
        elif not buy_write and con is not None and a is not None and q and held_qty > 0 and free < q:
            lots = free // 100 * 100
            choices = ([{"label": f"covered call on the {lots:,} held free", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(lots))])}] if lots > 0 else [])
            v = c.get("verb") or {}
            if v.get("span"):
                # fourth review: a buy write reads its account as an opening side (R-ACCT-1); it names the account this covered call was read in
                bw = self._clause_edit(c, [(v["span"][0], v["span"][1], "buy write")])
                if not shares.get("_acct_named") and self._opening_pick(shares, con) != a["account_id"]:
                    bw += " at " + _account_words_of(a)
                choices.append({"label": f"buy write {q:,} (buy the shares)", "clause": bw})
            shares["flags"].append(_flag("SIDE_SPLIT", f"covered call on {q:,} {sym} but {free:,} of the {held_qty:,} {sym} held in {a['account_id']} are free"
                                         + self._not_free_words(shares["position"], calls=True) + ": write calls on the free round lots, or buy the shares with the calls",
                                         c["span"], choices or None))
        elif con is not None and q and not covered_by_held:
            shares["qty"] = self._qty(c, shares, con, shares["position"])
            if buy_write and held_qty > 0:
                shares["flags"].append(_flag("SIDE_INFERRED", f"buy write: buys {q:,} {sym} although {held_qty:,} are held in {a['account_id']} ({free:,} free)"
                                             " (write 'covered call' to write calls on the shares held)", shares["span"]))
            elif not buy_write and a is not None:
                shares["flags"].append(_flag("SIDE_INFERRED", f"no {sym} held in {a['account_id']}: the covered call buys the {q:,} shares, then sells the calls"
                                             " as covered (a buy write: two tickets)", shares["span"]))
        self._price_tif(c, shares, con, market_only=True)
        if shares["qty"] and shares["order_type"] == "MARKET":
            shares["flags"].append(_flag("TYPE_DEFAULT", "no price: the shares at market (fills at the next executable delayed print)", shares["span"]))
        call = self._leg_base(ci, c, binfo, ia.get("symbol"))
        con2 = None
        if ia.get("kind") == "ticker" and o and call["book"] is not None:
            con2 = self._option_contract(ia["symbol"], o.get("expiry"), "C", o.get("strike"), call, self._editors(c, "covered"), ia["span"])
        self._set_instrument(call, con2)
        if a is not None:
            call["account_id"], call["account_name"] = a["account_id"], a["name"]
        if call["account_id"] and con2 is not None:
            call["position"] = self._position(call["account_id"], con2["id"], False)
        call["side"] = "STO"
        if q is not None and not q % 100:
            call["qty"] = q // 100
        elif q is not None:
            call["flags"].extend(f for f in shares["flags"] if f["code"] == "QTY_LOT")
        call["flags"].extend(f for f in shares["flags"] if f["code"] in ("SIDE_SPLIT", "NOTHING_HELD") or f is uflag)
        if covered_by_held:
            # the calls only, an order of their own (no group: nothing to wait for); a typed price is the calls' own limit
            call["flags"].extend(f for f in shares["flags"] if f["code"] in ("ACCOUNT_CHOICE", "ACCOUNT_NOT_IN_BOOK", "NOT_TRADEABLE_HERE") and f not in call["flags"])
            call["inferred"].extend(x for x in shares["inferred"] if x.startswith(("R-ACCT", "account")))
            call["flags"].append(_flag("SIDE_INFERRED", f"covered call on {q:,} of the {free:,} {ia.get('symbol')} held free in {a['account_id']}: the calls"
                                       " only, no shares bought, sold as covered by the shares held free when read - another order you send first may take"
                                       " them, and FinSim then refuses this one", call["span"]))
            self._price_tif(c, call, con2)
            # the calls are sold as covered (``covered: true``): the order route places them only while these shares are free, and keeps
            # them for the calls until they are closed (409 NOT_COVERED otherwise)
            call["_claim"] = True
            return [call]
        # a buy write (or a covered call on none held): two tickets - the shares, then the calls sold as covered by them, which the order
        # route places only once those shares are held free; a price typed on the whole is a net price (NET_PRICE, on its group)
        self._price_tif(c, call, con2, market_only=True)
        if q is not None and not q % 100 and con is not None and con2 is not None and not self._blocked(shares["flags"]):
            call["flags"].append(_flag("SIDE_INFERRED", f"sold as covered by the {q:,} {sym} bought: send it after the shares have filled - until they are held"
                                       " free FinSim refuses it as covered", call["span"]))
        call["_claim"] = True
        return [shares, call]

    def _collar_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        ia = c.get("instrument") or {}
        s = c.get("strategy") or {}
        ks = s.get("strikes") or [None, None]
        qa = c.get("qty") or {}
        q = qa.get("value") if qa.get("kind") == "num" else None
        shares = self._leg_base(ci, c, binfo, ia.get("symbol"))
        con = self._symbol_contract(ia["symbol"], shares, ia["span"]) if ia.get("kind") == "ticker" and shares["book"] is not None else None
        if con is not None and con.get("_own"):
            self._own_flag(c, shares, con, q)
            con = None
        self._set_instrument(shares, con)
        holders = self._holders(shares["lifetime_id"], con["id"]) if con is not None else None
        a = self._account(c, shares, binfo, tree, con, holders, aware=True)
        held = held_qty = 0
        if a is not None and con is not None:
            shares["position"] = self._position(a["account_id"], con["id"], True)
            held_qty = int(shares["position"]["qty"])
            # as the covered call: the shares free for NEW calls (``free_for_calls``), the account as it is
            held = self._cover_free(shares["position"])
        opts = []
        for right, k, side, i in (("P", ks[0], "BTO", 0), ("C", ks[1], "STO", 1)):
            leg = self._leg_base(ci, c, binfo, ia.get("symbol"))
            con2 = None
            if ia.get("kind") == "ticker" and k and leg["book"] is not None:
                con2 = self._option_contract(ia["symbol"], s.get("expiry"), right, k, leg, self._editors(c, "strategy", i, 0), ia["span"])
            self._set_instrument(leg, con2)
            if a is not None:
                leg["account_id"], leg["account_name"] = a["account_id"], a["name"]
            if leg["account_id"] and con2 is not None:
                leg["position"] = self._position(leg["account_id"], con2["id"], False)
            leg["side"] = side
            if q is not None and not q % 100:
                leg["qty"] = q // 100
            self._price_tif(c, leg, con2, market_only=True)
            opts.append(leg)
        uflag = self._share_unit(c, "collar")
        if uflag is not None:                             # fourth review: contracts / lots are never read as shares
            self._price_tif(c, shares, con, market_only=True)
            for leg in [shares] + opts:
                leg["flags"].append(uflag)
            return [shares] + opts
        if q is not None and q % 100:
            lo, hi = q // 100 * 100, (q // 100 + 1) * 100
            choices = [{"label": f"{x:,} shares", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(x))])} for x in (lo, hi) if x > 0]
            for leg in [shares] + opts:
                leg["flags"].append(_flag("QTY_LOT", f"a collar covers 100 shares per contract: {q:,} is not a multiple of 100", c["span"], choices))
            return [shares] + opts
        try:
            kp, kc = (Decimal(str(ks[0])), Decimal(str(ks[1]))) if ks[0] and ks[1] else (None, None)
        except (ArithmeticError, ValueError):
            kp = kc = None
        if kp is not None and kc is not None and kp >= kc:
            # round 8: a collar buys the put BELOW the call it sells - strikes typed the other way are refused, never swapped silently
            sp = s.get("strikes_span")
            lo, hi = _kstr(min(kp, kc)), _kstr(max(kp, kc))
            choice = [{"label": f"strikes {lo}/{hi}", "clause": self._clause_edit(c, [(sp[0], sp[1], f"{lo}/{hi}")])}] if sp and kp != kc else None
            # the orders the collar would be on the account as it is (the put and the call only on shares held free), each refused
            legs = opts if (q is not None and con is not None and a is not None and held_qty > 0 and held >= q) else [shares] + opts
            if legs is not opts:
                self._price_tif(c, shares, con, market_only=True)
            for leg in legs:
                leg["flags"].append(_flag("STRIKE_ORDER", f"a collar buys a put below the call it sells: the put strike first, then the call's ({ks[0]}/{ks[1]}"
                                          " is not)", c["span"], choice))
            return legs
        if q is None or con is None or a is None:
            return [shares] + opts
        sym = ia.get("symbol")
        # second review: the SIGNED holding decides (as for the covered call): a short holding is refused; shares held but not free are a
        # SIDE_SPLIT naming why; only a book that holds none buys the shares
        if held_qty < 0:
            self._price_tif(c, shares, con, market_only=True)        # the shares ticket's time in force (no second refusal about it)
            for leg in [shares] + opts:
                leg["flags"].append(_flag("NOTHING_HELD", f"collar on {q:,} {sym}: the {shares['book_name']} is short {-held_qty:,} {sym} in {a['account_id']} - a collar"
                                          " protects shares held long", leg["span"]))
            return [shares] + opts
        if held_qty > 0 and held >= q:
            for leg in opts:
                leg["flags"].append(_flag("SIDE_INFERRED", f"collar on {q:,} of the {held:,} {sym} held: the put and the call only, two tickets", leg["span"]))
            opts[1]["flags"].append(_flag("SIDE_INFERRED", f"sold as covered by the {sym} shares held free when read - another order you send first may take"
                                          " them, and FinSim then refuses this one", opts[1]["span"]))
            opts[1]["_claim"] = True                  # the collar's call is sold as covered by these shares (the order route checks it at Send)
            return opts
        if held_qty == 0:
            opts[1]["_claim"] = True                  # sold as covered by the shares the collar buys: placed only once they are held free
            shares["qty"], shares["side"] = q, "BUY"
            self._price_tif(c, shares, con, market_only=True)
            shares["flags"].append(_flag("TYPE_DEFAULT", "no price: the shares at market (fills at the next executable delayed print)", shares["span"]))
            for leg in [shares] + opts:
                leg["flags"].append(_flag("SIDE_INFERRED", f"no {sym} held: the collar buys the {q:,} shares first, then the put and the call", leg["span"]))
            opts[1]["flags"].append(_flag("SIDE_INFERRED", f"sold as covered by the {q:,} {sym} bought: send it after the shares have filled - until they are held"
                                          " free FinSim refuses it as covered", opts[1]["span"]))
            return [shares] + opts
        lots = held // 100 * 100
        choices = [{"label": f"collar the {lots:,} held free", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(lots))])}] if lots > 0 else None
        self._price_tif(c, shares, con, market_only=True)            # the shares ticket's time in force (no second refusal about it)
        for leg in [shares] + opts:
            leg["flags"].append(_flag("SIDE_SPLIT", f"collar {q:,} but {held:,} of the {held_qty:,} {sym} held in {a['account_id']} are free"
                                      + self._not_free_words(shares["position"], calls=True) + ": collar the free round lots, or buy the shares as their own order",
                                      leg["span"], choices))
        return [shares] + opts

    @staticmethod
    def _not_free_words(pos: Optional[dict], calls: bool = False) -> str:
        """Why shares held are not free: " (300 in working sells, 100 covering calls already sold, 50 lent)", or "" (``cover_state``'s
        counts).  ``calls``: the shares free for NEW calls, which working short sales take too."""
        pos = pos or {}
        why = []
        for n, words in ((pos.get("working_sells"), "in working sells"), (pos.get("reserved"), "covering calls already sold"),
                         (pos.get("calls_working"), "covering calls being sold (working orders)"), (pos.get("lent"), "lent"),
                         (pos.get("working_shorts") if calls else 0, "taken by working short sales")):
            if n:
                why.append(f"{int(n):,} {words}")
        return f" ({', '.join(why)})" if why else ""

    def _roll_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        ia = c.get("instrument") or {}
        o = c.get("option") or {}
        rt = c.get("roll_to") or {}
        old = self._leg_base(ci, c, binfo, ia.get("symbol") or ia.get("id"))
        con = None
        if old["book"] is not None:
            if ia.get("kind") == "occ":
                con = self._occ_contract(ia["id"], old, ia["span"])
            elif ia.get("kind") == "ticker" and o:
                con = self._option_contract(ia["symbol"], o.get("expiry"), o.get("right"), o.get("strike"), old, self._editors(c, "roll_old"), ia["span"])
        self._set_instrument(old, con)
        holders = self._holders(old["lifetime_id"], con["id"]) if con is not None else None
        a = self._account(c, old, binfo, tree, con, holders, aware=True)
        new = self._leg_base(ci, c, binfo, ia.get("symbol") or ia.get("id"))
        con2 = None
        if con is not None and c.get("roll_to") is not None:
            right = rt.get("right") or con["right"]
            if rt.get("right") and rt["right"] != con["right"]:
                new["flags"].append(_flag("MISSING_INSTRUMENT", "a roll keeps the call or put of the contract held", c["span"]))
            exp_new = rt.get("expiry") or {"date": con["expiry"]}
            strike_new = rt.get("strike") or con["strike"]
            sym_new = ia["symbol"] if ia.get("kind") == "ticker" else (con["_under"].symbol if con.get("_under") is not None else "")
            con2 = self._option_contract(sym_new, exp_new, right, strike_new, new, self._editors(c, "roll_new"), rt.get("span") or ia["span"])
            if con2 is not None and con2["id"] == con["id"]:
                new["flags"].append(_flag("MISSING_INSTRUMENT", "the roll goes to the same contract: name a new expiry or strike", c["span"]))
        self._set_instrument(new, con2)
        if a is not None:
            new["account_id"], new["account_name"] = a["account_id"], a["name"]
        pos = self._position(a["account_id"], con["id"], False) if a is not None and con is not None else None
        old["position"] = pos
        if new["account_id"] and con2 is not None:
            new["position"] = self._position(new["account_id"], con2["id"], False)
        qa = c.get("qty")
        if pos is not None:
            held, free = int(pos["qty"]), int(pos["free"])
            if held == 0:
                for leg in (old, new):
                    leg["flags"].append(_flag("NOTHING_TO_ROLL", f"no {con['words']} held in {a['account_id']}: nothing to roll", leg["span"]))
            else:
                if qa is None or qa.get("kind") == "all":
                    q = free
                elif qa.get("kind") == "half":
                    q = abs(held) // 2
                else:
                    q = qa.get("value")
                if q is not None and q > free:
                    ch = None
                    if free > 0 and qa is not None and qa.get("span"):
                        ch = [{"label": f"roll {free:,}", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(free))])}]
                    for leg in (old, new):
                        leg["flags"].append(_flag("SIDE_SPLIT", f"roll {q:,} but {free:,} {con['words']} free to close", leg["span"], ch))
                elif q:
                    old["qty"], new["qty"] = q, q
                    old["side"], new["side"] = ("BTC", "STO") if held < 0 else ("STC", "BTO")
                    word = "short" if held < 0 else "long"
                    old["flags"].append(_flag("SIDE_INFERRED", f"roll of the {abs(held):,} {word} held: {SIDE_TEXT[old['side']]} {q:,}", old["span"]))
                    new["flags"].append(_flag("SIDE_INFERRED", f"roll of the {abs(held):,} {word} held: {SIDE_TEXT[new['side']]} {q:,} of the new contract", new["span"]))
                    if qa is None:
                        old["inferred"].append(f"the {q:,} held")
                else:
                    for leg in (old, new):
                        leg["flags"].append(_flag("NOTHING_TO_ROLL", f"nothing free to roll ({abs(held):,} held, {free:,} free)", leg["span"]))
        for leg, cc in ((old, con), (new, con2)):
            self._price_tif(c, leg, cc, market_only=True)
        return [old, new]

    def _close_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        ia = c.get("instrument") or {}
        base = self._leg_base(ci, c, binfo, ia.get("symbol") or ia.get("id"))
        lid = base["lifetime_id"]
        if not ia or lid is None:
            self._price_tif(c, base, None)
            return [base]
        cls = c.get("close_class")
        named = self._named_account(c, binfo, tree)
        allowed_ids = None
        if named is not None:
            probe = dict(base, flags=[], inferred=[])
            acct = self._account(c, probe, binfo, tree, None)
            base["flags"].extend(f for f in probe["flags"] if f["severity"] == "blocker")
            allowed_ids = {acct["account_id"]} if acct else set()
        targets: List[dict] = []
        if cls in ("calls", "puts", "options"):
            under, _rf = self._underlying(ia.get("symbol") or "", base, ia.get("span"))
            if under is None:
                self._price_tif(c, base, None)
                return [base]
            ids = [a["account_id"] for a in self._accounts(lid) if allowed_ids is None or a["account_id"] in allowed_ids]
            rows = []
            if ids:
                rows = self.conn.execute(
                    "SELECT p.account_id, p.instrument_id, p.qty FROM v_positions p JOIN instruments i ON i.instrument_id = p.instrument_id"
                    f" WHERE i.kind='OPTION' AND i.underlying_id=? AND p.account_id IN ({','.join('?' * len(ids))}) AND p.qty <> 0",
                    (under.instrument_id, *ids)).fetchall()
            std = (under.cboe_symbol or under.symbol or "").replace("_", "").upper()
            for r in rows:
                inst = self.master.get(r["instrument_id"], self.conn)
                if inst is None or (cls == "calls" and inst.right != "C") or (cls == "puts" and inst.right != "P"):
                    continue
                con = self._opt_out(under, inst.instrument_id, inst.expiry, inst.right, Decimal(inst.strike), self._root(inst.instrument_id), std, True, inst, None, None)
                targets.append({"account_id": r["account_id"], "con": con})
            targets.sort(key=lambda t: (t["account_id"], t["con"]["expiry"], Decimal(t["con"]["strike"]), t["con"]["right"], t["con"]["id"]))
            if not targets:
                base["flags"].append(_flag("NOTHING_HELD", f"no {under.symbol} {cls} held in the {base['book_name']}", c["span"]))
                self._price_tif(c, base, None)
                return [base]
        else:
            con = self._contract_for(c, base)
            if con is None:
                self._price_tif(c, base, None)
                return [base]
            if con.get("_own"):
                self._set_instrument(base, con)
                self._own_flag(c, base, con, None)
                return [base]
            holders = self._holders(lid, con["id"])
            for aid in sorted(holders):
                if allowed_ids is None or aid in allowed_ids:
                    targets.append({"account_id": aid, "con": con})
            if not targets:
                self._set_instrument(base, con)
                base["flags"].append(_flag("NOTHING_HELD", f"no {con['words']} held in the {base['book_name']}", c["span"]))
                self._price_tif(c, base, con)
                return [base]
        qa = c.get("qty")
        if qa is not None and qa.get("kind") == "num" and len(targets) > 1:
            names = ", ".join(f"{t['account_id']} {t['con']['symbol']}" for t in targets)
            base["flags"].append(_flag("ACCOUNT_CHOICE", f"'close {qa['text']}' with {len(targets)} positions held ({names}): name the contract and the account",
                                       c["span"], severity="blocker"))
            self._price_tif(c, base, None)
            return [base]
        accts = {a["account_id"]: a for a in self._accounts(lid)}
        many = None
        p = c["mods"].get("price")
        if cls in ("calls", "puts", "options") and len(targets) > 1 and p is not None:
            # 1.0.7.2 review: one typed price is never copied onto several contracts (sides and strikes differ): each leg is blocked
            names = ", ".join(t["con"]["symbol"] for t in targets)
            many = _flag("PRICE_AMBIGUOUS", f"one price ('{self.text[p['span'][0]:p['span'][1]]}') for {len(targets)} contracts ({names}): name each contract with its"
                         " own price, or close them at market", c["span"],
                         [{"label": "close them at market (without the price)", "clause": self._clause_edit(c, [(p["span"][0], p["span"][1], "")])}])
        out = []
        for t in targets:
            leg = self._leg_base(ci, c, binfo, ia.get("symbol") or ia.get("id"))
            leg["flags"].extend(f for f in base["flags"] if f not in leg["flags"] and f["severity"] == "blocker")
            if many is not None:
                leg["flags"].append(dict(many))
            con = t["con"]
            self._set_instrument(leg, con)
            leg["account_id"], leg["account_name"] = t["account_id"], (accts.get(t["account_id"]) or {}).get("name")
            pos = self._position(t["account_id"], con["id"], self._cls(con) == "shares")
            leg["position"] = pos
            held, free = int(pos["qty"]), int(pos["free"])
            cls2 = self._cls(con)
            # a close of shares sells them: ``free`` is also net of the shares covering calls (``cover_state``), as a sale (``_side``)
            bz = pos if cls2 == "shares" and held > 0 else {}
            extra = int(bz.get("reserved") or 0) + int(bz.get("calls_working") or 0)
            side = ({"option": "STC", "future": "SELL", "shares": "SELL"} if held > 0 else {"option": "BTC", "future": "BUY", "shares": "BUY_TO_COVER"})[cls2]
            lot = max(1, int(con.get("lot") or 1))
            q = free
            if qa is not None and qa.get("kind") == "num" and qa.get("value"):
                q = int(qa["value"])
            elif qa is not None and qa.get("kind") == "half":
                q = (abs(held) // 2) // lot * lot
            if free <= 0 and extra:
                leg["flags"].append(_flag("WOULD_OVERSELL", f"the {abs(held):,} {con['words']} held in {t['account_id']} are not free{self._not_free_words(bz)}",
                                          leg["span"]))
            elif free <= 0:
                leg["flags"].append(_flag("WOULD_OVERSELL", f"the {abs(held):,} {con['words']} held in {t['account_id']} are already in working orders", leg["span"]))
            elif q > free:
                ch = [{"label": f"close {free:,}", "clause": self._clause_edit(c, [(qa["span"][0], qa["span"][1], str(free))])}] if qa and qa.get("span") else None
                leg["flags"].append(_flag("WOULD_OVERSELL" if held > 0 else "SIDE_SPLIT", f"close {q:,} but {free:,} {con['words']} free in {t['account_id']}",
                                          leg["span"], ch))
            elif q <= 0:
                leg["flags"].append(_flag("NOTHING_HELD", f"nothing to close in {t['account_id']}", leg["span"]))
            else:
                leg["qty"], leg["side"] = q, side
                busy = int(pos["working_sells"]) + int(pos["lent"]) + extra if held > 0 else int(pos["working_covers"])
                leg["flags"].append(_flag("SIDE_INFERRED", f"close = {SIDE_TEXT[side]} {q:,} of the {abs(held):,} {'held' if held > 0 else 'short'} in {t['account_id']}"
                                          + (f" ({busy:,} already in working orders or reserved)" if busy else ""), leg["span"]))
            self._price_tif(c, leg, con)
            out.append(leg)
        return out

    def _clause_legs(self, ci: int, c: dict, binfo: dict, tree: dict) -> List[dict]:
        k = c["kind"]
        if k == "order":
            return [self._order_leg(ci, c, binfo, tree)]
        if k == "strategy":
            return self._strategy_legs(ci, c, binfo, tree)
        if k == "covered":
            return self._covered_legs(ci, c, binfo, tree)
        if k == "collar":
            return self._collar_legs(ci, c, binfo, tree)
        if k == "roll":
            return self._roll_legs(ci, c, binfo, tree)
        if k == "close":
            return self._close_legs(ci, c, binfo, tree)
        return []

    # ---- groups: the orders written together, each its own ticket (1.0.7.2 simplification) -------------------------------------------------
    def _group(self, src: dict) -> dict:
        """1.0.7.2 simplification: the orders written together - a strategy, a roll, a collar, a covered call that buys its shares, a group
        word or parentheses: exactly the sources 1.0.7.2 made combinations of, so the same legs carry a group id (``combo``) - each its own
        ticket that goes and fills on its own (SEPARATE_TICKETS), sent in the order ``order`` gives (purchases first: shares, then options;
        then sales to close, sales to open, share sales and short sales).  A price on the whole (NET_PRICE) and words asking for one order
        (ONE_ORDER_ASKED) are blockers of the group, repeated without their choices on each of its orders, whose ``request`` is then null:
        no consumer can send one order of a group the owner asked to be one.  The group's own ``request`` is always null."""
        legs = src["legs"]
        span = src["span"]
        flags: List[dict] = []
        opts = [l for l in legs if (l.get("instrument") or {}).get("kind") == "OPTION"]
        names = sorted({str(l["instrument"].get("underlying_id")) for l in opts if l["instrument"].get("underlying_id")}
                       | {str(l["instrument"].get("id")) for l in legs if l["instrument"].get("kind") in EQUITY_KINDS and l["instrument"].get("id")})
        accts = sorted({str(l["account_id"]) for l in legs})
        books_ = sorted({str(l["book"]) for l in legs})
        name = src.get("strategy")
        option_only = bool(legs) and len(opts) == len(legs) and len(names) == 1
        label = f"the {name}" if name else "these orders"
        tab = None
        if option_only and self._has_template(name, legs) and len(accts) == 1 and accts[0] != "None":
            # the order ticket's Strategies tab builds one all-or-none order of options from its rules (the owner picks there)
            tab = {"kind": "strategies_tab", "label": "Strategies tab", "instrument_id": names[0], "account_id": accts[0]}
        asked = self._one_order_words(src)
        if asked:
            flags.append(_flag("ONE_ORDER_ASKED", f"'{asked}' asks for one order; the Smart Ticket fills separate tickets that go and fill on their own"
                               + ("; for one all-or-none order, use the Strategies tab" if tab else ""), span, self._separate_choice(src), action=tab))
        p = src.get("price")
        if p is not None:
            flags.append(_flag("NET_PRICE", f"'{self.text[p['span'][0]:p['span'][1]]}' on {label} is a price for the whole: separate tickets cannot carry a net"
                               " price - give each order its own price, or leave it out", span,
                               [{"label": "without the price", "clause": _edit(self.text, span, [(p["span"][0], p["span"][1], "")])}]))
        n = len(legs)
        if n == 1:                                       # a group word or parentheses around one order: that order is one ticket
            words = f"{label if name else 'this order'} as one ticket: it goes and fills on its own (no all-or-none, no sequence)"
        else:
            words = (f"{label} as {n} separate tickets: each order goes and fills on its own (no all-or-none, no sequence); send them in the order"
                     " shown" + ("; for one all-or-none order, use the Strategies tab" if tab else ""))
        flags.append(_flag("SEPARATE_TICKETS", words, span, action=None if asked else tab))
        if p is None:
            for l in opts:
                # each option of the group is a ticket of its own at market until the owner gives it a limit there: said, as every default
                if l.get("order_type") == "MARKET" and not any(f["code"] == "TYPE_DEFAULT" for f in l["flags"]) and l.get("side"):
                    what = "ask" if l["side"] in ("BTO", "BTC") else "bid"
                    l["flags"].append(_flag("TYPE_DEFAULT", f"no price: a market order (fills at the next executable Cboe {what})", l["span"]))
        for f in flags:
            _widen(self.text, f)
        for f in flags:
            if f["severity"] == "blocker":
                for l in legs:
                    l["flags"].append({k: v for k, v in f.items() if k not in ("choices", "action")})
        order = [l["n"] for l in sorted(legs, key=lambda l: (_send_rank(l), l["n"]))]
        return {"id": None, "legs": [l["n"] for l in legs], "fill_mode": None, "kind": "SEPARATE", "underlying_id": names[0] if len(names) == 1 else None,
                "strategy": name, "book": legs[0]["book"] if legs and len(books_) == 1 else None,
                "account_id": accts[0] if len(accts) == 1 and accts[0] != "None" else None, "span": list(span), "clause": self.text[span[0]:span[1]],
                "flags": sort_flags(flags), "request": None, "order": order}

    @staticmethod
    def _has_template(name: Optional[str], legs: List[dict]) -> bool:
        """Whether the order ticket's Strategies tab has a template for the strategy ``name`` (a butterfly: of calls only)."""
        if not name or name not in TEMPLATE_STRATEGIES:
            return False
        if name == "butterfly":
            return all((l.get("instrument") or {}).get("right") == "C" for l in legs)
        return True

    def _one_order_spans(self, src: dict) -> List[List[int]]:
        """The words of group ``src`` that ask for one order: its group word, and one typed inside its parentheses."""
        g = src.get("group") or {}
        spans = [list(g["word_span"])] if g.get("word_span") else []
        s0, e0 = src["span"]
        for ci in src.get("clauses") or []:
            gw = self._tree["clauses"][ci]["mods"].get("group")
            if gw and gw.get("span") and s0 <= gw["span"][0] and gw["span"][1] <= e0 and list(gw["span"]) not in spans:
                spans.append(list(gw["span"]))
        return spans

    def _one_order_words(self, src: dict) -> Optional[str]:
        spans = self._one_order_spans(src)
        return ", ".join(self.text[a:b] for a, b in spans) or None

    def _separate_choice(self, src: dict) -> Optional[List[dict]]:
        """ONE_ORDER_ASKED's choice "as separate tickets": the words asking for one order go (``_one_order_spans``); the parentheses stay
        and only group the cards.  Checked by the choice guard like every choice (dropped where it would change another order)."""
        edits = [(a, b, "") for a, b in self._one_order_spans(src)]
        return [{"label": "as separate tickets", "clause": _edit(self.text, src["span"], edits)}] if edits else None

    # ---- the whole answer --------------------------------------------------------------------------------------------------------------------
    def run(self, tree: dict) -> dict:
        self.text = tree["text"]
        self._tree = tree
        clauses = tree["clauses"]
        insts: List[List[dict]] = []
        for ci, c in enumerate(clauses):
            row = []
            for b in self._books_for(ci, c, tree):
                got = self._clause_legs(ci, c, b, tree)
                row.append({"rep": b["rep"], "legs": got})
            insts.append(row)
        legs = [l for row in insts for inst in row for l in inst["legs"]]
        if len(legs) > MAX_LEGS:
            raise SmartTicketError("TOO_MANY_LEGS", f"the instruction makes {len(legs)} legs: at most {MAX_LEGS}", {"legs": len(legs), "max": MAX_LEGS})
        for n, l in enumerate(legs, 1):
            l["n"] = n
        # SD-2: the clause that carries the book phrase at the end says so when the phrase also gave other legs their book
        suffix = tree.get("suffix")
        if suffix and suffix.get("clause") is not None and (suffix.get("scope") or {}).get("books") and any(l["_src"] == "suffix" for l in legs):
            sc = suffix["scope"]
            for l in legs:
                if l["_ci"] == suffix["clause"]:
                    l["flags"].append(_flag("BOOK_FROM_SCOPE", f"book from '{self.text[sc['span'][0]:sc['span'][1]]}' (it also applies to the legs that name no book)",
                                            sc["span"], severity="info"))
        for l in legs:
            l["request"] = self._request(l)
        # groups: strategy words, covered calls and collars that buy their shares, collars, rolls, then the group words and parentheses (a
        # strategy inside a group joins the group) - the same sources as 1.0.7.2's combinations
        grouped = {ci for g in tree["groups"] for ci in g["clauses"]}
        sources: List[dict] = []
        for ci, row in enumerate(insts):
            c = clauses[ci]
            if ci in grouped or c["kind"] not in ("strategy", "covered", "collar", "roll"):
                continue
            for inst in row:
                if c["kind"] in ("collar", "covered") and len(inst["legs"]) < 2:
                    continue                      # the calls alone (shares held): an order of their own, no group
                name = c["strategy"]["name"] if c["kind"] == "strategy" and c.get("strategy") else {"covered": "covered call", "collar": "collar", "roll": "roll"}.get(c["kind"])
                if name == "condor" and c.get("strategy") and c["strategy"].get("right"):
                    name = f"{_call_put(c['strategy']['right'])} {name}"
                # a typed price reaches the group of a strategy, a roll, a collar or a covered call that buys its shares (NET_PRICE there),
                # never dropped (1.0.7.2 review)
                sources.append({"legs": inst["legs"], "span": c["span"], "clauses": [ci], "kind": c["kind"], "strategy": name,
                                "price": c["mods"].get("price")})
        for g in tree["groups"]:
            reps = max((len(insts[ci]) for ci in g["clauses"]), default=0)
            names = [clauses[ci]["strategy"]["name"] for ci in g["clauses"] if clauses[ci]["kind"] == "strategy" and clauses[ci].get("strategy")]
            # second review: a price typed on a strategy, a roll, a collar or a covered call that buys its shares inside a group reaches
            # the group and is refused there like an ungrouped one (its legs are at market), never dropped
            gprice = next((clauses[ci]["mods"]["price"] for ci in g["clauses"] if clauses[ci]["mods"].get("price") is not None
                           and (clauses[ci]["kind"] in ("strategy", "roll", "collar")
                                or (clauses[ci]["kind"] == "covered" and any(len(inst["legs"]) > 1 for inst in insts[ci])))), None)
            for r in range(reps):
                members = [l for ci in g["clauses"] for inst in insts[ci] if inst["rep"] == r for l in inst["legs"]]
                if members:
                    sources.append({"legs": members, "span": g["span"], "clauses": list(g["clauses"]), "kind": "group", "group": g,
                                    "strategy": names[0] if len(names) == 1 and len(g["clauses"]) == 1 else None, "price": gprice})
        combos = [self._group(s) for s in sources]
        combos.sort(key=lambda cb: min(cb["legs"]) if cb["legs"] else 0)
        for n, cb in enumerate(combos, 1):
            cb["id"] = f"C{n}"
            for ln in cb["legs"]:
                legs[ln - 1]["combo"] = cb["id"]
        self._cross_checks(legs, clauses, grouped)
        for l in legs:
            l["request"] = self._request(l)
        self._last = (legs, combos)
        self._keep_others(tree, legs, combos)
        out_legs = []
        for l in legs:
            l["flags"] = sort_flags(l["flags"])
            o = {k: v for k, v in l.items() if not k.startswith("_")}
            if isinstance(o.get("position"), dict):
                # the answer's position keeps its keys (the reading's other counts - calls being sold, short sales working, the shares
                # free for new calls - stay inside the reading)
                o["position"] = {k: o["position"].get(k, 0) for k in POSITION_KEYS}
            out_legs.append(o)
        top = list(tree["flags"])
        if tree.get("prefix"):
            top.extend(tree["prefix"].get("flags") or [])
        if suffix:
            top.extend(suffix.get("flags") or [])
        n_multi = sum(1 for l in legs if any(f["code"] == "MULTI_BOOK" for f in l["flags"]))
        if n_multi:
            top.append(_flag("MULTI_BOOK", f"{n_multi} legs repeated across books at the same quantity (no allocation)"))
        if not out_legs and not any(f["severity"] == "blocker" for f in top):
            top.append(_flag("MISSING_INSTRUMENT", "nothing to trade: type an instruction such as 'buy 100 ACME limit 225'"))
        top = sort_flags(top)
        allf = [f for l in out_legs for f in l["flags"]] + [f for cb in combos for f in cb["flags"]] + top
        counts = {"legs": len(out_legs), "combos": len(combos), "blockers": sum(1 for f in allf if f["severity"] == "blocker"),
                  "checks": sum(1 for f in allf if f["severity"] == "check"), "infos": sum(1 for f in allf if f["severity"] == "info")}
        return {"grammar": GRAMMAR, "as_of": self.now, "selected_book": self.selected, "text": self.text, "ok": counts["blockers"] == 0 and bool(out_legs),
                "counts": counts, "legs": out_legs, "combos": combos, "unparsed": list(tree["unparsed"]), "flags": top}

    def _cross_checks(self, legs: List[dict], clauses: List[dict], grouped: set) -> None:
        """COVER_HINT on a share buy and a call sold as two orders on one name outside a group; DUPLICATE_LEG on two identical orders."""
        free = [l for l in legs if l["combo"] is None]
        for b in free:
            ib = b["instrument"]
            if ib.get("kind") != "OPTION" or ib.get("right") != "C" or b["side"] != "STO":
                continue
            for a in free:
                ia = a["instrument"]
                if a is b or ia.get("kind") not in EQUITY_KINDS or a["side"] != "BUY" or ia.get("id") != ib.get("underlying_id") or a["account_id"] != b["account_id"]:
                    continue
                # the call is its own ticket: sent after the shares have filled, it is covered by them (and only then can it be sold as
                # covered: the order route needs the shares held free)
                b["flags"].append(_flag("COVER_HINT", f"legs {a['n']} and {b['n']} look like a covered call: two tickets: send the call after the shares have"
                                        " filled", b["span"]))
                break
        seen: Dict[str, int] = {}
        for l in legs:
            if l["request"] is None:
                continue
            key = repr(sorted(l["request"].items()))
            if key in seen:
                first = legs[seen[key] - 1]
                for x in (first, l):
                    x["flags"].append(_flag("DUPLICATE_LEG", f"legs {first['n']} and {l['n']} are the same order", x["span"]))
            else:
                seen[key] = l["n"]

    # ---- a choice changes only the order it is on (1.0.7.2 third review) ---------------------------------------------------------------------
    #: the flags whose choices are about the book or account of the order they are on (only the OTHER orders are kept as read)
    PLACE_CODES = ("ACCOUNT_CHOICE", "ACCOUNT_NOT_IN_BOOK")

    def _keep_others(self, tree: dict, legs: List[dict], combos: List[dict]) -> None:
        """Every choice is re-read the way the page applies it (its text in place of the flag's span, then a new parse of the instruction).
        Third review: where a choice would change another order's inherited side, book or account (a side word written into an order
        that carries its side, book and account from the order before; the orders after it that carry ITS side; a book or account phrase
        that lands in the closing phrase of the last order), the choice also writes out, in trader words, what those orders had inherited
        (``_guard_flag``), its span widened over them.  Fourth and fifth reviews: each choice is then RESOLVED as the page would re-read
        it (``_reads_alike``: the positions, the accounts, the groups) and offered only when every order but
        the one it is on reads exactly as before - matched by position, the orders it wrote out too - and never becomes sendable; when
        the clause it is on is repeated per book ("in the Personal and Fund books"), the other books' orders of that clause read as
        before too (a choice computed from one book's holding is written for that book alone: ``_split_choice``), unless the same words
        are offered on them; the order it is on stays in its book and account (unless the choice is that book or account) and joins no
        other order's group; and its words parse (no new word "not understood").  Fifth review, the parse budget: an instruction
        whose orders fall into independent sections (each starting with its own side; no group; no closing phrase that reaches another
        section) has each flag checked on its section alone - the opening phrase and that section's words, kept only when they read
        those orders exactly as the whole instruction does - so the work grows with the section, not the instruction, and identical
        sections are checked once.  Pure, like the rest."""
        if getattr(self, "_nested", False):
            return                                        # a re-read made by the guard itself offers no guard of its own
        items = [(l, [l]) for l in legs] + [(cb, [legs[n - 1] for n in cb["legs"]]) for cb in combos]
        offered: Dict[tuple, set] = {}                    # (span, words) -> the books of the orders those words are offered on
        offered_k: Dict[tuple, set] = {}                  # sixth review: ... and those orders themselves, as (clause, copy)
        for it, its in items:
            bks = {l["book"] for l in its}
            kk = {(l["_ci"], l["_rep"]) for l in its}
            for f in it["flags"]:
                if f.get("span"):
                    for ch in f.get("choices") or []:
                        okey = (int(f["span"][0]), int(f["span"][1]), str(ch["clause"]))
                        offered.setdefault(okey, set()).update(bks)
                        offered_k.setdefault(okey, set()).update(kk)
        plan = self._sections(tree)
        readings: Dict[Any, Optional[dict]] = {}
        cache: Dict[tuple, tuple] = {}
        done: set = set()
        for it, its in items:
            for f in it["flags"]:
                if id(f) in done or not f.get("choices") or not f.get("span"):
                    continue
                done.add(id(f))
                own = {l["book"] for l in its}
                own_k = {(l["_ci"], l["_rep"]) for l in its}
                span0 = (int(f["span"][0]), int(f["span"][1]))
                chs0 = [{"label": str(ch["label"]), "clause": str(ch["clause"]), "_i": i} for i, ch in enumerate(f["choices"])]
                books = tuple(frozenset(own | offered.get((span0[0], span0[1], ch["clause"]), set())) for ch in chs0)
                keys = tuple(frozenset(own_k | offered_k.get((span0[0], span0[1], ch["clause"]), set())) for ch in chs0)
                sec = self._section_of(tree, plan, span0, chs0)
                R = None
                if sec is not None:
                    if sec not in readings:
                        readings[sec] = self._reading(tree, legs, combos, sec)
                    R = readings[sec]
                if R is None:
                    if "whole" not in readings:
                        readings["whole"] = self._reading(tree, legs, combos, None)
                    R = readings["whole"]
                shift = R["shift"]
                s0 = (span0[0] + shift, span0[1] + shift)
                c0 = int(R.get("c0") or 0)                # a section's reading numbers its clauses from the section's first
                keys = tuple(frozenset((ci - c0, rp) for ci, rp in k) for k in keys)
                key = (R["text"], f["code"], s0, tuple(ch["clause"] for ch in chs0), books, keys)
                if key not in cache:
                    cache[key] = R["res"]._check_flag(R, f["code"], s0, chs0, books, keys)
                span, kept = cache[key]
                f["span"] = [int(span[0]) - shift, int(span[1]) - shift]
                if kept:
                    f["choices"] = [{"label": ch["label"], "clause": ch["clause"]} for ch in kept]
                else:
                    f.pop("choices", None)

    def _sections(self, tree: dict) -> Optional[tuple]:
        """Fifth review: ``(sections, last_free, readable)`` - the clause ranges ``(lo, hi)`` that read independently (a section starts at an
        order with its own side or verb; the clauses that carry a side, or have none, belong to the section before), whether a closing
        book phrase written in the last section could reach no order before it (every one of them has a book of its own, carried, or from
        the opening phrase), and per section whether it may be read alone (its last order's own book phrase would otherwise close the
        section's reading for orders of it that name none) - or None when the instruction is read whole (a group, an account alone at the
        end, a closing phrase that gives an order outside the last section its book)."""
        cl = tree["clauses"]
        if len(cl) < 2 or tree.get("groups"):
            return None
        if sum(1 for c in cl if c.get("kind") in ("covered", "collar")) > 1:
            return None                                   # sixth review: covered calls and collars share one running count of their cover
        starts = [i for i, c in enumerate(cl) if i == 0 or (c.get("verb") and not c["verb"].get("carried"))]
        secs = [(s, (starts[k + 1] - 1) if k + 1 < len(starts) else len(cl) - 1) for k, s in enumerate(starts)]
        if len(secs) < 2:
            return None
        # seventh review (kept: it only makes the guard read whole more often): a name whose orders fall in two sections, one of them
        # reading the shares held (a covered call or collar, a close, a sale or short sale of the shares, an order on them with no side
        # yet), is read whole
        where: Dict[str, set] = {}
        reads: set = set()
        for k, (s, e) in enumerate(secs):
            for i in range(s, e + 1):
                key = cl[i].get("underlying_key")
                if key:
                    where.setdefault(key, set()).add(k)
                    if self._reads_cover(cl[i]):
                        reads.add(key)
        if any(len(where[key]) > 1 for key in reads):
            return None
        pre = tree.get("prefix") or {}

        def booked(c):
            return bool((c["mods"].get("scope") or {}).get("books") or (c.get("carried_scope") or {}).get("books") or pre.get("books"))
        last_free = all(booked(cl[i]) for i in range(secs[-1][0]))
        suf = tree.get("suffix")
        if suf is not None and (not (suf.get("scope") or {}).get("books") or not last_free):
            return None
        ok = []
        for lo, hi in secs:
            last_book = bool((cl[hi]["mods"].get("scope") or {}).get("books"))
            ok.append(not (hi > lo and last_book and not all(booked(cl[i]) for i in range(lo, hi))))
        return secs, last_free, ok

    @staticmethod
    def _reads_cover(c: dict) -> bool:
        """Seventh review: whether a clause's reading depends on the shares the instruction's other orders take (``_sections``)."""
        k = c.get("kind")
        if k in ("covered", "collar", "close"):
            return True
        if k == "order" and not c.get("option") and (c.get("instrument") or {}).get("kind") == "ticker":
            return (c.get("verb") or {}).get("side") in (None, "sell", "SELL_SHORT", "STO", "STC")
        return False

    def _section_of(self, tree: dict, plan: Optional[tuple], span0: Sequence[int], chs0: List[dict]) -> Optional[tuple]:
        """The section range a flag is checked on, or None (the whole instruction)."""
        if plan is None:
            return None
        secs, last_free, ok = plan
        cl = tree["clauses"]
        touched = [i for i, c in enumerate(cl) if c["span"][0] < span0[1] and c["span"][1] > span0[0]]
        if not touched or any(_groupish(ch["clause"]) for ch in chs0):
            return None
        k_lo = next((k for k, (s, e) in enumerate(secs) if s <= touched[0] <= e), None)
        k_hi = next((k for k, (s, e) in enumerate(secs) if s <= touched[-1] <= e), None)
        if k_lo is None or k_hi is None or (k_lo == 0 and k_hi == len(secs) - 1):
            return None
        if k_hi == len(secs) - 1 and not last_free:
            return None
        if not all(ok[k] for k in range(k_lo, k_hi + 1)):
            return None
        return secs[k_lo][0], secs[k_hi][1]

    def _reading(self, tree: dict, legs: List[dict], combos: List[dict], sec: Optional[tuple]) -> Optional[dict]:
        """What a flag's choices are checked against: the whole instruction as read (``sec`` None), or the section ``sec`` read alone (the
        opening phrase, its words and the closing book phrase) - kept only when it reads exactly as the whole instruction read those
        orders."""
        books = list(self.vocab.books)
        if sec is None:
            res, t, text, shift, L, C = self, tree, self.text, 0, legs, combos
        else:
            lo, hi = sec
            cl = tree["clauses"]
            a, b = int(cl[lo]["span"][0]), int(cl[hi]["span"][1])
            pre = tree.get("prefix")
            head = (self.text[pre["span"][0]:pre["span"][1]] + " ") if pre else ""
            # a closing book phrase outside the section goes with it (as a phrase alone at its end), so an order a choice frees from the
            # book it carried reads that phrase here as the whole instruction would
            tail = ""
            suf = tree.get("suffix") or {}
            ssp = (suf.get("scope") or {}).get("span")
            if ssp and not (a <= ssp[0] and ssp[1] <= b):
                tail = "; " + self.text[ssp[0]:ssp[1]]
                asp = (suf.get("account") or {}).get("span")
                if asp and not (ssp[0] <= asp[0] and asp[1] <= ssp[1]):
                    tail += " " + self.text[asp[0]:asp[1]]
            text = head + self.text[a:b] + tail
            shift = len(head) - a
            try:
                t = parse(text, books)
                res = self._shadow()
                res.run(t)
            except SmartTicketError:
                return None
            L, C = res._last
            if len(t["clauses"]) != hi - lo + 1:
                return None
            whole, part = self._facts(legs, combos), self._facts(L, C)
            for k in range(hi - lo + 1):
                x, y = whole.get(lo + k, []), part.get(k, [])
                if [(f, g, r) for f, g, _p, r in x] != [(f, g, r) for f, g, _p, r in y] or any(p for _f, _g, p, _r in x + y):
                    return None
        reps: Dict[int, List[int]] = {}
        for l in L:                                       # each leg's copy (its book's place in its clause's book phrase), in facts order
            reps.setdefault(l["_ci"], []).append(int(l.get("_rep") or 0))
        return {"res": res, "tree": t, "text": text, "shift": shift, "facts": self._facts(L, C), "unparsed": self._unparsed_of(t, L, C),
                "ctx": res._contexts(t) if len(t["clauses"]) > 1 else None, "accts": self._resolved_accounts(L), "c0": 0 if sec is None else sec[0],
                "reps": reps}

    def _check_flag(self, R: dict, code: str, span0: Sequence[int], chs0: List[dict], books: tuple, keys: Optional[tuple] = None) -> tuple:
        """``(span, kept choices)`` of one flag on reading ``R`` (``_keep_others``).  ``keys`` (sixth review): per choice, the orders it is
        on and those the same words are offered on, as (clause, copy) of the reading."""
        t = R["tree"]
        span, chs = list(span0), [dict(ch) for ch in chs0]
        if len(t["clauses"]) > 1:
            span, chs = self._guard_flag(t, R["ctx"], R["accts"], {"code": code, "span": list(span0), "choices": chs})
        place = code in self.PLACE_CODES
        kept = []
        for ch in chs or []:
            bks = books[ch["_i"]]
            kys = keys[ch["_i"]] if keys is not None else None
            if self._reads_alike(t, R, span0, span, ch["clause"], place, bks, kys):
                kept.append(ch)
            elif [int(span[0]), int(span[1])] == [int(span0[0]), int(span0[1])]:
                sp = self._split_choice(t, span0, ch["clause"], bks)
                if sp is not None and self._reads_alike(t, R, span0, span, sp, place, bks, kys):
                    kept.append(dict(ch, clause=sp))
        return (int(span[0]), int(span[1])), kept

    def _split_choice(self, tree: dict, span0: Sequence[int], clause: str, own_books: frozenset) -> Optional[str]:
        """Fifth review: a choice on ONE book's order of a clause repeated per book ("sell 300 WIDG in the Personal and Fund books"),
        computed from that book's holding, written for that book alone while the clause stays as typed for the other books ("sell 300
        WIDG in the Personal book; short 300 WIDG in the Fund book"); None when the clause cannot be written so."""
        cl = tree["clauses"]
        own = [i for i, c in enumerate(cl) if c["span"][0] < span0[1] and c["span"][1] > span0[0]]
        if len(own) != 1:
            return None
        c = cl[own[0]]
        if [int(c["span"][0]), int(c["span"][1])] != [int(span0[0]), int(span0[1])] or c["mods"].get("group") is not None \
                or any(own[0] in g["clauses"] for g in tree.get("groups") or []):
            return None
        sc = c["mods"].get("scope") or {}
        bks, bs = sc.get("books") or [], sc.get("book_span")
        if len(bks) < 2 or not bs or any(len(x["codes"]) != 1 for x in bks):
            return None
        codes = [x["codes"][0] for x in bks]
        mine, rest = [x for x in codes if x in own_books], [x for x in codes if x not in own_books]
        if not mine or not rest:
            return None
        phrase = self.text[bs[0]:bs[1]]
        p_mine, p_rest = self._book_phrase(mine), self._book_phrase(rest)
        if not p_mine or not p_rest or phrase not in clause:
            return None
        t_mine = clause.replace(phrase, p_mine.strip())
        t_rest = self._clause_edit(c, [(bs[0], bs[1], p_rest.strip())])
        return (t_mine + "; " + t_rest) if codes[0] in mine else (t_rest + "; " + t_mine)

    def _shadow(self) -> "Resolver":
        """A resolver on the same connection, sharing this one's reads (positions, accounts, chains, the cover of shares), for a re-read
        the guard makes; it offers no guard of its own."""
        r = Resolver.__new__(Resolver)
        r.__dict__.update(self.__dict__)
        r._nested = True
        return r

    @staticmethod
    def _facts(legs: List[dict], combos: List[dict]) -> Dict[int, list]:
        """Per clause: each leg's order body (book, account, side, quantity, contract, order type, prices, time in force), whether it can be
        sent (its own request: a group blocker is on its legs), the clauses its group joins it with, and whether it has a request."""
        of: Dict[int, dict] = {}
        for cb in combos:
            for n in cb["legs"]:
                of[n] = cb
        out: Dict[int, list] = {}
        for l in legs:
            cb = of.get(l["n"])
            partners = frozenset(legs[m - 1]["_ci"] for m in cb["legs"]) - {l["_ci"]} if cb else frozenset()
            goes = l["request"] is not None
            fact = (l["book"], l["account_id"], l["side"], l["qty"], (l.get("instrument") or {}).get("id"), l.get("order_type"), l.get("limit_px"),
                    l.get("stop_px"), l.get("trail_amt"), l.get("trail_pct"), l.get("tif"), l.get("gtd_date"))
            out.setdefault(l["_ci"], []).append((fact, goes, partners, l["request"] is not None))
        return out

    @staticmethod
    def _unparsed_of(tree: dict, legs: List[dict], combos: List[dict]) -> frozenset:
        """The words a reading does not understand (its UNPARSED_TEXT flags) and the fields it reads twice in one order (its DUPLICATE_FIELD
        flags, sixth review): a choice whose re-read adds one never does what its label says, so it is not offered."""
        fl = ([f for l in legs for f in l["flags"]] + [f for cb in combos for f in cb["flags"]] + list(tree.get("flags") or [])
              + list((tree.get("prefix") or {}).get("flags") or []) + list((tree.get("suffix") or {}).get("flags") or []))
        return frozenset((f["code"], f["text"]) for f in fl if f["code"] in ("UNPARSED_TEXT", "DUPLICATE_FIELD"))

    def _reads_alike(self, tree: dict, R: dict, span0: Sequence[int], span: Sequence[int], clause: str, place: bool, own_books: frozenset,
                     own_keys: Optional[frozenset] = None) -> bool:
        """Fourth and fifth reviews: whether the instruction a choice gives, RESOLVED as the page re-reads it, leaves every order but the
        one it is on as it was read - matched by position (the orders before and after the words it replaces keep their words; the
        orders it wrote out keep their place in it), each with the same order body and the same group, never newly sendable (its own
        request: a group blocker is repeated on the group's legs);
        the other books' orders of the clause it is on read as before (unless the same words are offered on them: ``own_books``); the
        order it is on stays in its book and account (unless the choice is about those, ``place``) and joins no other order's group;
        and its words parse (no new word "not understood").  ``span0`` = the words the flag is on, ``span`` = the words the choice
        replaces (wider when the guard wrote orders out).  A re-read the parser refuses stages nothing: that choice stays.

        Sixth review: ``own_keys`` = the orders the flag is on and those the same words are offered on, as (clause, copy) - the legs of
        the group a flag is on, never every leg of the books that group shares: every other copy of a clause inside the words it
        rewrites (another book's copy of a clause typed for several books, in another group or none) is found unchanged in the new
        reading and never newly sendable, else the choice is not offered."""
        a, b = int(span[0]), int(span[1])
        try:
            t2 = parse(self.text[:a] + clause + self.text[b:], list(self.vocab.books))
            sh = self._shadow()
            sh.run(t2)
        except SmartTicketError:
            return True
        legs2, combos2 = sh._last
        if self._unparsed_of(t2, legs2, combos2) - R["unparsed"]:
            return False
        facts0, facts2 = R["facts"], self._facts(legs2, combos2)
        cl0, cl2 = tree["clauses"], t2["clauses"]
        own = [i for i, c in enumerate(cl0) if c["span"][0] < span0[1] and c["span"][1] > span0[0]]
        before = [i for i, c in enumerate(cl0) if c["span"][1] <= a]
        after = [i for i, c in enumerate(cl0) if c["span"][0] >= b]
        region = [i for i in range(len(cl0)) if i not in before and i not in after]
        lead = [i for i in region if not own or i < own[0]]
        trail = [i for i in region if own and i > own[-1]]
        m: Dict[int, int] = {}
        for k, i in enumerate(before):
            m[i] = k
        for k, i in enumerate(after):
            m[i] = len(cl2) - len(after) + k
        mid = list(range(len(before), len(cl2) - len(after)))
        if len(mid) < len(lead) + len(trail) + (1 if own else 0):
            return False
        for k, i in enumerate(lead):
            m[i] = mid[k]
        for k, i in enumerate(trail):
            m[i] = mid[len(mid) - len(trail) + k]
        mine = mid[len(lead):len(mid) - len(trail)]
        for i in before + after:
            if _clause_words(cl0[i].get("text")) != _clause_words(cl2[m[i]].get("text")):
                return False
        inv = {j: i for i, j in m.items()}
        # sixth review: the orders whose book the words of this flag left unread (a book word that names no book: carried with the side,
        # under the closing or opening phrase) take the book the choice settles - they read in it as the same text typed right would
        settled = set()
        for i, j in m.items():
            w = self._unread_by(i, cl0[i], tree)
            if w is None or not (int(span0[0]) <= w[0] and w[1] <= int(span0[1])):
                continue                                  # not left unread by the words this choice rewrites
            bs2 = self._books_for(j, cl2[j], t2)
            if bs2 and (bs2[0].get("unread") or bs2[0]["src"] != "selected"):
                settled.add(i)                            # read in a book its words name (never the selected book), or still unread

        def k0(i):
            return "IN" if i in own else i

        def k2(j):
            return inv.get(j, "IN")
        for i, j in m.items():
            old, new = facts0.get(i, []), facts2.get(j, [])
            if len(old) != len(new):
                return False
            for (f0, g0, p0, r0), (f2, g2, p2, _r2) in zip(old, new):
                q0 = {k0(x) for x in p0}
                if q0 != {k2(x) for x in p2}:
                    return False
                if i in settled:
                    continue
                if f0 != f2:
                    return False
                if g2 and not g0 and not (r0 and "IN" in q0):
                    return False                          # newly sendable
        mine0 = [x for i in own for x in facts0.get(i, [])]
        mine2 = [x for j in mine for x in facts2.get(j, [])]
        others = {x[0][0] for x in mine0} - set(own_books) - {None}
        o0 = sorted(((x[0], x[1]) for x in mine0 if x[0][0] in others), key=repr)
        o2 = sorted(((x[0], x[1]) for x in mine2 if x[0][0] in others), key=repr)
        if [x[0] for x in o0] != [x[0] for x in o2] or any(g and (f, True) not in o0 for f, g in o2):
            return False                                  # another book's order of the same clause changed, or became sendable
        my0 = [x for x in mine0 if x[0][0] not in others]
        my2 = [x for x in mine2 if x[0][0] not in others]
        if own_keys is not None:
            reps0 = R.get("reps") or {}
            sw0, sw1 = int(span0[0]), int(span0[1])
            mine_k, other0 = [], []
            for i in own:
                w = self._unread_by(i, cl0[i], tree)
                settles = w is not None and sw0 <= w[0] and w[1] <= sw1      # an unread book these words settle: every copy is its own
                rp = reps0.get(i) or []
                for k, x in enumerate(facts0.get(i, [])):
                    if x[0][0] in others:
                        continue                          # checked above with the other books
                    (mine_k if settles or (i, rp[k] if k < len(rp) else 0) in own_keys else other0).append(x)
            pool = list(my2)
            for x in other0:
                hit = next((y for y in pool if y[0] == x[0] and (x[1] or not y[1])), None)
                if hit is None:
                    return False                          # a copy outside the orders the choice is on changed, or became sendable
                pool.remove(hit)
            my0, my2 = mine_k, pool
        if not place:                                     # a book or account not read yet (ambiguous, unknown) is settled, never moved
            where = {(x[0][0], x[0][1]) for x in my0}
            bks = {w[0] for w in where}
            book_known = bool(where) and None not in bks
            acct_known = book_known and all(w[1] is not None for w in where)
            for f, _g, _p, _r in my2:
                if (book_known and f[0] not in bks) or (acct_known and f[1] is not None and (f[0], f[1]) not in where):
                    return False
        joined0 = {k0(p) for x in my0 for p in x[2]} - {"IN"}
        joined2 = {k2(p) for x in my2 for p in x[2]} - {"IN"}
        return joined2 <= joined0

    def _contexts(self, tree: dict) -> List[dict]:
        """Per clause of a pure parse: its books (as ``_books_for`` reads them), its named account (``_named_account``), its side and
        whether it is grouped."""
        grouped = {ci for g in tree["groups"] for ci in g["clauses"]}
        out = []
        for ci, c in enumerate(tree["clauses"]):
            bs = self._books_for(ci, c, tree)
            named = self._named_account(c, bs[0], tree) if bs else None
            v = c.get("verb") or {}
            nkey = None if not named else (("id", named["id"]) if named.get("id") else ("role", named.get("role")))
            out.append({"books": tuple(b["code"] for b in bs), "src": bs[0]["src"] if bs else None, "named": named, "nkey": nkey,
                        "side": (v.get("kind"), v.get("side")) if v else None, "carried": bool(v.get("carried")), "word": v.get("word"),
                        "grouped": ci in grouped, "span": list(c["span"]), "own_account": c["mods"].get("account") is not None,
                        "carried_account": c.get("carried_account") is not None, "unread": bool(bs and bs[0].get("unread"))})
        return out

    def _unread_by(self, ci: int, c: dict, tree: dict) -> Optional[Tuple[int, int]]:
        """Sixth review: the words of the book word that names no book which leaves clause ``ci``'s book unread (its own, or the one it
        takes: carried with its side, the opening or the closing phrase), else None."""
        bs = self._books_for(ci, c, tree)
        if not bs or not bs[0].get("unread"):
            return None
        sc = bs[0].get("scope") or {}
        ws = sc.get("word_span") or sc.get("span")
        return (int(ws[0]), int(ws[1])) if ws else None

    @staticmethod
    def _resolved_accounts(legs: List[dict]) -> Dict[int, dict]:
        """The accounts the legs of each clause were read in, and whether one of them is not settled (no account, or a blocker on it)."""
        out: Dict[int, dict] = {}
        for l in legs:
            d = out.setdefault(l["_ci"], {"ids": set(), "blocked": False})
            d["ids"].add(l["account_id"])
            if l["account_id"] is None or any(f["code"] == "ACCOUNT_NOT_IN_BOOK" or (f["code"] == "ACCOUNT_CHOICE" and f["severity"] == "blocker")
                                              for f in l["flags"]):
                d["blocked"] = True
        return out

    def _named_to_id(self, named: Optional[dict], code: Optional[str]) -> Optional[str]:
        """The account a named account resolves to in a book (as ``_account`` resolves it), else None."""
        lt = self._lifetime(code) if code else None
        if lt is None or not named:
            return None
        accts = self._accounts(lt["lifetime_id"])
        if named.get("id"):
            return named["id"] if any(x["account_id"] == named["id"] for x in accts) else None
        if named.get("role") == "PB":
            pbs = [x for x in accts if x["kind"] == "PB"]
            return pbs[0]["account_id"] if pbs else None
        a = next((x for x in accts if x["role"] == named.get("role")), None)
        return a["account_id"] if a is not None else None

    def _same_account(self, o: dict, r: Optional[dict], n: dict) -> bool:
        if o["nkey"] == n["nkey"]:
            return True
        if n["nkey"] is None or r is None or r["blocked"] or len(r["ids"]) != 1 or len(n["books"]) != 1 or not n["books"][0]:
            return False
        return self._named_to_id(n["named"], n["books"][0]) == next(iter(r["ids"]))

    def _aspects(self, o: dict, r: Optional[dict], n: dict, outside: bool) -> set:
        asp = set()
        # sixth review: an order whose book was never read (a book word that names no book, its own or the one it takes) is settled by a
        # choice, never moved: neither its book nor its account is a change to write out (``_reads_alike`` judges what it then reads)
        unread = bool(o.get("unread"))
        if o["books"] != n["books"] and not unread:
            asp.add("book")
        if not unread and not self._same_account(o, r, n):
            asp.add("account")
        if outside and o["side"] != n["side"]:
            asp.add("side")
        if outside and o["grouped"] != n["grouped"]:
            asp.add("grouped")
        return asp

    def _book_phrase(self, codes: Sequence[Optional[str]]) -> Optional[str]:
        """' in the X book' (or 'X and Y books') with a word that names each book alone, else None."""
        words = []
        for code in codes:
            if not code:
                return None
            w = next((x for x in (self.vocab.first_word(code), code) if self.vocab.words.get(str(x).lower()) == [code]), None)
            if w is None:
                return None
            words.append(w)
        return (" in the " + " and ".join(words) + (" books" if len(words) > 1 else " book")) if words else None

    def _account_phrase(self, o: dict, r: Optional[dict]) -> Optional[str]:
        """' at <account>': the account the order named, else the one it was read in (when that is settled), else None."""
        nd = o.get("named")
        if nd:
            if nd.get("id"):
                return " at " + str(nd["id"])
            role = str(nd.get("role") or "")
            if role == "US_EXCHANGE":
                return " at us exchange"
            if role == "PB":
                return " at pb"
            return " at pb " + role[3:].lower() if role.startswith("PB_") else None
        if r is None or r["blocked"] or len(r["ids"]) != 1:
            return None
        aid = next(iter(r["ids"]))
        code = o["books"][0] if len(o["books"]) == 1 else None
        lt = self._lifetime(code) if code and aid else None
        row = next((x for x in self._accounts(lt["lifetime_id"]) if x["account_id"] == aid), None) if lt is not None else None
        return " at " + _account_words_of(row) if row is not None else None

    def _guard_flag(self, tree: dict, ctx0: List[dict], res0: Dict[int, dict], f: dict) -> tuple:
        """``(span, choices)`` of one flag once every choice is checked (``_keep_others``): unchanged when no choice of it can reach another
        order; else each kept choice rewritten over one common span, the ones that cannot be kept dropped."""
        clauses = tree["clauses"]
        s0, s1 = f["span"]
        touched = [i for i, c in enumerate(clauses) if c["span"][0] < s1 and c["span"][1] > s0]
        if not touched or any(not (s0 <= clauses[i]["span"][0] and clauses[i]["span"][1] <= s1) for i in touched):
            return f["span"], f["choices"]               # words inside one clause (a book word of a phrase ...): not a rewrite of an order
        lo, hi = touched[0], touched[-1]
        nxt = next((c for c in clauses[hi + 1:] if c["kind"] in ("order", "strategy")), None)
        reach = (any(ctx0[i]["carried"] or ctx0[i]["src"] in ("carried", "suffix") for i in touched)                  # its inherited context
                 or (nxt is not None and ((nxt.get("verb") or {}).get("carried") or nxt.get("verb") is None))         # orders that take its side
                 or (hi == len(clauses) - 1 and len(clauses) > 1))                                                       # the closing phrase
        if not reach:
            return f["span"], f["choices"]
        place = f["code"] in self.PLACE_CODES
        kept = []
        for ch in f["choices"]:
            got = self._guard_choice(ctx0, res0, touched, s0, s1, ch["clause"], place)
            if got is not None:
                kept.append((ch, got))
        if not kept:
            return f["span"], None
        u0, u1 = min(g[0] for _ch, g in kept), max(g[1] for _ch, g in kept)
        return [u0, u1], [{"label": ch["label"], "clause": self.text[u0:a] + t + self.text[b:u1], "_i": ch.get("_i")} for ch, (a, b, t) in kept]

    def _guard_choice(self, ctx0: List[dict], res0: Dict[int, dict], touched: List[int], a: int, b: int, text: str, place: bool) -> Optional[tuple]:
        """One choice: ``(start, end, text)`` that re-reads every other order as the parser read it (a widened span when orders around it
        had to be written out), or None."""
        books = list(self.vocab.books)
        for _round in range(4):
            whole = self.text[:a] + text + self.text[b:]
            try:
                t2 = parse(whole, books)
            except SmartTicketError:
                return (a, b, text) if _round == 0 else None       # the page's own re-read refuses it the same way: nothing is staged
            ctx2 = self._contexts(t2)
            changes = self._choice_changes(ctx0, res0, ctx2, touched, a, b, len(text), place)
            if changes is None:
                return None
            if not changes:
                return a, b, text
            got = self._write_out(t2, whole, ctx0, res0, a, b, len(text), changes)
            if got is None:
                return None
            a, b, text = got
        return None

    def _choice_changes(self, ctx0: List[dict], res0: Dict[int, dict], ctx2: List[dict], touched: List[int], a: int, b: int, n: int,
                        place: bool) -> Optional[List[tuple]]:
        """What a re-read changed: ``[(kind, old clause, new clause, aspects)]`` (kind "out": another order; "in": the order the choice is
        on), or None when the orders cannot be matched one to one."""
        delta = n - (b - a)
        by_span = {tuple(c["span"]): j for j, c in enumerate(ctx2)}
        changes: List[tuple] = []
        region = []
        for i, o in enumerate(ctx0):
            s, e = o["span"]
            if a <= s and e <= b:
                region.append(i)
                continue
            if s < b and e > a:
                return None
            j = by_span.get((s, e) if e <= a else (s + delta, e + delta))
            if j is None:
                return None
            asp = self._aspects(o, res0.get(i), ctx2[j], True)
            if asp:
                changes.append(("out", i, j, asp))
        new = sorted(j for j, c in enumerate(ctx2) if a <= c["span"][0] and c["span"][1] <= a + n)
        before = [i for i in region if i < touched[0]]
        after = [i for i in region if i > touched[-1]]
        if len(new) < len(before) + len(after) + 1:
            return None
        subs = new[len(before):len(new) - len(after)]
        for i, j in list(zip(before, new[:len(before)])) + list(zip(after, new[len(new) - len(after):])):
            asp = self._aspects(ctx0[i], res0.get(i), ctx2[j], True)
            if asp:
                changes.append(("out", i, j, asp))
        if not place:
            if len(touched) > 1 and len(touched) != len(subs):
                return None                               # fifth review: the orders it is on cannot be matched one to one: never offered
            pairs = [(touched[0], j) for j in subs] if len(touched) == 1 else list(zip(touched, subs))
            for i, j in pairs:
                asp = self._aspects(ctx0[i], res0.get(i), ctx2[j], False)
                if asp:
                    changes.append(("in", i, j, asp))
        return changes

    def _write_out(self, t2: dict, whole: str, ctx0: List[dict], res0: Dict[int, dict], a: int, b: int, n: int, changes: List[tuple]) -> Optional[tuple]:
        """Write into the re-read text what the changed orders had inherited, and widen the region over them: ``(start, end, text)`` in the
        instruction's coordinates, or None when an order's reading cannot be written out (a side it never had, an unsettled account ...)."""
        lo, hi = a, a + n
        ins: List[Tuple[int, str]] = []
        for kind, i, j, asp in changes:
            o, r = ctx0[i], res0.get(i)
            s, e = t2["clauses"][j]["span"]
            need_book, need_acct = "book" in asp, "account" in asp
            if "grouped" in asp:
                return None
            if "side" in asp:
                if not o["carried"] or not o["word"]:
                    return None                              # an order without a side of its own would take one: never offered
                ins.append((s, o["word"] + " "))
                need_book = need_book or o["src"] == "carried"
                need_acct = need_acct or (not o["own_account"] and o["carried_account"])
            elif need_book:
                need_acct = False                            # the book first: the account it then reads is checked on the next re-read
            tail = ""
            if need_book:
                p = self._book_phrase(o["books"])
                if p is None:
                    return None
                tail += p
            if need_acct:
                p = self._account_phrase(o, r)
                if p is None:
                    return None
                tail += p
            if tail:
                ins.append((e, tail))
            if kind == "out":
                lo, hi = min(lo, s), max(hi, e)
        for pos, piece in sorted(ins, key=lambda x: x[0], reverse=True):
            whole = whole[:pos] + piece + whole[pos:]
        added = sum(len(piece) for pos, piece in ins if pos <= hi)
        delta = n - (b - a)
        return lo, hi - delta, whole[lo:hi + added]


# ---- the route ----------------------------------------------------------------------------------------------------------------------------
def parse_view(services, body, selected_book: Optional[str] = None) -> dict:
    """The 200 answer of ``POST /api/smart-ticket/parse`` (CHANGE_PLAN 2.4).  ``body`` = ``{"text", "book"?, "account_id"?}``; the selected
    book is ``body.book``, else ``selected_book`` (the ``?book=`` query), else the first open book.  Raises SmartTicketError 400
    BAD_REQUEST / TEXT_TOO_LONG / TOO_MANY_LEGS and 404 UNKNOWN_BOOK.  Pure: one reader, nothing written, fetched or subscribed."""
    if not isinstance(body, dict):
        raise SmartTicketError("BAD_REQUEST", "the body must be a JSON object")
    text = body.get("text")
    if not isinstance(text, str):
        raise SmartTicketError("BAD_REQUEST", "text is required and must be a string")
    acct = body.get("account_id")
    if acct is not None and not isinstance(acct, str):
        raise SmartTicketError("BAD_REQUEST", "account_id must be a string")
    book = body.get("book")
    if book is not None and not isinstance(book, str):
        raise SmartTicketError("BAD_REQUEST", "book must be a book code")
    now = int(services.clock.epoch())
    from . import books as _books
    with services.db.reader() as conn:
        rows = _books.books(conn)
        spec = [(b.code, b.name, b.profile) for b in rows]
        sel = book or selected_book
        sel = str(sel).strip().upper() if sel else None
        if sel:
            if sel not in {b.code for b in rows}:
                raise _UnknownBook("UNKNOWN_BOOK", f"book {sel!r} does not exist", {"book": sel})
        else:
            lids = _books.open_lifetimes(conn)
            sel = _books.book_code_of(lids[0]) if lids else (rows[0].code if rows else None)
        tree = parse(text, spec)
        return Resolver(services, conn, sel, acct, now, Vocab(spec)).run(tree)


def _route(req):
    try:
        return parse_view(req.rt, req.body if isinstance(req.body, dict) else {}, req.q("book"))
    except RejectError:
        raise
    except Exception as e:   # noqa: BLE001 - a fault of the parser: logged with its traceback, answered 500, nothing written
        log = getattr(req.rt, "log", None)
        if log is not None:
            log.exception("smart ticket: the parse failed")
        raise _ParseFailed("PARSE_FAILED", f"the instruction could not be read ({type(e).__name__}): nothing was staged") from e


def register_routes(router) -> None:
    """``POST /api/smart-ticket/parse``, registered NOT mutating (no request id; it writes nothing - CHANGE_PLAN 2.6 proves it)."""
    router.add("POST", ROUTE, _route, mutating=False)


def unsupported() -> List[dict]:
    return [{"item": "FX, bonds, OTC and private trades on the Smart Ticket", "state": "LIMITED",
             "wording": "FX, bonds, OTC and private trades open their own ticket from the Smart Ticket", "where": "Smart Ticket"},
            {"item": "One all-or-none order from the Smart Ticket", "state": "NOT_OFFERED",
             "wording": "the Smart Ticket fills separate tickets; one all-or-none order of options is built in the order ticket's Strategies tab",
             "where": "Smart Ticket"}]


__all__ = ["GRAMMAR", "MAX_CHARS", "MAX_LEGS", "SEVERITY_RANK", "FLAG_SEVERITY", "Token", "Vocab", "Resolver", "SmartTicketError", "normalise", "tokenize",
           "split_clauses", "parse_clause", "parse", "expand_strategy", "sort_flags", "parse_view", "register_routes", "unsupported"]

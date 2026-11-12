"""Smart Ticket grammar ``smart-ticket/1``: the pure layer only (FinSim 1.0.7.2, CHANGE_PLAN r1072 2.6; design FINSIM_TERMINAL_GAPS 3.4).

No runtime, no database, no clock: ``normalise``, ``tokenize``, ``split_clauses``, ``parse_clause``, ``parse`` and ``expand_strategy``
on texts with fictional names.  Every token class and every phrase of the table; clause splitting (the comma of "Dec 18, 2026" never
splits; "," / "and" split only before a clause); every production of the grammar (order, side-carried order, strategy, close, roll,
covered call / buy-write, collar) and every strategy expansion, for buy and for sell; the collision rule (one- and two-letter tickers
need capitals or "$", a lower-case keyword is never a ticker, the slot decides, TOKEN_AMBIGUOUS when two readings are complete); US
dates only with a year; the year rules after a day; the spans cover the text; ASCII normalisation; the limits (2,000 characters,
20 legs).  DETERMINISM: the whole corpus parsed twice in-process, and in three child interpreters with PYTHONHASHSEED 0, 1 and
random (no -I / -E, so the variable applies; PYTHONPATH = this tree's finsim folder), gives byte-identical
``json.dumps(..., sort_keys=True)``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

from tests import terminal_smart_corpus as corpus
from finsim.terminal import smart_ticket as st

FINSIM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def kinds(text):
    return [(t.kind, t.text) for t in st.tokenize(text)]


def clause(text):
    out = st.parse(text)
    assert len(out["clauses"]) == 1, out["clauses"]
    return out["clauses"][0]


def codes(c):
    return {f["code"] for f in c["flags"]}


def legs(text):
    return [(l["side"], l.get("right"), l.get("strike"), l.get("ratio")) for l in clause(text)["legs"]]


def corpus_digest() -> str:
    out = []
    for c in corpus.CASES:
        try:
            out.append(st.parse(c["text"]))
        except st.SmartTicketError as e:
            out.append({"error": e.code})
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode("ascii")).hexdigest()


CHILD = ("import hashlib, json\n"
         "from tests import terminal_smart_corpus as corpus\n"
         "from finsim.terminal import smart_ticket as st\n"
         "out = []\n"
         "for c in corpus.CASES:\n"
         "    try:\n"
         "        out.append(st.parse(c['text']))\n"
         "    except st.SmartTicketError as e:\n"
         "        out.append({'error': e.code})\n"
         "print(hashlib.sha256(json.dumps(out, sort_keys=True).encode('ascii')).hexdigest())\n")


class Normalise(unittest.TestCase):
    def test_ascii_mapping_and_unparsed_spans(self):
        text, bad = st.normalise("buy\u00a0100 \u201cACME\u201d \u2013 limit 2\u00d73\r\nsell 1 \uff21\uff23\uff2d\uff25\ttoday \u2605\u2605 ok")
        self.assertEqual('buy 100 "ACME" - limit 2x3\nsell 1 ACME today ?? ok', text)       # NFKC (full-width ACME), quotes, dash, NBSP, x, CRLF, tab
        self.assertEqual([[45, 47]], bad)                                                     # the two stars: one span, two "?"
        self.assertTrue(text.isascii())
        self.assertEqual((text, []), st.normalise(text))                                     # idempotent on its own output (the "?" are ASCII)
        self.assertEqual(("x?", [[1, 2]]), st.normalise("x\x00"))                           # a control character is unparsed too
        self.assertEqual(("", []), st.normalise(""))

    def test_non_ascii_becomes_an_unparsed_blocker(self):
        out = st.parse("buy 100 ACME \u00e9")
        self.assertEqual([{"span": [13, 14], "text": "?"}], out["unparsed"])
        self.assertIn("UNPARSED_TEXT", codes(out["clauses"][0]))
        self.assertTrue(json.dumps(out).isascii())


class Tokens(unittest.TestCase):
    def test_every_token_class(self):
        self.assertEqual([("occ", "ACME261218C00240000")], kinds("ACME261218C00240000"))
        self.assertEqual("ACME261218P00240000", st.tokenize("acme261218p00240000")[0].value)
        self.assertEqual([("fut", "ESZ26"), ("fut", "6EH27"), ("fut", "esm27")], kinds("ESZ26 6EH27 esm27"))
        self.assertEqual({"id": "ESZ26", "root": "ES", "year": 2026, "month": 12}, st.tokenize("ESZ26")[0].value)
        self.assertEqual([("word", "MAX26")], kinds("MAX26"))                               # MA is no futures root: a word
        self.assertEqual([("acct", "F1:US_EXCHANGE")], kinds("F1:US_EXCHANGE"))
        self.assertEqual([("date", "2026-12-18"), ("date", "12/18/26"), ("date", "12/18/2026")], kinds("2026-12-18 12/18/26 12/18/2026"))
        self.assertEqual(["2026-12-18"] * 3, [t.value for t in st.tokenize("2026-12-18 12/18/26 12/18/2026")])
        self.assertEqual([("strikes", "12/18"), ("strikes", "240/250"), ("strikes", "220 / 230 / 250 / 260"), ("strikes", "13/40/26")],
                         kinds("12/18 240/250 220 / 230 / 250 / 260 13/40/26"))               # a US date needs its year; 13/40/26 is no date
        self.assertEqual(["240", "250.5"], st.tokenize("240/250.5")[0].value)
        self.assertEqual([("strike_right", "240c"), ("strike_right", "240.5P")], kinds("240c 240.5P"))
        self.assertEqual({"k": "240.5", "right": "P"}, st.tokenize("240.5P")[0].value)
        self.assertEqual([("year2", "'26")], kinds("'26"))
        self.assertEqual([("pair", "EUR/USD")], kinds("EUR/USD"))
        self.assertEqual([("word", "Dec"), ("punct", "/"), ("word", "Jan")], kinds("Dec/Jan"))   # two months are never a currency pair
        self.assertEqual([("forced", "$ACME"), ("forced", "$0700-HK"), ("forced", "$c")], kinds("$ACME $0700-HK $c"))
        self.assertEqual("C", st.tokenize("$c")[0].value)
        nums = st.tokenize("225 41.20 $1,225.50 1,000 1.5k 2m 2% 2x 18th .50")
        self.assertEqual(["num"] * 10, [t.kind for t in nums])
        self.assertEqual(["225", "41.20", "1225.50", "1000", "1.5", "2", "2", "2", "18", "0.50"], [t.value["num"] for t in nums])
        self.assertEqual([None, None, None, None, "k", "m", "%", "x", "ord", None], [t.value["suffix"] for t in nums])
        self.assertEqual([False, False, True, False], [t.value["dollar"] for t in nums[:4]])
        self.assertEqual([("word", "BRK-B"), ("word", "BRK.B"), ("word", "buy-write"), ("word", "6E")], kinds("BRK-B BRK.B buy-write 6E"))
        self.assertEqual([("punct", c) for c in ",;:@()./&+"], kinds(", ; : @ ( ) . / & +"))
        self.assertEqual([("word", "a"), ("nl", "\n"), ("word", "b")], kinds("a\nb"))
        self.assertEqual([("word", "buy"), ("bad", "#now"), ("bad", "100ACME"), ("bad", "?")], kinds("buy #now 100ACME ?"))
        for t in st.tokenize("buy 100 ACME, then sell 2 ACME Dec 18, 2026 240c at the bid"):
            self.assertEqual(t.text.lower() if t.kind != "phrase" else t.lower, t.lower)
        self.assertEqual({"kind", "text", "start", "end", "value"}, set(st.tokenize("ACME")[0].to_dict()))

    def test_every_phrase_merges(self):
        for words, (pkind, pval) in sorted(st.PHRASES.items()):
            text = " ".join(words)
            toks = st.tokenize(text)
            self.assertEqual(1, len(toks), text)
            self.assertEqual(("phrase", {"kind": pkind, "value": pval}, 0, len(text)), (toks[0].kind, toks[0].value, toks[0].start, toks[0].end), text)
            upper = st.tokenize(text.upper())
            self.assertEqual(("phrase", pval), (upper[0].kind, upper[0].value["value"]), text)       # keywords match in any case
        self.assertEqual("BTC", st.tokenize("buy to close")[0].value["value"])                  # three words before two ("buy to" is no phrase)
        self.assertEqual("GTC", st.tokenize("good till cancelled")[0].value["value"])           # longest first: not GTD + a word
        self.assertEqual(["word", "nl", "word"], [t.kind for t in st.tokenize("sell\nshort")])  # a newline never joins a phrase
        self.assertEqual(["word", "num", "word", "num"], [t.kind for t in st.tokenize("stop 110 limit 109.5")])


class Splitting(unittest.TestCase):
    def split(self, text):
        segs, groups, stray = st.split_clauses(st.tokenize(text))
        return [text[s["tokens"][0].start:s["tokens"][-1].end] for s in segs], groups, stray

    def test_hard_and_soft_separators(self):
        self.assertEqual(["buy 100 ACME", "sell 50 WIDG", "buy 1 Q", "short 5 GIZM"], self.split("buy 100 ACME; sell 50 WIDG\nbuy 1 Q. short 5 GIZM")[0])
        self.assertEqual(["buy 100 ACME", "sell 50 WIDG", "buy 2 GIZM"], self.split("buy 100 ACME, and sell 50 WIDG then buy 2 GIZM")[0])
        self.assertEqual(["buy 100 ACME", "50 WIDG", "20 GIZM"], self.split("buy 100 ACME and 50 WIDG, 20 GIZM")[0])     # a quantity + an instrument starts one
        self.assertEqual(["buy 1 ACME Dec 18, 2026 240 call"], self.split("buy 1 ACME Dec 18, 2026 240 call")[0])           # the date's comma never splits
        self.assertEqual(["buy 1 ACME Dec 18, 26 C"], self.split("buy 1 ACME Dec 18, 26 C")[0])                            # a right is no instrument
        self.assertEqual(["buy 100 ACME in the Personal and Fund books"], self.split("buy 100 ACME in the Personal and Fund books")[0])
        self.assertEqual(["buy 100 ACME", "sell 2 ACME Dec 18 2026 240 calls"], self.split("buy 100 ACME plus sell 2 ACME Dec 18 2026 240 calls")[0])
        self.assertEqual(["buy 100 ACME", "close WIDG", "roll 2 ACME Dec 18 2026 240 calls to 250"],
                         self.split("buy 100 ACME and close WIDG & roll 2 ACME Dec 18 2026 240 calls to 250")[0])

    def test_parentheses(self):
        segs, groups, stray = self.split("(buy 1 ACME Dec 18 2026 240 call and sell 1 ACME Dec 18 2026 250 call) together; buy 100 WIDG")
        self.assertEqual(["buy 1 ACME Dec 18 2026 240 call", "sell 1 ACME Dec 18 2026 250 call", "buy 100 WIDG"], segs)
        self.assertEqual(1, len(groups))
        self.assertEqual(([0, 1], [69, 70], [71, 79]), (groups[0]["open"], groups[0]["close"], groups[0]["word"]))
        self.assertEqual([], stray)
        self.assertEqual([[12, 13]], self.split("buy 100 ACME)")[2])
        self.assertEqual([[13, 14]], self.split("buy 100 ACME (and more")[2])
        self.assertEqual(([[1, 2], [15, 16]], 1), (self.split("((buy 100 ACME))")[2], len(self.split("((buy 100 ACME))")[1])))


class Productions(unittest.TestCase):
    def test_order(self):
        c = clause("buy 200 ACME limit 225 gtc in the Fund book at us exchange")
        self.assertEqual(("order", "buy", 200, "ticker", "ACME"), (c["kind"], c["verb"]["side"], c["qty"]["value"], c["instrument"]["kind"], c["instrument"]["symbol"]))
        m = c["mods"]
        self.assertEqual(("LIMIT", "225", "GTC", ["F"], "US_EXCHANGE", None), (m["price"]["type"], m["price"]["limit"], m["tif"]["tif"],
                                                                               [b["codes"][0] for b in m["scope"]["books"]], m["account"]["role"], m["group"]))
        self.assertEqual([], c["flags"])
        self.assertEqual([{"role": "single", "option": False, "ratio": 1}], c["legs"])

    def test_every_side_word(self):
        for text, side in (("buy", "buy"), ("sell", "sell"), ("short", "SELL_SHORT"), ("sell short", "SELL_SHORT"), ("cover", "BUY_TO_COVER"),
                           ("buy to cover", "BUY_TO_COVER"), ("bto", "BTO"), ("sto", "STO"), ("btc", "BTC"), ("stc", "STC"), ("buy to open", "BTO"),
                           ("sell to open", "STO"), ("buy to close", "BTC"), ("sell to close", "STC"), ("BTC", "BTC"), ("buy-to-open", "BTO")):
            self.assertEqual(side, clause(f"{text} 1 ACME")["verb"]["side"], text)

    def test_prices_and_times_in_force(self):
        rows = [("market", "MARKET", {}), ("mkt", "MARKET", {}), ("at market", "MARKET", {}), ("at the market", "MARKET", {}),
                ("limit 225", "LIMIT", {"limit": "225"}), ("lmt 225", "LIMIT", {"limit": "225"}), ("lim 225", "LIMIT", {"limit": "225"}),
                ("at 225", "LIMIT", {"limit": "225", "from_at": True}), ("@ 225", "LIMIT", {"limit": "225", "from_at": True}), ("225 limit", "LIMIT", {"limit": "225"}),
                ("225 lmt", "LIMIT", {"limit": "225"}), ("limit at 225", "LIMIT", {"limit": "225"}), ("stop 110.5", "STOP", {"stop": "110.5"}),
                ("stop 110 limit 109.50", "STOP_LIMIT", {"stop": "110", "limit": "109.50"}), ("trail 1.50", "TRAILING_STOP", {"trail_amt": "1.50"}),
                ("trail 2%", "TRAILING_STOP", {"trail_pct": "2"}), ("trailing stop 2 pct", "TRAILING_STOP", {"trail_pct": "2"}),
                ("trailing 3 percent", "TRAILING_STOP", {"trail_pct": "3"}), ("at the bid", "LIMIT", {"quote": "bid"}), ("@ ask", "LIMIT", {"quote": "ask"}),
                ("at mid", "LIMIT", {"quote": "mid"}), ("limit at the ask", "LIMIT", {"quote": "ask"})]
        for words, typ, extra in rows:
            p = clause(f"buy 100 ACME {words}")["mods"]["price"]
            self.assertEqual(typ, p["type"], words)
            for k, v in extra.items():
                self.assertEqual(v, p.get(k), words)
        for words, tif in (("day", "DAY"), ("gtc", "GTC"), ("good till cancelled", "GTC"), ("good till canceled", "GTC"), ("next session", "NEXT_SESSION"),
                           ("gtd 2026-12-18", "GTD"), ("good till Dec 18 2026", "GTD"), ("until 12/18/26", "GTD"), ("until 18 Dec", "GTD")):
            self.assertEqual(tif, clause(f"buy 100 ACME {words}")["mods"]["tif"]["tif"], words)
        self.assertEqual({"m": 12, "d": 18, "y": None}, clause("buy 100 ACME until Dec 18")["mods"]["tif"]["date"])
        self.assertIn("TYPE_FROM_AT", codes(clause("buy 100 ACME at 225")))
        self.assertIn("DUPLICATE_FIELD", codes(clause("buy 100 ACME limit 225 stop 220")))

    def test_side_carried_and_missing_parts(self):
        out = st.parse("buy 100 ACME and 50 WIDG; 20 GIZM")
        self.assertEqual([False, True, True], [c["side_carried"] for c in out["clauses"]])
        self.assertEqual(["buy", "buy", "buy"], [c["verb"]["side"] for c in out["clauses"]])
        self.assertIn("SIDE_CARRIED", codes(out["clauses"][1]))
        first = st.parse("50 WIDG")["clauses"][0]
        self.assertEqual(["buy 50 WIDG", "sell 50 WIDG"], [ch["clause"] for f in first["flags"] if f["code"] == "MISSING_SIDE" for ch in f["choices"]])
        self.assertIn("MISSING_SIDE", codes(st.parse("close WIDG and 50 ACME")["clauses"][1]))   # a close has no side to carry
        self.assertIn("MISSING_QTY", codes(clause("buy ACME")))
        self.assertIn("MISSING_INSTRUMENT", codes(clause("buy 100")))
        self.assertIn("QTY_NOT_WHOLE", codes(clause("buy 1.5 ACME")))
        self.assertEqual(1500, clause("buy 1.5k ACME")["qty"]["value"])
        self.assertEqual(2000000, clause("buy 2m ACME")["qty"]["value"])
        self.assertIn("MISSING_QTY", codes(clause("buy $500 ACME")))

    def test_option_terms_in_every_order(self):
        for terms in ("Dec 18 2026 240 call", "Dec 18 2026 call 240", "240 call Dec 18 2026", "Dec 18 2026 240c", "240c Dec 18 2026", "2026-12-18 240 C",
                      "12/18/26 240 calls", "18 Dec 2026 240 call", "Dec 18th 2026 240 call", "Dec 18, 2026 240 call", "Dec 18 '26 240 call", "Dec 18, 26 240 call",
                      "December 18 2026 240 CALL"):
            o = clause(f"buy 1 ACME {terms}")["option"]
            self.assertIsNotNone(o, terms)
            self.assertEqual(("240", "C"), (o["strike"], o["right"]), terms)
            self.assertIn(o["expiry"], ({"m": 12, "d": 18, "y": 2026}, {"date": "2026-12-18"}), terms)
        self.assertEqual({"m": 12, "d": 18, "y": None}, clause("buy 1 ACME Dec 18 240 put")["option"]["expiry"])          # the year is inferred later
        self.assertEqual({"m": 12, "d": None, "y": 2026}, clause("buy 1 ACME Dec 2026 240 put")["option"]["expiry"])      # a month with its year
        for bad in ("240 Dec 18 2026 call", "call 240 Dec 18 2026", "Dec 18 2026 call", "Dec 20 call", "Dec 240 call", "Dec 18 26 240 call", "12/18 240 call",
                    "Dec 18 2050 call", "20 Dec 2026 call"):
            c = clause(f"buy 1 ACME {bad}")
            self.assertIsNone(c["option"], bad)                                          # never a guessed contract
            self.assertTrue({"MISSING_INSTRUMENT", "UNPARSED_TEXT"} & codes(c), bad)

    def test_year_rules(self):
        self.assertEqual({"m": 12, "d": 18, "y": 2026}, clause("buy 1 ACME Dec 18 2026 240 call")["option"]["expiry"])   # 2000-2099 after a day: the year
        self.assertEqual({"m": 12, "d": 18, "y": None}, clause("buy 1 ACME Dec 18 6000 call")["option"]["expiry"])       # ... any other number: a strike
        self.assertEqual("6000", clause("buy 1 ACME Dec 18 6000 call")["option"]["strike"])
        self.assertIsNone(clause("buy 1 ACME Dec 18 26 240 call")["option"])             # a bare 2-digit number after a day is a strike
        self.assertEqual("26", clause("buy 1 ACME Dec 18 26 call")["option"]["strike"])

    def test_strategies_and_their_expansions(self):
        self.assertEqual([("BTO", "C", "240", 1), ("STO", "C", "250", 1)], legs("buy 1 ACME Dec 18 2026 240/250 call spread"))
        self.assertEqual([("STO", "C", "240", 1), ("BTO", "C", "250", 1)], legs("sell 1 ACME Dec 18 2026 240/250 call spread"))
        # 1.0.7.2 review: the side decides debit or credit, never the typed strike order (a bought put spread buys the higher put)
        self.assertEqual([("STO", "P", "240", 1), ("BTO", "P", "250", 1)], legs("buy 1 ACME Dec 18 2026 240/250 put spread"))
        self.assertEqual([("STO", "P", "240", 1), ("BTO", "P", "250", 1)], legs("buy 1 ACME Dec 18 2026 240/250 puts vertical"))
        self.assertEqual([("BTO", "P", "240", 1), ("STO", "P", "250", 1)], legs("sell 1 ACME Dec 18 2026 240/250 put spread"))
        self.assertEqual([("BTO", "C", "240", 1), ("STO", "C", "250", 1)], legs("buy 1 ACME Dec 18 2026 250/240 call spread"))
        self.assertEqual([("BTO", "C", "210", 1), ("STO", "C", "220", 1), ("STO", "C", "250", 1), ("BTO", "C", "260", 1)],
                         legs("buy 1 ACME Dec 18 2026 210/220/250/260 call condor"))                  # a condor of calls: the wings bought
        self.assertEqual([("STO", "P", "210", 1), ("BTO", "P", "220", 1), ("BTO", "P", "250", 1), ("STO", "P", "260", 1)],
                         legs("sell 1 ACME Dec 18 2026 210/220/250/260 put condor"))
        self.assertIn("TOKEN_AMBIGUOUS", codes(clause("buy 1 ACME Dec 18 2026 210/220/250/260 condor")))   # never silently an iron condor
        self.assertIn("RIGHT_DEFAULT", codes(clause("buy 1 ACME Dec 18 2026 220/230/240 butterfly")))      # calls filled in: a check
        self.assertEqual([("BTO", "C", "240", 1), ("STO", "C", "250", 1)], legs("buy 1 ACME Dec 18 2026 240/250 call vertical"))
        self.assertEqual([("BTO", "C", "240", 1), ("BTO", "P", "240", 1)], legs("buy 1 ACME Dec 18 2026 240 straddle"))
        self.assertEqual([("STO", "C", "240", 1), ("STO", "P", "240", 1)], legs("sell 1 ACME Dec 18 2026 240 straddle"))
        self.assertEqual([("BTO", "P", "220", 1), ("BTO", "C", "240", 1)], legs("buy 1 ACME Dec 18 2026 220/240 strangle"))
        self.assertEqual([("STO", "P", "220", 1), ("BTO", "P", "230", 1), ("BTO", "C", "250", 1), ("STO", "C", "260", 1)],
                         legs("buy 1 ACME Dec 18 2026 220/230/250/260 iron condor"))                  # buy = debit
        self.assertEqual([("BTO", "P", "220", 1), ("STO", "P", "230", 1), ("STO", "C", "250", 1), ("BTO", "C", "260", 1)],
                         legs("sell 1 ACME Dec 18 2026 220/230/250/260 iron condor"))                 # sell = credit
        self.assertEqual([("BTO", "P", "220", 1), ("STO", "P", "230", 1), ("STO", "C", "230", 1), ("BTO", "C", "240", 1)],
                         legs("sell 1 ACME Dec 18 2026 220/230/240 iron butterfly"))
        self.assertEqual([("STO", "P", "220", 1), ("BTO", "P", "230", 1), ("BTO", "C", "230", 1), ("STO", "C", "240", 1)],
                         legs("buy 1 ACME Dec 18 2026 220/230/240 iron fly"))
        self.assertEqual([("BTO", "C", "220", 1), ("STO", "C", "230", 2), ("BTO", "C", "240", 1)], legs("buy 1 ACME Dec 18 2026 220/230/240 butterfly"))
        self.assertEqual([("BTO", "P", "220", 1), ("STO", "P", "230", 2), ("BTO", "P", "240", 1)], legs("buy 1 ACME Dec 18 2026 220/230/240 put fly"))
        self.assertEqual([("STO", "C", "220", 1), ("BTO", "C", "230", 2), ("STO", "C", "240", 1)], legs("sell 1 ACME Dec 18 2026 220/230/240 call butterfly"))
        cal = clause("buy 1 ACME Jan 15 2027/Dec 18 2026 240 put calendar")["legs"]      # near STO, far BTO whatever the typed order
        self.assertEqual([("STO", {"m": 12, "d": 18, "y": 2026}), ("BTO", {"m": 1, "d": 15, "y": 2027})], [(l["side"], l["expiry"]) for l in cal])
        self.assertEqual([("BTO", "C", "240", 1), ("STO", "C", "250", 1)], legs("buy 1 ACME 240/250 call spread Dec 18 2026"))    # the expiry after the word
        self.assertEqual(["STRATEGY_EXPANDED", "STRATEGY_SIDE_CONVENTION"], sorted(codes(clause("buy 1 ACME Dec 18 2026 240/250 call spread"))))
        self.assertIn("STRATEGY_SIDE_CONVENTION", codes(clause("sell 1 ACME Dec 18 2026 220/230/250/260 iron condor")))
        for bad, code in (("buy 1 ACME Dec 18 2026 240/220 strangle", "STRIKE_ORDER"), ("buy 1 ACME Dec 18 2026 240/230/220 butterfly", "STRIKE_ORDER"),
                          ("buy 1 ACME Dec 18 2026 240 call spread", "MISSING_INSTRUMENT"), ("buy 1 ACME Dec 18 2026 240/250 vertical", "MISSING_INSTRUMENT"),
                          ("buy 1 ACME Dec 18 2026 240 call straddle", "MISSING_INSTRUMENT"), ("buy 1 ACME Dec 18 2026/Jan 15 2027 240 calendar", "MISSING_INSTRUMENT"),
                          ("buy 1 ACME Dec 18 2026/Dec 18 2026 240 call calendar", "MISSING_INSTRUMENT"), ("short 1 ACME Dec 18 2026 240/250 call spread", "MISSING_SIDE")):
            self.assertIn(code, codes(clause(bad)), bad)
        f = next(f for f in clause("buy 1 ACME Dec 18 2026 240/220 strangle")["flags"] if f["code"] == "STRIKE_ORDER")
        self.assertEqual(["buy 1 ACME Dec 18 2026 220/240 strangle"], [c["clause"] for c in f["choices"]])

    def test_covered_call_collar_close_and_roll(self):
        c = clause("covered call 100 ACME Dec 18 2026 240")
        self.assertEqual(("covered", [("BUY", None, None, 1), ("STO", "C", "240", 1)]), (c["kind"], legs("covered call 100 ACME Dec 18 2026 240")))
        self.assertEqual("covered", clause("buy write 200 ACME Dec 18 2026 250")["kind"])
        self.assertEqual("covered", clause("buy-write 200 ACME Dec 18 2026 250")["kind"])
        c = clause("collar 100 ACME Dec 18 2026 220/260")
        self.assertEqual(("collar", ["220", "260"]), (c["kind"], c["strategy"]["strikes"]))
        self.assertEqual(["shares_if_not_held", "option", "option"], [l["role"] for l in c["legs"]])
        for text, cls in (("close ACME", None), ("close out ACME", None), ("close ACME calls", "calls"), ("close ACME puts", "puts"), ("close ACME options", "options"),
                          ("close ACME shares", "shares"), ("close 2 ACME Dec 18 2026 240 calls", None)):
            c = clause(text)
            self.assertEqual(("close", cls), (c["kind"], c["close_class"]), text)
        self.assertIsNotNone(clause("close 2 ACME Dec 18 2026 240 calls")["option"])
        c = clause("roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250")
        self.assertEqual(("roll", {"m": 1, "d": 15, "y": 2027}, "250"), (c["kind"], c["roll_to"]["expiry"], c["roll_to"]["strike"]))
        self.assertEqual(["roll_old", "roll_new"], [l["role"] for l in c["legs"]])
        self.assertEqual((None, "250"), (clause("roll ACME Dec 18 2026 240 calls to 250")["roll_to"]["expiry"], clause("roll ACME Dec 18 2026 240 calls to 250")["roll_to"]["strike"]))
        self.assertIsNone(clause("roll ACME Dec 18 2026 240 calls to Jan 15 2027")["roll_to"]["strike"])
        self.assertIn("MISSING_INSTRUMENT", codes(clause("roll 2 ACME Dec 18 2026 240 calls")))

    def test_instruments_and_scopes(self):
        self.assertEqual(("occ", "ACME261218C00240000"), (clause("buy 1 ACME261218C00240000")["instrument"]["kind"], clause("buy 1 ACME261218C00240000")["instrument"]["id"]))
        self.assertEqual("future", clause("buy 1 ESZ26")["instrument"]["kind"])
        fm = clause("buy 2 ES Mar")["instrument"]
        self.assertEqual(("future_month", "ES", 3, None), (fm["kind"], fm["root"], fm["month"], fm["year"]))
        self.assertEqual(2027, clause("buy 2 ES Mar 2027")["instrument"]["year"])
        self.assertEqual(("front", "ES"), (clause("sell 1 front ES")["instrument"]["kind"], clause("sell 1 front ES")["instrument"]["root"]))
        self.assertEqual(("pair", "EURUSD"), (clause("buy 1m EUR/USD")["instrument"]["kind"], clause("buy 1m EUR/USD")["instrument"]["symbol"]))
        self.assertEqual(("ticker", "ES"), (clause("buy 2 ES")["instrument"]["kind"], clause("buy 2 ES")["instrument"]["symbol"]))   # a root without a month is a ticker
        out = st.parse("in the Fund book: buy 100 ACME; buy 100 WIDG")
        self.assertEqual(([["F"]], [0, 17]), ([b["codes"] for b in out["prefix"]["books"]], out["prefix"]["span"]))      # "in the Fund book:"
        out = st.parse("buy 100 ACME; sell 50 WIDG; in the Fund book")                   # a book phrase alone at the end
        self.assertEqual((2, None), (len(out["clauses"]), out["suffix"]["clause"]))
        out = st.parse("buy 100 ACME, sell 50 WIDG in the Fund book")                    # ... or ending the last clause
        self.assertEqual(1, out["suffix"]["clause"])
        # 1.0.7.2 review: only the last order's own modifiers after the phrase: it still ends the text; other words: the clause's own
        self.assertEqual(0, st.parse("buy 100 WIDG in the Fund book gtc")["suffix"]["clause"])
        self.assertEqual(1, st.parse("buy 100 ACME, buy 50 WIDG in the Fund book at market")["suffix"]["clause"])
        self.assertIsNone(st.parse("buy 100 WIDG in the Fund book on monday")["suffix"])
        self.assertEqual(["P", "F"], [b["codes"][0] for b in clause("buy 1 ACME in the Personal and Fund books")["mods"]["scope"]["books"]])
        self.assertIsNone(clause("buy 1 ACME Fund")["mods"]["scope"])                    # a bare book word needs "in" / "for" or "book"
        self.assertEqual(["F"], [b["codes"][0] for b in clause("buy 1 ACME Fund book")["mods"]["scope"]["books"]])
        self.assertEqual("PB_MS", clause("buy 1 ACME at pb ms")["mods"]["account"]["role"])
        self.assertEqual("PB", clause("buy 1 ACME in prime broker")["mods"]["account"]["role"])
        self.assertEqual("F1:US_EXCHANGE", clause("buy 1 ACME in F1:US_EXCHANGE")["mods"]["account"]["id"])
        custom = st.parse("buy 1 ACME in the Alpha book", [("P", "Personal Book", "INDIVIDUAL"), ("F", "Alpha Fund", "FUND")])
        self.assertEqual(["F"], [b["codes"][0] for b in custom["clauses"][0]["mods"]["scope"]["books"]])
        shared = st.parse("buy 1 ACME in the Fund book", [("F", "Fund Book", "FUND"), ("I", "Fund Partners", "INSTITUTION")])
        self.assertIn("TOKEN_AMBIGUOUS", codes(shared["clauses"][0]))
        self.assertIn("UNPARSED_TEXT", codes(clause("buy 1 ACME in the Foo book")))


class Collisions(unittest.TestCase):
    def test_short_tickers_need_capitals_or_dollar(self):
        c = clause("buy 100 q")
        self.assertIsNone(c["instrument"])
        f = next(f for f in c["flags"] if f["code"] == "MISSING_INSTRUMENT")
        self.assertEqual(["buy 100 $Q"], [ch["clause"] for ch in f["choices"]])
        self.assertEqual("Q", clause("buy 100 Q")["instrument"]["symbol"])
        self.assertEqual("Q", clause("buy 100 $q")["instrument"]["symbol"])
        self.assertEqual("ZZ", clause("buy 100 ZZ")["instrument"]["symbol"])
        self.assertIsNone(clause("buy 100 zz")["instrument"])

    def test_keywords_in_the_instrument_slot(self):
        self.assertIsNone(clause("buy 100 all")["instrument"])                           # a lower-case keyword is never a ticker
        self.assertEqual("ALL", clause("sell all ALL")["instrument"]["symbol"])          # quantity slot: the keyword; instrument slot: the ticker
        self.assertEqual("all", clause("sell all ALL")["qty"]["kind"])
        self.assertEqual("BTC", clause("buy 100 BTC")["instrument"]["symbol"])           # a side word in the instrument slot
        self.assertEqual("BTC", clause("BTC 1 ACME Dec 18 2026 240 call")["verb"]["side"])
        self.assertEqual("MAR", clause("buy 100 MAR")["instrument"]["symbol"])
        self.assertEqual(3, clause("buy 1 MAR Mar 19 2027 50 call")["option"]["expiry"]["m"])   # the ticker, then the month
        self.assertEqual("ACME", clause("buy 100 acme")["instrument"]["symbol"])          # not a keyword: a ticker in any case
        self.assertEqual("LMT", clause("buy 100 LMT")["instrument"]["symbol"])
        for word in ("now", "key", "so", "it", "on"):                                     # G 3.4.5's collision words: English unless in capitals or "$"
            self.assertIsNone(clause(f"buy 100 {word}")["instrument"], word)
            self.assertEqual(word.upper(), clause(f"buy 100 {word.upper()}")["instrument"]["symbol"], word)
        for text in ("buy 2 NQ", "close ES"):                                             # a futures root alone: shares or the future?
            f = next(f for f in clause(text)["flags"] if f["code"] == "TOKEN_AMBIGUOUS")
            sym = text.split()[-1]
            self.assertEqual([text.replace(sym, "$" + sym), text.replace(sym, "front " + sym)], [ch["clause"] for ch in f["choices"]])
        self.assertNotIn("TOKEN_AMBIGUOUS", codes(clause("buy 2 $NQ")))
        self.assertNotIn("TOKEN_AMBIGUOUS", codes(clause("buy 1 ES Dec 18 2026 60 call")))     # an option names its terms: a ticker
        f = next(f for f in clause("buy ACME 100")["flags"] if f["code"] == "UNPARSED_TEXT")
        self.assertNotIn("choices", f)                                                    # no quantity: the bare number gets no "limit" choice
        self.assertEqual(("LIMIT", "30"), (clause("buy 100 ACME LMT 30")["mods"]["price"]["type"], clause("buy 100 ACME LMT 30")["mods"]["price"]["limit"]))

    def test_two_complete_readings_are_ambiguous(self):
        c = clause("buy 100 LOT TOGETHER")                                                # "LOT": a unit or a ticker; "TOGETHER": a ticker or a group word
        f = next(f for f in c["flags"] if f["code"] == "TOKEN_AMBIGUOUS")
        self.assertEqual(("blocker", [0, 20]), (f["severity"], f["span"]))
        self.assertEqual(["buy 100 $LOT TOGETHER", "buy 100 lots $TOGETHER"], [ch["clause"] for ch in f["choices"]])
        for ch in f["choices"]:
            self.assertNotIn("TOKEN_AMBIGUOUS", codes(clause(ch["clause"])), ch)         # each choice reads one way only
        self.assertNotIn("TOKEN_AMBIGUOUS", codes(clause("buy 100 SH")))                  # one complete reading: the ticker
        self.assertNotIn("TOKEN_AMBIGUOUS", codes(clause("buy 100 sh ACME")))             # lower-case sh is the unit only
        self.assertEqual("future_month", clause("buy 2 ES Mar 2027")["instrument"]["kind"])        # the equity reading is incomplete
        self.assertEqual(("ticker", "6000"), (clause("buy 1 ES Mar 2027 6000 call")["instrument"]["kind"], clause("buy 1 ES Mar 2027 6000 call")["option"]["strike"]))


class SpansLimitsAndGroups(unittest.TestCase):
    def test_spans_cover_the_text(self):
        text = "buy 200 ACME limit 225, sell 2 ACME Dec 18, 2026 240 calls, and buy 50 WIDG at market in the Fund book"
        toks = st.tokenize(text)
        for t in toks:
            self.assertEqual(t.text, text[t.start:t.end] if t.kind != "phrase" else " ".join(text[t.start:t.end].split()))
        covered = set()
        for t in toks:
            covered.update(range(t.start, t.end))
        self.assertEqual({i for i, ch in enumerate(text) if not ch.isspace()}, {i for i in covered if not text[i].isspace()})
        out = st.parse(text)
        self.assertEqual(["buy 200 ACME limit 225", "sell 2 ACME Dec 18, 2026 240 calls", "buy 50 WIDG at market in the Fund book"],
                         [text[c["span"][0]:c["span"][1]] for c in out["clauses"]])
        self.assertEqual([c["text"] for c in out["clauses"]], [text[c["span"][0]:c["span"][1]] for c in out["clauses"]])
        for c in out["clauses"]:
            for key in ("price", "tif", "scope", "account", "group"):
                m = c["mods"].get(key)
                if m:
                    self.assertTrue(c["span"][0] <= m["span"][0] < m["span"][1] <= c["span"][1], key)

    def test_limits(self):
        ok = "buy 1 ACME " + "x" * (st.MAX_CHARS - 11)
        self.assertEqual(st.MAX_CHARS, len(ok))
        st.parse(ok)
        with self.assertRaises(st.SmartTicketError) as cm:
            st.parse(ok + "x")
        self.assertEqual(("TEXT_TOO_LONG", 400, {"length": 2001, "max": 2000}), (cm.exception.code, cm.exception.http, cm.exception.details))
        with self.assertRaises(st.SmartTicketError):
            st.parse("\u00e9" * 2001)                                                      # counted after normalisation
        self.assertEqual(20, st.parse("; ".join(["buy 1 ACME"] * 20))["legs"])
        self.assertEqual(20, st.parse("; ".join(["sell 1 ACME Dec 18 2026 210/220/250/260 iron condor"] * 5))["legs"])
        for text in ("; ".join(["buy 1 ACME"] * 21), "; ".join(["buy 1 ACME Dec 18 2026 220/230/240 butterfly"] * 7),
                     "; ".join(["buy 1 ACME"] * 7) + " in the Personal and Fund and Institutional books"):
            with self.assertRaises(st.SmartTicketError) as cm:
                st.parse(text)
            self.assertEqual("TOO_MANY_LEGS", cm.exception.code, text[:60])
        self.assertEqual((2000, 20, "smart-ticket/1"), (st.MAX_CHARS, st.MAX_LEGS, st.GRAMMAR))

    def test_groups(self):
        out = st.parse("buy 100 WIDG; buy 200 ACME limit 225 and sell 2 ACME Dec 18 2026 240 calls as a package")
        # simplify round 1: the run of orders the words end, whatever their names (7b634e2 took the ACME orders only, clauses [1, 2], and
        # left "buy 100 WIDG" a ticket of its own): every order the words may mean is held until "as separate tickets" is chosen
        self.assertEqual([{"id": "G1", "kind": "word", "clauses": [0, 1, 2], "span": [0, 87], "word_span": [75, 87]}], out["groups"])
        out = st.parse("(buy 1 ACME Dec 18 2026 240 call and sell 1 ACME Dec 18 2026 250 call)")
        self.assertEqual(("paren", [0, 1]), (out["groups"][0]["kind"], out["groups"][0]["clauses"]))
        out = st.parse("buy 1 ACME Dec 18 2026 240 call; buy 1 ACME Dec 18 2026 250 call aon; buy 1 ACME Dec 18 2026 260 call together")
        self.assertEqual([[0, 1], [2]], [g["clauses"] for g in out["groups"]])            # a group word ends its run; the next starts a new one
        for word in ("as a package", "as one order", "together", "all or none", "aon", "AON"):
            self.assertEqual(1, len(st.parse(f"buy 1 ACME Dec 18 2026 240 call and sell 1 ACME Dec 18 2026 250 call {word}")["groups"]), word)


FUZZ_WORDS = ("buy sell short cover bto sto btc stc close roll collar covered call buy write to out all half 100 2 1.5 1k 2m $225 225 240 250 260 "
              "240/250 220/230/240 220/230/250/260 240c 250p 12/18/26 12/18 2026-12-18 Dec 18 2026 Jan 15 2027 '26 , ; ( ) . / & + @ at the market mkt "
              "limit lmt stop trail trailing 2% pct day gtc gtd good till until next session in for book books Fund Personal Institutional P F I us "
              "exchange pb ms prime broker as a package together aon all or none call calls put puts c p C P straddle strangle spread vertical butterfly "
              "fly iron condor calendar ACME WIDG Q ESZ26 ES NQ front active Mar EURUSD EUR/USD ACME261218C00240000 SPX SPXW shares contracts lots x "
              "F1:US_EXCHANGE and then plus options ? # 0 -1 99999999999 .5 18th").split(" ")


class Fuzz(unittest.TestCase):
    def test_random_word_salad_never_breaks_the_parser(self):
        """A seeded word salad from the vocabulary and corpus texts with a word spliced in: ``parse`` answers or raises its own 400s only,
        and every answer is JSON in ASCII (no other exception reaches the route)."""
        import random
        rng = random.Random(20261105)
        texts = []
        for _ in range(1200):
            texts.append(" ".join(rng.choice(FUZZ_WORDS) for _ in range(rng.randint(1, 14))))
            base = rng.choice(corpus.CASES)["text"]
            if base:
                i = rng.randrange(len(base))
                texts.append(base[:i] + rng.choice(FUZZ_WORDS) + base[i:])
        for t in texts:
            try:
                out = st.parse(t)
            except st.SmartTicketError as e:
                self.assertIn(e.code, ("TEXT_TOO_LONG", "TOO_MANY_LEGS"), t)
                continue
            self.assertTrue(json.dumps(out, sort_keys=True).isascii(), t)
            for c in out["clauses"]:
                self.assertTrue(0 <= c["span"][0] <= c["span"][1] <= len(out["text"]), t)


class FifthReviewGrammar(unittest.TestCase):
    """1.0.7.2 fifth review, the pure layer: a number written before "limit" is the "225 limit" price only when it is none of the
    contract's words (a year or a day typed after a month, a roll's target strike) and no price follows the word; a connector alone
    between two groups is a separator, never an order; an account typed with "at" / "in" has that word in its span; a book phrase keeps
    the span of its book words alone (``book_span``)."""

    def test_a_year_or_day_typed_before_limit_is_never_the_price(self):
        for text, want_exp, want_price in (("buy 2 ACME 240c Dec 18 2026 limit", {"m": 12, "d": 18, "y": 2026}, None),
                                           ("buy 2 ACME 240c Dec 18 2026 limit 2.50", {"m": 12, "d": 18, "y": 2026}, "2.50"),
                                           ("buy 2 WIDG 45c Dec 18 2026 lmt", {"m": 12, "d": 18, "y": 2026}, None),
                                           ("buy 2 ACME 240c Dec 18 limit 2.50", {"m": 12, "d": 18, "y": None}, "2.50"),
                                           ("buy 2 ACME 240c 18 Dec 2026 limit", {"m": 12, "d": 18, "y": 2026}, None),
                                           ("buy 2 ACME Dec 18 2026 240 calls 2.50 limit", {"m": 12, "d": 18, "y": 2026}, "2.50"),
                                           ("buy 2 ACME 240c Dec 18 2026 together limit 0.50", {"m": 12, "d": 18, "y": 2026}, "0.50")):
            with self.subTest(text=text):
                c = clause(text)
                self.assertEqual(want_exp, c["option"]["expiry"])
                p = c["mods"]["price"]
                self.assertEqual(("LIMIT", want_price), (p["type"], p.get("limit")))
                self.assertEqual([], c["unparsed"])
                if want_price is None:                                # a bare "limit": MISSING_PRICE, never a price the owner did not type
                    self.assertIn("MISSING_PRICE", codes(c))
        self.assertEqual(("LIMIT", "225"), (clause("buy 100 ACME 225 limit")["mods"]["price"]["type"], clause("buy 100 ACME 225 limit")["mods"]["price"]["limit"]))
        c = clause("buy 100 ACME 225 limit 226")                      # a price after the word is the price: the number before it is not read
        self.assertEqual("226", c["mods"]["price"]["limit"])
        self.assertTrue(c["unparsed"])

    def test_a_roll_target_strike_typed_before_limit_is_the_strike(self):
        for text, strike, price in (("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 limit 1.20", "260", "1.20"),
                                    ("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 260 limit", "260", None),
                                    ("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 limit 1.20", None, "1.20"),
                                    ("roll 3 ACME Dec 18 2026 250 calls to 260 limit", "260", None),
                                    ("roll 1 WIDG Dec 2026 45 calls to Dec 2026 35 aon limit 1.00", "35", "1.00")):
            with self.subTest(text=text):
                c = clause(text)
                self.assertEqual("roll", c["kind"])
                self.assertEqual(strike, c["roll_to"]["strike"])
                self.assertEqual(price, c["mods"]["price"].get("limit"))
                self.assertEqual([], c["unparsed"])
        self.assertEqual({"m": 1, "d": 15, "y": 2027}, clause("roll 3 ACME Dec 18 2026 250 calls to Jan 15 2027 limit 1.20")["roll_to"]["expiry"])

    def test_a_connector_alone_between_groups_is_a_separator(self):
        for text in ("(buy 50 WIDG and sell 1 WIDG Nov 20 2026 45 call) then (buy 100 ACME, sell 1 ACME Dec 18 2026 260 call)",
                     "(200 WIDG), (buy 100 ACME, sell 1 ACME Dec 18 2026 260 call)", "(buy 100 ACME) and (sell 1 ACME Dec 18 2026 260 call)"):
            with self.subTest(text=text):
                out = st.parse(text)
                self.assertFalse([f for f in out["flags"] if f["code"] == "UNPARSED_TEXT" and f["text"].startswith(("'then'", "','", "'and'"))], out["flags"])
                self.assertEqual(2, len(out["groups"]))
        segs, _g, _s = st.split_clauses(st.tokenize("buy 100 ACME and; sell 50 WIDG"))
        self.assertEqual(["buy 100 ACME and", "sell 50 WIDG"], [" ".join(t.text for t in s["tokens"]) for s in segs])

    def test_account_and_book_spans(self):
        text = "sell 2 ACME Dec 18 2026 250 calls in the Institutional book at us exchange"
        c = clause(text)
        acct, scope = c["mods"]["account"], c["mods"]["scope"]
        self.assertEqual("at us exchange", text[acct["span"][0]:acct["span"][1]])            # the account choice replaces it whole
        self.assertEqual("in the Institutional book", text[scope["book_span"][0]:scope["book_span"][1]])
        self.assertEqual("in the Institutional book at us exchange", text[scope["span"][0]:scope["span"][1]])
        text = "buy 100 ACME at pb ms"
        a = clause(text)["mods"]["account"]
        self.assertEqual("at pb ms", text[a["span"][0]:a["span"][1]])


class SixthReviewGrammar(unittest.TestCase):
    """1.0.7.2 sixth review, the pure layer: a one- or two-digit number written right after "<month> <day>" with no comma ("Dec 18 26", "Jan 15
    27", "December 18 26": trader shorthand for a year) is never the "<n> limit" price - before a bare "limit" / "lmt" it stays one of the
    contract's words, so the price is MISSING_PRICE and the reading names the year form; the "<n> limit" price after the contract's own
    words, and a price after the word, are read as before; an unknown book word carries with the side and closes the text as a known one
    does; a group word inside parentheses is a modifier of its order."""

    def test_a_two_digit_year_after_a_month_and_day_is_never_the_price(self):
        for text in ("bto 2 ACME 240c Dec 18 26 limit", "sell 2 ACME 250c Dec 18 26 lmt", "bto 3 ACME 250 puts Jan 15 27 limit", "buy 2 ACME 240 calls Dec 18 26 limit",
                     "buy 2 ACME 240c December 18 26 limit", "bto 1 WIDG 45p Nov 20 26 lmt gtc"):
            with self.subTest(text=text):
                c = clause(text)
                p = c["mods"]["price"]
                self.assertEqual(("LIMIT", None), (p["type"], p.get("limit")))
                self.assertIn("MISSING_PRICE", codes(c))
        for text, price in (("buy 2 ACME Dec 18 2026 240 calls 26 limit", "26"), ("buy 2 ACME 240c Dec 18 26 limit 2.50", "2.50"), ("buy 100 ACME 26 limit", "26")):
            with self.subTest(text=text):
                self.assertEqual(("LIMIT", price), (clause(text)["mods"]["price"]["type"], clause(text)["mods"]["price"].get("limit")))

    def test_an_unknown_book_word_carries_and_closes_as_a_known_one(self):
        out = st.parse("buy 100 ACME in the Fnd book and 50 WIDG")
        self.assertEqual("Fnd", out["clauses"][1]["carried_scope"]["unknown"])
        self.assertEqual("Fnd", out["text"][slice(*out["clauses"][1]["carried_scope"]["word_span"])])
        out = st.parse("buy 100 ACME, buy 50 WIDG in the Fnd book at pb ms")
        self.assertEqual(("Fnd", 1), (out["suffix"]["scope"]["unknown"], out["suffix"]["clause"]))

    def test_a_group_word_inside_parentheses_is_a_word_of_its_order(self):
        out = st.parse("(buy 1 ACME Dec 18 2026 240 call limit 1.00, sell 1 ACME Dec 18 2026 250 call as a package)")
        self.assertEqual(1, len(out["groups"]))
        self.assertEqual("paren", out["groups"][0]["kind"])
        self.assertIsNotNone(out["clauses"][1]["mods"]["group"])


class Determinism(unittest.TestCase):
    def test_corpus_twice_in_process(self):
        self.assertEqual(corpus_digest(), corpus_digest())
        for c in corpus.CASES[:40]:                                                       # and case by case, byte for byte
            try:
                a, b = json.dumps(st.parse(c["text"]), sort_keys=True), json.dumps(st.parse(c["text"]), sort_keys=True)
            except st.SmartTicketError:
                continue
            self.assertEqual(a, b, c["id"])
            self.assertTrue(a.isascii(), c["id"])

    def test_corpus_in_three_interpreters_with_different_hash_seeds(self):
        here = corpus_digest()
        for seed in ("0", "1", "random"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=FINSIM_DIR)
            env.pop("PYTHONSTARTUP", None)
            proc = subprocess.run([sys.executable, "-c", CHILD], cwd=FINSIM_DIR, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(0, proc.returncode, proc.stderr[-2000:])
            self.assertEqual(here, proc.stdout.strip(), f"PYTHONHASHSEED={seed}")


if __name__ == "__main__":
    unittest.main()

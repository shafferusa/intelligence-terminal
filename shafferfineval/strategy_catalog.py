"""
ShafferFinEval -- approved strategy catalog.

Reads the "Playbook strategies" sheet of the tradeable-universe workbook and
parses its machine-readable leg definitions. The workbook is the source of
truth: nothing here invents a strategy that is not in it.

Leg grammar observed in the workbook
------------------------------------
    buy stock                       sell stock
    buy trs                         sell trs
    buy future                      sell future
    buy option P @ pct:-7           sell option C @ pct:+10
    buy option P @ ATM              sell option C @ zero_cost
    sell option C @ pct:+12 x2.0    sell option C @ pct:+10 x0.5

Legs are separated by "; ". The "x<n>" suffix is a size ratio relative to the
base hedge size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import universe as uni

# Instrument kinds a leg can reference.
STOCK = "stock"
OPTION = "option"
TRS = "trs"
FUTURE = "future"

BUY = "buy"
SELL = "sell"

PUT = "P"
CALL = "C"

# Strike conventions.
STRIKE_PCT = "pct"
STRIKE_ATM = "ATM"
STRIKE_ZERO_COST = "zero_cost"

SHEET_NAME = "Playbook strategies"

_LEG_RE = re.compile(
    r"""^\s*
    (?P<side>buy|sell)\s+
    (?P<instrument>stock|option|trs|future)
    (?:\s+(?P<right>[PC]))?
    (?:\s*@\s*(?P<strike>pct:[+-]?\d+(?:\.\d+)?|ATM|zero_cost))?
    (?:\s*[x××*]\s*(?P<ratio>\d+(?:\.\d+)?))?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class StrategyLeg:
    """One structured instruction parsed from a workbook leg."""

    side: str                              # buy | sell
    instrument: str                        # stock | option | trs | future
    right: Optional[str] = None            # P | C for options
    strike_mode: Optional[str] = None      # pct | ATM | zero_cost
    strike_pct: Optional[float] = None     # e.g. -7.0 meaning 7% below spot
    ratio: float = 1.0                     # size multiplier vs the hedge size
    raw: str = ""

    @property
    def is_option(self) -> bool:
        return self.instrument == OPTION

    def describe(self) -> str:
        if self.instrument == OPTION:
            if self.strike_mode == STRIKE_ATM:
                where = "ATM"
            elif self.strike_mode == STRIKE_ZERO_COST:
                where = "zero-cost strike"
            elif self.strike_pct is not None:
                where = f"{self.strike_pct:+.0f}% strike"
            else:
                where = "strike TBD"
            right = "put" if self.right == PUT else "call"
            size = f" x{self.ratio:g}" if self.ratio != 1.0 else ""
            return f"{self.side} {right} @ {where}{size}"
        return f"{self.side} {self.instrument}"


@dataclass
class Strategy:
    """One row of the workbook's approved strategy catalog."""

    key: str
    name: str
    group: str
    description: str = ""
    best_when: str = ""
    risk: str = ""
    legs: list[StrategyLeg] = field(default_factory=list)
    raw_legs: str = ""
    parse_errors: list[str] = field(default_factory=list)

    @property
    def parsed_cleanly(self) -> bool:
        return not self.parse_errors and bool(self.legs)

    @property
    def instruments(self) -> set[str]:
        return {leg.instrument for leg in self.legs}

    def legs_of(self, instrument: str) -> list[StrategyLeg]:
        return [leg for leg in self.legs if leg.instrument == instrument]

    def has(self, side: str, instrument: str, right: Optional[str] = None) -> bool:
        return any(
            leg.side == side and leg.instrument == instrument
            and (right is None or leg.right == right)
            for leg in self.legs
        )


def parse_strategy_legs(raw: str) -> tuple[list[StrategyLeg], list[str]]:
    """Parse a workbook leg string into structured legs.

    Returns (legs, errors). An unparseable fragment is reported rather than
    silently dropped, so a catalog row is never half-understood.
    """
    legs: list[StrategyLeg] = []
    errors: list[str] = []
    if not raw or not raw.strip():
        return legs, ["empty leg definition"]

    for fragment in raw.split(";"):
        text = fragment.strip()
        if not text:
            continue
        match = _LEG_RE.match(text)
        if not match:
            errors.append(text)
            continue

        strike_mode: Optional[str] = None
        strike_pct: Optional[float] = None
        strike = match.group("strike")
        if strike:
            if strike.lower().startswith("pct:"):
                strike_mode = STRIKE_PCT
                try:
                    strike_pct = float(strike.split(":", 1)[1])
                except ValueError:
                    errors.append(text)
                    continue
            elif strike.upper() == STRIKE_ATM:
                strike_mode = STRIKE_ATM
                strike_pct = 0.0
            else:
                strike_mode = STRIKE_ZERO_COST

        instrument = match.group("instrument").lower()
        right = (match.group("right") or "").upper() or None
        if instrument == OPTION and right is None:
            errors.append(text)
            continue

        legs.append(StrategyLeg(
            side=match.group("side").lower(),
            instrument=instrument,
            right=right,
            strike_mode=strike_mode,
            strike_pct=strike_pct,
            ratio=float(match.group("ratio")) if match.group("ratio") else 1.0,
            raw=text,
        ))

    return legs, errors


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def load_strategy_catalog(path: Optional[str] = None) -> tuple[list[Strategy], list[str]]:
    """Read the approved strategy catalog from the workbook.

    Returns (strategies, notes). An absent workbook yields an empty catalog and
    a note -- never a hand-built substitute.
    """
    notes: list[str] = []
    resolved = uni.find_universe_file(path)
    if not resolved:
        notes.append(
            "No workbook found, so the approved strategy catalog is empty. "
            "The hedge engine will not invent strategies."
        )
        return [], notes

    try:
        sheets = uni.read_xlsx_sheets(resolved)
    except Exception as exc:
        notes.append(f"Could not read the workbook: {exc}")
        return [], notes

    rows = sheets.get(SHEET_NAME)
    if not rows:
        notes.append(
            f"Workbook has no '{SHEET_NAME}' sheet. Sheets present: "
            + ", ".join(sheets)
        )
        return [], notes

    header = [_normalise(h) for h in rows[0]]

    def col(*aliases) -> Optional[int]:
        for alias in aliases:
            if alias in header:
                return header.index(alias)
        return None

    c_key, c_name, c_group = col("key"), col("strategy"), col("group")
    c_desc = col("whatyouretrading", "whatyouaretrading")
    c_best, c_risk, c_legs = col("bestwhen"), col("risk"), col("legs")

    def cell(row, index) -> str:
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    strategies: list[Strategy] = []
    unparsed: list[str] = []
    for row in rows[1:]:
        key = cell(row, c_key)
        if not key:
            continue
        raw_legs = cell(row, c_legs)
        legs, errors = parse_strategy_legs(raw_legs)
        strategy = Strategy(
            key=key,
            name=cell(row, c_name) or key,
            group=cell(row, c_group),
            description=cell(row, c_desc),
            best_when=cell(row, c_best),
            risk=cell(row, c_risk),
            legs=legs,
            raw_legs=raw_legs,
            parse_errors=errors,
        )
        strategies.append(strategy)
        if errors:
            unparsed.append(f"{key}: " + "; ".join(errors))

    notes.append(f"Loaded {len(strategies)} strategies from '{SHEET_NAME}'.")
    if unparsed:
        notes.append("Legs that could not be parsed: " + " | ".join(unparsed))
    else:
        notes.append("Every strategy's legs parsed cleanly.")
    return strategies, notes

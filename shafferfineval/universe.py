"""
ShafferFinEval -- tradeable universe loader.

Reads the tradeable-asset workbook that defines the equity universe and filters
it down to actual operating-company equities.

The .xlsx reader is written against stdlib `zipfile` + `xml.etree` (an .xlsx is
a zip of XML parts), so no openpyxl/pandas dependency is introduced. `.csv` and
`.tsv` are also accepted.

Two-stage filtering
-------------------
1. CHEAP PRE-FILTER (here): drop rows whose type column or symbol shape marks
   them as non-equity. This exists to avoid spending HTTP requests on
   instruments we already know are ineligible.
2. AUTHORITATIVE FILTER (market_data): keep only symbols Yahoo reports with
   `quoteType == "EQUITY"` and a real sector.

Stage 1 is a cost optimisation. Stage 2 is the rule.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

#: Where the workbook is looked for, relative to this file and its parents.
DEFAULT_SEARCH_NAMES = (
    "universe.xlsx", "universe.xlsm", "universe.csv",
    "tradeable-assets.xlsx", "tradeable_assets.xlsx",
    "assets.xlsx", "instruments.xlsx",
)
DEFAULT_SEARCH_DIRS = (".", "data", "../data", "..", "config", "../config")

SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELS_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Header aliases, lowercased and stripped of non-alphanumerics.
TICKER_HEADERS = ("ticker", "symbol", "tick", "code", "localsymbol", "yahooticker")
NAME_HEADERS = ("name", "companyname", "company", "description", "security",
                "securityname", "longname", "issuername")
SECTOR_HEADERS = ("sector", "gicssector", "yahoosector", "sectorname")
TYPE_HEADERS = ("type", "assettype", "assetclass", "securitytype", "instrumenttype",
                "instrument", "class", "category", "quotetype", "producttype")

#: Type-column values that are never operating-company equities.
EXCLUDED_TYPE_WORDS = (
    "etf", "etn", "etp", "index", "indice", "fund", "mutual", "closedend",
    "bond", "note", "bill", "treasury", "fixedincome", "debt", "credit",
    "preferred", "pref", "warrant", "right", "unit",
    "option", "call", "put", "future", "futures", "forward", "swap",
    "crypto", "coin", "token", "currency", "forex", "fx",
    "commodity", "metal", "energy contract", "otc", "derivative", "cfd",
    "reit fund", "adr fund", "trust fund", "money market",
)

#: Type-column values that positively indicate an operating company.
EQUITY_TYPE_WORDS = ("equity", "stock", "common", "share", "ordinary", "reit", "adr")


@dataclass
class UniverseRow:
    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    asset_type: Optional[str] = None


@dataclass
class UniverseSource:
    """Provenance for whatever universe ended up being used."""

    kind: str                      # "workbook" | "fallback"
    path: Optional[str] = None
    sheet: Optional[str] = None
    rows_read: int = 0
    rows_kept: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_fallback(self) -> bool:
        return self.kind == "fallback"


# --------------------------------------------------------------------------
# .xlsx reading (stdlib only)
# --------------------------------------------------------------------------

def _column_index(cell_ref: str) -> int:
    """'A1' -> 0, 'B7' -> 1, 'AA3' -> 26."""
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    for si in ET.fromstring(raw).findall(f"{SPREADSHEET_NS}si"):
        # A shared string may be split across several runs.
        out.append("".join(t.text or "" for t in si.iter(f"{SPREADSHEET_NS}t")))
    return out


def _first_sheet_path(archive: zipfile.ZipFile) -> tuple[str, Optional[str]]:
    """Resolve the first worksheet's part name and display name."""
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return "xl/worksheets/sheet1.xml", None

    target_by_id = {
        rel.get("Id"): rel.get("Target")
        for rel in rels.findall(f"{RELS_NS}Relationship")
    }
    sheets = workbook.find(f"{SPREADSHEET_NS}sheets")
    if sheets is None:
        return "xl/worksheets/sheet1.xml", None

    for sheet in sheets.findall(f"{SPREADSHEET_NS}sheet"):
        rid = sheet.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = target_by_id.get(rid)
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        return target, sheet.get("name")

    return "xl/worksheets/sheet1.xml", None


def read_xlsx(path: str) -> tuple[list[list[str]], Optional[str]]:
    """Return (rows of cell strings, sheet name) for the first worksheet."""
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        sheet_path, sheet_name = _first_sheet_path(archive)
        try:
            sheet_xml = archive.read(sheet_path)
        except KeyError:
            sheet_xml = archive.read("xl/worksheets/sheet1.xml")

    root = ET.fromstring(sheet_xml)
    rows: list[list[str]] = []
    for row in root.iter(f"{SPREADSHEET_NS}row"):
        cells: list[str] = []
        for cell in row.findall(f"{SPREADSHEET_NS}c"):
            ref = cell.get("r") or ""
            index = _column_index(ref) if ref else len(cells)
            cell_type = cell.get("t")

            if cell_type == "inlineStr":
                is_node = cell.find(f"{SPREADSHEET_NS}is")
                value = "".join(
                    t.text or "" for t in (is_node.iter(f"{SPREADSHEET_NS}t") if is_node is not None else [])
                )
            else:
                v_node = cell.find(f"{SPREADSHEET_NS}v")
                value = (v_node.text or "") if v_node is not None else ""
                if cell_type == "s":
                    try:
                        value = strings[int(value)]
                    except (ValueError, IndexError):
                        value = ""

            while len(cells) < index:
                cells.append("")
            cells.append(value.strip())
        rows.append(cells)
    return rows, sheet_name


def read_delimited(path: str) -> list[list[str]]:
    delimiter = "\t" if path.lower().endswith((".tsv", ".tab")) else ","
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        return [[c.strip() for c in row] for row in csv.reader(handle, delimiter=delimiter)]


# --------------------------------------------------------------------------
# Column mapping
# --------------------------------------------------------------------------

def _normalise_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _map_columns(header: list[str]) -> dict[str, int]:
    """Locate the ticker / name / sector / type columns, however they're spelled."""
    normalised = [_normalise_header(h) for h in header]
    mapping: dict[str, int] = {}
    for key, aliases in (
        ("ticker", TICKER_HEADERS),
        ("name", NAME_HEADERS),
        ("sector", SECTOR_HEADERS),
        ("asset_type", TYPE_HEADERS),
    ):
        for index, cell in enumerate(normalised):
            if cell in aliases:
                mapping[key] = index
                break
        if key not in mapping:                       # looser contains-match
            for index, cell in enumerate(normalised):
                if cell and any(alias in cell for alias in aliases):
                    mapping[key] = index
                    break
    return mapping


def _find_header_row(rows: list[list[str]], limit: int = 15) -> tuple[int, dict[str, int]]:
    """Workbooks often carry title rows above the real header."""
    for index, row in enumerate(rows[:limit]):
        mapping = _map_columns(row)
        if "ticker" in mapping:
            return index, mapping
    return -1, {}


# --------------------------------------------------------------------------
# Eligibility pre-filter
# --------------------------------------------------------------------------

def classify_exclusion(ticker: str, name: str, asset_type: str) -> Optional[str]:
    """Reason this row is not an operating-company equity, or None to keep it."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return "no ticker"

    type_text = _normalise_header(asset_type)
    if type_text:
        if any(word.replace(" ", "") in type_text for word in EQUITY_TYPE_WORDS):
            pass                                     # explicitly an equity
        elif any(word.replace(" ", "") in type_text for word in EXCLUDED_TYPE_WORDS):
            return f"type: {asset_type.strip()}"

    # Yahoo symbol shapes that are never operating companies.
    if symbol.startswith("^"):
        return "index symbol"
    if symbol.endswith("=F"):
        return "futures symbol"
    if symbol.endswith("=X"):
        return "currency symbol"
    if re.search(r"-(USD|EUR|GBP|JPY|USDT)$", symbol):
        return "crypto symbol"
    if re.search(r"[-.]P[A-Z]?$", symbol) or symbol.endswith(".PR"):
        return "preferred share symbol"
    if re.search(r"[-.]W[STU]?$", symbol):
        return "warrant/when-issued symbol"
    if len(symbol) == 5 and symbol.isalpha() and symbol.endswith("X"):
        return "mutual fund symbol"
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", symbol):
        return "unsupported symbol format"

    lowered = (name or "").lower()
    for phrase in (" etf", "etf ", "exchange traded", "exchange-traded", " etn",
                   "index fund", "ishares", "spdr", "proshares", "direxion",
                   "invesco qqq", "vaneck", "wisdomtree", " depositary units"):
        if phrase in f" {lowered} ":
            return "fund/ETF name"
    return None


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def find_universe_file(explicit: Optional[str] = None) -> Optional[str]:
    """Locate the workbook. An explicit path wins; otherwise search known spots."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None

    env_path = os.environ.get("SHAFFERFINEVAL_UNIVERSE")
    if env_path and os.path.isfile(env_path):
        return env_path

    here = os.path.dirname(os.path.abspath(__file__))
    for directory in DEFAULT_SEARCH_DIRS:
        for filename in DEFAULT_SEARCH_NAMES:
            candidate = os.path.normpath(os.path.join(here, directory, filename))
            if os.path.isfile(candidate):
                return candidate
    return None


def parse_universe_rows(
    rows: list[list[str]], sheet: Optional[str] = None, path: Optional[str] = None
) -> tuple[list[UniverseRow], UniverseSource]:
    """Turn raw sheet rows into a filtered universe plus its provenance."""
    source = UniverseSource(kind="workbook", path=path, sheet=sheet)

    header_index, mapping = _find_header_row(rows)
    if header_index < 0:
        source.notes.append(
            "No ticker/symbol column found. Expected a header containing one of: "
            + ", ".join(TICKER_HEADERS)
        )
        return [], source

    def cell(row: list[str], key: str) -> str:
        index = mapping.get(key)
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    kept: list[UniverseRow] = []
    seen: set[str] = set()
    for row in rows[header_index + 1:]:
        if not any(c.strip() for c in row):
            continue
        source.rows_read += 1

        ticker = cell(row, "ticker").upper()
        name = cell(row, "name")
        sector = cell(row, "sector")
        asset_type = cell(row, "asset_type")

        reason = classify_exclusion(ticker, name, asset_type)
        if reason:
            source.excluded[reason] = source.excluded.get(reason, 0) + 1
            continue
        if ticker in seen:
            source.excluded["duplicate"] = source.excluded.get("duplicate", 0) + 1
            continue

        seen.add(ticker)
        kept.append(UniverseRow(ticker, name or None, sector or None, asset_type or None))

    source.rows_kept = len(kept)
    if "sector" in mapping:
        source.notes.append(
            "Workbook supplies a sector column; it is used as a hint but Yahoo's "
            "classification remains authoritative."
        )
    return kept, source


def load_universe(path: Optional[str] = None) -> tuple[list[UniverseRow], UniverseSource]:
    """Load the tradeable universe.

    Returns ([], UniverseSource) when no workbook is present -- the caller
    decides whether to fall back, and the UI says which universe was used.
    """
    resolved = find_universe_file(path)
    if not resolved:
        source = UniverseSource(kind="fallback")
        source.notes.append(
            "No universe workbook found. Looked for "
            + ", ".join(DEFAULT_SEARCH_NAMES)
            + " in "
            + ", ".join(DEFAULT_SEARCH_DIRS)
            + ", and at $SHAFFERFINEVAL_UNIVERSE."
        )
        return [], source

    try:
        if resolved.lower().endswith((".xlsx", ".xlsm")):
            rows, sheet = read_xlsx(resolved)
        else:
            rows, sheet = read_delimited(resolved), None
    except Exception as exc:
        source = UniverseSource(kind="fallback", path=resolved)
        source.notes.append(f"Could not read {os.path.basename(resolved)}: {exc}")
        return [], source

    return parse_universe_rows(rows, sheet=sheet, path=resolved)

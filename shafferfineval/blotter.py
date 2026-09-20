"""
ShafferFinEval -- FinSim blotter integration.

Imports trade/order history from the FinSim application, maps its securities
onto the Shaffer universe, groups related trades into single economic
positions, and links hedge legs to what they hedge.

Accepts CSV, TSV, Excel (.xlsx via the stdlib reader in `universe`) and a
SQLite path. The adapter is field-tolerant: it maps whatever the FinSim schema
provides rather than insisting on a fixed column list, and unknown columns are
preserved verbatim in `extra` so richer data is never discarded.

Critical distinction the ML Lab depends on:

    MARKET DATASET   every scored asset  -> general return models
    MY TRADE DATASET assets I chose      -> execution, hedging, behaviour only

A personal book is not a random sample of the market, so the two are never
merged for training a general return model.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Optional

import universe as uni

BUY, SELL = "buy", "sell"
LONG, SHORT = "long", "short"

#: Column aliases. The left side is what we want; the right is what FinSim (or
#: any reasonable blotter) might call it.
FIELD_ALIASES = {
    "trade_id": ("tradeid", "id", "tradeno", "tradenumber", "executionid", "fillid"),
    "order_id": ("orderid", "ordno", "ordernumber", "clientorderid"),
    "trade_date": ("tradedate", "date", "executiondate", "timestamp", "datetime",
                   "executedat", "time"),
    "settle_date": ("settledate", "settlementdate", "valuedate"),
    "symbol": ("security", "symbol", "ticker", "instrument", "asset", "secid",
               "securityid", "contract", "underlying"),
    "asset_class": ("assetclass", "class", "type", "securitytype", "producttype",
                    "instrumenttype"),
    "side": ("side", "buysell", "direction", "action", "bs"),
    "quantity": ("quantity", "qty", "shares", "size", "amount", "units",
                 "contracts", "notionalunits"),
    "price": ("price", "executionprice", "fillprice", "avgprice", "averageprice"),
    "gross": ("gross", "grossamount", "grossconsideration", "principal"),
    "accrued": ("accrued", "accruedinterest"),
    "commission": ("commission", "fees", "fee", "brokerage", "charges"),
    "net_cash": ("netcash", "net", "netamount", "cash", "netconsideration"),
    "realized_pnl": ("realizedpnl", "realisedpnl", "pnl", "realizedpl", "profit"),
    "status": ("status", "state", "tradestatus"),
    "strategy_tag": ("strategytag", "strategy", "tag", "book", "strategyid"),
    "position_id": ("positionid", "position", "posid"),
    "hedge_relationship": ("hedgerelationship", "hedgerel", "hedgetype", "hedge"),
    "parent_trade": ("parenttrade", "parenttradeid", "parent"),
    "linked_hedge_trade": ("linkedhedgetrade", "hedgetradeid", "linkedtrade",
                           "hedgeleg"),
    "trade_group_id": ("tradegroupid", "groupid", "group", "packageid"),
}

#: FinSim asset-class strings -> Shaffer universe classes.
ASSET_CLASS_MAP = {
    "stock": uni.EQUITY, "equity": uni.EQUITY, "common": uni.EQUITY,
    "adr": uni.EQUITY, "reit": uni.EQUITY, "share": uni.EQUITY,
    "etf": uni.ETF, "fund": uni.ETF,
    "preferred": uni.PREFERRED,
    "bond": uni.BOND, "treasury": uni.BOND, "corporate": uni.BOND,
    "mbs": uni.BOND, "credit": uni.BOND,
    "future": uni.FUTURE, "futures": uni.FUTURE,
    "fx": uni.FX, "currency": uni.FX, "forward": uni.FX, "ndf": uni.FX,
    "commodity": uni.COMMODITY,
    "crypto": uni.CRYPTO, "coin": uni.CRYPTO,
    "option": "Option", "call": "Option", "put": "Option",
    "cds": uni.CDS, "swap": uni.OTC, "trs": uni.OTC, "irs": uni.OTC,
}

#: Relationship types between legs of one economic position.
PRIMARY, HEDGE, SPREAD, ROLL = "primary", "hedge", "spread", "roll"


@dataclass
class BlotterTrade:
    """One normalized trade record."""

    trade_id: Optional[str] = None
    order_id: Optional[str] = None
    trade_date: Optional[str] = None
    settle_date: Optional[str] = None
    symbol: Optional[str] = None
    asset_class: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    gross: Optional[float] = None
    accrued: Optional[float] = None
    commission: Optional[float] = None
    net_cash: Optional[float] = None
    realized_pnl: Optional[float] = None
    status: Optional[str] = None
    strategy_tag: Optional[str] = None
    position_id: Optional[str] = None
    hedge_relationship: Optional[str] = None
    parent_trade: Optional[str] = None
    linked_hedge_trade: Optional[str] = None
    trade_group_id: Optional[str] = None
    mapped_asset_id: Optional[int] = None
    mapped_symbol: Optional[str] = None
    mapping_note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def direction(self) -> Optional[str]:
        if not self.side:
            return None
        return LONG if str(self.side).lower().startswith("b") else SHORT

    @property
    def signed_quantity(self) -> Optional[float]:
        if self.quantity is None:
            return None
        return abs(self.quantity) * (1 if self.direction == LONG else -1)


@dataclass
class ImportResult:
    source: str = ""
    trades: list[BlotterTrade] = field(default_factory=list)
    rows_read: int = 0
    mapped: int = 0
    unmapped: list[str] = field(default_factory=list)
    columns_seen: list[str] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _normalise(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def map_columns(header: Iterable[str]) -> tuple[dict, list]:
    """Map blotter column names onto our field names. Returns (mapping, unmapped)."""
    normalized = [_normalise(h) for h in header]
    mapping, used = {}, set()
    for field_name, aliases in FIELD_ALIASES.items():
        for index, cell in enumerate(normalized):
            if index in used:
                continue
            if cell in aliases:
                mapping[field_name] = index
                used.add(index)
                break
    unmapped = [list(header)[i] for i, cell in enumerate(normalized)
                if i not in used and cell]
    return mapping, unmapped


def _row_to_trade(row: list, header: list, mapping: dict) -> BlotterTrade:
    def cell(name):
        index = mapping.get(name)
        if index is None or index >= len(row):
            return None
        value = row[index]
        return value.strip() if isinstance(value, str) else value

    trade = BlotterTrade(
        trade_id=cell("trade_id"), order_id=cell("order_id"),
        trade_date=(str(cell("trade_date"))[:19] if cell("trade_date") else None),
        settle_date=(str(cell("settle_date"))[:10] if cell("settle_date") else None),
        symbol=(str(cell("symbol")).upper() if cell("symbol") else None),
        asset_class=cell("asset_class"), side=cell("side"),
        quantity=_num(cell("quantity")), price=_num(cell("price")),
        gross=_num(cell("gross")), accrued=_num(cell("accrued")),
        commission=_num(cell("commission")), net_cash=_num(cell("net_cash")),
        realized_pnl=_num(cell("realized_pnl")), status=cell("status"),
        strategy_tag=cell("strategy_tag"), position_id=cell("position_id"),
        hedge_relationship=cell("hedge_relationship"),
        parent_trade=cell("parent_trade"),
        linked_hedge_trade=cell("linked_hedge_trade"),
        trade_group_id=cell("trade_group_id"),
    )
    used = set(mapping.values())
    trade.extra = {header[i]: row[i] for i in range(min(len(header), len(row)))
                   if i not in used and header[i]}
    return trade


def map_security(conn, trade: BlotterTrade) -> BlotterTrade:
    """Resolve a FinSim security onto the Shaffer universe.

    Tries the symbol as written, then the Yahoo translation, then the workbook
    form. An unmapped trade is kept and flagged -- never dropped silently.
    """
    import storage

    if not trade.symbol:
        trade.mapping_note = "no symbol on the trade record"
        return trade

    declared = ASSET_CLASS_MAP.get(_normalise(trade.asset_class)) if trade.asset_class else None
    candidates = [trade.symbol, trade.symbol.replace(".", "-"),
                  trade.symbol.replace("-", ".")]

    for candidate in candidates:
        row = storage.get_asset(conn, candidate, declared)
        if row is None and declared:
            row = storage.get_asset(conn, candidate)
        if row is not None:
            trade.mapped_asset_id = row["asset_id"]
            trade.mapped_symbol = row["symbol"]
            if declared and row["asset_class"] != declared:
                trade.mapping_note = (
                    f"blotter says {trade.asset_class}, universe says "
                    f"{row['asset_class']}"
                )
            return trade

    trade.mapping_note = f"'{trade.symbol}' is not in the Shaffer universe"
    return trade


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

def _read_delimited(path: str) -> tuple[list, list]:
    delimiter = "\t" if path.lower().endswith((".tsv", ".tab")) else ","
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        rows = [r for r in csv.reader(handle, delimiter=delimiter)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _read_xlsx(path: str) -> tuple[list, list]:
    sheets = uni.read_xlsx_sheets(path)
    for name, rows in sheets.items():
        if not rows:
            continue
        mapping, _ = map_columns(rows[0])
        if "symbol" in mapping and "quantity" in mapping:
            return rows[0], rows[1:]
    first = next(iter(sheets.values()), [])
    return (first[0], first[1:]) if first else ([], [])


def _read_sqlite(path: str) -> tuple[list, list, str]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
    best, best_score, header, rows = None, -1, [], []
    for table in tables:
        try:
            cursor = conn.execute(f"SELECT * FROM {table} LIMIT 500")
        except sqlite3.Error:
            continue
        columns = [d[0] for d in cursor.description]
        mapping, _ = map_columns(columns)
        score = len(mapping) + (5 if "trade" in table.lower() or
                                "blotter" in table.lower() else 0)
        if "symbol" in mapping and "quantity" in mapping and score > best_score:
            best, best_score = table, score
            header = columns
            rows = [list(r) for r in cursor.fetchall()]
    conn.close()
    return header, rows, (best or "")


def import_finsim_blotter(
    conn, source: str, map_securities: bool = True
) -> ImportResult:
    """Import FinSim trade history from CSV, TSV, Excel or a SQLite database.

    Returns every parsed trade, mapped where possible. Nothing is written to
    the database here -- persistence is a separate, explicit step.
    """
    result = ImportResult(source=source)
    if not source or not os.path.exists(source):
        result.notes.append(f"No blotter found at '{source}'.")
        return result

    lower = source.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            header, rows = _read_xlsx(source)
        elif lower.endswith((".db", ".sqlite", ".sqlite3")):
            header, rows, table = _read_sqlite(source)
            if table:
                result.notes.append(f"Read from table '{table}'.")
        else:
            header, rows = _read_delimited(source)
    except Exception as exc:
        result.notes.append(f"Could not read the blotter: {exc}")
        return result

    if not header:
        result.notes.append("Blotter has no readable header row.")
        return result

    result.columns_seen = list(header)
    mapping, unmapped = map_columns(header)
    result.unmapped_columns = unmapped
    if "symbol" not in mapping:
        result.notes.append(
            "No security/symbol column found. Looked for: "
            + ", ".join(FIELD_ALIASES["symbol"])
        )
        return result

    for row in rows:
        if not any(str(c).strip() for c in row):
            continue
        result.rows_read += 1
        trade = _row_to_trade(list(row), list(header), mapping)
        if map_securities:
            trade = map_security(conn, trade)
            if trade.mapped_asset_id is not None:
                result.mapped += 1
            elif trade.symbol:
                result.unmapped.append(trade.symbol)
        result.trades.append(trade)

    result.notes.append(
        f"{result.rows_read} rows read, {result.mapped} mapped to the universe."
    )
    if unmapped:
        result.notes.append(
            "Columns preserved but not mapped: " + ", ".join(unmapped[:12])
        )
    return result


# --------------------------------------------------------------------------
# Trade grouping and hedge linking
# --------------------------------------------------------------------------

@dataclass
class TradeGroup:
    """Several trades forming ONE economic position."""

    group_key: str
    trades: list[BlotterTrade] = field(default_factory=list)
    primary: Optional[BlotterTrade] = None
    hedges: list[BlotterTrade] = field(default_factory=list)
    relationship: str = PRIMARY
    strategy_tag: Optional[str] = None
    note: str = ""

    @property
    def symbols(self) -> set:
        return {t.mapped_symbol or t.symbol for t in self.trades}


def group_trades(trades: list[BlotterTrade]) -> list[TradeGroup]:
    """Group trades into economic positions.

    Explicit grouping wins: trade_group_id, then position_id, then a
    parent/linked-hedge chain, then strategy tag plus symbol. Long stock and
    its protective puts are ONE position; a long and short of the same future
    in different months is a calendar spread.
    """
    groups: dict[str, TradeGroup] = {}

    def key_for(trade: BlotterTrade) -> str:
        for candidate in (trade.trade_group_id, trade.position_id):
            if candidate:
                return f"explicit:{candidate}"
        if trade.parent_trade:
            return f"parent:{trade.parent_trade}"
        if trade.strategy_tag:
            return f"strategy:{trade.strategy_tag}"
        return f"symbol:{trade.mapped_symbol or trade.symbol}"

    for trade in trades:
        key = key_for(trade)
        group = groups.setdefault(key, TradeGroup(group_key=key))
        group.trades.append(trade)
        if trade.strategy_tag and not group.strategy_tag:
            group.strategy_tag = trade.strategy_tag

    # Trades naming another trade as their hedge pull that trade into the group.
    by_id = {t.trade_id: t for t in trades if t.trade_id}
    for trade in trades:
        if not trade.linked_hedge_trade:
            continue
        partner = by_id.get(trade.linked_hedge_trade)
        if partner is None:
            continue
        own, other = key_for(trade), key_for(partner)
        if own == other:
            continue
        target, source = groups.get(own), groups.get(other)
        if target is None or source is None:
            continue
        for moved in source.trades:
            if moved not in target.trades:
                target.trades.append(moved)
        groups.pop(other, None)

    for group in groups.values():
        _classify_group(group)
    return list(groups.values())


def _classify_group(group: TradeGroup) -> None:
    """Identify the primary leg and the hedge legs inside one group."""
    explicit_hedges = [t for t in group.trades
                       if (t.hedge_relationship or "").lower().startswith("hedg")]
    if explicit_hedges:
        group.hedges = explicit_hedges
        remaining = [t for t in group.trades if t not in explicit_hedges]
        group.primary = max(remaining, key=_notional, default=None)
        group.relationship = HEDGE
        group.note = "hedge relationship declared in the blotter"
        return

    options = [t for t in group.trades
               if _normalise(t.asset_class) in ("option", "call", "put")]
    cash = [t for t in group.trades if t not in options]
    if options and cash:
        group.primary = max(cash, key=_notional, default=None)
        group.hedges = options
        group.relationship = HEDGE
        group.note = "cash position plus listed options inferred as one hedged position"
        return

    symbols = {t.mapped_symbol or t.symbol for t in group.trades}
    directions = {t.direction for t in group.trades if t.direction}
    if len(group.trades) > 1 and len(directions) == 2:
        group.primary = max(group.trades, key=_notional, default=None)
        group.hedges = [t for t in group.trades if t is not group.primary]
        group.relationship = SPREAD if len(symbols) == 1 else HEDGE
        group.note = ("offsetting legs in the same security inferred as a spread"
                      if len(symbols) == 1
                      else "offsetting legs across securities inferred as a hedge")
        return

    group.primary = max(group.trades, key=_notional, default=None)
    group.relationship = PRIMARY
    group.note = "single-leg position"


def _notional(trade: BlotterTrade) -> float:
    if trade.quantity is None or trade.price is None:
        return abs(trade.gross or 0.0)
    return abs(trade.quantity * trade.price)


def hedge_ratio_for(group: TradeGroup) -> Optional[float]:
    """Hedged fraction of the primary leg implied by the hedge legs."""
    if group.primary is None or not group.hedges:
        return None
    primary = abs(group.primary.quantity or 0)
    if primary <= 0:
        return None
    hedged = 0.0
    for leg in group.hedges:
        quantity = abs(leg.quantity or 0)
        if _normalise(leg.asset_class) in ("option", "call", "put"):
            quantity *= 100          # contracts cover 100 shares
        hedged += quantity
    return hedged / primary

"""The user's portfolio: a ledger of deposits, withdrawals, buys and sells, valued on the research calendar.

Everything is reported in US dollars. A position in another currency is converted at that day's rate (EUR, GBP,
AUD and NZD via their USD pairs, JPY, CAD, CHF, CNY, MXN and INR via the inverse of theirs, HKD at the 7.8 peg).
Cost basis uses average cost; realised P&L is booked on sales. Risk is measured on the current holdings over
their recent history (a historical simulation of today's weights), and on the actual NAV once it has enough days.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from .align import Panel
from .features import log_returns

ANN = 252
FX_DIRECT = {"EUR": "EURUSD", "GBP": "GBPUSD", "AUD": "AUDUSD", "NZD": "NZDUSD"}
FX_INVERSE = {"JPY": "USDJPY", "CAD": "USDCAD", "CHF": "USDCHF", "CNY": "USDCNY", "MXN": "USDMXN", "INR": "USDINR"}
PEGS = {"HKD": 1 / 7.8, "USD": 1.0}
FACTORS = {                       # factor -> (long leg, short leg or None)
    "Market": ("SPY", None), "Size (small − large)": ("IWM", "SPY"), "Value (value − growth)": ("IWD", "IWF"),
    "Momentum": ("MTUM", "SPY"), "Quality": ("QUAL", "SPY"), "Low volatility": ("USMV", "SPY"),
    "Technology / Nasdaq": ("QQQ", "SPY"), "Rates (Treasuries)": ("IEF", None), "Credit (HY − Treasuries)": ("HYG", "IEF"),
    "Dollar": ("DXY", None), "Oil": ("WTI", None), "Gold": ("GOLD", None),
}


SUBUNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def fx_series(panel: Panel, ccy: str) -> List[Optional[float]]:
    """USD per unit of `ccy` on the calendar (None where no rate is known). Sub-units (GBp) scale their parent; any
    other currency is looked up among the store's FX assets (e.g. one added as "USDSEK=X")."""
    n = len(panel.calendar())
    if ccy in SUBUNITS:
        parent, k = SUBUNITS[ccy]
        return [(v * k) if v is not None else None for v in fx_series(panel, parent)]
    if ccy in PEGS:
        return [PEGS[ccy]] * n
    if ccy in FX_DIRECT:
        return panel.series(FX_DIRECT[ccy], "close", max_fill=10)
    if ccy in FX_INVERSE:
        return [(1.0 / v) if v else None for v in panel.series(FX_INVERSE[ccy], "close", max_fill=10)]
    for a in panel.store.assets("FX"):
        sym = (a.get("yahoo") or "").upper()
        if sym == f"{ccy}USD=X":
            return panel.series(a["id"], "close", max_fill=10)
        if sym == f"USD{ccy}=X":
            return [(1.0 / v) if v else None for v in panel.series(a["id"], "close", max_fill=10)]
    return [None] * n


def _ffill(xs):
    out, last = [], None
    for v in xs:
        last = v if v is not None else last
        out.append(last)
    return out


DERIVATIVE_PREFIX = ("FUT:", "OPT:", "FWD:")


def is_instrument(a: Optional[str]) -> bool:
    """True for a futures, option or forward instrument id (not a universe asset)."""
    return bool(a) and str(a).startswith(DERIVATIVE_PREFIX)


# stitched continuous futures series (Yahoo "=F"): the price jumps to the next contract at each roll, so a position
# held through a roll would book the calendar spread as P&L. They can be analysed, and a position booked before this
# rule can be closed, but none can be opened. Funds that hold and roll the contracts are the tradeable exposure.
CONTINUOUS_PROXY = {"GOLD": "GLD", "SILVER": "SLV", "WTI": "USO", "BRENT": "USO", "NATGAS": "UNG", "COPPER": "CPER",
                    "CORN": "DBA", "WHEAT": "DBA", "SOYBEANS": "DBA", "COFFEE": "DBA", "PLATINUM": "DBC"}


def continuous_series(a: Optional[dict]) -> bool:
    return bool(a) and (a.get("asset_class") in ("COMMODITY", "FUTURE") or bool((a.get("meta") or {}).get("continuous_front_month")))


ANALYSIS_ONLY = "ANALYSIS ONLY — CONTINUOUS FUTURES SERIES"


def continuous_refusal(a: dict) -> str:
    proxy = CONTINUOUS_PROXY.get(a["id"])
    return (f"{ANALYSIS_ONLY}. {a['id']} is a continuous front-month futures series: its price jumps to the next contract at every roll, so a "
            f"held position would book the roll as profit or loss. It cannot be bought or shorted"
            + (f"; {proxy} holds and rolls the contracts and is tradeable." if proxy else ".") + " An existing position can still be sold.")


class LedgerError(ValueError):
    """A transaction that would make the ledger inconsistent (overdraft, selling more than is held, …)."""


def usd_base_fx(asset: dict) -> bool:
    """A dollar-first pair (USDJPY, USDCAD, …): buying it means long dollars, short the other currency."""
    meta = asset.get("meta") or {}
    return asset.get("asset_class") == "FX" and (meta.get("base_currency") == "USD" or str(asset.get("id", "")).startswith("USD")) and asset.get("currency") != "USD"


class Ledger:
    """The portfolio's transactions, replayed. One replay (`_replay`) produces holdings on any date, the daily NAV,
    and the validation every new or removed transaction must pass:
      * buying power never goes below zero at any point: cash, less 150% of the value of short sales (Reg T: the
        proceeds plus 50%), less the margin estimate on futures and forwards (no unfunded borrowing);
      * nothing is sold that is not held at that moment, including after back-dated entries or deletions; options are
        only bought (selling more than is held would write options and is refused);
      * quantities and prices are converted to today's share basis: a transaction records the date of the share
        basis its figures are in (`basis_date`: the trade date for a price you typed, the latest data date for a
        price FinSim2 filled in from its split-adjusted history); every split after that date is applied;
      * cash dividends (Yahoo's split-adjusted amounts) are credited on the ex-date to positions held at the prior
        close, and DEBITED to short positions, in the asset's currency converted to dollars that day;
      * short sales (SHORT / COVER): the proceeds are cash held as collateral; a general-collateral borrow fee
        (0.30% a year of the short value) is charged every session;
      * futures and forwards (instrument ids FUT:/FWD:) are marked to their model price every day: value =
        quantity × contract size × (price − average entry) in dollars; P&L is realised on reduction and at expiry;
      * options (OPT:) are bought for their premium, marked at their model price and settled at intrinsic value on
        the expiry date;
      * dollar-first FX pairs are valued as financed positions: long N dollars funded by borrowing N·P₀ of the other
        currency, so their dollar value is 2N − (Σ N·P₀)/Pₜ (N at entry, rising when the dollar rises)."""

    SHORT_REQUIREMENT = 1.5
    BORROW_FEE = 0.0030

    def __init__(self, store, panel: Panel, portfolio_id: str = "main"):
        self.store = store
        self.panel = panel
        self.pid = portfolio_id
        if not any(p["id"] == portfolio_id for p in store.portfolios()):
            store.create_portfolio(portfolio_id, "My Portfolio", "USD")
        self._fx_cache: Dict[str, list] = {}
        self._inst_cache: Dict[str, object] = {}
        from . import cash as cashmod
        self.cash_settings = cashmod.load(store, portfolio_id)

    # ------------------------------------------------------------------ instruments
    def inst(self, a: str):
        if a not in self._inst_cache:
            from ..hedge.products import parse
            self._inst_cache[a] = parse(a, self.store)
        return self._inst_cache[a]

    def _meta(self, a: str) -> dict:
        if is_instrument(a):
            i = self.inst(a)
            return {"id": a, "name": i.name, "asset_class": "DERIVATIVE", "currency": i.currency, "product_type": i.product_type}
        return self.store.asset(a) or {"id": a, "currency": "USD"}

    def mark(self, a: str, i: int) -> Optional[float]:
        """Price per unit of `a` at session i: the close (carried at most 10 sessions) or the instrument's model price."""
        if is_instrument(a):
            from ..hedge.series import price_series
            s = price_series(self.panel, self.store, self.inst(a))
        else:
            s = self.panel.series(a, "close", max_fill=10)
        for k in range(i, max(-1, i - 6), -1):
            if s[k] is not None:
                return s[k]
        return None

    # ------------------------------------------------------------------ commands
    def _latest(self) -> str:
        return self.panel.calendar()[-1]

    def _add(self, tx: dict) -> int:
        txs = self.store.transactions(self.pid) + [{**tx, "id": 10 ** 12}]
        self._replay(txs, strict=True)                  # raises LedgerError if the ledger would become inconsistent
        tid = self.store.add_transaction({"portfolio_id": self.pid, **tx})
        self.store.audit("transaction.add", f"{self.pid}/{tid}", tx)
        return tid

    def deposit(self, amount: float, date: Optional[str] = None, note: str = "") -> int:
        if not amount or amount <= 0:
            raise ValueError("the amount must be positive")
        d = date or self._latest()
        return self._add({"date": d, "kind": "DEPOSIT", "asset_id": None, "quantity": None, "price": float(amount), "fee": 0.0,
                          "currency": "USD", "note": note, "basis_date": d})

    def withdraw(self, amount: float, date: Optional[str] = None, note: str = "") -> int:
        if not amount or amount <= 0:
            raise ValueError("the amount must be positive")
        d = date or self._latest()
        return self._add({"date": d, "kind": "WITHDRAW", "asset_id": None, "quantity": None, "price": float(amount), "fee": 0.0,
                          "currency": "USD", "note": note, "basis_date": d})

    def prepare(self, asset_id: str, quantity: float, side: str = "BUY", price: Optional[float] = None, date: Optional[str] = None,
                fee: float = 0.0, note: str = "", package_id: Optional[str] = None) -> dict:
        """Validate one trade's own fields and fill in its price; returns the transaction row (not yet stored)."""
        if not quantity or quantity <= 0:
            raise ValueError("the quantity must be positive")
        if fee is not None and fee < 0:
            raise ValueError("the fee cannot be negative")
        d = date or self._latest()
        if d > self._latest():
            raise ValueError(f"{d} is after the latest market data ({self._latest()})")
        side = side.upper()
        if side not in ("BUY", "SELL", "SHORT", "COVER"):
            raise ValueError("side must be BUY, SELL, SHORT or COVER")
        k = self.panel.index_of(d)
        if is_instrument(asset_id):
            ins = self.inst(asset_id)                       # ValueError for a malformed id
            if side in ("SHORT", "COVER"):
                raise ValueError("futures, forwards and options are traded with BUY and SELL (a SELL opens a short future)")
            if ins.expiry and d >= ins.expiry:
                raise ValueError(f"{asset_id} expires on {ins.expiry}")
            if ins.product_type in ("commodity_future", "crypto_future"):
                from ..hedge.products import PRODUCT_TYPE
                raise ValueError(PRODUCT_TYPE[ins.product_type]["reason"])
            basis = d
            if price is None:
                price = self.mark(asset_id, k)
                if price is None:
                    raise ValueError(f"no model price for {asset_id} on {d} (a missing input: underlying, rate or implied volatility)")
            ccy = ins.currency
        else:
            a = self.store.asset(asset_id)
            if a is None:
                raise ValueError(f"unknown asset {asset_id}")
            if side in ("BUY", "SHORT") and continuous_series(a):
                raise ValueError(continuous_refusal(a))
            if side in ("SHORT", "COVER"):
                meta = a.get("meta") or {}
                if a.get("asset_class") in ("CRYPTO", "INDEX") or meta.get("synthetic") or usd_base_fx(a):
                    raise ValueError(f"{asset_id} cannot be shorted here ({'no borrow for spot crypto' if a.get('asset_class') == 'CRYPTO' else 'not a borrowable security'})")
            basis = d
            if price is None:
                px = self.panel.series(asset_id, "close")[k]
                if px is None:
                    raise ValueError(f"no {asset_id} price on {d}")
                price, basis = px, self._latest()
            ccy = a.get("currency") or "USD"
        if not self._fx_at(ccy, k):
            raise ValueError(f"no {ccy} exchange rate on {d}")
        tx = {"date": d, "kind": side, "asset_id": asset_id, "quantity": float(quantity), "price": float(price),
              "fee": float(fee or 0.0), "currency": ccy, "note": note, "basis_date": basis}
        if package_id:
            tx["package_id"] = package_id
        return tx

    def trade(self, asset_id: str, quantity: float, side: str = "BUY", price: Optional[float] = None, date: Optional[str] = None,
              fee: float = 0.0, note: str = "") -> int:
        """Record a trade. Spot: BUY/SELL a long position, SHORT/COVER a short one. Futures and forwards: BUY/SELL (a SELL
        from flat opens a short). Options: BUY, and SELL what is held. Without a price, the close (spot, today's share
        basis) or the model price (instruments) of that date is used."""
        return self._add(self.prepare(asset_id, quantity, side, price, date, fee, note))

    def trade_package(self, legs: List[dict], note: str = "") -> List[int]:
        """Several trades (a primary trade and its hedge legs) validated together and stored in ONE database
        transaction: either every leg is recorded or none is. Raises LedgerError/ValueError naming the failing leg."""
        import uuid
        pkg = "pkg-" + uuid.uuid4().hex[:12]
        txs = []
        for n, leg in enumerate(legs):
            try:
                txs.append(self.prepare(leg["asset_id"], leg["quantity"], leg.get("side", "BUY"), leg.get("price"), leg.get("date"),
                                        leg.get("fee", 0.0), leg.get("note") or note, pkg))
            except ValueError as e:
                raise ValueError(f"leg {n + 1} ({leg.get('asset_id')}): {e}") from None
        existing = self.store.transactions(self.pid)
        try:
            self._replay(existing + [{**t, "id": 10 ** 12 + n} for n, t in enumerate(txs)], strict=True)
        except LedgerError as e:
            raise LedgerError(f"the package was not recorded (no leg was): {e}") from None
        ids = self.store.add_transactions([{"portfolio_id": self.pid, **t} for t in txs])
        self.store.audit("package.add", f"{self.pid}/{pkg}", {"legs": txs, "ids": ids})
        return ids

    def void(self, tx_id: int) -> None:
        """Remove a transaction (soft delete, audited) if the ledger stays consistent without it."""
        t = self.store.transaction(tx_id)
        if t is None or t["portfolio_id"] != self.pid or t.get("voided_at"):
            raise KeyError(f"transaction {tx_id}")
        rest = [x for x in self.store.transactions(self.pid) if x["id"] != int(tx_id)]
        try:
            self._replay(rest, strict=True)
        except LedgerError as e:
            raise LedgerError(f"removing it would leave the ledger inconsistent: {e}") from None
        self.store.void_transaction(tx_id)
        self.store.audit("transaction.void", f"{self.pid}/{tx_id}", t)

    # ------------------------------------------------------------------ the replay
    def _fx_list(self, ccy: str) -> list:
        ccy = ccy or "USD"
        if ccy not in self._fx_cache:
            self._fx_cache[ccy] = _ffill(fx_series(self.panel, ccy))
        return self._fx_cache[ccy]

    def _fx_at(self, ccy: str, i: int) -> Optional[float]:
        s = self._fx_list(ccy)
        return s[min(max(i, 0), len(s) - 1)] if s else None

    def _split_factor(self, asset_id: str, basis_date: Optional[str]) -> float:
        if is_instrument(asset_id):
            return 1.0
        f = 1.0
        for a in self.store.actions(asset_id, "SPLIT"):
            if basis_date is None or a["date"] > basis_date:
                f *= a["value"]
        return f

    def value(self, asset: dict, pos: dict, price: Optional[float], fx: Optional[float]) -> Optional[float]:
        """Dollar value of a position at a price (asset currency) and USD-per-unit rate."""
        if not pos["quantity"] and not pos.get("qcost"):
            return 0.0
        if price is None or not fx:
            return None
        a = asset.get("id", "")
        if is_instrument(a):
            ins = self.inst(a)
            from ..hedge.series import unit_size
            if ins.type == "OPTION":
                return pos["quantity"] * unit_size(ins) * price * fx
            return pos["quantity"] * unit_size(ins) * (price - pos.get("avg", price)) * fx
        if usd_base_fx(asset):
            return 2.0 * pos["quantity"] - pos["qcost"] / price
        return pos["quantity"] * price * fx

    def requirement(self, pos: Dict[str, dict], meta: Dict[str, dict], i: int) -> float:
        """Collateral the positions tie up at session i: 150% of the value of short sales, plus the margin estimate
        on futures and forwards (share of notional; see hedge.series.margin_rate)."""
        req = 0.0
        for a, p in pos.items():
            q = p["quantity"]
            if not q:
                continue
            if is_instrument(a):
                ins = self.inst(a)
                if ins.type in ("FUTURE", "FORWARD"):
                    from ..hedge.series import margin_rate, unit_size
                    px = self.mark(a, i) or p.get("avg") or 0.0
                    req += margin_rate(ins) * abs(q) * unit_size(ins) * px * (self._fx_at(ins.currency, i) or 1.0)
            elif q < 0:
                px = self.mark(a, i) or p.get("avg") or 0.0
                req += self.SHORT_REQUIREMENT * abs(q) * px * (self._fx_at(meta[a].get("currency"), i) or 1.0)
        return req

    def _replay(self, txs: List[dict], until: Optional[str] = None, daily: bool = False, strict: bool = False) -> dict:
        cal = self.panel.calendar()
        txs = sorted(txs, key=lambda x: (x["date"], x["id"]))
        issues: List[str] = []
        state = {"cash": 0.0, "contributed": 0.0, "realized": 0.0, "income": 0.0, "fees": 0.0, "financing": 0.0, "interest": 0.0}
        settled: List[dict] = []
        pos: Dict[str, dict] = {}
        if not txs:
            return {**state, "positions": pos, "issues": issues, "dates": [], "nav": [], "flows": [], "as_of": until or cal[-1],
                    "settled": settled, "requirement": 0.0}
        end = self.panel.index_of(until) if until else len(cal) - 1
        i0 = self.panel.index_of(txs[0]["date"])
        assets = {t["asset_id"] for t in txs if t.get("asset_id")}
        meta = {a: self._meta(a) for a in assets}
        by_day: Dict[int, List[dict]] = {}
        for t in txs:
            if until and t["date"] > until:
                continue
            by_day.setdefault(self.panel.index_of(t["date"]), []).append(t)
        divs: Dict[int, List[tuple]] = {}
        for a in assets:
            if is_instrument(a):
                continue
            for ev in self.store.actions(a, "DIVIDEND"):
                k = self.panel.index_of(ev["date"])
                if cal[k] == ev["date"] and i0 < k <= end:        # ex-date on a trading day after the first transaction
                    divs.setdefault(k, []).append((a, ev["value"]))
        shorted = {t["asset_id"] for t in txs if t["kind"] in ("SHORT", "COVER")}
        insts = {a: self.inst(a) for a in assets if is_instrument(a)}

        def fail(msg):
            if strict:
                raise LedgerError(msg)
            issues.append(msg)

        def free_cash(i):
            return state["cash"] - self.requirement(pos, meta, i)

        dates, navs, flows = [], [], []
        from ..hedge.series import settlement, unit_size
        from . import cash as cashmod
        cs = self.cash_settings
        accrue = cs.get("cash_mode") != "none" or cs.get("short_mode") != "none"
        bills = self.panel.macro("DGS3MO") if accrue else None
        for i in range(i0, end + 1):
            flow = 0.0
            if accrue and i > i0:
                # interest on the previous close's balances: short proceeds (up to the cash held) at the short rate,
                # the rest of positive cash at the idle-cash rate
                bill = bills[i - 1] / 100.0 if bills[i - 1] is not None else None
                rc, rs = cashmod.rates(cs, bill)
                short_mv = 0.0
                for a in shorted:
                    p = pos.get(a)
                    if p and p["quantity"] < -1e-12:
                        px_ = self.mark(a, i - 1)
                        if px_:
                            short_mv += -p["quantity"] * px_ * (self._fx_at(meta[a].get("currency"), i - 1) or 0.0)
                base = max(0.0, state["cash"])
                proceeds = min(base, short_mv)
                intr = (base - proceeds) * rc / 252.0 + proceeds * rs / 252.0
                if intr:
                    state["cash"] += intr; state["interest"] += intr
            # expiries: settle derivative positions whose expiry has arrived
            for a, ins in insts.items():
                p = pos.get(a)
                if p and p["quantity"] and ins.expiry and ins.expiry <= cal[i]:
                    px_set = settlement(self.panel, self.store, ins)
                    rate = self._fx_at(ins.currency, i) or 1.0
                    if px_set is None:
                        fail(f"{cal[i]}: {a} expired but its settlement price is unknown; kept at its last mark")
                        continue
                    if ins.type == "OPTION":
                        cashflow = p["quantity"] * unit_size(ins) * px_set * rate
                        pnl = cashflow - p["cost"]
                    else:
                        cashflow = p["quantity"] * unit_size(ins) * (px_set - p["avg"]) * rate
                        pnl = cashflow
                    state["cash"] += cashflow; state["realized"] += pnl; p["realized"] += pnl
                    settled.append({"date": cal[i], "asset_id": a, "quantity": p["quantity"], "price": px_set, "cash": cashflow, "pnl": pnl})
                    p["quantity"], p["cost"], p["avg"] = 0.0, 0.0, 0.0
            for a, amt in divs.get(i, ()):                     # dividends go to holders at the previous close
                p = pos.get(a)
                if p and abs(p["quantity"]) > 1e-12 and not usd_base_fx(meta[a]):
                    usd = p["quantity"] * amt * (self._fx_at(meta[a].get("currency"), i) or 0.0)   # negative for a short
                    state["cash"] += usd; state["income"] += usd; p["income"] += usd
            for a in shorted:                                  # borrow fee on open shorts, every session
                p = pos.get(a)
                if p and p["quantity"] < -1e-12 and i > p.get("opened", i):
                    px_ = self.mark(a, i)
                    if px_:
                        fee_b = -p["quantity"] * px_ * (self._fx_at(meta[a].get("currency"), i) or 0.0) * self.BORROW_FEE / 252.0
                        state["cash"] -= fee_b; state["financing"] += fee_b; p["income"] -= fee_b
            for t in by_day.get(i, ()):
                k = t["kind"]
                if k == "DEPOSIT":
                    state["cash"] += t["price"]; state["contributed"] += t["price"]; flow += t["price"]
                    continue
                if k == "WITHDRAW":
                    fc = free_cash(i)
                    if t["price"] > fc + 1e-6:
                        fail(f"{t['date']}: withdrawing {t['price']:,.2f} USD but only {fc:,.2f} is free cash then")
                    state["cash"] -= t["price"]; state["contributed"] -= t["price"]; flow -= t["price"]
                    continue
                a = t["asset_id"]
                m = meta[a]
                f = self._split_factor(a, t.get("basis_date"))
                q, price = t["quantity"] * f, t["price"] / f
                rate = self._fx_at(t.get("currency") or m.get("currency"), i)
                if not rate:
                    fail(f"{t['date']}: no {t.get('currency')} exchange rate for {a}")
                    continue
                fee = t.get("fee") or 0.0
                p = pos.setdefault(a, {"quantity": 0.0, "cost": 0.0, "realized": 0.0, "income": 0.0, "qcost": 0.0, "avg": 0.0})
                fc0 = free_cash(i)
                if a in insts:
                    ok = self._apply_instrument(insts[a], p, k, q, price, fee, rate, state, t, fail)
                else:
                    ok = self._apply_spot(a, m, p, k, q, price, fee, rate, state, t, fail, i)
                if ok and k in ("BUY", "SHORT") or (ok and a in insts and k == "SELL"):
                    fc = free_cash(i)
                    if fc < -1e-6:
                        need = fc0 - fc
                        fail(f"{t['date']}: {'buying' if k == 'BUY' else 'selling' if k == 'SELL' else 'shorting'} {q:,.6g} {a} needs {need:,.2f} USD "
                             f"of buying power but only {fc0:,.2f} is free then (cash less 150% of shorts and futures margin)")
            if daily:
                v = state["cash"]
                for a, p in pos.items():
                    if p["quantity"]:
                        mv = self.value(meta[a], p, self.mark(a, i), self._fx_at(meta[a].get("currency"), i))
                        v += mv or 0.0
                dates.append(cal[i]); navs.append(v); flows.append(flow)
        return {**state, "positions": pos, "issues": issues, "dates": dates, "nav": navs, "flows": flows, "as_of": cal[end],
                "settled": settled, "requirement": self.requirement(pos, meta, end)}

    def _apply_spot(self, a, m, p, k, q, price, fee, rate, state, t, fail, i) -> bool:
        fx_pair = usd_base_fx(m)
        usd = q if fx_pair else q * price * rate
        if k == "BUY":
            if p["quantity"] < -1e-12:
                fail(f"{t['date']}: {a} is held short: COVER it before buying")
                return False
            state["cash"] -= usd + fee; state["fees"] += fee
            p["quantity"] += q; p["cost"] += usd + fee
            if fx_pair:
                p["qcost"] += q * price
            return True
        if k == "SHORT":
            if p["quantity"] > 1e-12:
                fail(f"{t['date']}: {a} is held long: SELL it before shorting")
                return False
            state["cash"] += usd - fee; state["fees"] += fee
            p["quantity"] -= q; p["cost"] -= (usd - fee)          # a short's basis is minus the net proceeds
            p.setdefault("opened", i)
            return True
        if k == "COVER":
            held = -p["quantity"]
            if q > held + 1e-9:
                fail(f"{t['date']}: covering {q:,.6g} {a} but only {held:,.6g} is short then")
                q = max(0.0, held)
                usd = q * price * rate
            if q <= 0:
                return False
            frac = q / held if held else 0.0
            basis_out = p["cost"] * frac                          # negative: the proceeds received for this part
            pnl = -basis_out - usd - fee
            state["cash"] -= usd + fee; state["fees"] += fee
            state["realized"] += pnl; p["realized"] += pnl
            p["cost"] -= basis_out; p["quantity"] += q
            if abs(p["quantity"]) < 1e-12:
                p["quantity"], p["cost"] = 0.0, 0.0
                p.pop("opened", None)
            return True
        # SELL
        held = p["quantity"]
        if q > held + 1e-9:
            fail(f"{t['date']}: selling {q:,.6g} {a} but only {held:,.6g} is held then")
            q = max(0.0, held)                   # (reading only) never credit more than was held
            usd = q if fx_pair else q * price * rate
        if q <= 0:
            return False
        frac = q / held if held else 0.0
        cost_out = p["cost"] * frac
        if fx_pair:
            qc_out = p["qcost"] * frac
            proceeds = 2.0 * q - qc_out / price     # collateral back plus the short leg's gain or loss
            p["qcost"] -= qc_out
        else:
            proceeds = usd
        pnl = proceeds - fee - cost_out
        state["cash"] += proceeds - fee; state["fees"] += fee
        state["realized"] += pnl; p["realized"] += pnl
        p["cost"] -= cost_out; p["quantity"] -= q
        if abs(p["quantity"]) < 1e-12:
            p["quantity"], p["cost"], p["qcost"] = 0.0, 0.0, 0.0
        return True

    def _apply_instrument(self, ins, p, k, q, price, fee, rate, state, t, fail) -> bool:
        from ..hedge.series import unit_size
        u = unit_size(ins)
        a = ins.id
        if ins.type == "OPTION":
            if k == "BUY":
                paid = q * u * price * rate + fee
                state["cash"] -= paid; state["fees"] += fee
                p["quantity"] += q; p["cost"] += paid
                return True
            held = p["quantity"]
            if q > held + 1e-9:
                fail(f"{t['date']}: selling {q:,.6g} {a} but only {held:,.6g} is held then (options are only bought: writing is not supported)")
                q = max(0.0, held)
            if q <= 0:
                return False
            frac = q / held
            cost_out = p["cost"] * frac
            proceeds = q * u * price * rate - fee
            pnl = proceeds - cost_out
            state["cash"] += proceeds; state["fees"] += fee
            state["realized"] += pnl; p["realized"] += pnl
            p["cost"] -= cost_out; p["quantity"] -= q
            if abs(p["quantity"]) < 1e-12:
                p["quantity"], p["cost"] = 0.0, 0.0
            return True
        # futures and forwards: signed quantity, average entry, P&L realised on reduction
        dq = q if k == "BUY" else -q
        state["cash"] -= fee; state["fees"] += fee
        Q = p["quantity"]
        if Q == 0 or (Q > 0) == (dq > 0):
            p["avg"] = (abs(Q) * p["avg"] + abs(dq) * price) / (abs(Q) + abs(dq))
            p["quantity"] = Q + dq
            return True
        closing = min(abs(dq), abs(Q))
        pnl = closing * u * (price - p["avg"]) * (1 if Q > 0 else -1) * rate
        state["cash"] += pnl; state["realized"] += pnl; p["realized"] += pnl
        p["quantity"] = Q + (closing if dq > 0 else -closing)
        rest = abs(dq) - closing
        if abs(p["quantity"]) < 1e-12:
            p["quantity"], p["avg"] = 0.0, 0.0
        if rest > 1e-12:                                          # flipped through zero: the remainder opens at the price
            p["quantity"] = rest if dq > 0 else -rest
            p["avg"] = price
        return True

    # ------------------------------------------------------------------ state
    def holdings(self, as_of: Optional[str] = None) -> dict:
        r = self._replay(self.store.transactions(self.pid), until=as_of)
        return {k: r[k] for k in ("as_of", "cash", "positions", "realized", "contributed", "income", "fees", "issues", "financing",
                                  "settled", "requirement", "interest")}

    def nav_history(self) -> dict:
        """Daily NAV from the first transaction to today (positions at each day's close or model price and rate)."""
        r = self._replay(self.store.transactions(self.pid), daily=True)
        return {"dates": r["dates"], "nav": r["nav"], "flows": r["flows"], "issues": r["issues"]}

    def positions_for_hedge(self) -> List[dict]:
        """Current positions in the form Shaffer Hedge prices: [{"id", "quantity", "entry"}] (entry: futures/forwards)."""
        h = self.holdings()
        out = []
        for a, p in h["positions"].items():
            if abs(p["quantity"]) < 1e-12:
                continue
            e = {"id": a, "quantity": p["quantity"]}
            if is_instrument(a) and self.inst(a).type in ("FUTURE", "FORWARD"):
                e["entry"] = p.get("avg")
            out.append(e)
        return out


# ------------------------------------------------------------------ performance
def xirr(flows: List[tuple]) -> Optional[float]:
    """Money-weighted annual return: the rate that zeroes Σ CF / (1+r)^(days/365). flows = [(date, amount)]
    with contributions negative and the ending value positive."""
    import datetime as _dt
    if len(flows) < 2 or not any(c > 0 for _, c in flows) or not any(c < 0 for _, c in flows):
        return None
    d0 = _dt.date.fromisoformat(flows[0][0])
    ts = [((_dt.date.fromisoformat(d) - d0).days / 365.0, c) for d, c in flows]

    def npv(r):
        return sum(c / (1 + r) ** t for t, c in ts)
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


PERIODS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252), ("3Y", 756), ("5Y", 1260)]


def performance(hist: dict, bench_px: Optional[List[Optional[float]]] = None, cal: Optional[List[str]] = None) -> dict:
    """Time-weighted return (daily returns net of external flows, chained), money-weighted return, period returns,
    daily P&L, and the same periods for a benchmark over the same dates."""
    dates, nav, flows = hist.get("dates") or [], hist.get("nav") or [], hist.get("flows") or []
    if len(nav) < 2:
        return {"days": len(nav)}
    r = [None]
    for k in range(1, len(nav)):
        a = nav[k - 1]
        r.append(((nav[k] - flows[k]) / a - 1) if a and a > 0 else None)   # a flow arrives at the close: not a return
    idx = [1.0]
    for x in r[1:]:
        idx.append(idx[-1] * (1 + (x or 0.0)))

    def period(series, n):
        if len(series) <= n:
            return None
        a, b = series[-1 - n], series[-1]
        return (b / a - 1) if a else None
    ytd_start = next((k for k, d in enumerate(dates) if d[:4] == dates[-1][:4]), None)
    out = {"days": len(nav), "twr": idx[-1] - 1, "twr_index": idx,
           "periods": {lab: period(idx, n) for lab, n in PERIODS},
           "daily_pnl": nav[-1] - nav[-2] - flows[-1], "daily_return": r[-1]}
    if ytd_start is not None and ytd_start > 0:
        out["periods"]["YTD"] = idx[-1] / idx[ytd_start - 1] - 1
    years = len(nav) / 252.0
    out["twr_annual"] = (idx[-1] ** (1 / years) - 1) if years >= 1 and idx[-1] > 0 else None
    cf = [(dates[k], -flows[k]) for k in range(len(nav)) if flows[k]]
    cf.append((dates[-1], nav[-1]))
    out["mwr"] = xirr(cf)
    if bench_px and cal:
        pos = {d: k for k, d in enumerate(cal)}
        b = [bench_px[pos[d]] if d in pos else None for d in dates]
        b = _ffill(b)
        if b[0]:
            bidx = [x / b[0] if x else None for x in b]
            out["benchmark"] = {"total": bidx[-1] - 1 if bidx[-1] else None, "index": bidx,
                                "periods": {lab: period(bidx, n) for lab, n in PERIODS}}
            if ytd_start is not None and ytd_start > 0 and bidx[ytd_start - 1]:
                out["benchmark"]["periods"]["YTD"] = bidx[-1] / bidx[ytd_start - 1] - 1
            ex = [(r[k] or 0.0) - (bidx[k] / bidx[k - 1] - 1) for k in range(1, len(r)) if bidx[k] and bidx[k - 1]]
            if len(ex) > 20:
                m = sum(ex) / len(ex)
                te = math.sqrt(sum((x - m) ** 2 for x in ex) / (len(ex) - 1)) * math.sqrt(ANN)
                out["benchmark"]["tracking_error"] = te
                out["benchmark"]["information_ratio"] = (m * ANN / te) if te else None
                out["benchmark"]["excess_annual"] = m * ANN
    return out


# ------------------------------------------------------------------ analytics
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def aligned_returns(panel: Panel, assets: List[str], days: int = 756) -> Dict[str, List[Optional[float]]]:
    """Daily simple returns in USD (price × rate) of each asset over the last `days` sessions."""
    out = {}
    for a in assets:
        p = panel.series(a, "adj_close")
        ccy = (panel.store.asset(a) or {}).get("currency") or "USD"
        fx = fx_series(panel, ccy) if ccy != "USD" else None
        usd = [(p[i] * fx[i]) if (fx is not None and p[i] is not None and fx[i] is not None) else (p[i] if fx is None else None) for i in range(len(p))]
        r = [None] + [(usd[i] / usd[i - 1] - 1) if usd[i] is not None and usd[i - 1] else None for i in range(1, len(usd))]
        out[a] = r[-days:]
    return out


def covariance(rets: Dict[str, list], assets: List[str]) -> List[List[float]]:
    n = len(assets)
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            pairs = [(x, y) for x, y in zip(rets[assets[i]], rets[assets[j]]) if x is not None and y is not None]
            if len(pairs) < 20:
                c = 0.0
            else:
                mx = sum(x for x, _ in pairs) / len(pairs)
                my = sum(y for _, y in pairs) / len(pairs)
                c = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) - 1) * ANN
            cov[i][j] = cov[j][i] = c
    return cov


def correlation_from_cov(cov):
    n = len(cov)
    return [[(cov[i][j] / math.sqrt(cov[i][i] * cov[j][j])) if cov[i][i] > 0 and cov[j][j] > 0 else None for j in range(n)] for i in range(n)]


def portfolio_series(rets: Dict[str, list], weights: Dict[str, float]) -> List[Optional[float]]:
    n = len(next(iter(rets.values()))) if rets else 0
    out = []
    for t in range(n):
        s = 0.0
        ok = False
        for a, w in weights.items():
            v = rets[a][t]
            if v is not None:
                s += w * v
                ok = True
        out.append(s if ok else None)
    return out


def risk_stats(r: List[Optional[float]], rf: float = 0.0) -> dict:
    xs = [x for x in r if x is not None]
    if len(xs) < 30:
        return {}
    m, sd = _mean(xs), _std(xs)
    dn = math.sqrt(sum(min(x, 0) ** 2 for x in xs) / len(xs))
    eq, peak, mdd = 1.0, 1.0, 0.0
    for x in xs:
        eq *= 1 + x; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    srt = sorted(xs)
    q95, q99 = srt[int(0.05 * len(srt))], srt[int(0.01 * len(srt))]
    es95 = -_mean(srt[:max(1, int(0.05 * len(srt)))])
    es99 = -_mean(srt[:max(1, int(0.01 * len(srt)))])
    ann_ret = m * ANN
    return {"ann_return": ann_ret, "vol": sd * math.sqrt(ANN), "sharpe": (ann_ret - rf) / (sd * math.sqrt(ANN)) if sd else None,
            "sortino": (ann_ret - rf) / (dn * math.sqrt(ANN)) if dn else None, "max_drawdown": mdd,
            "var95": -q95, "var99": -q99, "es95": es95, "es99": es99, "days": len(xs)}


def regress(y: List[Optional[float]], xs: Dict[str, List[Optional[float]]]) -> dict:
    """Multivariate OLS with intercept (rows with any missing value dropped): betas, t-stats, R², and each factor's
    share of the explained variance (Euler: β_k Cov(f_k, ŷ) / Var(ŷ))."""
    from finsim.quant.linalg import solve
    names = list(xs)
    rows = [(y[t], [xs[k][t] for k in names]) for t in range(len(y)) if y[t] is not None and all(xs[k][t] is not None for k in names)]
    p = len(names) + 1
    if len(rows) < p + 10:
        return {}
    X = [[1.0] + r for _, r in rows]
    Y = [v for v, _ in rows]
    XtX = [[sum(X[t][a] * X[t][b] for t in range(len(X))) for b in range(p)] for a in range(p)]
    for a in range(p):
        XtX[a][a] += 1e-12
    XtY = [sum(X[t][a] * Y[t] for t in range(len(X))) for a in range(p)]
    try:
        beta = solve(XtX, XtY)
    except Exception:
        return {}
    fit = [sum(beta[a] * X[t][a] for a in range(p)) for t in range(len(X))]
    res = [Y[t] - fit[t] for t in range(len(X))]
    my = sum(Y) / len(Y)
    sst = sum((v - my) ** 2 for v in Y)
    sse = sum(e * e for e in res)
    s2 = sse / max(1, len(Y) - p)
    try:
        from finsim.quant.linalg import inverse
        inv = inverse(XtX)
        se = [math.sqrt(max(0.0, s2 * inv[a][a])) for a in range(p)]
    except Exception:
        se = [None] * p
    mf = sum(fit) / len(fit)
    vf = sum((f - mf) ** 2 for f in fit) or 1e-18
    shares = {}
    for k, name in enumerate(names, start=1):
        col = [X[t][k] for t in range(len(X))]
        mc = sum(col) / len(col)
        cov = sum((col[t] - mc) * (fit[t] - mf) for t in range(len(X)))
        shares[name] = beta[k] * cov / vf
    return {"alpha": beta[0], "betas": {n: beta[i + 1] for i, n in enumerate(names)},
            "t": {n: (beta[i + 1] / se[i + 1]) if se[i + 1] else None for i, n in enumerate(names)},
            "r2": 1 - sse / sst if sst > 0 else None, "shares": shares, "n": len(Y)}


def factor_returns(panel: Panel, days: int = 756, weekly: bool = True) -> Dict[str, List[Optional[float]]]:
    ids = sorted({x for a, b in FACTORS.values() for x in (a, b) if x})
    rets = aligned_returns(panel, [i for i in ids if panel.store.asset(i)], days)
    out = {}
    for name, (a, b) in FACTORS.items():
        if a not in rets or (b and b not in rets):
            continue
        out[name] = [(x - (rets[b][t] if b else 0.0)) if x is not None and (not b or rets[b][t] is not None) else None for t, x in enumerate(rets[a])]
    return _weekly(out) if weekly else out


def _weekly(series: Dict[str, list], k: int = 5) -> Dict[str, list]:
    out = {}
    for name, s in series.items():
        agg = []
        for i in range(0, len(s) - k + 1, k):
            chunk = s[i:i + k]
            if any(v is None for v in chunk):
                agg.append(None)
            else:
                g = 1.0
                for v in chunk:
                    g *= 1 + v
                agg.append(g - 1)
        out[name] = agg
    return out


def benchmark_id(store) -> str:
    b = store.kv_get("settings:benchmark") or "SPY"
    return b if store.asset(b) else "SPY"


FX_PROXY = {"EUR": "FXE", "JPY": "FXY", "GBP": "FXB"}


def risk_equivalent(ledger: Ledger, a: str, p: dict, px: Optional[float], i: int) -> List[tuple]:
    """The position's economic exposure as (asset with a price history, dollars): itself for spot positions (negative
    for shorts); the tracking ETF × notional for equity-index futures; IEF-duration dollars (DV01 ÷ IEF's DV01 per $)
    for Treasury futures; currency-trust dollars for currency futures and forwards; delta-dollars on the underlying
    for options."""
    q = p["quantity"]
    if not q:
        return []
    if not is_instrument(a):
        meta = ledger.store.asset(a) or {}
        fx = ledger._fx_at(meta.get("currency") or "USD", i) or 0.0
        if usd_base_fx(meta):
            return [(a, ledger.value(meta, p, px, fx) or 0.0)]
        return [(a, q * (px or 0.0) * fx)]
    ins = ledger.inst(a)
    from ..hedge.series import unit_size
    if px is None:
        return []
    if ins.type == "FUTURE":
        k = ins.spec["kind"]
        if k == "equity_index":
            return [(ins.spec["proxy"], q * unit_size(ins) * px)]
        if k == "treasury":
            from ..hedge import pricing as hp
            dv = hp.bond_dv01(0.06, ins.spec["ctd_years"], 0.045) * ins.spec["face"] / 100.0
            ief_d = (ledger.store.asset("IEF") or {}).get("duration") or 7.1
            return [("IEF", q * dv / (ief_d * 1e-4))]
        if k == "fx":
            ccy = ins.spec["ccy"]
            proxy = FX_PROXY.get(ccy) or ins.spec.get("pair")
            return [(proxy, q * unit_size(ins) * px)]
        return []
    if ins.type == "FORWARD":
        base, quote = ins.spec["base"], ins.spec["quote"]
        if base == "USD":
            proxy = FX_PROXY.get(quote)
            return [(proxy, -q)] if proxy else []
        proxy = FX_PROXY.get(base) or ins.underlying
        return [(proxy, q * (ledger._fx_at(base, i) or 0.0))]
    if ins.type == "OPTION":
        from ..hedge import pricing as hp
        from ..hedge.market import Market
        S = ledger.mark(ins.underlying, i)
        if not S:
            return []
        # delta from the same model inputs as the mark: back out the implied vol the series used
        from ..hedge.series import _rate
        from ..hedge.market import VOL_INDEX
        spec = VOL_INDEX.get(ins.underlying) or []
        pts = [(mat, _rate(ledger.panel, sid)[i]) for mat, sid in spec]
        pts = [(m_, v) for m_, v in pts if v is not None]
        T = hp.year_frac(ledger.panel.calendar()[i], ins.expiry)
        vol = hp.interp_var_time(max(T, 1 / 365), pts) if pts else 0.2
        r = _rate(ledger.panel, "DGS3MO")[i] or 0.0
        g = hp.option(ins.right, S, ins.strike, T, r, 0.0, vol, "EUROPEAN")
        return [(ins.underlying, q * unit_size(ins) * g["delta"] * S)]
    return []


def analytics(store, panel: Panel, ledger: Ledger, scores: Optional[Dict[str, dict]] = None) -> dict:
    """Everything on the Portfolio page. `scores`: {asset_id: {quant, ml, shaffer, expected, confidence, horizon}} if known."""
    cal = panel.calendar()
    i = len(cal) - 1
    h = ledger.holdings()
    rows = []
    warnings = list(h.get("issues") or [])
    nav = h["cash"]
    for a, p in h["positions"].items():
        if abs(p["quantity"]) < 1e-12 and not p["realized"] and not p.get("income"):
            continue
        meta = ledger._meta(a) if is_instrument(a) else (store.asset(a) or {"id": a, "name": a, "asset_class": "?", "currency": "USD"})
        px = ledger.mark(a, i)
        if p["quantity"] and px is None:
            warnings.append(f"{a}: no current price or model price (a missing input)")
        elif p["quantity"] and not is_instrument(a):
            closes = panel.series(a, "close", max_fill=10)
            last = next((k for k in range(i, -1, -1) if closes[k] is not None), None)
            if last is not None and i - last > 3:
                warnings.append(f"{a}: the latest price is from {cal[last]} ({i - last} sessions old)")
        mv = ledger.value(meta, p, px, ledger._fx_at(meta.get("currency") or "USD", i))
        if mv is None:
            warnings.append(f"{a}: no price or {meta.get('currency')} exchange rate; valued at cost")
            mv = p["cost"] if not is_instrument(a) else 0.0
        nav += mv
        eq = risk_equivalent(ledger, a, p, px, i)
        row = {"asset_id": a, "name": meta.get("name"), "asset_class": meta.get("asset_class"), "sector": meta.get("sector"),
               "country": meta.get("country"), "currency": meta.get("currency"), "duration": meta.get("duration"),
               "quantity": p["quantity"], "price": px, "market_value": mv, "cost_basis": p["cost"],
               "unrealized": (mv - p["cost"]) if p["quantity"] else 0.0, "realized": p["realized"], "income": p.get("income", 0.0),
               "side": "short" if p["quantity"] < 0 else "long", "exposure": sum(v for _, v in eq), "risk_proxy": eq}
        if is_instrument(a):
            from ..hedge.products import pricing_label
            ins = ledger.inst(a)
            row.update({"instrument": ins.view(), "entry": p.get("avg") if ins.type in ("FUTURE", "FORWARD") else None,
                        "unrealized": mv if ins.type in ("FUTURE", "FORWARD") else (mv - p["cost"] if p["quantity"] else 0.0),
                        "product_type": ins.product_type, "pricing_label": pricing_label(ins)})
        elif abs(p["quantity"]) > 1e-12 and continuous_series(meta):
            row["continuous_series"] = True
            warnings.append(f"{a}: a continuous front-month futures series — its P&L includes roll jumps between contracts; "
                            f"it can no longer be bought{(' (' + CONTINUOUS_PROXY[a] + ' is the tradeable exposure)') if a in CONTINUOUS_PROXY else ''}")
        rows.append(row)
    held = [r for r in rows if abs(r["quantity"]) > 1e-12]
    for r in rows:
        r["weight"] = r["market_value"] / nav if nav else None
    weights: Dict[str, float] = {}
    if nav:
        for r in held:
            for aid, usd in r["risk_proxy"]:
                weights[aid] = weights.get(aid, 0.0) + usd / nav
    rets = aligned_returns(panel, list(weights), 756) if weights else {}
    ps = portfolio_series(rets, weights) if weights else []
    rf = next((v for v in reversed(panel.macro("DGS3MO")) if v is not None), 0.0) / 100.0
    risk = risk_stats(ps, rf) if ps else {}
    assets = list(weights)
    cov = covariance(rets, assets) if assets else []
    w = [weights[a] for a in assets]
    port_var = sum(w[i] * cov[i][j] * w[j] for i in range(len(w)) for j in range(len(w))) if w else 0.0
    sig = math.sqrt(port_var) if port_var > 0 else None
    mrc = [sum(cov[i][j] * w[j] for j in range(len(w))) / sig if sig else None for i in range(len(w))]
    rc = {a: ((w[k] * mrc[k] / sig) if sig and mrc[k] is not None else None) for k, a in enumerate(assets)}
    for r in held:
        prox = r["risk_proxy"]
        if len(prox) == 1 and prox[0][0] == r["asset_id"]:
            a = r["asset_id"]
            r["risk_contribution"] = rc.get(a)
            r["marginal_risk"] = mrc[assets.index(a)] if a in assets else None
            vol = _std(rets.get(a) or [])
            r["volatility"] = vol * math.sqrt(ANN) if vol else None
            r["sharpe"] = risk_stats(rets.get(a) or [], rf).get("sharpe")
        else:
            # a derivative or a position also held through another line: its share of the proxy's contribution
            tot = {aid: sum(u for x in held for b, u in x["risk_proxy"] if b == aid) for aid, _ in prox}
            r["risk_contribution"] = sum((rc.get(aid) or 0.0) * (u / tot[aid]) for aid, u in prox if tot.get(aid)) if prox else None
            r["marginal_risk"] = None
            r["volatility"] = None
            r["sharpe"] = None
    mkt = aligned_returns(panel, ["SPY"], 756)["SPY"] if store.asset("SPY") else []
    for r in held:
        base = r["risk_proxy"][0][0] if r["risk_proxy"] else r["asset_id"]
        pairs = [(x, m) for x, m in zip(rets.get(base, []), mkt) if x is not None and m is not None]
        if len(pairs) > 60:
            mx = sum(m for _, m in pairs) / len(pairs); my = sum(x for x, _ in pairs) / len(pairs)
            vx = sum((m - mx) ** 2 for _, m in pairs)
            r["beta"] = sum((m - mx) * (x - my) for x, m in pairs) / vx if vx else None
        else:
            r["beta"] = None
        sc = (scores or {}).get(r["asset_id"]) or {}
        r.update({"quant_score": sc.get("quant"), "ml_score": sc.get("ml"), "shaffer_score": sc.get("shaffer"), "expected_return": sc.get("expected"),
                  "signal_confidence": sc.get("confidence"), "primary_horizon": sc.get("horizon")})
    long_ = sum(r["exposure"] for r in held if r["exposure"] > 0)          # economic exposure (notional / delta-adjusted)
    short_ = -sum(r["exposure"] for r in held if r["exposure"] < 0)

    def group(key):
        g: Dict[str, float] = {}
        for r in held:
            g[r.get(key) or "Other"] = g.get(r.get(key) or "Other", 0.0) + r["market_value"]
        if key == "asset_class" and h["cash"]:
            g["Cash"] = g.get("Cash", 0.0) + h["cash"]
        if key == "currency" and h["cash"]:
            g["USD"] = g.get("USD", 0.0) + h["cash"]
        return dict(sorted(g.items(), key=lambda kv: -abs(kv[1])))
    dv01 = 0.0
    for r in held:
        for aid, usd in r["risk_proxy"]:
            d = (store.asset(aid) or {}).get("duration")
            if d:
                dv01 += usd * d * 1e-4
    duration = (dv01 / nav * 1e4) if nav else 0.0
    beta_p = sum((r["beta"] or 0.0) * r["exposure"] for r in held) / nav if nav else None
    corr = correlation_from_cov(cov) if cov else []
    factors = {}
    if ps:
        fr = factor_returns(panel, 756, weekly=True)
        pw = _weekly({"p": ps})["p"]
        m = min(len(pw), *(len(v) for v in fr.values())) if fr else 0
        if m > 30:
            factors = regress(pw[-m:], {k: v[-m:] for k, v in fr.items()})
    hist = ledger.nav_history()
    nav_r = []
    for k in range(1, len(hist["nav"])):
        a, b = hist["nav"][k - 1], hist["nav"][k]
        nav_r.append(((b - hist["flows"][k]) / a - 1) if a else None)
    actual = risk_stats(nav_r, rf) if len(nav_r) >= 30 else {}
    bench = benchmark_id(store)
    perf = performance(hist, panel.series(bench), cal)
    perf.pop("twr_index", None)
    if perf.get("benchmark"):
        perf["benchmark"].pop("index", None)
        perf["benchmark"]["id"] = bench
    return {"as_of": cal[-1], "nav": nav, "cash": h["cash"], "contributed": h["contributed"], "realized": h["realized"],
            "financing": h.get("financing", 0.0), "interest": h.get("interest", 0.0), "cash_settings": ledger.cash_settings,
            "collateral_required": h.get("requirement", 0.0),
            "free_cash": h["cash"] - (h.get("requirement") or 0.0), "settled": h.get("settled") or [], "dv01": dv01,
            "income": h.get("income", 0.0), "fees": h.get("fees", 0.0), "performance": perf, "warnings": warnings, "benchmark": bench,
            "unrealized": sum(r["unrealized"] for r in held), "pnl": nav - h["contributed"],
            "gross": long_ + short_, "net": long_ - short_, "long": long_, "short": short_,
            "allocation": group("asset_class"), "sector": group("sector"), "country": group("country"), "currency": group("currency"),
            "duration": duration, "beta": beta_p, "risk": risk, "risk_actual": actual, "volatility": sig,
            "positions": sorted(rows, key=lambda r: -abs(r["market_value"])),
            "correlation": {"assets": assets, "matrix": corr}, "factors": factors, "rf": rf,
            "history": {"dates": hist["dates"][-2520:], "nav": hist["nav"][-2520:]}}

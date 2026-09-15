"""Framework-agnostic API service.

Every method takes plain Python values and returns JSON-safe dicts. The stdlib
HTTP server (server.py) and an optional FastAPI adapter both call into this
class, so the web framework is replaceable.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..domain.events import E
from ..engines.ledger import CHART, account_name, account_type
from ..engines.market import REGIMES
from ..engines.pricing import BondPricer, Instrument
from ..money import D, money, ZERO
from ..store import EventStore
from ..world import CommandError, World


class NotFound(Exception):
    pass


def jsonable(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, date):
        return o.isoformat()
    if is_dataclass(o) and not isinstance(o, type):
        return jsonable(asdict(o))
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    return o


class Service:
    def __init__(self, store: EventStore):
        self.store = store
        self.worlds: Dict[str, World] = {}

    # ------------------------------------------------------------------ worlds
    def list_worlds(self) -> List[Dict]:
        return self.store.list_worlds()

    def create_world(self, name: str, seed: int, start_date: str, capital: float = 10_000_000, portfolio_name: str = "Main Portfolio",
                     portfolio_type: str = "PERSONAL", realism: str = "PROFESSIONAL", mode: str = "SANDBOX", initial_regime: str = "NORMAL_GROWTH",
                     benchmark: Optional[str] = "SPXE") -> Dict:
        if initial_regime not in REGIMES:
            raise CommandError(f"unknown regime {initial_regime}")
        wid = "W-" + uuid.uuid4().hex[:8]
        sd = date.fromisoformat(start_date)
        cal_sd = World(wid).calendar.roll(sd)
        w = World.create(wid, name or "Untitled world", int(seed), cal_sd, store=self.store, initial_regime=initial_regime)
        self.worlds[wid] = w
        pf = w.create_portfolio(portfolio_name, portfolio_type, D(str(capital)), "USD", benchmark, realism, mode)
        return {"world_id": wid, "portfolio_id": pf.id, "start_date": cal_sd.isoformat()}

    def world(self, world_id: str) -> World:
        if world_id not in self.worlds:
            if not any(x["id"] == world_id for x in self.store.list_worlds()):
                raise NotFound(f"world {world_id} not found")
            self.worlds[world_id] = World.load(self.store, world_id)
        return self.worlds[world_id]

    def delete_world(self, world_id: str) -> None:
        self.worlds.pop(world_id, None)
        self.store.delete_world(world_id)

    def world_info(self, world_id: str) -> Dict:
        w = self.world(world_id)
        r = w.market.regime()
        return jsonable({"id": w.id, "name": w.name, "seed": w.seed, "start_date": w.start_date, "current_date": w.current_date,
                         "day_index": w.day_count, "events": len(w.events), "regime": {"name": r.name, "label": r.label, "description": r.description},
                         "portfolios": [{"id": p.id, "name": p.name, "type": p.portfolio_type, "realism": p.realism, "mode": p.mode, "benchmark": p.benchmark}
                                        for p in w.portfolios.values()],
                         "settlement_cycles": w.settlement_config.cycles, "policy_rate": w.market.curve().policy_rate})

    # ------------------------------------------------------------------ commands
    def create_portfolio(self, world_id: str, name: str, portfolio_type: str, capital: float, realism: str, mode: str, benchmark: Optional[str]) -> Dict:
        w = self.world(world_id)
        pf = w.create_portfolio(name, portfolio_type, D(str(capital)), "USD", benchmark, realism, mode)
        return {"portfolio_id": pf.id}

    def contribute(self, world_id: str, portfolio_id: str, amount: float, currency: str = "USD") -> Dict:
        w = self.world(world_id)
        ev = w.contribute_capital(portfolio_id, currency, D(str(amount)))
        return {"event_id": ev.id}

    def place_order(self, world_id: str, portfolio_id: str, security_id: str, side: str, quantity: float, order_type: str = "MARKET",
                    limit_price: Optional[float] = None, stop_price: Optional[float] = None, time_in_force: str = "DAY", strategy_tag: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        try:
            o = w.place_order(portfolio_id, security_id, side, D(str(quantity)), order_type,
                              D(str(limit_price)) if limit_price is not None else None,
                              D(str(stop_price)) if stop_price is not None else None, time_in_force, strategy_tag)
        finally:
            w.flush()
        return self.order(world_id, portfolio_id, o.id)

    def cancel_order(self, world_id: str, portfolio_id: str, order_id: str) -> Dict:
        w = self.world(world_id)
        w.cancel_order(portfolio_id, order_id)
        return self.order(world_id, portfolio_id, order_id)

    def advance(self, world_id: str, days: int = 1) -> Dict:
        w = self.world(world_id)
        closed = w.advance(int(days))
        return {"closed_days": closed, "current_date": w.current_date.isoformat(), "events": len(w.events)}

    # ------------------------------------------------------------------ markets
    def securities(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        out = []
        for sec in w.securities.values():
            bar = w.market.last_bar(sec.id)
            h = w.market.history[sec.id]
            prev = h[-2].close if len(h) > 1 else bar.close
            row = {"id": sec.id, "name": sec.name, "asset_class": sec.asset_class, "sector": sec.sector, "country": sec.country, "currency": sec.currency,
                   "market": sec.market, "isin": sec.isin, "cusip": sec.cusip, "last": bar.close, "bid": bar.bid, "ask": bar.ask, "open": bar.open,
                   "high": bar.high, "low": bar.low, "volume": bar.volume, "prev_close": prev, "change": bar.close - prev,
                   "change_pct": float((bar.close - prev) / prev) if prev else 0.0, "spread_bps": float((bar.ask - bar.bid) / bar.close * 10000),
                   "adv": sec.adv, "liquidity_tier": sec.liquidity_tier, "beta": sec.beta, "realized_vol": w.market.realized_vol(sec.id),
                   "dividend_yield": sec.dividend_yield, "dividend_per_share": sec.dividend_per_share, "lot_size": sec.lot_size,
                   "market_cap": (float(bar.close) * sec.shares_outstanding) if sec.shares_outstanding else None, "rating": sec.rating,
                   "coupon": sec.coupon, "maturity": sec.maturity}
            if sec.is_bond:
                row.update({k: v for k, v in BondPricer.risk_metrics(sec, w.current_date, float(bar.close), w.market.curve()).items()})
            out.append(row)
        return jsonable(out)

    def security(self, world_id: str, security_id: str, period: str = "1Y") -> Dict:
        w = self.world(world_id)
        if security_id not in w.securities:
            raise NotFound(f"security {security_id} not found")
        sec = w.securities[security_id]
        h = w.market.history[sec.id]
        n = {"1D": 2, "5D": 6, "1M": 22, "3M": 64, "YTD": None, "1Y": 252, "5Y": 1260, "MAX": len(h)}.get(period, 252)
        if period == "YTD":
            bars = [b for b in h if b.date >= f"{w.current_date.year}-01-01"]
        else:
            bars = h[-n:]
        bar = h[-1]
        out = {"security": jsonable(asdict(sec)), "last": bar.close, "bid": bar.bid, "ask": bar.ask, "volume": bar.volume,
               "bars": [[b.date, b.open, b.high, b.low, b.close, b.volume] for b in bars],
               "realized_vol_20d": w.market.realized_vol(sec.id), "spread_bps": float((bar.ask - bar.bid) / bar.close * 10000)}
        inst = Instrument(sec, bar.close, w.current_date, w.market.curve())
        out["analytics"] = inst.risk_metrics(D(100) if not sec.is_bond else D(1_000_000))
        out["cash_flows"] = inst.cash_flows()[:12]
        out["next_events"] = inst.next_events()
        if sec.shares_outstanding and sec.fundamentals:
            f = sec.fundamentals
            px = float(bar.close)
            mcap = px * sec.shares_outstanding
            ev_ = mcap + f.get("total_debt", 0) - f.get("cash", 0)
            out["fundamentals"] = {**f, "market_cap": mcap, "enterprise_value": ev_,
                                   "pe": (px / f["eps"]) if f.get("eps") else None,
                                   "ev_ebitda": (ev_ / f["ebitda"]) if f.get("ebitda") else None,
                                   "pb": (px / f["book_value_per_share"]) if f.get("book_value_per_share") else None,
                                   "fcf_yield": (f["free_cash_flow"] / mcap) if f.get("free_cash_flow") else None,
                                   "debt_ebitda": (f["total_debt"] / f["ebitda"]) if f.get("ebitda") else None,
                                   "roe": (f["net_income"] / (f["book_value_per_share"] * sec.shares_outstanding)) if f.get("book_value_per_share") else None}
        divs = [jsonable(asdict(ca)) for ca in w.corporate_actions.values() if ca.security_id == sec.id]
        out["corporate_actions"] = sorted(divs, key=lambda x: x["ex_date"])
        out["order_book"] = self._synthetic_book(w, sec, bar)
        out["news"] = [jsonable(asdict(nw)) for nw in w.news if sec.id in nw.refs][-20:]
        return jsonable(out)

    def _synthetic_book(self, w: World, sec, bar) -> Dict:
        """Depth ladder implied by ADV and spread: illustrative of resting liquidity at each tick."""
        tick = D("0.01") if not sec.is_bond else D("0.0039")
        depth = w.market.regime().depth_mult
        levels = []
        base = int(sec.adv * 0.004 * depth)
        for i in range(5):
            size = int(base * (1 + 0.6 * i))
            levels.append({"bid": bar.bid - tick * i, "bid_size": size, "ask": bar.ask + tick * i, "ask_size": int(size * 1.05)})
        return {"levels": levels, "note": "synthetic depth implied by ADV, spread and regime liquidity"}

    def yield_curve(self, world_id: str) -> Dict:
        w = self.world(world_id)
        c = w.market.curve()
        hist = w.market.curves
        prev = hist[-2] if len(hist) > 1 else c
        return jsonable({"date": c.date, "tenors": c.tenors, "rates": c.rates, "prev_rates": prev.rates, "ig_spread_bps": c.ig_spread_bps,
                         "hy_spread_bps": c.hy_spread_bps, "policy_rate": c.policy_rate,
                         "history": [{"date": x.date, "2y": x.rates[3], "10y": x.rates[7], "30y": x.rates[9], "ig": x.ig_spread_bps, "hy": x.hy_spread_bps} for x in hist[-260:]],
                         "regime_history": w.market.regime_history})

    def news(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        return jsonable([asdict(n) for n in reversed(w.news[-100:])])

    def corporate_actions(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        return jsonable(sorted([asdict(c) for c in w.corporate_actions.values()], key=lambda x: x["ex_date"]))

    # ------------------------------------------------------------------ portfolio
    def dashboard(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        s = w.pnl.compute_summary(pf)
        last = pf.nav_history[-1] if pf.nav_history else None
        day_pnl_live = s["nav"] - (last.nav if last else pf.contributed_capital) - pf.day_capital_flows
        positions = self._positions(w, pf)
        largest = max(positions, key=lambda p: abs(p["market_value"]), default=None)
        upcoming_settle = [jsonable(asdict(si)) for si in pf.settlements.values() if si.status in ("PENDING", "MATCHED", "FAILED")]
        upcoming_settle.sort(key=lambda x: x["settlement_date"])
        cashflows = self._upcoming_cash_flows(w, pf)
        ytd = mtd = ZERO
        if pf.nav_history:
            y = w.current_date.year
            m = w.current_date.month
            ytd = sum((x.day_pnl for x in pf.nav_history if x.date >= f"{y}-01-01"), ZERO) + day_pnl_live
            mtd = sum((x.day_pnl for x in pf.nav_history if x.date >= f"{y}-{m:02d}-01"), ZERO) + day_pnl_live
        led = w.ledgers[pf.id]
        # benchmark
        bench = None
        if pf.benchmark and pf.benchmark in w.market.history and pf.nav_history:
            h = w.market.history[pf.benchmark]
            start_px = next((b.close for b in h if b.date >= pf.created), h[0].close)
            bench = {"id": pf.benchmark, "return": float(h[-1].close / start_px - 1)}
        pf_return = float(s["nav"] / pf.contributed_capital - 1) if pf.contributed_capital else 0.0
        return jsonable({
            "portfolio": {"id": pf.id, "name": pf.name, "type": pf.portfolio_type, "base_currency": pf.base_currency, "realism": pf.realism, "mode": pf.mode,
                          "custody_account": pf.custody_account, "benchmark": pf.benchmark, "created": pf.created},
            "date": w.current_date, "nav": s["nav"], "ledger_nav": s["ledger_nav"], "day_pnl": day_pnl_live, "mtd_pnl": mtd, "ytd_pnl": ytd,
            "since_inception_pnl": s["nav"] - pf.contributed_capital, "return_since_inception": pf_return, "benchmark": bench,
            "cash": s["cash"], "projected_cash": {c: w.trading.projected_cash(pf, c) for c in pf.cash},
            "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"],
            "unrealized": s["unrealized"], "realized": s["realized"], "income": {
                "dividends": led.balance("4200"), "interest": led.balance("4300"), "commissions": led.balance("5000"), "interest_expense": led.balance("5100")},
            "gross_exposure": s["gross_exposure"], "net_exposure": s["net_exposure"], "long_exposure": s["long_exposure"], "short_exposure": s["short_exposure"],
            "leverage": s["leverage"], "margin_used": ZERO, "available_liquidity": w.trading.projected_cash(pf, pf.base_currency),
            "collateral_posted": ZERO, "collateral_received": ZERO, "margin_calls": [],
            "largest_risk": largest, "upcoming_settlements": upcoming_settle[:10], "upcoming_cash_flows": cashflows[:10],
            "positions": positions, "working_orders": [jsonable(asdict(o)) for o in pf.orders.values() if o.status in ("WORKING", "PARTIALLY_FILLED")],
            "exposure_by_asset_class": self._group(positions, "asset_class"), "exposure_by_sector": self._group(positions, "sector"),
            "exposure_by_currency": self._group(positions, "currency"), "exposure_by_country": self._group(positions, "country"),
            "nav_history": [{"date": x.date, "nav": x.nav, "day_pnl": x.day_pnl} for x in pf.nav_history],
            "contributed_capital": pf.contributed_capital,
        })

    def _group(self, positions, key):
        g: Dict[str, float] = {}
        for p in positions:
            g[p[key]] = g.get(p[key], 0.0) + float(p["market_value"])
        return g

    def _upcoming_cash_flows(self, w: World, pf) -> List[Dict]:
        out = []
        for si in pf.settlements.values():
            if si.status in ("PENDING", "MATCHED", "FAILED"):
                out.append({"date": si.settlement_date, "kind": "SETTLEMENT", "amount": -si.cash_amount if si.instruction_type == "RVP" else si.cash_amount,
                            "currency": si.currency, "ref": f"{si.id} {si.security_id}"})
        for ca in w.corporate_actions.values():
            ent = ca.entitlements.get(pf.id)
            if ent and not ent.get("paid"):
                out.append({"date": ca.pay_date, "kind": "DIVIDEND", "amount": ent["amount"], "currency": ca.currency, "ref": ca.id})
            elif ca.status == "DECLARED" and ca.security_id in pf.positions and pf.positions[ca.security_id].quantity > 0:
                out.append({"date": ca.pay_date, "kind": "DIVIDEND (projected)", "amount": money(pf.positions[ca.security_id].quantity * ca.amount_per_unit),
                            "currency": ca.currency, "ref": ca.id})
        for pos in pf.positions.values():
            sec = w.securities[pos.security_id]
            if sec.is_bond and pos.quantity > 0:
                for d, cf in BondPricer.cash_flows(sec, w.current_date)[:4]:
                    out.append({"date": d.isoformat(), "kind": "COUPON" if cf < 100 else "COUPON+PRINCIPAL", "amount": money(pos.quantity * D(cf) / 100),
                                "currency": sec.currency, "ref": sec.id})
        out.sort(key=lambda x: x["date"])
        return jsonable(out)

    def _positions(self, w: World, pf) -> List[Dict]:
        rows = []
        for pos in pf.positions.values():
            if pos.quantity == 0 and pos.settled_quantity == 0 and pos.pending_deliver == 0 and pos.pending_receive == 0 and not pos.trade_ids:
                continue
            sec = w.securities[pos.security_id]
            inst = Instrument(sec, pos.mark, w.current_date, w.market.curve())
            rm = inst.risk_metrics(pos.quantity) if pos.quantity else {}
            avg_cost = pos.average_cost * (100 if sec.is_bond else 1)   # bonds: price per 100 face
            rows.append({"security_id": sec.id, "name": sec.name, "asset_class": sec.asset_class, "sector": sec.sector, "currency": sec.currency,
                         "country": sec.country, "quantity": pos.quantity, "settled_quantity": pos.settled_quantity, "pending_receive": pos.pending_receive,
                         "pending_deliver": pos.pending_deliver, "average_cost": avg_cost, "cost_basis": pos.cost_basis, "mark": pos.mark,
                         "market_value": pos.market_value, "unrealized_pnl": pos.unrealized_pnl, "realized_pnl": pos.realized_pnl,
                         "dividend_income": pos.dividend_income, "interest_income": pos.interest_income, "commissions": pos.commissions,
                         "accrued_interest": pos.accrued_interest, "weight": 0.0, "beta": sec.beta, "risk": rm, "lots": len(pos.lots),
                         "borrow_status": "n/a (long)", "collateral_status": "unencumbered", "financing": "none"})
        nav = w.pnl.compute_summary(pf)["nav"]
        for r in rows:
            r["weight"] = float(r["market_value"] / nav) if nav else 0.0
        rows.sort(key=lambda r: -abs(float(r["market_value"])))
        return jsonable(rows)

    def position(self, world_id: str, portfolio_id: str, security_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        if security_id not in pf.positions:
            raise NotFound(f"no position in {security_id}")
        pos = pf.positions[security_id]
        sec = w.securities[security_id]
        led = w.ledgers[pf.id]
        row = [r for r in self._positions(w, pf) if r["security_id"] == security_id][0]
        trades = [jsonable(asdict(pf.trades[t])) for t in pos.trade_ids]
        sis = [jsonable(asdict(si)) for si in pf.settlements.values() if si.security_id == security_id]
        entries = [self._entry(e) for e in led.entries if any(l.security_id == security_id for l in e.lines)]
        divs = [{"ca_id": ca.id, "ex_date": ca.ex_date, "pay_date": ca.pay_date, "amount_per_unit": ca.amount_per_unit, **ca.entitlements[pf.id]}
                for ca in w.corporate_actions.values() if pf.id in ca.entitlements and ca.security_id == security_id]
        custody = [jsonable(asdict(c)) for c in pf.custody_movements if c.security_id == security_id]
        pnl_days = [{"date": s.date, **{k: v for k, v in s.by_position.get(security_id, {}).items() if not k.startswith("_")}}
                    for s in pf.nav_history if security_id in s.by_position]
        relationships = [{"kind": "MARKET_EXPOSURE", "description": f"Long {pos.quantity:,} {sec.id}: beta-adjusted exposure {float(pos.market_value) * sec.beta:,.0f}"}]
        if sec.is_bond:
            relationships.append({"kind": "INTEREST_RATE_RISK", "description": f"DV01 {row['risk'].get('position_dv01', 0):,.2f} per bp (unhedged)"})
            if sec.spread_bps_credit:
                relationships.append({"kind": "CREDIT_RISK", "description": f"Credit spread {sec.spread_bps_credit:.0f}bp, rating {sec.rating}, no CDS hedge"})
        if pos.pending_receive or pos.pending_deliver:
            relationships.append({"kind": "SETTLEMENT", "description": f"Pending receive {pos.pending_receive:,}, pending deliver {pos.pending_deliver:,}"})
        return jsonable({"position": row, "lots": [asdict(l) for l in pos.lots], "trades": trades, "settlements": sis, "ledger_entries": entries,
                         "dividends": divs, "custody_movements": custody, "daily_pnl": pnl_days, "relationships": relationships,
                         "ledger_balances": {a: led.security_balance(security_id, a) for a in sorted({l.account for e in led.entries for l in e.lines if l.security_id == security_id})}})

    # ------------------------------------------------------------------ trading & ops
    def orders(self, world_id: str, portfolio_id: str) -> List[Dict]:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        return jsonable([asdict(o) for o in reversed(list(pf.orders.values()))])

    def order(self, world_id: str, portfolio_id: str, order_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        if order_id not in pf.orders:
            raise NotFound(f"order {order_id} not found")
        o = pf.orders[order_id]
        return jsonable({"order": asdict(o), "trades": [asdict(pf.trades[t]) for t in o.trade_ids]})

    def trades(self, world_id: str, portfolio_id: str) -> List[Dict]:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        return jsonable([asdict(t) for t in reversed(list(pf.trades.values()))])

    def trade(self, world_id: str, portfolio_id: str, trade_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        if trade_id not in pf.trades:
            raise NotFound(f"trade {trade_id} not found")
        t = pf.trades[trade_id]
        led = w.ledgers[pf.id]
        si = pf.settlements.get(t.settlement_instruction_id)
        entries = [self._entry(e) for e in led.entries if e.reference.get("trade_id") == trade_id or (si and e.reference.get("si_id") == si.id)]
        exec_ev = next((e for e in w.events if e.type == E.TRADE_EXECUTED and e.payload.get("trade_id") == trade_id), None)
        related = [jsonable(e.to_dict()) for e in w.events if e.payload.get("trade_id") == trade_id or (si and e.payload.get("si_id") == si.id)]
        return jsonable({"trade": asdict(t), "order": asdict(pf.orders[t.order_id]), "settlement": asdict(si) if si else None, "ledger_entries": entries,
                         "events": related, "execution_event_id": exec_ev.id if exec_ev else None})

    def settlements(self, world_id: str, portfolio_id: str) -> List[Dict]:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        rows = sorted(pf.settlements.values(), key=lambda s: (s.settlement_date, s.id), reverse=True)
        return jsonable([asdict(s) for s in rows])

    def custody(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        holdings = [{"security_id": p.security_id, "name": w.securities[p.security_id].name, "isin": w.securities[p.security_id].isin,
                     "settled_quantity": p.settled_quantity, "trade_date_quantity": p.quantity, "pending_receive": p.pending_receive,
                     "pending_deliver": p.pending_deliver} for p in pf.positions.values() if p.settled_quantity or p.pending_receive or p.pending_deliver or p.quantity]
        return jsonable({"custodian": "Meridian Custody Services", "account": pf.custody_account, "holdings": holdings,
                         "movements": [asdict(c) for c in reversed(pf.custody_movements)]})

    def cash(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        policy = w.market.curve().policy_rate
        accounts = [{"currency": c.currency, "settled_balance": c.balance, "accrued_interest": c.accrued_interest, "projected": w.trading.projected_cash(pf, c.currency),
                     "deposit_rate": policy - 0.0025, "overdraft_rate": policy + 0.015} for c in pf.cash.values()]
        proj = self._liquidity_projection(w, pf)
        return jsonable({"accounts": accounts, "movements": [asdict(m) for m in reversed(pf.cash_movements)], "upcoming": self._upcoming_cash_flows(w, pf),
                         "projection": proj, "policy_rate": policy})

    def _liquidity_projection(self, w: World, pf) -> List[Dict]:
        ccy = pf.base_currency
        bal = pf.cash_account(ccy).balance
        flows = [f for f in self._upcoming_cash_flows(w, pf) if f["currency"] == ccy]
        out = [{"date": w.current_date.isoformat(), "balance": bal, "flow": ZERO, "ref": "settled cash today"}]
        d = w.current_date
        for _ in range(15):
            d = w.calendar.next_business_day(d)
            day_flows = [f for f in flows if f["date"] == d.isoformat()]
            amt = sum((D(str(f["amount"])) for f in day_flows), ZERO)
            bal += amt
            out.append({"date": d.isoformat(), "balance": bal, "flow": amt, "ref": ", ".join(f["ref"] for f in day_flows)})
        return jsonable(out)

    # ------------------------------------------------------------------ accounting
    def _entry(self, e) -> Dict:
        return jsonable({"id": e.id, "date": e.date, "memo": e.memo, "event_id": e.event_id, "cause_id": e.cause_id, "reference": e.reference,
                         "lines": [{"account": l.account, "name": account_name(l.account), "debit": l.debit, "credit": l.credit, "security_id": l.security_id, "memo": l.memo} for l in e.lines]})

    def ledger(self, world_id: str, portfolio_id: str, limit: int = 200, account: Optional[str] = None, security_id: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        led = w.ledgers[pf.id]
        entries = led.entries
        if account:
            entries = [e for e in entries if any(l.account == account or l.account.startswith(account + ":") for l in e.lines)]
        if security_id:
            entries = [e for e in entries if any(l.security_id == security_id for l in e.lines)]
        entries = list(reversed(entries))[:limit]
        return jsonable({"entries": [self._entry(e) for e in entries], "trial_balance": led.trial_balance(), "nav": led.nav(),
                         "net_income": led.net_income(), "income_statement": [{"account": a, "name": account_name(a), "type": account_type(a), "balance": b}
                                                                                for a, b in sorted(led.income_statement().items())],
                         "chart": [{"code": c, "name": n, "type": t} for c, (n, t) in CHART.items()], "count": len(led.entries)})

    def balance_sheet(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        led = w.ledgers[pf.id]
        rows = {"ASSET": [], "LIABILITY": [], "EQUITY": [], "INCOME": [], "EXPENSE": []}
        for a in sorted(led.balances):
            rows[account_type(a)].append({"account": a, "name": account_name(a), "balance": led.balance(a)})
        tot = {k: sum((r["balance"] for r in v), ZERO) for k, v in rows.items()}
        return jsonable({"sections": rows, "totals": tot, "nav": led.nav(), "net_income": led.net_income(),
                         "check": tot["ASSET"] - tot["LIABILITY"] - tot["EQUITY"] - (tot["INCOME"] - tot["EXPENSE"])})

    def pnl_explain(self, world_id: str, portfolio_id: str, day: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        snaps = pf.nav_history
        if not snaps:
            return {"available": False}
        s = snaps[-1] if not day else next((x for x in snaps if x.date == day), None)
        if s is None:
            raise NotFound(f"no snapshot for {day}")
        explain = {k: v for k, v in s.explain.items() if not k.startswith("_")}
        by_pos = {sid: {k: v for k, v in row.items() if not k.startswith("_")} for sid, row in s.by_position.items()}
        return jsonable({"available": True, "date": s.date, "nav": s.nav, "prev_nav": s.nav - s.day_pnl - s.capital_flows, "day_pnl": s.day_pnl,
                         "capital_flows": s.capital_flows, "explain": explain, "by_position": by_pos, "event_id": s.event_id,
                         "reconciles": sum(explain.values(), ZERO) == s.day_pnl,
                         "history": [{"date": x.date, "nav": x.nav, "day_pnl": x.day_pnl, **{k: v for k, v in x.explain.items() if not k.startswith("_")}} for x in snaps],
                         "dates": [x.date for x in snaps]})

    def nav_explain(self, world_id: str, portfolio_id: str) -> Dict:
        """Explain live NAV: what changed since the last snapshot (the number on the dashboard)."""
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        led = w.ledgers[pf.id]
        last = pf.nav_history[-1] if pf.nav_history else None
        prev_bal = last.explain.get("_balances", {}) if last else {}
        deltas = {a: led.balance(a) - D(prev_bal.get(a, 0)) for a in ["4000", "4100", "4200", "4300", "5000", "5100"]}
        rows = [{"account": a, "name": account_name(a), "change": (v if account_type(a) == "INCOME" else -v)} for a, v in deltas.items()]
        entries_since = [self._entry(e) for e in led.entries if (last is None or int(e.event_id[3:]) > int(last.event_id[3:]))
                         and any(l.account in ("4000", "4100", "4200", "4300", "5000", "5100") for l in e.lines)]
        s = w.pnl.compute_summary(pf)
        return jsonable({"nav": s["nav"], "since": last.date if last else None, "prev_nav": last.nav if last else pf.contributed_capital,
                         "capital_flows": pf.day_capital_flows, "components": rows, "entries": entries_since[-100:],
                         "composition": {"cash": s["cash"], "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"]}})

    # ------------------------------------------------------------------ audit
    def events(self, world_id: str, limit: int = 200, offset: int = 0, etype: Optional[str] = None, portfolio_id: Optional[str] = None, q: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        evs = w.events
        if etype:
            evs = [e for e in evs if e.type == etype]
        if portfolio_id:
            evs = [e for e in evs if e.portfolio_id == portfolio_id or e.portfolio_id is None]
        if q:
            ql = q.lower()
            evs = [e for e in evs if ql in e.to_json().lower()]
        total = len(evs)
        page = list(reversed(evs))[offset:offset + limit]
        return {"total": total, "types": sorted({e.type for e in w.events}),
                "events": [self._event_summary(w, e) for e in page]}

    def _event_summary(self, w: World, e) -> Dict:
        p = e.payload
        d = jsonable(e.to_dict())
        if e.type == E.MARKET_CLOSE:
            d["payload"] = {"date": p["date"], "securities": len(p["bars"]), "regime": p["state"]["regime"], "policy_rate": p["curve"]["policy"], "regime_change": p.get("regime_change")}
        d["children"] = len(w.children.get(e.id, []))
        return d

    def event(self, world_id: str, event_id: str) -> Dict:
        w = self.world(world_id)
        if event_id not in w.events_by_id:
            raise NotFound(f"event {event_id} not found")
        chain = w.audit_chain(event_id)
        def conv(node):
            return {"event": jsonable(node["event"].to_dict()), "children": [conv(c) for c in node["children"]]}
        ev = w.events_by_id[event_id]
        out = {"event": jsonable(ev.to_dict()), "ancestors": [jsonable(a.to_dict()) for a in chain["ancestors"]], "tree": conv(chain["tree"])}
        if ev.type == E.MARKET_CLOSE:
            out["event"]["payload"]["bars"] = {k: v for k, v in list(out["event"]["payload"]["bars"].items())}
        return out

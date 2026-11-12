"""Client flow for the bank-trader jobs: institutional clients ask the desk for
prices. Each request names a product, size and the client's side; the player
answers with a level (or passes) before the next update. At the update the
client deals if the level is competitive against the street (the dealer mid
and widths from the OTC engine): a tight quote wins the trade and earns the
spread, a wide one loses it. Won trades are booked against the client under
the client's ISDA (OTC) or as a contractual block trade (equities, bonds).

Clients are seeded, with sector preferences and sensitivity to price; flow
rises in stress (hedging demand) and after the desk has shown consistent
prices.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import Event
from ..engines.counterparties import HALF_WIDTH, quote_half_width
from ..money import D, money, qty as qqty, ZERO

CLIENTS = [
    ("PENSION_A", "Ontario Teachers' Pension Plan", ("IRS", "SWAPTION", "UST"), 0.6),
    ("INSURER_B", "MetLife", ("IRS", "CDS", "CORP"), 0.5),
    ("HF_C", "Citadel", ("IRS", "XCCY", "TRS", "EQUITY"), 0.9),
    ("CORP_D", "Caterpillar Treasury", ("IRS", "XCCY", "FRA"), 0.4),
    ("AM_E", "PIMCO", ("EQUITY", "TRS", "CORP", "UST"), 0.7),
    ("SOV_F", "GIC (Singapore)", ("UST", "IRS", "XCCY"), 0.3),
]
CLIENT_JOBS = ("BANK_TRADER", "DERIVATIVES_TRADER")


class CE:
    CLIENT_RFQ_RECEIVED = "CLIENT_RFQ_RECEIVED"
    CLIENT_RFQ_QUOTED = "CLIENT_RFQ_QUOTED"
    CLIENT_RFQ_RESOLVED = "CLIENT_RFQ_RESOLVED"


class ClientEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(CE.CLIENT_RFQ_RECEIVED, lambda world, ev: self._h_received(ev))
        w.on(CE.CLIENT_RFQ_QUOTED, lambda world, ev: self._h_quoted(ev))
        w.on(CE.CLIENT_RFQ_RESOLVED, lambda world, ev: self._h_resolved(ev))

    def applies(self, pf) -> bool:
        return pf.job in CLIENT_JOBS

    # ------------------------------------------------------------------ daily: new requests arrive, yesterday's quotes resolve
    def process_day(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            if not self.applies(pf):
                continue
            # resolve open requests from earlier sessions
            for r in list(pf.client_rfqs.values()):
                if r["status"] in ("OPEN", "QUOTED") and r["date"] < today:
                    self._resolve(pf, r, cause)
            # new requests
            rng = random.Random(f"{w.seed}|clients|{pf.id}|{today}")
            regime = w.market.state.regime
            n = 1 + (1 if rng.random() < 0.6 else 0) + (1 if regime in ("RECESSION", "LIQUIDITY_STRESS") and rng.random() < 0.5 else 0)
            if pf.job == "DERIVATIVES_TRADER":
                n = max(1, n - 1)
            for i in range(n):
                self._new_request(pf, rng, cause)

    def _new_request(self, pf, rng: random.Random, cause: Event) -> None:
        w = self.w
        key, name, prefs, price_sens = rng.choice(CLIENTS)
        product = rng.choice(prefs)
        if pf.job == "DERIVATIVES_TRADER" and product not in ("IRS", "SWAPTION", "CDS", "TRS", "XCCY", "FRA"):
            product = "IRS"
        side = rng.choice(["BUY", "SELL"])          # the client's side
        req: Dict = {"client": key, "client_name": name, "product": product, "client_side": side, "price_sensitivity": price_sens}
        if product == "UST":
            sid = rng.choice(["UST-2Y", "UST-5Y", "UST-10Y", "UST-30Y"])
            req.update({"security_id": sid, "quantity": rng.choice([5, 10, 25, 50]) * 1_000_000, "unit": "price per 100", "mid": float(w.market.last_bar(sid).close)})
        elif product == "CORP":
            sid = rng.choice(["JPM-29", "F-32", "AAL-28"])
            req.update({"security_id": sid, "quantity": rng.choice([2, 5, 10]) * 1_000_000, "unit": "price per 100", "mid": float(w.market.last_bar(sid).close)})
        elif product == "EQUITY":
            sid = rng.choice([s.id for s in w.securities.values() if s.asset_class == "EQUITY" and s.liquidity_tier in ("LARGE", "MID") and not s.delisted])
            bar = w.market.last_bar(sid)
            req.update({"security_id": sid, "quantity": int(w.securities[sid].adv * rng.choice([0.1, 0.25, 0.5])), "unit": "price per share", "mid": float(bar.close)})
        else:
            params = {"IRS": {"notional": rng.choice([10, 25, 50, 100]) * 1_000_000, "tenor_years": rng.choice([2, 5, 10]), "pay_fixed": side == "BUY"},
                      "FRA": {"notional": rng.choice([25, 50]) * 1_000_000, "start_months": rng.choice([1, 3, 6]), "length_months": 3, "pay_fixed": side == "BUY"},
                      "SWAPTION": {"notional": rng.choice([25, 50]) * 1_000_000, "expiry_months": rng.choice([3, 6, 12]), "swap_years": rng.choice([2, 5]), "payer": side == "BUY", "buyer": True},
                      "CDS": {"reference": rng.choice(["JPM-29", "F-32", "AAL-28"]), "notional": rng.choice([5, 10, 25]) * 1_000_000, "tenor_years": 5, "buyer": side == "BUY"},
                      "TRS": {"security_id": rng.choice(["NVDA", "JPM", "SPY", "F"]), "units": rng.choice([10_000, 25_000, 50_000]), "tenor_years": 1, "receiver": side == "BUY"},
                      "XCCY": {"usd_notional": rng.choice([10, 25, 50]) * 1_000_000, "ccy": rng.choice(["EUR", "GBP", "JPY"]), "tenor_years": rng.choice([1, 2, 5]), "direction": "BORROW_FOREIGN" if side == "BUY" else "LEND_FOREIGN"}}[product]
            fair = w.otc.fair(product, w.otc._norm_params(product, params))
            req.update({"params": params, "unit": fair["unit"], "mid": fair["mid"], "fair": {k: v for k, v in fair.items() if k != "strip"}})
        req["street_half_width"] = self._street_width(product, req)
        rid = w.new_id("CRQ")
        w.emit(CE.CLIENT_RFQ_RECEIVED, {"portfolio_id": pf.id, "rfq_id": rid, **req, "expires": w.calendar.next_business_day(w.current_date).isoformat()},
               cause_id=cause.id, portfolio_id=pf.id)

    def _street_width(self, product: str, req: Dict) -> float:
        w = self.w
        regime = w.market.state.regime
        if product in ("UST", "CORP", "EQUITY"):
            sec = w.securities[req["security_id"]]
            return float(req["mid"]) * (sec.spread_bps * w.market.regime().spread_mult / 2 / 1e4) * (1.5 if product == "CORP" else 1.0)
        return quote_half_width(product, "GOLDMAN", regime, 0.0) * (float(req["mid"]) if product in ("CAP", "FLOOR", "SWAPTION") else 1.0)

    # ------------------------------------------------------------------ player command
    def quote(self, pf, rfq_id: str, level: Optional[float], pass_: bool = False) -> Dict:
        from ..world import CommandError
        w = self.w
        r = pf.client_rfqs.get(rfq_id)
        if r is None:
            raise CommandError(f"unknown client request {rfq_id}")
        if r["status"] not in ("OPEN", "QUOTED"):
            raise CommandError(f"request {rfq_id} is {r['status']}")
        if not pass_ and level is None:
            raise CommandError("a level is required unless passing")
        w.emit(CE.CLIENT_RFQ_QUOTED, {"portfolio_id": pf.id, "rfq_id": rfq_id, "level": None if pass_ else float(level), "passed": bool(pass_)}, portfolio_id=pf.id)
        return pf.client_rfqs[rfq_id]

    # ------------------------------------------------------------------ resolution
    def _resolve(self, pf, r: Dict, cause: Event) -> None:
        w = self.w
        rng = random.Random(f"{w.seed}|cresolve|{r['id']}")
        if r["status"] != "QUOTED" or r.get("passed"):
            w.emit(CE.CLIENT_RFQ_RESOLVED, {"portfolio_id": pf.id, "rfq_id": r["id"], "outcome": "PASSED" if r.get("passed") else "EXPIRED", "note": "no price shown; the client dealt elsewhere"},
                   cause_id=cause.id, portfolio_id=pf.id)
            return
        mid, hw, level = float(r["mid"]), float(r["street_half_width"]), float(r["level"])
        # the client buys at the desk's offer: an offer above mid + hw loses to the street; below mid gives the client a gift
        premium_products = ("SWAPTION",)
        if r["product"] in premium_products or r["product"] in ("UST", "CORP", "EQUITY"):
            edge = (level - mid) if r["client_side"] == "BUY" else (mid - level)       # desk's spread earned per unit
        else:
            edge = (level - mid) if r["client_side"] == "BUY" else (mid - level)
        competitiveness = edge / hw if hw > 0 else 0.0                                # 1.0 = street offer; 0 = mid; <0 = giving it away
        p_win = 0.95 if competitiveness <= 0.2 else 0.8 if competitiveness <= 0.6 else 0.5 if competitiveness <= 1.0 else 0.2 if competitiveness <= 1.5 else 0.03
        p_win *= (1.0 - 0.3 * float(r["price_sensitivity"])) if competitiveness > 0.6 else 1.0
        won = rng.random() < p_win
        if not won:
            w.emit(CE.CLIENT_RFQ_RESOLVED, {"portfolio_id": pf.id, "rfq_id": r["id"], "outcome": "LOST", "competitiveness": competitiveness,
                                            "note": f"client dealt elsewhere: your level was {competitiveness:.2f}x the street half-width from mid"}, cause_id=cause.id, portfolio_id=pf.id)
            return
        ev = w.emit(CE.CLIENT_RFQ_RESOLVED, {"portfolio_id": pf.id, "rfq_id": r["id"], "outcome": "WON", "competitiveness": competitiveness, "edge": edge,
                                             "note": f"client dealt at {level}; spread earned {competitiveness:.2f}x the street half-width"}, cause_id=cause.id, portfolio_id=pf.id)
        self._book(pf, r, level, ev)

    def _book(self, pf, r: Dict, level: float, ev: Event) -> None:
        w = self.w
        desk_side = "SELL" if r["client_side"] == "BUY" else "BUY"
        if r["product"] in ("UST", "CORP", "EQUITY"):
            sec = w.securities[r["security_id"]]
            w.trading.system_order(pf, sec, desk_side, qqty(r["quantity"]), ev, f"client block with {r['client_name']} at {level}", forced=False,
                                   fixed_price=D(repr(level)), allow_short=True, commission_free=True)
            return
        # OTC: the desk takes the opposite of the client's request at the desk's level
        params = dict(r["params"])
        if r["product"] == "IRS":
            params["pay_fixed"] = not params["pay_fixed"]
        elif r["product"] == "FRA":
            params["pay_fixed"] = not params["pay_fixed"]
        elif r["product"] == "SWAPTION":
            params["buyer"] = False
        elif r["product"] == "CDS":
            params["buyer"] = not params["buyer"]
        elif r["product"] == "TRS":
            params["receiver"] = not params["receiver"]
        elif r["product"] == "XCCY":
            params["direction"] = "LEND_FOREIGN" if params["direction"] == "BORROW_FOREIGN" else "BORROW_FOREIGN"
        w.otc.open_client_trade(pf, r["product"], params, level, f"CLIENT:{r['client']}", r["client_name"], ev)

    # ------------------------------------------------------------------ handlers
    def _h_received(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.client_rfqs[p["rfq_id"]] = {"id": p["rfq_id"], "date": ev.sim_date, "status": "OPEN", **{k: v for k, v in p.items() if k not in ("portfolio_id", "rfq_id")},
                                       "level": None, "passed": False, "outcome": None}

    def _h_quoted(self, ev: Event) -> None:
        p = ev.payload
        r = self.w.portfolios[p["portfolio_id"]].client_rfqs[p["rfq_id"]]
        r["status"] = "QUOTED"
        r["level"] = p["level"]
        r["passed"] = p["passed"]

    def _h_resolved(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        r = pf.client_rfqs[p["rfq_id"]]
        r["status"] = p["outcome"]
        r["outcome"] = p["outcome"]
        r["note"] = p.get("note", "")
        r["resolved"] = ev.sim_date
        stats = pf.client_stats
        stats["requests"] = stats.get("requests", 0) + 1
        if p["outcome"] == "WON":
            stats["won"] = stats.get("won", 0) + 1
        elif p["outcome"] == "LOST":
            stats["lost"] = stats.get("lost", 0) + 1
        elif p["outcome"] == "PASSED":
            stats["passed"] = stats.get("passed", 0) + 1
        else:
            stats["expired"] = stats.get("expired", 0) + 1

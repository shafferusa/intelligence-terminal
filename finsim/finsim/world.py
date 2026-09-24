"""The World: an event-sourced financial system.

Commands (place_order, advance, ...) validate against current state and *emit*
events. Applying an event mutates state through a registered handler. Handlers
may *derive* further events (a trade derives ledger postings, a settlement
instruction, a valuation mark). During replay, derivation is suppressed because
the derived events are already in the log — state is rebuilt purely by applying
the stored sequence, which is the auditability guarantee: every number on screen
is a projection of the log.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from .calendar import BusinessCalendar, SettlementConfig
from .clock import ClockConfig, next_update, target_sim_date, trading_window, session_closed, instruction_session, next_quote_tick, latest_quote_tick
from .domain.events import E, Event
from .version import SAVE_VERSION, ENGINE_VERSION
from .log import get_logger
from .domain.models import Bar, NewsItem, Portfolio, Security, YieldCurve
from .engines.ledger import Ledger
from .engines.market import MarketEngine, REGIMES, build_universe
from .money import D, money, ZERO


class ReplayError(Exception):
    """A stored event could not be applied on load (strict replay)."""
    def __init__(self, seq: int, etype: str, error: Exception):
        super().__init__(f"replay failed at event {seq} ({etype}): {error!r}")
        self.seq, self.etype, self.error = seq, etype, error


class CommandError(Exception):
    """Raised when a command is invalid. Nothing is emitted."""


class World:
    def __init__(self, world_id: str, store=None):
        self.id = world_id
        self.store = store
        self.events: List[Event] = []
        self.events_by_id: Dict[str, Event] = {}
        self.children: Dict[str, List[str]] = {}
        self.replaying = False
        self.handlers: Dict[str, List[Callable[["World", Event], None]]] = {}
        # config (set by WORLD_CREATED)
        self.name = ""
        self.seed = 0
        self.start_date: Optional[date] = None
        self.current_date: Optional[date] = None
        self.base_currency = "USD"
        self.settlement_config = SettlementConfig()
        self.calendar = BusinessCalendar()
        self.securities: Dict[str, Security] = {}
        self.market: Optional[MarketEngine] = None
        self.portfolios: Dict[str, Portfolio] = {}
        self.ledgers: Dict[str, Ledger] = {}
        self.corporate_actions: Dict[str, Any] = {}
        self.news: List[NewsItem] = []
        self.day_count = 0
        self._pending: List[Event] = []
        self.clock = ClockConfig()
        self.real_news = None                    # a career world: the headline fetcher (stubbed in tests)
        self.treasury_cash: Dict[str, Decimal] = {}   # the save's cash pool by currency: books draw from it and return to it
        self.treasury_log: List[Dict] = []
        self.last_processed_utc: Optional[str] = None
        # engines
        from .engines import trading, settlement, corporate_actions, accruals, pnl, simulation, futures, briefing
        from .engines import collateral, seclending, repo, prime, fx, options, otc, risk, corporate_events
        from .engines import investors, clients, treasury, institutions, commodity_desk, private_credit, private_equity, investment_banking, live, mbs, ccy_funding
        from . import careers
        self.trading = trading.TradingEngine(self)
        self.live = live.LiveDesk(self)
        self.settlement = settlement.SettlementEngine(self)
        self.corporate = corporate_actions.CorporateActionEngine(self)
        self.accruals = accruals.AccrualEngine(self)
        self.pnl = pnl.PnLEngine(self)
        self.futures = futures.FuturesEngine(self)
        self.collateral = collateral.CollateralEngine(self)
        self.seclending = seclending.SecLendingEngine(self)
        self.repo = repo.RepoEngine(self)
        self.prime = prime.PrimeEngine(self)
        self.fx = fx.FXEngine(self)
        self.options = options.OptionsEngine(self)
        self.otc = otc.OTCEngine(self)
        self.risk = risk.RiskEngine(self)
        self.cevents = corporate_events.CorporateEventsEngine(self)
        self.investors = investors.InvestorEngine(self)
        self.clients = clients.ClientEngine(self)
        self.treasury = treasury.TreasuryEngine(self)
        self.institutions = institutions.InstitutionEngine(self)
        self.lenddesk = seclending.LendDeskEngine(self)
        self.cdesk = commodity_desk.CommodityDeskEngine(self)
        self.pcredit = private_credit.PrivateCreditEngine(self)
        self.pequity = private_equity.PrivateEquityEngine(self)
        self.ibank = investment_banking.InvestmentBankingEngine(self)
        self.mbsdesk = mbs.MBSDesk(self)
        self.funding = ccy_funding.CcyFundingEngine(self)
        from .engines import playbook
        self.playbook = playbook.Playbook(self)
        self.scenario = "NONE"
        self.scenario_log: List[Dict] = []
        self.save_version = SAVE_VERSION
        self.migration: Dict = {"from": SAVE_VERSION, "to": SAVE_VERSION, "notes": [], "migrated": False}
        self.replay_errors: List[Dict] = []
        self.integrity: Dict = {}
        self.run_log: List[Dict] = []        # {date, events, ms} per processed day in this process (not part of the save)
        self.careers = careers.CareerEngine(self)
        self.briefing = briefing.BriefingEngine(self)
        self.simulation = simulation.SimulationEngine(self)
        self._register_core()

    # ------------------------------------------------------------------ events
    def on(self, etype: str, fn: Callable[["World", Event], None]) -> None:
        self.handlers.setdefault(etype, []).append(fn)

    def next_seq(self) -> int:
        return len(self.events) + 1

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{self.next_seq():06d}"

    def emit(self, etype: str, payload: Dict[str, Any], cause_id: Optional[str] = None, portfolio_id: Optional[str] = None,
             sim_date: Optional[str] = None) -> Event:
        seq = self.next_seq()
        ev = Event(seq=seq, id=f"EV-{seq:06d}", type=etype,
                   sim_date=sim_date or (self.current_date.isoformat() if self.current_date else ""),
                   payload=_clean(payload), cause_id=cause_id, portfolio_id=portfolio_id)
        self._append(ev)
        self._apply(ev)
        return ev

    def derive(self, etype: str, payload: Dict[str, Any], cause: Event, portfolio_id: Optional[str] = None) -> Optional[Event]:
        """Emit an event caused by another. No-op during replay (it's already in the log)."""
        if self.replaying:
            return None
        return self.emit(etype, payload, cause_id=cause.id, portfolio_id=portfolio_id or cause.portfolio_id, sim_date=cause.sim_date)

    def _append(self, ev: Event) -> None:
        self.events.append(ev)
        self.events_by_id[ev.id] = ev
        if ev.cause_id:
            self.children.setdefault(ev.cause_id, []).append(ev.id)
        if self.store is not None and not self.replaying:
            self._pending.append(ev)

    def flush(self) -> None:
        if self.store is not None and self._pending:
            self.store.append_events(self.id, self._pending)
            self._pending = []

    def _apply(self, ev: Event) -> None:
        for fn in self.handlers.get(ev.type, []):
            fn(self, ev)

    def replay(self, events: List[Event], strict: bool = True) -> None:
        """Rebuild state from the log. Strict: the first failing event raises ReplayError. Resilient (strict=False):
        failures are recorded in `replay_errors` and replay continues, so a damaged save still opens (read-mostly)
        and the damage is reported instead of hiding behind a crash."""
        log = get_logger("world")
        self.replaying = True
        try:
            for ev in events:
                self._append(ev)
                try:
                    self._apply(ev)
                except Exception as e:      # noqa: BLE001 — the whole point is to isolate a bad event
                    if strict:
                        raise ReplayError(ev.seq, ev.type, e) from e
                    self.replay_errors.append({"seq": ev.seq, "type": ev.type, "sim_date": ev.sim_date, "error": repr(e)})
                    log.error("world %s: replay error at %s %s: %r", self.id, ev.seq, ev.type, e)
        finally:
            self.replaying = False

    def check_integrity(self) -> Dict:
        """Cheap post-load checks: every ledger balances and reconciles to the economic NAV; no negative custody."""
        problems: List[str] = []
        navs: Dict[str, str] = {}
        for pid, led in self.ledgers.items():
            pf = self.portfolios.get(pid)
            if pf is None:
                continue
            tb = led.trial_balance()
            if not tb["balanced"]:
                problems.append(f"{pid}: trial balance does not balance")
            try:
                nav = self.pnl.compute_summary(pf)["nav"]
                if nav != led.nav():
                    problems.append(f"{pid}: ledger NAV {led.nav()} != economic NAV {nav}")
                navs[pid] = str(led.nav())
            except Exception as e:      # noqa: BLE001
                problems.append(f"{pid}: NAV could not be computed: {e!r}")
            for pos in pf.positions.values():
                if pos.settled_quantity < 0 and not pos.is_option and not pos.is_future and pos.borrowed_quantity == 0:
                    problems.append(f"{pid}: negative custody in {pos.security_id}")
        self.integrity = {"ok": not problems and not self.replay_errors, "problems": problems, "replay_errors": len(self.replay_errors),
                          "events": len(self.events), "nav": navs, "engine_version": ENGINE_VERSION, "save_version": self.save_version}
        return self.integrity

    # ------------------------------------------------------------------ core handlers
    def _register_core(self) -> None:
        self.on(E.WORLD_CREATED, World._h_world_created)
        self.on(E.REAL_HISTORY_LOADED, World._h_real_history)
        self.on(E.MARKET_SOURCE_CHANGED, World._h_market_source_changed)
        self.on(E.REAL_MACRO_LOADED, World._h_real_macro)
        self.on(E.CLOCK_CHANGED, World._h_clock_changed)
        self.on(E.PORTFOLIO_CREATED, World._h_portfolio_created)
        self.on(E.PORTFOLIO_DELETED, World._h_portfolio_deleted)
        self.on(E.TREASURY_FUNDED, World._h_treasury_funded)
        self.on(E.TREASURY_ALLOCATED, World._h_treasury_allocated)
        self.on(E.CAPITAL_CONTRIBUTED, World._h_capital)
        self.on(E.MARKET_CLOSE, World._h_market_close)
        self.on(E.LEDGER_POSTED, World._h_ledger_posted)
        self.on(E.NEWS_PUBLISHED, World._h_news)
        self.on(E.DAY_STARTED, World._h_day_started)
        self.on(E.DAY_CLOSED, World._h_day_closed)
        self.trading.register()
        self.settlement.register()
        self.corporate.register()
        self.accruals.register()
        self.pnl.register()
        self.futures.register()
        self.collateral.register()
        self.seclending.register()
        self.repo.register()
        self.prime.register()
        self.fx.register()
        self.options.register()
        self.otc.register()
        self.risk.register()
        self.cevents.register()
        self.investors.register()
        self.clients.register()
        self.treasury.register()
        self.institutions.register()
        self.lenddesk.register()
        self.cdesk.register()
        self.pcredit.register()
        self.pequity.register()
        self.mbsdesk.register()
        self.funding.register()
        self.ibank.register()
        self.on(E.SCENARIO_EVENT, World._h_scenario_event)
        for et in (E.ECONOMIC_RELEASE, E.EARNINGS_REPORTED, E.RATING_CHANGED, E.ISSUER_DEFAULTED):
            self.on(et, lambda world, ev: None)
        self.careers.register()
        self.briefing.register()
        self.on(E.REGIME_FORCED, World._h_regime_forced)
        self.on(E.RATES_SHOCK_FORCED, World._h_rates_forced)

    def _h_world_created(self, ev: Event) -> None:
        p = ev.payload
        self.name = p["name"]
        self.seed = int(p["seed"])
        self.start_date = date.fromisoformat(p["start_date"])
        self.current_date = self.start_date
        self.base_currency = p.get("base_currency", "USD")
        self.clock = ClockConfig(p.get("clock_mode", "SANDBOX"), p.get("timezone", "America/New_York"), p.get("update_time", "09:00"),
                                 bool(p.get("lock_session", False)))
        self.scenario = p.get("scenario", "NONE")
        self.save_version = int(p.get("save_version", 1))
        if p.get("settlement_cycles"):
            self.settlement_config = SettlementConfig(cycles=dict(p["settlement_cycles"]))
        self.securities = build_universe(self.start_date, self.seed)
        self.market = MarketEngine(self.seed, self.start_date, self.calendar, self.securities,
                                   prehistory_days=int(p.get("prehistory_days", 260)),
                                   initial_regime=p.get("initial_regime", "NORMAL_GROWTH"))
        self.market.bar_provider = lambda sid: self.options.option_bar(self.securities[sid])
        self.market.mbs_holder = lambda sid: any(p.positions[sid].quantity != 0 for p in self.portfolios.values() if sid in p.positions)
        self.market_source = p.get("market_source", "SIMULATED")
        self.market.bootstrap()
        if self.market_source == "REAL":
            self._setup_real_feeds()

    def _setup_real_feeds(self) -> None:
        from .engines.realfeed import RealFeed, equity_symbols_for, equity_scales_for
        from .engines.realmacro import RealMacro
        from .engines.realnews import RealNews
        self.market.real_feed = RealFeed(equity_symbols_for(self.securities), scales=equity_scales_for(self.securities))
        self.market.real_macro_feed = RealMacro()
        self.real_news = RealNews()

    def _h_market_source_changed(self, ev: Event) -> None:
        self.market_source = ev.payload.get("market_source", "REAL")
        if self.market_source == "REAL" and getattr(self.market, "real_feed", None) is None:
            self._setup_real_feeds()

    def switch_to_real(self, real_history: Dict, real_macro: Optional[Dict] = None) -> Event:
        """Make a simulated save track the real market from now on: its price history is overlaid with the real closes
        (positions are re-marked at real prices at the next update; past fills keep their prices), real closes drive every
        day from here, and live quotes, live tickets and the 15-minute quote updates come on. Stored as events, so replay
        needs no network."""
        if getattr(self, "market_source", "SIMULATED") == "REAL":
            raise CommandError("this save already tracks the real market")
        spy = (real_history or {}).get("equities", {}).get("SPY") or {}
        if not spy:
            raise CommandError("no real market data could be fetched (is the machine online?)")
        cur = self.current_date.isoformat()
        if cur not in spy:
            raise CommandError(f"the real market has no close for this save's current date {cur}: a save can switch only while its date is a real session (a career save always is)")
        ev = self.emit(E.MARKET_SOURCE_CHANGED, {"market_source": "REAL", "from": "SIMULATED"})
        self.emit(E.REAL_HISTORY_LOADED, {"history": real_history}, cause_id=ev.id)
        if real_macro:
            self.emit(E.REAL_MACRO_LOADED, {"series": real_macro}, cause_id=ev.id)
        self.flush()
        return ev

    def _h_real_history(self, ev: Event) -> None:
        h = self._fix_quote_scale(ev.payload["history"])
        self.market.real_history = h
        self.market.apply_real_history(h)

    def _fix_quote_scale(self, h: Dict) -> Dict:
        """Saves made before 2026-09-24 stored London closes in pence (the feed that built them had no quote scales).
        A series sitting far above the save's own generated prehistory (which is in pounds) is brought to pounds;
        deterministic, so replay gives the same prices."""
        eq = h.get("equities") or {}
        out = None
        for sid, series in eq.items():
            sec = self.securities.get(sid)
            k = float(getattr(sec, "yahoo_scale", 1.0) or 1.0) if sec is not None else 1.0
            bars = self.market.history.get(sid)
            if k == 1.0 or not series or not bars:
                continue
            vals = sorted(series.values())
            mid = vals[len(vals) // 2]
            gen = float(bars[len(bars) // 2].close)
            if gen > 0 and mid > gen / (k * 5):            # pence (100x) rather than pounds: the gap is far beyond any move
                out = out or {**h, "equities": dict(eq)}
                out["equities"][sid] = {d: v * k for d, v in series.items()}
        return out or h

    def _h_real_macro(self, ev: Event) -> None:
        self.market.real_macro = ev.payload.get("series") or {}
        self.market.macro.real_series = self.market.real_macro_series()

    def real_market_ready(self, d: date, at=None) -> Optional[str]:
        """For a world that tracks the market: None when `d` can be processed, else why not (no close published yet)."""
        if getattr(self, "market_source", "SIMULATED") != "REAL":
            return None
        if not session_closed(d, at):
            return f"the {d.isoformat()} session has not closed yet (the market closes at 16:00 New York); the world waits for it"
        if self.market.real_targets(d) is not None:
            return None
        feed = self.market.real_feed
        if feed is not None:
            try:
                feed.refresh("1mo")
            except Exception as e:  # offline: say so, keep the log untouched
                return f"could not reach the market data feed ({e.__class__.__name__}); the session for {d.isoformat()} waits until it can"
            if self.market.real_macro_feed is not None:
                try:
                    self.market.real_macro_feed.refresh()        # cached six hours; a failure just keeps yesterday's series
                except Exception:
                    pass
            if feed.targets_for(d) is not None:
                return None
        return f"the real market has not closed for {d.isoformat()} yet (closes are published after the session); the world waits for it"

    def _h_day_started(self, ev: Event) -> None:
        self.current_date = date.fromisoformat(ev.payload["date"])

    def _h_scenario_event(self, ev: Event) -> None:
        self.scenario_log.append({"date": ev.sim_date, **ev.payload})

    def _h_rates_forced(self, ev: Event) -> None:
        self.market.force_rate_shock(float(ev.payload["bp"]))

    def _h_regime_forced(self, ev: Event) -> None:
        r = ev.payload["regime"]
        if self.market.state is not None:
            self.market.state.regime = r
            self.market.regime_history.append((ev.sim_date, r))

    def _h_portfolio_created(self, ev: Event) -> None:
        p = ev.payload
        pf = Portfolio(id=p["portfolio_id"], name=p["name"], portfolio_type=p["portfolio_type"], base_currency=p["base_currency"],
                       benchmark=p.get("benchmark"), created=ev.sim_date, realism=p.get("realism", "PROFESSIONAL"),
                       mode=p.get("mode", "SANDBOX"), custody_account=p["custody_account"], job=p.get("job", "SANDBOX"))
        pf.cash_account(pf.base_currency)
        self.portfolios[pf.id] = pf
        self.ledgers[pf.id] = Ledger(pf.id)

    def _h_portfolio_deleted(self, ev: Event) -> None:
        pid = ev.payload["portfolio_id"]
        self.portfolios.pop(pid, None)
        self.ledgers.pop(pid, None)

    # ------------------------------------------------------------------ the treasury: the save's first book, the one the others draw from
    TREASURY_TYPE = "TREASURY"

    def treasury_book(self) -> Optional[Portfolio]:
        return next((p for p in self.portfolios.values() if p.portfolio_type == self.TREASURY_TYPE), None)

    def _h_treasury_funded(self, ev: Event) -> None:
        # legacy (a cash pool outside the books, from the first version of the treasury): kept so those saves still replay
        p = ev.payload
        self.treasury_cash[p["currency"]] = self.treasury_cash.get(p["currency"], ZERO) + money(p["amount"])
        self.treasury_log.append({"date": ev.sim_date, "kind": "FUNDED", "currency": p["currency"], "amount": money(p["amount"]), "note": p.get("note", "capital into the treasury")})

    def _base_of(self, ccy: str, amt: Decimal) -> Decimal:
        """Dollars (base) for a local amount at today's rate: what capital moved in another currency is booked at."""
        return money(amt * self.fx.k(ccy)) if ccy != "USD" else money(amt)

    def _move_capital(self, src: Optional[Portfolio], dst: Optional[Portfolio], ccy: str, amt: Decimal, ev: Event, memo: str, base: Optional[Decimal] = None) -> None:
        """Capital leaves `src` (its cash and contributed capital fall) and enters `dst`; None on either side is the legacy pool.
        `base`: the dollar value (saves before 2026-09-24 carried none and booked a foreign amount one-for-one)."""
        from .engines.ledger import dr, cr
        b = amt if base is None else money(base)
        if src is not None:
            ca = src.cash_account(ccy)
            ca.balance -= amt
            ca.base_value -= b
            src.contributed_capital -= b
            src.day_capital_flows -= b
            self.record_cash_movement(src, ccy, -amt, "TREASURY", memo, ev)
            self.post(src.id, f"Capital out {ccy} {amt:,.2f}: {memo}", [dr("3000", b), cr(f"1010:{ccy}", b)], ev, {"kind": "TREASURY"})
        else:
            self.treasury_cash[ccy] = self.treasury_cash.get(ccy, ZERO) - amt
        if dst is not None:
            ca = dst.cash_account(ccy)
            ca.balance += amt
            ca.base_value += b
            dst.contributed_capital += b
            dst.day_capital_flows += b
            self.record_cash_movement(dst, ccy, amt, "TREASURY", memo, ev)
            self.post(dst.id, f"Capital in {ccy} {amt:,.2f}: {memo}", [dr(f"1010:{ccy}", b), cr("3000", b)], ev, {"kind": "TREASURY"})
        else:
            self.treasury_cash[ccy] = self.treasury_cash.get(ccy, ZERO) + amt

    def _h_treasury_allocated(self, ev: Event) -> None:
        p = ev.payload
        pf = self.portfolios[p["portfolio_id"]]
        ccy, amt = p["currency"], money(p["amount"])
        tb = self.portfolios.get(p["treasury_id"]) if p.get("treasury_id") else None      # None: the legacy pool
        base = D(str(p["base_amount"])) if p.get("base_amount") is not None else None
        if p["direction"] == "TO_BOOK":
            self._move_capital(tb, pf, ccy, amt, ev, f"drawn from the Treasury into {pf.name}", base)
        else:
            self._move_capital(pf, tb, ccy, amt, ev, f"returned from {pf.name} to the Treasury", base)
        self.treasury_log.append({"date": ev.sim_date, "kind": p["direction"], "portfolio_id": pf.id, "book": pf.name, "currency": ccy, "amount": amt,
                                  "base_amount": base if base is not None else amt, "note": p.get("note", "")})

    def _treasury_or_raise(self) -> Portfolio:
        tb = self.treasury_book()
        if tb is None:
            raise CommandError("this save has no Treasury book: create one (+ Book → Treasury) and the other books can draw from it")
        return tb

    def fund_treasury(self, currency: str, amount, note: str = "capital into the treasury") -> Event:
        """Fresh capital into the Treasury book."""
        tb = self._treasury_or_raise()
        amt = money(amount)
        if amt <= 0:
            raise CommandError("the amount must be positive")
        return self.contribute_capital(tb.id, currency.upper(), amt)

    def spare_cash(self, pf: Portfolio, ccy: str) -> Decimal:
        """Settled cash a book can give up after everything pending."""
        return max(ZERO, min(pf.cash_account(ccy).balance, self.trading.projected_cash(pf, ccy)))

    def treasury_spare_for(self, pf: Portfolio, ccy: str) -> Decimal:
        """What the Treasury could put into `pf` in `ccy` right now (zero for the Treasury itself or a save without one)."""
        tb = self.treasury_book()
        if tb is None or pf.id == tb.id:
            return ZERO
        return self.spare_cash(tb, ccy)

    def treasury_backstop(self, pf: Portfolio, ccy: str, amount: Decimal, cause: Event, note: str = "") -> bool:
        """The Treasury covers a book's shortfall when it can: capital moves into the book as an allocation, recorded against `cause`."""
        amt = money(amount)
        if amt <= 0:
            return True
        tb = self.treasury_book()
        if tb is None or pf.id == tb.id or self.spare_cash(tb, ccy) < amt:
            return False
        self.emit(E.TREASURY_ALLOCATED, {"portfolio_id": pf.id, "treasury_id": tb.id, "currency": ccy, "amount": amt, "base_amount": self._base_of(ccy, amt), "direction": "TO_BOOK",
                                         "note": note or f"backstop: the Treasury covered a {ccy} shortfall in {pf.name}"}, cause_id=cause.id, portfolio_id=pf.id)
        return True

    def treasury_cover_overdrafts(self, cause: Event) -> None:
        """Any book overdrawn in any currency after the day's cash flows is topped up from the Treasury while it has the spare cash."""
        for pf in list(self.portfolios.values()):
            if pf.portfolio_type == self.TREASURY_TYPE:
                continue
            for ccy, ca in list(pf.cash.items()):
                if ca.balance < 0:
                    self.treasury_backstop(pf, ccy, -ca.balance, cause, f"backstop: the Treasury covered {pf.name}'s {ccy} overdraft")

    def allocate_from_treasury(self, portfolio_id: str, currency: str, amount) -> Event:
        """Move capital from the Treasury book into another book."""
        tb = self._treasury_or_raise()
        pf = self.portfolio(portfolio_id)
        if pf.id == tb.id:
            raise CommandError("that is the Treasury itself")
        ccy, amt = currency.upper(), money(amount)
        if amt <= 0:
            raise CommandError("the amount must be positive")
        have = self.spare_cash(tb, ccy)
        if amt > have:
            raise CommandError(f"the Treasury can spare {ccy} {have:,.0f}; {amt:,.0f} asked")
        ev = self.emit(E.TREASURY_ALLOCATED, {"portfolio_id": pf.id, "treasury_id": tb.id, "currency": ccy, "amount": amt, "base_amount": self._base_of(ccy, amt), "direction": "TO_BOOK"}, portfolio_id=pf.id)
        self.flush()
        return ev

    def return_to_treasury(self, portfolio_id: str, currency: str, amount, note: str = "") -> Event:
        """Move cash a book does not need back to the Treasury: settled cash it can spare after everything pending."""
        tb = self._treasury_or_raise()
        pf = self.portfolio(portfolio_id)
        if pf.id == tb.id:
            raise CommandError("that is the Treasury itself")
        ccy, amt = currency.upper(), money(amount)
        if amt <= 0:
            raise CommandError("the amount must be positive")
        spare = self.spare_cash(pf, ccy)
        if amt > spare:
            raise CommandError(f"{pf.name} can spare {ccy} {spare:,.0f} (settled cash after what is pending); {amt:,.0f} asked")
        ev = self.emit(E.TREASURY_ALLOCATED, {"portfolio_id": pf.id, "treasury_id": tb.id, "currency": ccy, "amount": amt, "base_amount": self._base_of(ccy, amt), "direction": "TO_TREASURY", "note": note}, portfolio_id=pf.id)
        self.flush()
        return ev

    def open_items(self, pf: Portfolio) -> List[str]:
        """What stands in the way of closing a book: anything held, working or owed."""
        out = []
        n = sum(1 for p in pf.positions.values() if p.quantity != 0)
        if n:
            out.append(f"{n} open position(s)")
        n = sum(1 for o in pf.orders.values() if o.status in ("WORKING", "PARTIALLY_FILLED", "ENTERED"))
        if n:
            out.append(f"{n} working instruction(s)")
        n = sum(1 for t in pf.otc_trades.values() if t.status == "OPEN")
        if n:
            out.append(f"{n} open OTC trade(s)")
        n = sum(1 for l in pf.loans.values() if l.status == "OPEN") + sum(1 for r in pf.repos.values() if r.status == "OPEN")
        if n:
            out.append(f"{n} open borrow(s) / repo(s)")
        n = sum(1 for l in pf.private_loans.values() if l.status in ("OPEN", "DEFAULTED"))
        if n:
            out.append(f"{n} private credit loan(s)")
        n = sum(1 for c in getattr(pf, "pe_companies", {}).values() if c.status not in ("SOLD", "IPO_COMPLETE", "RESTRUCTURED", "FAILED"))
        if n:
            out.append(f"{n} portfolio compan{'y' if n == 1 else 'ies'}")
        n = sum(1 for e in getattr(pf, "ib_engagements", {}).values() if e.status in ("ACTIVE", "DECISION"))
        if n:
            out.append(f"{n} live banking engagement(s)")
        n = sum(1 for f in pf.fx_forwards.values() if f.status == "OPEN")
        if n:
            out.append(f"{n} open FX forward(s)")
        if sum(1 for c in pf.collateral_calls.values() if c.status == "OPEN"):
            out.append("an open collateral call")
        if pf.margin_loan > 0:
            out.append(f"a prime-broker loan of {pf.margin_loan:,.0f}")
        pend = sum(1 for si in pf.settlements.values() if si.status in ("PENDING", "MATCHED", "FAILED"))
        if pend:
            out.append(f"{pend} unsettled trade(s)")
        return out

    def delete_portfolio(self, portfolio_id: str) -> Event:
        """Close a book: only a flat one (nothing held, working or owed), never the last one. The event stays in the log."""
        pf = self.portfolio(portfolio_id)
        if len(self.portfolios) <= 1:
            raise CommandError("a save keeps at least one book")
        if pf.portfolio_type == self.TREASURY_TYPE:
            raise CommandError("the Treasury cannot be deleted: the other books draw from it")
        items = self.open_items(pf)
        if items:
            raise CommandError(f"{pf.name} is not flat: {', '.join(items)}. Close everything and let it settle, then delete the book")
        tb = self.treasury_book()
        for ccy, ca in list(pf.cash.items()):
            if ca.balance < 0:
                raise CommandError(f"{pf.name} owes {ccy} {-ca.balance:,.0f}: cover it before deleting the book")
            if ca.balance > 0:                                              # the book's cash goes back to the Treasury
                if tb is None:
                    raise CommandError(f"{pf.name} still holds {ccy} {ca.balance:,.0f} and this save has no Treasury book to return it to: create one (+ Book → Treasury) first")
                self.emit(E.TREASURY_ALLOCATED, {"portfolio_id": pf.id, "treasury_id": tb.id, "currency": ccy, "amount": ca.balance, "base_amount": ca.base_value, "direction": "TO_TREASURY", "note": "book closed"}, portfolio_id=pf.id)
        ev = self.emit(E.PORTFOLIO_DELETED, {"portfolio_id": pf.id, "name": pf.name, "nav": self.ledgers[pf.id].nav()}, portfolio_id=pf.id)
        self.flush()
        return ev

    def _h_capital(self, ev: Event) -> None:
        from .engines.ledger import dr, cr
        p = ev.payload
        pf = self.portfolios[p["portfolio_id"]]
        amt = money(p["amount"])
        base = money(D(str(p["base_amount"]))) if p.get("base_amount") is not None else amt     # older saves: one-for-one
        ca = pf.cash_account(p["currency"])
        ca.balance += amt
        ca.base_value += base
        pf.contributed_capital += base
        pf.day_capital_flows += base
        self.record_cash_movement(pf, p["currency"], amt, "CAPITAL", "Capital contribution", ev)
        self.post(pf.id, f"Capital contribution {p['currency']} {amt:,.2f}",
                  [dr(f"1010:{p['currency']}", base), cr("3000", base)], ev, {"kind": "CAPITAL"})

    def _h_market_close(self, ev: Event) -> None:
        p = ev.payload
        d = date.fromisoformat(p["date"])
        if self.replaying:
            bars = {t: Bar(p["date"], D(b[0]), D(b[1]), D(b[2]), D(b[3]), int(b[4]), D(b[5]), D(b[6])) for t, b in p["bars"].items()}
            curve = YieldCurve(p["date"], p["curve"]["tenors"], p["curve"]["rates"], p["curve"]["ig"], p["curve"]["hy"], p["curve"]["policy"])
            self.market.ingest_close(d, bars, curve, p["state"], p.get("commodities"), p.get("lending"), p.get("fx"), p.get("vol"), p.get("dealers"),
                                     p.get("macro"), p.get("corporate_events"))
        self.current_date = d
        self.day_count = int(p.get("day_index", self.day_count))
        self.options.ensure_listings(d)

    def _h_ledger_posted(self, ev: Event) -> None:
        p = ev.payload
        self.ledgers[p["portfolio_id"]].post(p["entry_id"], ev.sim_date, p["memo"], p["lines"], ev.id, ev.cause_id, p.get("reference", {}))

    def _h_news(self, ev: Event) -> None:
        p = ev.payload
        self.news.append(NewsItem(id=f"NEWS-{ev.seq:06d}", date=ev.sim_date, headline=p["headline"], body=p["body"],
                                  category=p["category"], refs=p.get("refs", []), event_id=ev.id,
                                  publisher=p.get("publisher", ""), link=p.get("link", ""), time=p.get("time", "")))

    def _h_day_closed(self, ev: Event) -> None:
        for pf in self.portfolios.values():
            pf.day_trade_ids = []
            pf.day_capital_flows = ZERO
            for pos in pf.positions.values():
                pos.day_fills = []

    # ------------------------------------------------------------------ helpers used by engines
    def post(self, portfolio_id: str, memo: str, lines: List[Dict], cause: Event, reference: Optional[Dict] = None) -> Optional[Event]:
        """Derive a LEDGER_POSTED event from `cause`."""
        lines = [l for l in lines if D(l.get("debit", 0)) != 0 or D(l.get("credit", 0)) != 0]
        if not lines:
            return None
        return self.derive(E.LEDGER_POSTED, {"portfolio_id": portfolio_id, "entry_id": f"JE-{self.next_seq():06d}", "memo": memo,
                                             "lines": lines, "reference": reference or {}}, cause, portfolio_id=portfolio_id)

    def record_cash_movement(self, pf: Portfolio, ccy: str, amount: Decimal, kind: str, reference: str, ev: Event) -> None:
        from .domain.models import CashMovement
        pf.cash_movements.append(CashMovement(id=f"CM-{ev.seq:06d}-{len(pf.cash_movements)+1}", portfolio_id=pf.id, currency=ccy,
                                              amount=money(amount), kind=kind, reference=reference, date=ev.sim_date,
                                              balance_after=pf.cash_account(ccy).balance, event_id=ev.id))

    def record_custody_movement(self, pf: Portfolio, security_id: str, quantity: Decimal, kind: str, reference: str, ev: Event) -> None:
        from .domain.models import CustodyMovement
        pos = pf.position(security_id)
        pf.custody_movements.append(CustodyMovement(id=f"CU-{ev.seq:06d}-{len(pf.custody_movements)+1}", portfolio_id=pf.id,
                                                    security_id=security_id, quantity=quantity, kind=kind, reference=reference,
                                                    date=ev.sim_date, balance_after=pos.settled_quantity, event_id=ev.id))

    def security(self, security_id: str) -> Security:
        if security_id not in self.securities:
            raise CommandError(f"Unknown security {security_id!r}")
        return self.securities[security_id]

    def portfolio(self, portfolio_id: str) -> Portfolio:
        if portfolio_id not in self.portfolios:
            raise CommandError(f"Unknown portfolio {portfolio_id!r}")
        return self.portfolios[portfolio_id]

    # ------------------------------------------------------------------ commands
    @classmethod
    def create(cls, world_id: str, name: str, seed: int, start_date: date, store=None, base_currency: str = "USD",
               prehistory_days: int = 260, initial_regime: str = "NORMAL_GROWTH", clock: Optional[ClockConfig] = None,
               scenario: str = "NONE", market_source: str = "SIMULATED", real_history: Optional[Dict] = None, real_macro: Optional[Dict] = None) -> "World":
        w = cls(world_id, store)
        clock = clock or ClockConfig()
        if store is not None:
            store.create_world(world_id, name)
        w.emit(E.WORLD_CREATED, {"name": name, "seed": seed, "start_date": start_date.isoformat(), "base_currency": base_currency,
                                 "prehistory_days": prehistory_days, "initial_regime": initial_regime,
                                 "settlement_cycles": SettlementConfig().cycles, "clock_mode": clock.mode, "timezone": clock.timezone,
                                 "update_time": clock.update_time, "lock_session": bool(clock.lock_session), "scenario": scenario, "save_version": SAVE_VERSION, "engine_version": ENGINE_VERSION,
                                 "market_source": market_source},
                sim_date=start_date.isoformat())
        if market_source == "REAL":
            # the real closes the world starts from, stored once so replay never needs the network
            fetch_here = real_history is None          # a caller that brings its own history (the service, tests) brings the economy too
            if real_history is None:
                from .engines.realfeed import RealFeed, equity_symbols_for, equity_scales_for
                feed = RealFeed(equity_symbols_for(w.securities), scales=equity_scales_for(w.securities))
                feed.refresh("1y", force=True)
                real_history = feed.history(start_date - timedelta(days=420), date.fromisoformat(feed.latest_date() or start_date.isoformat()))
            if not real_history.get("equities", {}).get("SPY"):
                raise CommandError("no real market data could be fetched (is the machine online?)")
            if start_date.isoformat() not in real_history["equities"]["SPY"]:
                raise CommandError(f"the real market has no close for {start_date.isoformat()}: pick a past session, or today after the close")
            w.emit(E.REAL_HISTORY_LOADED, {"history": real_history}, sim_date=start_date.isoformat())
            if real_macro is None and fetch_here:
                from .engines.realmacro import RealMacro
                rm = RealMacro()
                try:
                    rm.refresh()
                except Exception:
                    pass
                real_macro = rm.snapshot(start_date - timedelta(days=800)) if rm.data else {}
            if real_macro:
                w.emit(E.REAL_MACRO_LOADED, {"series": real_macro}, sim_date=start_date.isoformat())
            else:
                get_logger("world").warning("world %s: no real economic series could be fetched; the macro screens stay simulated until a later refresh", world_id)
        # The start date is the first processed day: the world opens with a briefing already waiting.
        w.current_date = w.calendar.prev_business_day(start_date)
        w.simulation.run_daily_process(start_date)
        w.flush()
        return w

    @classmethod
    def load(cls, store, world_id: str, strict: bool = True) -> "World":
        from .migrations import migrate
        events, report = migrate(store.load_events(world_id))
        w = cls(world_id, store)
        w.migration = report
        if report["migrated"]:
            get_logger("world").info("world %s: migrated save v%s -> v%s (%s)", world_id, report["from"], report["to"], "; ".join(report["notes"]))
        w.replay(events, strict=strict)
        w._relist_orphans()
        w.save_version = report["to"]
        w.check_integrity()
        return w

    def _relist_orphans(self) -> None:
        """Option contracts an order, trade or position refers to but today's chains do not list (a chain relisted
        around a repaired price) are listed again from their ids, so every book still resolves what it holds."""
        refs = set()
        for pf in self.portfolios.values():
            refs.update(o.security_id for o in pf.orders.values())
            refs.update(t.security_id for t in pf.trades.values())
            refs.update(pf.positions)
        for sid in sorted(refs - set(self.securities)):
            self.options.list_contract(sid)

    def create_portfolio(self, name: str, portfolio_type: str = "PERSONAL", capital: Decimal = D(10_000_000), currency: str = "USD",
                         benchmark: Optional[str] = "SPY", realism: str = "PROFESSIONAL", mode: str = "SANDBOX", job: str = "SANDBOX",
                         _ai_desk: bool = False) -> Portfolio:
        from .careers import JOBS
        if not name.strip():
            raise CommandError("Portfolio name is required")
        if job not in JOBS:
            raise CommandError(f"unknown job {job}")
        if JOBS[job].status != "PLAYABLE" and not (_ai_desk and JOBS[job].status == "AI"):
            raise CommandError(f"{JOBS[job].title} is not playable: {JOBS[job].status}")
        pid = self.new_id("PF")
        self.emit(E.PORTFOLIO_CREATED, {"portfolio_id": pid, "name": name.strip(), "portfolio_type": portfolio_type, "base_currency": currency,
                                        "benchmark": benchmark, "realism": realism, "mode": mode, "job": job,
                                        "custody_account": f"BNYM-CUST-{pid[-6:]}"}, portfolio_id=pid)
        if D(capital) > 0:
            self.contribute_capital(pid, currency, D(capital))
        pf = self.portfolios[pid]
        pf.peak_nav = D(capital)
        cause = self.events[-1]
        # job-specific books
        if job == "TREASURY_MANAGER":
            self.treasury.setup(pf, cause)
        if job == "RISK_MANAGER" and not self.institutions.desks():
            from .engines.institutions import AI_JOBS
            for ai_job in AI_JOBS:
                self.create_portfolio(JOBS[ai_job].title, "INSTITUTIONAL", JOBS[ai_job].capital, currency, JOBS[ai_job].benchmark, realism, mode, ai_job, _ai_desk=True)
        self.careers.start_missions(pf, cause)
        # a fresh portfolio gets a briefing for the current day immediately
        self.pnl.snapshot(cause)
        self.risk.process_day(cause)
        self.briefing.build(cause)
        self.flush()
        return pf

    def contribute_capital(self, portfolio_id: str, currency: str, amount: Decimal) -> Event:
        self.portfolio(portfolio_id)
        if money(amount) <= 0:
            raise CommandError("Contribution must be positive")
        ev = self.emit(E.CAPITAL_CONTRIBUTED, {"portfolio_id": portfolio_id, "currency": currency, "amount": money(amount),
                                               "base_amount": self._base_of(currency, money(amount))}, portfolio_id=portfolio_id)
        self.flush()
        return ev

    def place_order(self, portfolio_id: str, security_id: str, side: str, quantity, order_type: str = "MARKET",
                    limit_price=None, stop_price=None, time_in_force: str = "DAY", strategy_tag: Optional[str] = None,
                    trail_pct: Optional[float] = None, condition: Optional[Dict] = None, execution: str = "NEXT_UPDATE", at=None):
        """An instruction for the next update (against the next session's open, or its close when that session is already
        running), or with execution LIVE an immediate fill at the latest real quote (saves that track the market only)."""
        execution = (execution or "NEXT_UPDATE").upper()
        if execution == "LIVE" and not self.live.available():
            raise CommandError("live execution needs a save that tracks the real market; this save is simulated — send an instruction instead")
        if execution == "LIVE" and condition and condition.get("ref"):
            raise CommandError("a conditional order is an instruction for the daily update (its reference is evaluated at the close); send it as NEXT UPDATE")
        executes_at = None
        if execution != "LIVE" and self.quote_ticks_active():
            executes_at = next_quote_tick(self.calendar, at).isoformat()
        try:
            order = self.trading.enter_order(portfolio_id, security_id, side, quantity, order_type, limit_price, stop_price, time_in_force,
                                             strategy_tag, trail_pct, condition, execute_at=self.instruction_session(at), execution=execution,
                                             executes_at=executes_at)
            if execution == "LIVE":
                self.live.fill(order, self.events[-1])
        finally:
            self.flush()
        return order

    def work_live(self, max_age_s: float = 60.0, at=None) -> Dict:
        """Sweep the live book against the latest real quotes: resting live orders at every call, instructions at the
        15-minute quote updates (every quote refresh and the server's minute tick call this)."""
        try:
            return self.live.work(max_age_s, at)
        finally:
            self.flush()

    def quote_ticks_active(self) -> bool:
        """Career saves with live quotes work instructions at the quote updates (09:45 to 16:15 New York, every 15 minutes)."""
        return getattr(self, "market_source", "SIMULATED") == "REAL" and self.live.available()

    def quote_updates(self, at=None) -> Dict:
        if not self.quote_ticks_active():
            if getattr(self, "market_source", "SIMULATED") != "REAL":
                why = "this save is simulated (a sandbox, or a career created before saves tracked the real market): instructions execute at the daily update; a new Career save trades on live quotes"
            else:
                why = "the live quote feed is not set up for this save: instructions execute at the daily update"
            return {"active": False, "reason": why}
        return {"active": True, "schedule": "every 15 minutes from 09:45 to 16:15 New York (the quote is up to 15 minutes delayed)",
                "last": latest_quote_tick(self.calendar, at).isoformat(), "next": next_quote_tick(self.calendar, at).isoformat(),
                "last_worked": self.live.last_tick}

    def instruction_session(self, at=None) -> str:
        """Which print of the next session an instruction entered now executes against: OPEN, or CLOSE once it has opened."""
        return instruction_session(self.clock, self.calendar, self.current_date, at)

    def cancel_order(self, portfolio_id: str, order_id: str):
        o = self.trading.cancel(portfolio_id, order_id)
        self.flush()
        return o

    # ------------------------------------------------------------------ phase 2 commands
    def ccy_borrow(self, portfolio_id: str, currency: str, amount, term_days: int = 0, tag=None):
        return self.funding.borrow(self.portfolio(portfolio_id), currency, amount, term_days, tag)

    def ccy_repay(self, portfolio_id: str, loan_id: str, amount=None):
        return self.funding.repay(self.portfolio(portfolio_id), loan_id, amount)

    def _cmd(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            self.flush()

    def request_locate(self, portfolio_id: str, security_id: str, quantity):
        return self._cmd(self.seclending.request_locate, self.portfolio(portfolio_id), self.security(security_id), quantity)

    def borrow_securities(self, portfolio_id: str, locate_id: str, quantity, collateral_type: str = "CASH"):
        return self._cmd(self.seclending.borrow, self.portfolio(portfolio_id), locate_id, quantity, collateral_type)

    def return_securities(self, portfolio_id: str, loan_id: str, quantity=None):
        return self._cmd(self.seclending.return_loan, self.portfolio(portfolio_id), loan_id, quantity)

    def repo_quote(self, side: str, security_id: str, quantity, term_type: str = "OVERNIGHT", term_days: int = 1):
        return self.repo.quote(side.upper(), self.security(security_id), quantity, term_type.upper(), int(term_days))

    def open_repo(self, portfolio_id: str, side: str, security_id: str, quantity, term_type: str = "OVERNIGHT", term_days: int = 1, auto_roll: bool = True):
        return self._cmd(self.repo.open, self.portfolio(portfolio_id), side, self.security(security_id), quantity, term_type, int(term_days), auto_roll)

    def close_repo(self, portfolio_id: str, repo_id: str):
        return self._cmd(self.repo.close, self.portfolio(portfolio_id), repo_id)

    def commit_private_credit(self, portfolio_id: str, deal_id: str, amount):
        return self._cmd(self.pcredit.commit, self.portfolio(portfolio_id), deal_id, amount)

    def sell_private_credit(self, portfolio_id: str, loan_id: str, amount):
        return self._cmd(self.pcredit.sell, self.portfolio(portfolio_id), loan_id, amount)

    # private equity
    def pe_diligence(self, portfolio_id: str, deal_id: str):
        return self._cmd(self.pequity.buy_diligence, self.portfolio(portfolio_id), deal_id)

    def pe_bid(self, portfolio_id: str, deal_id: str, multiple: float, leverage: float):
        return self._cmd(self.pequity.bid, self.portfolio(portfolio_id), deal_id, multiple, leverage)

    def pe_initiative(self, portfolio_id: str, company_id: str, kind: str, size_pct=None):
        return self._cmd(self.pequity.initiative, self.portfolio(portfolio_id), company_id, kind, size_pct)

    def pe_recap(self, portfolio_id: str, company_id: str, target_leverage: float):
        return self._cmd(self.pequity.recap, self.portfolio(portfolio_id), company_id, target_leverage)

    def pe_cure(self, portfolio_id: str, company_id: str):
        return self._cmd(self.pequity.cure, self.portfolio(portfolio_id), company_id)

    def pe_exit(self, portfolio_id: str, company_id: str, route: str):
        return self._cmd(self.pequity.start_exit, self.portfolio(portfolio_id), company_id, route)

    def pe_selldown(self, portfolio_id: str, company_id: str, fraction: float):
        return self._cmd(self.pequity.selldown, self.portfolio(portfolio_id), company_id, fraction)

    # investment banking
    def ib_pitch(self, portfolio_id: str, mandate_id: str, fee_pct: float, promise: float):
        return self._cmd(self.ibank.pitch, self.portfolio(portfolio_id), mandate_id, fee_pct, promise)

    def ib_decide(self, portfolio_id: str, engagement_id: str, choice: str, value=None):
        return self._cmd(self.ibank.decide, self.portfolio(portfolio_id), engagement_id, choice, value)

    def repo_post_collateral(self, portfolio_id: str, repo_id: str, security_id: str, quantity):
        return self._cmd(self.repo.post_collateral, self.portfolio(portfolio_id), repo_id, self.security(security_id), quantity)

    def repo_post_cash(self, portfolio_id: str, repo_id: str, amount):
        return self._cmd(self.repo.post_cash, self.portfolio(portfolio_id), repo_id, amount)

    def repo_reduce(self, portfolio_id: str, repo_id: str, amount):
        return self._cmd(self.repo.reduce, self.portfolio(portfolio_id), repo_id, amount)

    def repo_substitute(self, portfolio_id: str, repo_id: str, old_security_id: str, old_quantity, new_security_id: str, new_quantity):
        return self._cmd(self.repo.substitute, self.portfolio(portfolio_id), repo_id, old_security_id, old_quantity, self.security(new_security_id), new_quantity)

    def margin_draw(self, portfolio_id: str, amount):
        return self._cmd(self.prime.draw, self.portfolio(portfolio_id), amount)

    def margin_repay(self, portfolio_id: str, amount):
        return self._cmd(self.prime.repay, self.portfolio(portfolio_id), amount)

    def fx_spot(self, portfolio_id: str, buy_ccy: str, sell_ccy: str, amount, amount_ccy: str = "BUY", execution: str = "NEXT_UPDATE", tag: Optional[str] = None):
        return self._cmd(self.fx.spot, self.portfolio(portfolio_id), buy_ccy, sell_ccy, amount, amount_ccy, execution, tag)

    def fx_forward(self, portfolio_id: str, buy_ccy: str, sell_ccy: str, buy_amount, maturity: str, tag: Optional[str] = None):
        return self._cmd(self.fx.forward, self.portfolio(portfolio_id), buy_ccy, sell_ccy, buy_amount, maturity, tag)

    # ------------------------------------------------------------------ phase 3 commands (listed options)
    def exercise_option(self, portfolio_id: str, contract_id: str, quantity=None):
        return self._cmd(self.options.exercise, self.portfolio(portfolio_id), contract_id, quantity)

    def place_strategy(self, portfolio_id: str, strategy_type: str, underlying: str, expiry: str, strikes, quantity, net_limit=None,
                       expiry2: Optional[str] = None, time_in_force: str = "DAY"):
        return self._cmd(self.options.place_strategy, self.portfolio(portfolio_id), strategy_type, underlying, expiry, strikes, quantity, net_limit,
                         expiry2, time_in_force)

    def force_split(self, security_id: str, ratio: float) -> Event:
        """Scenario control for sandbox worlds: an immediate stock split (ratio 2 = 2-for-1, 0.5 = 1-for-2 reverse)."""
        if self.clock.mode == "REAL_TIME":
            raise CommandError("splits cannot be forced in a career world")
        sec = self.security(security_id)
        if sec.is_bond or sec.is_future or sec.is_option:
            raise CommandError("only equities split")
        if float(ratio) <= 0 or float(ratio) == 1.0:
            raise CommandError("ratio must be positive and not 1")
        ev = self.options.apply_split(sec.id, float(ratio), None)
        self.flush()
        return ev

    # ------------------------------------------------------------------ phase 4 commands (OTC, dealers, ISDA/CSA)
    def request_quote(self, portfolio_id: str, product: str, params: Dict):
        return self._cmd(self.otc.request_quote, self.portfolio(portfolio_id), product, params)

    def execute_rfq(self, portfolio_id: str, rfq_id: str, dealer: str):
        return self._cmd(self.otc.execute_rfq, self.portfolio(portfolio_id), rfq_id, dealer)

    def terminate_otc(self, portfolio_id: str, trade_id: str):
        return self._cmd(self.otc.terminate, self.portfolio(portfolio_id), trade_id)

    def credit_event(self, reference: str, recovery: Optional[float] = None) -> Event:
        """Scenario control (sandbox): an issuer fails today — CDS settle, its bonds mark at recovery, its equity collapses next session."""
        if self.clock.mode == "REAL_TIME":
            raise CommandError("credit events cannot be forced in a career world")
        sec = self.security(reference)
        if sec.asset_class != "CORP_BOND":
            raise CommandError("credit events apply to corporate bond issuers")
        if sec.defaulted:
            raise CommandError(f"{reference} has already defaulted")
        rec = recovery if recovery is not None else sec.recovery_rate
        return self._cmd(self.issuer_default, reference, rec, None, True)

    def issuer_default(self, reference: str, recovery: float, cause: Optional[Event], forced: bool = False) -> Event:
        ev = self.emit(E.ISSUER_DEFAULTED, {"reference": reference, "issuer": self.securities[reference].issuer, "recovery": recovery, "forced": forced},
                       cause_id=cause.id if cause else None)
        if self.securities[reference].asset_class == "CORP_BOND":     # single-name CDS reference corporates; a structured tranche just writes down
            self.otc.credit_event(reference, ev, recovery)
        self.corporate.default_bond(reference, recovery, ev)
        if forced:
            from .engines.market import BOND_ISSUER_TICKER
            self.market.macro.mark_defaulted(reference, self.current_date)
            tick = BOND_ISSUER_TICKER.get(reference)
            if tick:
                self.market.force_return(tick, -1.5)      # the issuer's equity collapses next session
        return ev

    # ------------------------------------------------------------------ phase 6 commands (corporate events)
    def elect(self, portfolio_id: str, ca_id: str, quantity):
        return self._cmd(self.cevents.elect, self.portfolio(portfolio_id), ca_id, quantity)

    def force_corporate_event(self, security_id: str, kind: str, terms: Dict, effective: str) -> Event:
        """Scenario control for sandbox worlds: announce a corporate event with the given terms and effective date."""
        from .engines.corporate_events import EVENT_TYPES
        if self.clock.mode == "REAL_TIME":
            raise CommandError("corporate events cannot be forced in a career world")
        sec = self.security(security_id)
        kind = kind.upper()
        if kind not in EVENT_TYPES:
            raise CommandError(f"kind must be one of {', '.join(EVENT_TYPES)}")
        eff = self.calendar.roll(date.fromisoformat(effective))
        if eff <= self.current_date:
            raise CommandError("effective date must be after today")
        payload = {"id": f"CE-{sec.id}-{self.current_date.isoformat()}-F", "kind": kind, "security_id": sec.id, "announced": self.current_date.isoformat(),
                   "effective": eff.isoformat(), "terms": dict(terms), "forced": True}
        ev = self.emit(E.CORPORATE_EVENT_ANNOUNCED, payload)
        self.market.cevents.ingest([payload])
        if kind in ("TENDER_OFFER", "CASH_MERGER"):
            ref = float(terms.get("offer_price", terms.get("deal_price", 0)))
            last = float(self.market.last_bar(sec.id).close)
            if ref and last:
                self.market.force_return(sec.id, 0.8 * math.log(ref / last))
        self.flush()
        return ev

    def default_counterparty(self, dealer: str, recovery: float = 0.4) -> Event:
        if self.clock.mode == "REAL_TIME":
            raise CommandError("counterparty defaults cannot be forced in a career world")
        return self._cmd(self.otc.default_counterparty, dealer, None, recovery)

    def force_regime(self, regime: str) -> Event:
        """Scenario control for sandbox worlds: the next processed day starts in `regime`."""
        from .engines.market import REGIMES
        if self.clock.mode == "REAL_TIME":
            raise CommandError("regimes cannot be forced in a career world")
        if regime not in REGIMES:
            raise CommandError(f"unknown regime {regime}")
        ev = self.emit(E.REGIME_FORCED, {"regime": regime, "label": REGIMES[regime].label})
        self.flush()
        return ev

    def apply_scenario(self, cause: Event) -> None:
        """Scripted scenarios (crisis mode): forced events keyed on the processed-day index, replay-safe (they are events)."""
        if self.scenario != "CRISIS" or self.replaying:
            return
        from .careers import CRISIS_SCRIPT
        idx = self.day_count      # the creation day is processed with day_count 0; the first advance is day 1
        for step in CRISIS_SCRIPT:
            if step["day"] != idx:
                continue
            self.emit(E.SCENARIO_EVENT, {"scenario": self.scenario, "day": idx, "note": step["note"], **{k: v for k, v in step.items() if k not in ("day", "note")}},
                      cause_id=cause.id)
            if "regime" in step:
                from .engines.market import REGIMES
                self.emit(E.REGIME_FORCED, {"regime": step["regime"], "label": REGIMES[step["regime"]].label, "scenario": True}, cause_id=cause.id)
            if "rates_bp" in step:
                self.emit(E.RATES_SHOCK_FORCED, {"bp": float(step["rates_bp"]), "note": step["note"]}, cause_id=cause.id)
            if "issuer_default" in step:
                self.issuer_default(step["issuer_default"], float(step.get("recovery", 0.3)), cause, forced=True)
            if "dealer_default" in step and not self.market.dealers.state[step["dealer_default"]].defaulted:
                self.otc.default_counterparty(step["dealer_default"], cause)

    # ------------------------------------------------------------------ phase 8 commands (careers: clients, lending desk, oversight)
    def quote_client(self, portfolio_id: str, rfq_id: str, level: Optional[float] = None, pass_: bool = False) -> Dict:
        return self._cmd(self.clients.quote, self.portfolio(portfolio_id), rfq_id, level, pass_)

    def lend_out(self, portfolio_id: str, security_id: str, quantity) -> Dict:
        return self._cmd(self.lenddesk.lend, self.portfolio(portfolio_id), self.security(security_id), quantity)

    def recall_lent(self, portfolio_id: str, lend_id: str, quantity=None) -> Dict:
        return self._cmd(self.lenddesk.recall, self.portfolio(portfolio_id), lend_id, quantity)

    def decide_request(self, portfolio_id: str, desk_id: str, request_id: str, approve: bool, note: str = "") -> Dict:
        return self._cmd(self.institutions.decide, self.portfolio(portfolio_id), self.portfolio(desk_id), request_id, approve, note)

    def set_desk_limit(self, portfolio_id: str, desk_id: str, key: str, value: float) -> Dict:
        return self._cmd(self.institutions.set_limit, self.portfolio(portfolio_id), self.portfolio(desk_id), key, value)

    def force_reduce(self, portfolio_id: str, desk_id: str, security_id: str, fraction: float) -> Dict:
        return self._cmd(self.institutions.force_reduce, self.portfolio(portfolio_id), self.portfolio(desk_id), security_id, fraction)

    # ------------------------------------------------------------------ phase 9 commands (commodity depth)
    def place_spread(self, portfolio_id: str, near_id: str, far_id: str, side: str, quantity, limit_points=None, time_in_force: str = "DAY"):
        return self._cmd(self.cdesk.place_spread, self.portfolio(portfolio_id), near_id, far_id, side, quantity, limit_points, time_in_force)

    def set_physical_delivery(self, portfolio_id: str, on: bool) -> Dict:
        return self._cmd(self.cdesk.set_physical_delivery, self.portfolio(portfolio_id), on)

    def force_rates(self, bp: float, note: str = "") -> Event:
        """Scenario control for sandbox worlds: an exogenous parallel curve shift (bp) on the next processed day."""
        if self.clock.mode == "REAL_TIME":
            raise CommandError("rate shocks cannot be forced in a career world")
        if abs(float(bp)) > 500:
            raise CommandError("rate shocks are limited to ±500bp")
        ev = self.emit(E.RATES_SHOCK_FORCED, {"bp": float(bp), "note": note})
        self.flush()
        return ev

    def advance(self, days: int = 1, force: bool = False) -> List[str]:
        if self.clock.mode == "REAL_TIME" and not force:
            raise CommandError("This is a career world: the market updates once a day at "
                               f"{self.clock.update_time} {self.clock.timezone}. Create a sandbox world to advance manually.")
        closed = []
        import time as _time
        for _ in range(max(1, days)):
            why = self.real_market_ready(self.calendar.next_business_day(self.current_date))
            if why:
                if closed:
                    break
                raise CommandError(why)
            _t0 = _time.perf_counter()
            _n0 = len(self.events)
            closed.append(self.simulation.advance_one_day())
            self.run_log.append({"date": closed[-1], "events": len(self.events) - _n0, "ms": round((_time.perf_counter() - _t0) * 1000, 1)})
        self.flush()
        get_logger("world").info("world %s: advanced %d day(s) to %s", self.id, len(closed), self.current_date.isoformat())
        return closed

    def catch_up(self, at=None) -> List[str]:
        """Career worlds: process every business day whose update time has passed."""
        if self.clock.mode != "REAL_TIME":
            return []
        target = target_sim_date(self.clock, self.calendar, at)
        closed = []
        import time as _time
        while self.current_date < target:
            if self.real_market_ready(self.calendar.next_business_day(self.current_date), at):
                break            # a world that tracks the market waits for the real close
            _t0, _n0 = _time.perf_counter(), len(self.events)
            closed.append(self.simulation.advance_one_day())
            self.run_log.append({"date": closed[-1], "events": len(self.events) - _n0, "ms": round((_time.perf_counter() - _t0) * 1000, 1)})
        if closed:
            self.flush()
            get_logger("world").info("world %s: caught up %d day(s) to %s", self.id, len(closed), self.current_date.isoformat())
        return closed

    def next_update_at(self, at=None):
        return next_update(self.clock, self.calendar, at) if self.clock.mode == "REAL_TIME" else None

    def trading_window(self, at=None) -> Dict:
        return trading_window(self.clock, self.calendar, at)

    def set_clock(self, update_time: Optional[str] = None, timezone: Optional[str] = None, lock_session: Optional[bool] = None) -> Event:
        """Change when a career save processes its day and whether instructions lock while the session runs (the settings
        page). Real-market saves must update after the close."""
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt, time as _time
        from .clock import MARKET_TZ, SESSION_FINAL
        tz = timezone or self.clock.timezone
        try:
            ZoneInfo(tz)
        except Exception:
            raise CommandError(f"unknown timezone {tz}")
        ut = update_time or self.clock.update_time
        try:
            h, m = ut.split(":"); _time(int(h), int(m))
        except Exception:
            raise CommandError("update_time must be HH:MM")
        if getattr(self, "market_source", "SIMULATED") == "REAL":
            ny = _dt.combine(date(2026, 1, 5), _time(int(h), int(m)), tzinfo=ZoneInfo(tz)).astimezone(MARKET_TZ).time()
            if ny < SESSION_FINAL:
                raise CommandError(f"a save that tracks the real market must update after the close: {ut} {tz} is {ny.strftime('%H:%M')} New York, before 16:00")
        lock = self.clock.lock_session if lock_session is None else bool(lock_session)
        ev = self.emit(E.CLOCK_CHANGED, {"update_time": ut, "timezone": tz, "mode": self.clock.mode, "lock_session": lock})
        self.flush()
        return ev

    def _h_clock_changed(self, ev: Event) -> None:
        p = ev.payload
        self.clock = ClockConfig(p.get("mode", self.clock.mode), p.get("timezone", self.clock.timezone), p.get("update_time", self.clock.update_time),
                                 bool(p.get("lock_session", self.clock.lock_session)))

    # ------------------------------------------------------------------ audit
    def audit_chain(self, event_id: str) -> Dict:
        """Ancestors and descendants of an event."""
        ev = self.events_by_id[event_id]
        ancestors = []
        cur = ev
        while cur.cause_id:
            cur = self.events_by_id[cur.cause_id]
            ancestors.append(cur)
        def tree(eid):
            return {"event": self.events_by_id[eid], "children": [tree(c) for c in self.children.get(eid, [])]}
        return {"event": ev, "ancestors": list(reversed(ancestors)), "tree": tree(event_id)}


def _clean(o):
    """Make payloads JSON-safe: Decimals -> str, dates -> iso, dataclasses -> dict."""
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "__dataclass_fields__"):
        return _clean(asdict(o))
    return o

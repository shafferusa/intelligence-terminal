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
from .clock import ClockConfig, next_update, target_sim_date
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
        self.last_processed_utc: Optional[str] = None
        # engines
        from .engines import trading, settlement, corporate_actions, accruals, pnl, simulation, futures, briefing
        from .engines import collateral, seclending, repo, prime, fx, options, otc, risk, corporate_events
        from .engines import investors, clients, treasury, institutions, commodity_desk, private_credit
        from . import careers
        self.trading = trading.TradingEngine(self)
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
        self.on(E.PORTFOLIO_CREATED, World._h_portfolio_created)
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
        self.clock = ClockConfig(p.get("clock_mode", "SANDBOX"), p.get("timezone", "America/New_York"), p.get("update_time", "09:00"))
        self.scenario = p.get("scenario", "NONE")
        self.save_version = int(p.get("save_version", 1))
        if p.get("settlement_cycles"):
            self.settlement_config = SettlementConfig(cycles=dict(p["settlement_cycles"]))
        self.securities = build_universe(self.start_date, self.seed)
        self.market = MarketEngine(self.seed, self.start_date, self.calendar, self.securities,
                                   prehistory_days=int(p.get("prehistory_days", 260)),
                                   initial_regime=p.get("initial_regime", "NORMAL_GROWTH"))
        self.market.bar_provider = lambda sid: self.options.option_bar(self.securities[sid])
        self.market_source = p.get("market_source", "SIMULATED")
        self.market.bootstrap()
        if self.market_source == "REAL":
            from .engines.realfeed import RealFeed, equity_symbols_for
            self.market.real_feed = RealFeed(equity_symbols_for(self.securities))

    def _h_real_history(self, ev: Event) -> None:
        h = ev.payload["history"]
        self.market.real_history = h
        self.market.apply_real_history(h)

    def real_market_ready(self, d: date) -> Optional[str]:
        """For a world that tracks the market: None when `d` can be processed, else why not (no close published yet)."""
        if getattr(self, "market_source", "SIMULATED") != "REAL":
            return None
        if self.market.real_targets(d) is not None:
            return None
        feed = self.market.real_feed
        if feed is not None:
            try:
                feed.refresh("1mo")
            except Exception as e:  # offline: say so, keep the log untouched
                return f"could not reach the market data feed ({e.__class__.__name__}); the session for {d.isoformat()} waits until it can"
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

    def _h_capital(self, ev: Event) -> None:
        from .engines.ledger import dr, cr
        p = ev.payload
        pf = self.portfolios[p["portfolio_id"]]
        amt = money(p["amount"])
        ca = pf.cash_account(p["currency"])
        ca.balance += amt
        ca.base_value += amt
        pf.contributed_capital += amt
        pf.day_capital_flows += amt
        self.record_cash_movement(pf, p["currency"], amt, "CAPITAL", "Capital contribution", ev)
        self.post(pf.id, f"Capital contribution {p['currency']} {amt:,.2f}",
                  [dr(f"1010:{p['currency']}", amt), cr("3000", amt)], ev, {"kind": "CAPITAL"})

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
                                  category=p["category"], refs=p.get("refs", []), event_id=ev.id))

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
               scenario: str = "NONE", market_source: str = "SIMULATED", real_history: Optional[Dict] = None) -> "World":
        w = cls(world_id, store)
        clock = clock or ClockConfig()
        if store is not None:
            store.create_world(world_id, name)
        w.emit(E.WORLD_CREATED, {"name": name, "seed": seed, "start_date": start_date.isoformat(), "base_currency": base_currency,
                                 "prehistory_days": prehistory_days, "initial_regime": initial_regime,
                                 "settlement_cycles": SettlementConfig().cycles, "clock_mode": clock.mode, "timezone": clock.timezone,
                                 "update_time": clock.update_time, "scenario": scenario, "save_version": SAVE_VERSION, "engine_version": ENGINE_VERSION,
                                 "market_source": market_source},
                sim_date=start_date.isoformat())
        if market_source == "REAL":
            # the real closes the world starts from, stored once so replay never needs the network
            if real_history is None:
                from .engines.realfeed import RealFeed, equity_symbols_for
                feed = RealFeed(equity_symbols_for(w.securities))
                feed.refresh("1y", force=True)
                real_history = feed.history(start_date - timedelta(days=420), date.fromisoformat(feed.latest_date() or start_date.isoformat()))
            if not real_history.get("equities", {}).get("SPY"):
                raise CommandError("no real market data could be fetched (is the machine online?)")
            if start_date.isoformat() not in real_history["equities"]["SPY"]:
                raise CommandError(f"the real market has no close for {start_date.isoformat()}: pick a past session, or today after the close")
            w.emit(E.REAL_HISTORY_LOADED, {"history": real_history}, sim_date=start_date.isoformat())
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
        w.save_version = report["to"]
        w.check_integrity()
        return w

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
        ev = self.emit(E.CAPITAL_CONTRIBUTED, {"portfolio_id": portfolio_id, "currency": currency, "amount": money(amount)}, portfolio_id=portfolio_id)
        self.flush()
        return ev

    def place_order(self, portfolio_id: str, security_id: str, side: str, quantity, order_type: str = "MARKET",
                    limit_price=None, stop_price=None, time_in_force: str = "DAY", strategy_tag: Optional[str] = None,
                    trail_pct: Optional[float] = None, condition: Optional[Dict] = None):
        order = self.trading.enter_order(portfolio_id, security_id, side, quantity, order_type, limit_price, stop_price, time_in_force,
                                         strategy_tag, trail_pct, condition)
        self.flush()
        return order

    def cancel_order(self, portfolio_id: str, order_id: str):
        o = self.trading.cancel(portfolio_id, order_id)
        self.flush()
        return o

    # ------------------------------------------------------------------ phase 2 commands
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

    def fx_spot(self, portfolio_id: str, buy_ccy: str, sell_ccy: str, amount, amount_ccy: str = "BUY"):
        return self._cmd(self.fx.spot, self.portfolio(portfolio_id), buy_ccy, sell_ccy, amount, amount_ccy)

    def fx_forward(self, portfolio_id: str, buy_ccy: str, sell_ccy: str, buy_amount, maturity: str):
        return self._cmd(self.fx.forward, self.portfolio(portfolio_id), buy_ccy, sell_ccy, buy_amount, maturity)

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
            if self.real_market_ready(self.calendar.next_business_day(self.current_date)):
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

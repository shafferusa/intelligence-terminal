"""Framework-agnostic API service.

Every method takes plain Python values and returns JSON-safe dicts. The stdlib
HTTP server (server.py) and an optional FastAPI adapter both call into this
class, so the web framework is replaceable.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..careers import JOBS
from ..clock import ClockConfig, next_update, now_utc, target_sim_date
from ..domain.events import E
from ..engines.commodities import SPECS as COMMODITY_SPECS, SPEC_BY_CODE, MONTH_CODES
from ..engines.ledger import CHART, account_name, account_type
from ..engines.market import REGIMES
from ..engines.options import REGIME_MARGIN_MULT, STRATEGY_TEMPLATES
from ..engines.pricing import BondPricer, Instrument, interp_rate
from ..money import D, money, ZERO, price as qprice
from ..store import EventStore
from ..log import get_logger
from ..version import ENGINE_VERSION
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


MAX_CAPITAL = D(1_000_000_000_000)          # one trillion: the most any book can start with


def _capital(capital, job: str) -> Decimal:
    """Any starting amount for any job, capped at a trillion; blank means the job's standard capital."""
    try:
        c = D(str(capital)) if capital not in (None, "", 0, "0") else None
    except Exception:
        raise CommandError("capital must be a number")
    if c is None:
        return JOBS[job].capital
    if c <= 0:
        raise CommandError("capital must be positive")
    if c > MAX_CAPITAL:
        raise CommandError("capital is capped at $1 trillion")
    return money(c)


class Service:
    def __init__(self, store: EventStore, strict_replay: Optional[bool] = None):
        import os as _os
        import time as _time
        self.now = None                                  # injectable clock (tests): callable returning an aware datetime
        self.store = store
        self.worlds: Dict[str, World] = {}
        self.strict_replay = (_os.environ.get("FINSIM_STRICT_REPLAY") == "1") if strict_replay is None else bool(strict_replay)
        self.started_utc = _time.time()
        self.scheduler_state: Dict = {"last_tick": None, "ticks": 0, "last_result": {}}
        self.log = get_logger("service")

    # ------------------------------------------------------------------ worlds
    def list_worlds(self) -> List[Dict]:
        return self.store.list_worlds()

    def create_world(self, name: str, seed: int, start_date: Optional[str] = None, capital: Optional[float] = None, portfolio_name: str = "Main Portfolio",
                     portfolio_type: str = "PERSONAL", realism: str = "PROFESSIONAL", mode: str = "SANDBOX", initial_regime: str = "NORMAL_GROWTH",
                     benchmark: Optional[str] = "SPY", job: str = "SANDBOX", clock_mode: str = "SANDBOX", timezone: str = "America/New_York",
                     update_time: Optional[str] = None, scenario: str = "NONE", at=None, market_source: Optional[str] = None, treasury: bool = False,
                     lock_session: bool = False) -> Dict:
        """A save. Career saves (clock_mode REAL_TIME) start at the latest processed real date and advance by
        themselves at the update time; sandbox saves start wherever you like and advance on demand. market_source
        REAL makes the world track the actual market: real prehistory, real closes for every session it enters."""
        if initial_regime not in REGIMES:
            raise CommandError(f"unknown regime {initial_regime}")
        if job not in JOBS:
            raise CommandError(f"unknown job {job}")
        if JOBS[job].status != "PLAYABLE":
            raise CommandError(f"{JOBS[job].title} is not playable: {JOBS[job].status}")
        scenario = (scenario or "NONE").upper()
        if scenario not in ("NONE", "CRISIS"):
            raise CommandError("scenario must be NONE or CRISIS")
        clock_mode = (clock_mode or "SANDBOX").upper()
        # a career runs on the real market's day, processed after the close; a sandbox is generated from the seed unless asked otherwise
        market_source = (market_source or ("REAL" if clock_mode == "REAL_TIME" else "SIMULATED")).upper()
        if market_source not in ("SIMULATED", "REAL"):
            raise CommandError("market_source must be SIMULATED or REAL")
        update_time = update_time or ("17:00" if clock_mode == "REAL_TIME" else "09:00")
        clock = ClockConfig(clock_mode, timezone, update_time, bool(lock_session))
        if clock_mode == "REAL_TIME" and market_source == "REAL":
            from ..clock import MARKET_TZ, SESSION_FINAL
            from datetime import datetime as _dt
            ny = _dt.combine(date(2026, 1, 5), clock.update_t(), tzinfo=clock.tz()).astimezone(MARKET_TZ).time()
            if ny < SESSION_FINAL:
                raise CommandError(f"a career save updates after the real close: {update_time} {timezone} is {ny.strftime('%H:%M')} New York, before 16:00")
        cal = World("tmp").calendar
        real_history = None
        real_macro = None
        if market_source == "REAL":
            from ..engines.realfeed import RealFeed, equity_symbols_for
            from ..engines.market import build_universe
            feed = RealFeed(equity_symbols_for(build_universe(date.today(), int(seed))))
            feed.refresh("1y", force=True)
            latest = feed.latest_date()
            if not latest:
                raise CommandError("no real market data could be fetched: check the internet connection and try again")
            if clock.mode == "REAL_TIME":
                cal_sd = min(target_sim_date(clock, cal, at), date.fromisoformat(latest))
            else:
                cal_sd = date.fromisoformat(start_date) if start_date else date.fromisoformat(latest)
                if not feed.has(cal_sd):
                    cal_sd = date.fromisoformat(max(k for k in feed.data.get("SPY", {}) if k <= cal_sd.isoformat()) if any(k <= cal_sd.isoformat() for k in feed.data.get("SPY", {})) else latest)
            real_history = feed.history(cal_sd - timedelta(days=420), date.fromisoformat(latest))
            from ..engines.realmacro import RealMacro
            rm = RealMacro()
            try:
                rm.refresh()
            except Exception:
                pass
            real_macro = rm.snapshot(cal_sd - timedelta(days=800)) if rm.data else None
        elif clock.mode == "REAL_TIME":
            cal_sd = target_sim_date(clock, cal, at)
        else:
            cal_sd = cal.roll(date.fromisoformat(start_date) if start_date else target_sim_date(ClockConfig("SANDBOX", timezone, update_time), cal, at))
        wid = "W-" + uuid.uuid4().hex[:8]
        w = World.create(wid, name or "Untitled world", int(seed), cal_sd, store=self.store, initial_regime=initial_regime, clock=clock, scenario=scenario,
                         market_source=market_source, real_history=real_history, real_macro=real_macro)
        self.log.info("created world %s (%s, job %s, clock %s, seed %s, scenario %s)", wid, name, job, clock.mode, seed, scenario)
        self.worlds[wid] = w
        cap = _capital(capital, job)
        bench = benchmark if job == "SANDBOX" else JOBS[job].benchmark
        if treasury:
            # the save's first and only book is the Treasury: it holds the whole capital and the books the player opens draw from it
            pf = w.create_portfolio("Treasury", World.TREASURY_TYPE, cap, "USD", bench, realism, mode, job)
        else:
            pf = w.create_portfolio(portfolio_name, portfolio_type if job == "SANDBOX" else job, cap, "USD", bench, realism, mode, job)
        return {"world_id": wid, "portfolio_id": pf.id, "start_date": cal_sd.isoformat(), "clock_mode": clock.mode, "treasury": float(cap) if treasury else 0.0}

    def _open_for_instructions(self, w: World, at=None) -> None:
        """With the session lock on, a career save takes instructions only while the market is shut (update → 09:29 New York)."""
        if at is None and self.now is not None:
            at = self.now()
        tw = w.trading_window(at)
        if not tw["open"]:
            raise CommandError(f"instructions are locked while the session runs: {tw['reason']}")

    def set_clock(self, world_id: str, update_time: Optional[str] = None, timezone: Optional[str] = None, lock_session: Optional[bool] = None) -> Dict:
        w = self.world(world_id)
        w.set_clock(update_time, timezone, lock_session)
        return self.world_info(world_id)

    def live(self, world_id: str, ids: Optional[List[str]] = None) -> Dict:
        """Latest real quotes for securities in a save that tracks the market (Yahoo, up to 15 minutes delayed), next to
        the save's last close. Without ids: the index ETFs plus everything held in the save's books."""
        w = self.world(world_id)
        avail = w.live.available()
        out = {"available": avail, "source": "Yahoo Finance (up to 15 minutes delayed)" if avail else None, "last_close_date": w.current_date.isoformat(),
               "asof": now_utc().isoformat(), "quotes": {}}
        if not avail:
            out["note"] = "a simulated save has no live market: prices move when the day is processed"
            return jsonable(out)
        if w.live.pending():                                   # every quote refresh works the live book first (same cached fetch)
            try:
                out["worked"] = w.work_live(at=self.now() if self.now else None)
            except Exception as e:
                out["work_error"] = f"could not check the resting live orders ({e.__class__.__name__})"
        if not ids:
            ids = ["SPY", "QQQ", "IWM"] + sorted({p.security_id for pf in w.portfolios.values() for p in pf.positions.values() if p.quantity != 0})
        ids = [str(i).strip().upper() for i in ids if str(i).strip()][:250]
        try:
            out["quotes"] = w.live.quotes(ids)
        except Exception as e:  # offline: the page still renders on the last close
            out["error"] = f"could not reach the quote feed ({e.__class__.__name__})"
        return jsonable(out)

    def switch_to_real(self, world_id: str) -> Dict:
        """Switch a simulated save to the real market (real closes, live quotes, live tickets, 15-minute quote updates)."""
        from ..engines.realfeed import RealFeed, equity_symbols_for
        from ..engines.realmacro import RealMacro
        w = self.world(world_id)
        if getattr(w, "market_source", "SIMULATED") == "REAL":
            raise CommandError("this save already tracks the real market")
        feed = RealFeed(equity_symbols_for(w.securities))
        feed.refresh("1y", force=True)
        latest = feed.latest_date()
        if not latest:
            raise CommandError("no real market data could be fetched: check the internet connection and try again")
        end = min(w.current_date, date.fromisoformat(latest))
        history = feed.history(w.current_date - timedelta(days=420), end)
        rm = RealMacro()
        try:
            rm.refresh()
        except Exception:
            pass
        macro = rm.snapshot(w.current_date - timedelta(days=800)) if rm.data else None
        w.switch_to_real(history, macro)
        self.log.info("world %s now tracks the real market (closes through %s)", world_id, end.isoformat())
        return self.world_info(world_id)

    def work_live(self, world_id: str) -> Dict:
        """Check the resting live orders of one save against the latest real quotes now."""
        w = self.world(world_id)
        if not w.live.available():
            return {"available": False, "checked": 0, "filled": [], "triggered": [], "ratcheted": [], "note": "a simulated save has no live market"}
        return jsonable({"available": True, **w.work_live(at=self.now() if self.now else None)})

    def work_live_loaded(self) -> Dict[str, Dict]:
        """The server's minute tick: work the resting live book of every loaded save that has one (a save nobody has opened
        this run is not loaded just for this; opening it, or its own daily update, brings it in). Network trouble is logged, not raised."""
        out = {}
        for w in list(self.worlds.values()):
            if not w.live.available() or not w.live.pending():
                continue
            try:
                r = w.work_live(at=self.now() if self.now else None)
            except Exception as e:
                self.log.warning("world %s: resting live orders not checked (%s)", w.id, e.__class__.__name__)
                continue
            if r["filled"] or r["triggered"] or r["ratcheted"]:
                out[w.id] = r
                self.log.info("world %s: live sweep filled %d, triggered %d, ratcheted %d", w.id, len(r["filled"]), len(r["triggered"]), len(r["ratcheted"]))
        return out

    def catch_up_all(self, at=None) -> Dict[str, List[str]]:
        """Process due days for every career world (called by the scheduler and on access). `at` injects the clock."""
        out = {}
        for info in self.store.list_worlds():
            closed = self.catch_up_one(info["id"], at)
            if closed:
                out[info["id"]] = closed
        return out

    def catch_up_one(self, world_id: str, at=None) -> List[str]:
        """Load one save if needed and process its due days; the scheduler calls this per save so the lock is short."""
        if not any(x["id"] == world_id for x in self.store.list_worlds()):
            return []
        w = self.world(world_id, at, catch_up=False)
        return w.catch_up(at)

    def world(self, world_id: str, at=None, catch_up: bool = True) -> World:
        if at is None and self.now is not None:
            at = self.now()                      # an injected clock (tests) drives the catch-up too
        if world_id not in self.worlds:
            if not any(x["id"] == world_id for x in self.store.list_worlds()):
                raise NotFound(f"world {world_id} not found")
            w = World.load(self.store, world_id, strict=self.strict_replay)
            if w.migration.get("migrated"):
                self.store.set_save_version(world_id, w.save_version)
            if w.replay_errors:
                self.log.error("world %s opened with %d replay error(s); first: %s", world_id, len(w.replay_errors), w.replay_errors[0])
            self.worlds[world_id] = w
        w = self.worlds[world_id]
        if catch_up:
            w.catch_up(at)
        return w

    def health(self) -> Dict:
        import time as _time
        from ..version import ENGINE_VERSION, SAVE_VERSION
        loaded = list(self.worlds.values())
        return jsonable({"status": "ok" if not any(w.replay_errors for w in loaded) else "degraded", "engine_version": ENGINE_VERSION, "save_version": SAVE_VERSION,
                         "uptime_s": round(_time.time() - self.started_utc, 1), "worlds_stored": len(self.store.list_worlds()), "worlds_loaded": len(loaded), "loading": self.scheduler_state.get("busy"),
                         "strict_replay": self.strict_replay, "scheduler": self.scheduler_state,
                         "worlds": [{"id": w.id, "current_date": w.current_date, "clock_mode": w.clock.mode, "events": len(w.events), "replay_errors": len(w.replay_errors),
                                     "integrity_ok": w.integrity.get("ok", True), "save_version": w.save_version, "migrated": w.migration.get("migrated", False),
                                     "last_run": w.run_log[-1] if w.run_log else None} for w in loaded]})

    def delete_world(self, world_id: str) -> None:
        self.worlds.pop(world_id, None)
        self.store.delete_world(world_id)

    def world_info(self, world_id: str) -> Dict:
        w = self.world(world_id)
        r = w.market.regime()
        nu = w.next_update_at()
        return jsonable({"id": w.id, "name": w.name, "seed": w.seed, "start_date": w.start_date, "current_date": w.current_date, "engine_version": ENGINE_VERSION,
                         "day_index": w.day_count, "events": len(w.events), "regime": {"name": r.name, "label": r.label, "description": r.description},
                         "scenario": w.scenario, "scenario_log": w.scenario_log, "market_source": getattr(w, "market_source", "SIMULATED"),
                         "trading_window": w.trading_window(self.now() if self.now else None),
                         "live": {"available": w.live.available(), "source": "Yahoo Finance (up to 15 minutes delayed)" if w.live.available() else None,
                                  "instruction_session": w.instruction_session(self.now() if self.now else None)},
                         "quote_updates": w.quote_updates(self.now() if self.now else None),
                         # invented desks (private equity deal flow, investment banking mandates) run only where the market itself is simulated
                         "made_up_desks": getattr(w, "market_source", "SIMULATED") != "REAL",
                         "treasury": self._treasury_state(w),
                         "real_market": ({"latest_close": (w.market.real_feed.latest_date() if w.market.real_feed else None),
                                          "next_session": w.calendar.next_business_day(w.current_date).isoformat(),
                                          "waiting": w.real_market_ready(w.calendar.next_business_day(w.current_date))} if getattr(w, "market_source", "SIMULATED") == "REAL" else None),
                         "save_version": w.save_version, "engine_version": ENGINE_VERSION, "migration": w.migration, "replay_errors": w.replay_errors,
                         "integrity": w.integrity,
                         "portfolios": [{"id": p.id, "name": p.name, "type": p.portfolio_type, "realism": p.realism, "mode": p.mode, "benchmark": p.benchmark,
                                         "job": p.job, "job_title": JOBS[p.job].title if p.job in JOBS else p.job, "level_title": w.careers.level_title(p)}
                                        for p in w.portfolios.values()],
                         "settlement_cycles": w.settlement_config.cycles, "policy_rate": w.market.curve().policy_rate,
                         "clock": {"mode": w.clock.mode, "timezone": w.clock.timezone, "update_time": w.clock.update_time, "lock_session": w.clock.lock_session,
                                   "next_update": nu.isoformat() if nu else None, "now": now_utc().isoformat(),
                                   "weekday": w.current_date.strftime("%A")},
                         "vol_index": w.market.vol_index()})

    def jobs(self) -> List[Dict]:
        return [{"key": j.key, "title": j.title, "description": j.description, "capital": float(j.capital), "benchmark": j.benchmark,
                 "allowed_classes": sorted(j.allowed_classes), "max_gross_leverage": j.max_gross_leverage, "max_position_pct": j.max_position_pct,
                 "max_drawdown": j.max_drawdown, "ladder": list(j.ladder), "status": j.status} for j in JOBS.values() if j.status != "AI"]

    # ------------------------------------------------------------------ commands
    def create_portfolio(self, world_id: str, name: str, portfolio_type: str, capital: float, realism: str, mode: str, benchmark: Optional[str],
                         job: str = "SANDBOX", from_treasury: bool = False) -> Dict:
        """A new book. `from_treasury`: its capital is drawn from the treasury (blank capital = the job's standard, capped by what the
        treasury holds; an empty treasury gives an empty book); otherwise the capital is fresh money contributed to the book."""
        w = self.world(world_id)
        if job not in JOBS:
            raise CommandError(f"unknown job {job}")
        bench = benchmark if job == "SANDBOX" else JOBS[job].benchmark
        ptype = World.TREASURY_TYPE if str(portfolio_type).upper() == World.TREASURY_TYPE else (portfolio_type if job == "SANDBOX" else job)
        if ptype == World.TREASURY_TYPE and w.treasury_book() is not None:
            raise CommandError("this save already has a Treasury book")
        if from_treasury and ptype != World.TREASURY_TYPE:
            tb = w.treasury_book()
            have = w.spare_cash(tb, "USD") if tb else ZERO
            cap = min(_capital(capital, job), have) if capital not in (None, "") else min(_capital(None, job), have)
            pf = w.create_portfolio(name, ptype, D(0), "USD", bench, realism, mode, job)
            if cap > 0:
                w.allocate_from_treasury(pf.id, "USD", cap)
            return {"portfolio_id": pf.id, "allocated": float(cap)}
        cap = D(0) if capital == 0 else _capital(capital, job)                 # an explicit zero is an empty book (a Treasury to be funded later)
        pf = w.create_portfolio(name, ptype, cap, "USD", bench, realism, mode, job)
        return {"portfolio_id": pf.id, "allocated": 0.0}

    def contribute(self, world_id: str, portfolio_id: str, amount: float, currency: str = "USD") -> Dict:
        w = self.world(world_id)
        ev = w.contribute_capital(portfolio_id, currency, D(str(amount)))
        return {"event_id": ev.id}

    def place_order(self, world_id: str, portfolio_id: str, security_id: str, side: str, quantity: float, order_type: str = "MARKET",
                    limit_price: Optional[float] = None, stop_price: Optional[float] = None, time_in_force: str = "DAY", strategy_tag: Optional[str] = None,
                    trail_pct: Optional[float] = None, condition: Optional[Dict] = None, settle_ccy: Optional[str] = None,
                    execution: Optional[str] = None) -> Dict:
        """An instruction for the next update, or with execution LIVE an immediate fill at the latest real quote. With
        `settle_ccy` another currency pays for it (a buy) or receives its proceeds (a sell): the spot conversion is dealt
        alongside (at the live rate for a live ticket) and settles T+2, so the cash is there when the trade settles."""
        w = self.world(world_id)
        self._open_for_instructions(w)
        pf = w.portfolio(portfolio_id)
        sec = w.security(security_id)
        execution = (execution or "NEXT_UPDATE").upper()
        fx_trade = None
        settle = (settle_ccy or "").upper() or None
        at = self.now() if self.now else None
        try:
            if settle and settle != (sec.currency or pf.base_currency):
                pv = self.order_preview(world_id, portfolio_id, {"security_id": security_id, "side": side, "quantity": quantity, "limit_price": limit_price, "settle_ccy": settle, "execution": execution})
                fx = pv.get("fx")
                need = D(str(pv["cash_needed"]))
                if fx and need > 0:                               # a buy paid with another currency: buy the security's currency first
                    fx_trade = w.fx_spot(portfolio_id, sec.currency, settle, need, "BUY", execution)
            o = w.place_order(portfolio_id, security_id, side, D(str(quantity)), order_type,
                              D(str(limit_price)) if limit_price is not None else None,
                              D(str(stop_price)) if stop_price is not None else None, time_in_force, strategy_tag,
                              float(trail_pct) if trail_pct else None, condition, execution, at)
            if settle and settle != (sec.currency or pf.base_currency) and fx_trade is None:
                if o.filled_quantity > 0:                          # a live sale: convert what it actually raised
                    need = -(o.avg_fill_price * o.filled_quantity * w.trading._unit(sec))
                else:
                    pv = self.order_preview(world_id, portfolio_id, {"security_id": security_id, "side": side, "quantity": quantity, "limit_price": limit_price, "settle_ccy": settle, "execution": execution})
                    need = D(str(pv["cash_needed"]))
                if need < 0:                                      # a sale whose proceeds go into another currency: sell them forward into it at spot
                    fx_trade = w.fx_spot(portfolio_id, settle, sec.currency, -need, "SELL", execution)
        finally:
            w.flush()
        out = self.order(world_id, portfolio_id, o.id)
        out["fx"] = jsonable(asdict(fx_trade)) if fx_trade is not None else None
        out["execution"] = execution
        out["trade"] = jsonable(asdict(pf.trades[o.trade_ids[-1]])) if o.trade_ids else None
        return out

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
            if sec.is_option or (sec.is_future and (sec.expired or not w.market.history.get(sec.id))):
                continue
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
                   "coupon": sec.coupon, "maturity": sec.maturity, "is_bond": bool(sec.is_bond), "is_future": sec.is_future, "underlying": sec.underlying, "issuer": sec.issuer,
                   "is_index": sec.asset_class == "INDEX", "qty_step": sec.qty_step, "yahoo": sec.yahoo,
                   "underlying_class": sec.underlying_class, "contract_month": sec.contract_month, "multiplier": sec.multiplier, "tick_size": sec.tick_size,
                   "expiry": sec.expiry, "unit": sec.unit}
            if sec.is_future:
                row["initial_margin"] = w.futures.initial_margin_per_contract(sec)
                row["notional_per_contract"] = float(bar.close) * sec.multiplier
            if sec.is_bond:
                row.update({k: v for k, v in BondPricer.risk_metrics(sec, w.current_date, float(bar.close), w.market.curve()).items()})
            out.append(row)
        return jsonable(out)

    def security(self, world_id: str, security_id: str, period: str = "1Y") -> Dict:
        w = self.world(world_id)
        if security_id in w.securities and w.securities[security_id].is_option:
            return self.option_contract(world_id, security_id)
        if security_id not in w.securities:
            raise NotFound(f"security {security_id} not found")
        sec = w.securities[security_id]
        h = w.market.history.get(sec.id) or []
        if not h:
            raise NotFound(f"no market data for {security_id}")
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
        if sec.is_future:
            spec = SPEC_BY_CODE[sec.underlying]
            out["futures"] = {"spec": {"code": spec.code, "name": spec.name, "group": spec.group, "unit": spec.unit, "multiplier": spec.multiplier,
                                       "tick": spec.tick, "tick_value": spec.tick * spec.multiplier, "margin_pct": spec.margin_pct},
                              "initial_margin": w.futures.initial_margin_per_contract(sec), "margin_multiplier": w.futures.margin_multiplier(),
                              "notional_per_contract": float(bar.close) * sec.multiplier, "expiry": sec.expiry, "contract_month": sec.contract_month,
                              "days_to_expiry": w.calendar.business_days_between(w.current_date, date.fromisoformat(sec.expiry)),
                              "spot": w.market.spot(sec.underlying), "curve": w.market.curve_for(sec.underlying)}
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

    def commodities(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        out = []
        for spec in COMMODITY_SPECS:
            sh = w.market.commodities.spot_history.get(spec.code) or []
            if not sh:
                continue
            spot, prev = sh[-1][1], (sh[-2][1] if len(sh) > 1 else sh[-1][1])
            curve = w.market.curve_for(spec.code)
            months = list(curve.keys())
            front = w.market.front_contract(spec.code)
            st = w.market.commodities.state.get(spec.code)
            shape = "flat"
            if len(months) >= 2:
                shape = "backwardation" if curve[months[-1]] < curve[months[0]] * 0.995 else "contango" if curve[months[-1]] > curve[months[0]] * 1.005 else "flat"
            wk = sh[-6][1] if len(sh) > 6 else sh[0][1]
            mo = sh[-22][1] if len(sh) > 22 else sh[0][1]
            out.append({"code": spec.code, "name": spec.name, "group": spec.group, "unit": spec.unit, "spot": spot, "change_pct": (spot / prev - 1) if prev else 0.0,
                        "week_pct": (spot / wk - 1) if wk else 0.0, "month_pct": (spot / mo - 1) if mo else 0.0,
                        "front": front.id if front else None, "front_price": curve.get(front.contract_month) if front else None,
                        "curve_shape": shape, "curve_slope_pct": (curve[months[-1]] / curve[months[0]] - 1) if len(months) >= 2 else 0.0,
                        "inventory_z": st.inventory_z if st else None, "demand_z": st.demand_z if st else None, "supply_shock": st.supply_shock if st else None,
                        "conv_yield": st.conv_yield if st else None, "report": spec.report_name or None, "multiplier": spec.multiplier,
                        "listed": len(months), "vol": spec.vol})
        return jsonable(out)

    def commodity(self, world_id: str, code: str) -> Dict:
        w = self.world(world_id)
        if code not in SPEC_BY_CODE:
            raise NotFound(f"unknown commodity {code}")
        spec = SPEC_BY_CODE[code]
        sh = w.market.commodities.spot_history.get(code) or []
        ch = w.market.commodities.curve_history.get(code) or []
        cur = ch[-1][1] if ch else {}
        prev5 = ch[-6][1] if len(ch) > 6 else cur
        prev22 = ch[-23][1] if len(ch) > 23 else cur
        contracts = []
        for sec in sorted((s for s in w.securities.values() if s.is_future and s.underlying == code and not s.expired and w.market.history.get(s.id)), key=lambda s: s.contract_month):
            bar = w.market.last_bar(sec.id)
            h = w.market.history[sec.id]
            pc = h[-2].close if len(h) > 1 else bar.close
            contracts.append({"id": sec.id, "contract_month": sec.contract_month, "expiry": sec.expiry, "last": bar.close, "change": bar.close - pc,
                              "bid": bar.bid, "ask": bar.ask, "volume": bar.volume, "initial_margin": w.futures.initial_margin_per_contract(sec),
                              "notional": float(bar.close) * sec.multiplier,
                              "days_to_expiry": w.calendar.business_days_between(w.current_date, date.fromisoformat(sec.expiry))})
        st = w.market.commodities.state.get(code)
        fundamentals = None
        if st:
            fundamentals = {"inventory_z": st.inventory_z, "demand_z": st.demand_z, "supply_shock": st.supply_shock, "conv_yield": st.conv_yield,
                            "storage_cost": spec.storage, "seasonal_amp": spec.season_amp, "seasonal_peak_month": spec.season_peak_month,
                            "demand_beta": spec.demand_beta, "market_beta": spec.mkt_beta, "event_probability_daily": spec.event_p, "report": spec.report_name or None,
                            "regime_growth": w.market.regime().growth,
                            "narrative": self._commodity_narrative(spec, st)}
        return jsonable({"spec": {"code": spec.code, "name": spec.name, "group": spec.group, "unit": spec.unit, "multiplier": spec.multiplier, "tick": spec.tick,
                                  "tick_value": spec.tick * spec.multiplier, "margin_pct": spec.margin_pct, "months": spec.months, "expiry_rule": spec.expiry_rule},
                         "spot": sh[-1][1] if sh else None, "spot_history": sh[-260:], "curve": cur, "curve_5d_ago": prev5, "curve_1m_ago": prev22,
                         "contracts": contracts, "fundamentals": fundamentals,
                         "curve_history": [{"date": d, "front": list(c.values())[0] if c else None, "back": list(c.values())[-1] if c else None} for d, c in ch[-260:]],
                         "news": [jsonable(asdict(n)) for n in w.news if code in n.refs][-15:], "margin_multiplier": w.futures.margin_multiplier()})

    def _commodity_narrative(self, spec, st) -> str:
        inv = "tight" if st.inventory_z < -0.7 else "ample" if st.inventory_z > 0.7 else "near normal"
        dem = "strong" if st.demand_z > 0.3 else "weak" if st.demand_z < -0.3 else "steady"
        sup = "constrained" if st.supply_shock > 0.5 else "abundant" if st.supply_shock < -0.5 else "unremarkable"
        curve = "backwardated" if st.conv_yield > spec.storage + 0.04 else "in contango" if st.conv_yield < spec.storage else "roughly flat"
        return f"Inventories are {inv} ({st.inventory_z:+.2f}σ), demand is {dem}, supply is {sup}; the curve is {curve} (convenience yield {st.conv_yield:.1%})."

    def briefing(self, world_id: str, portfolio_id: str, day: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        if not pf.briefings:
            return {"available": False}
        b = pf.briefings[-1] if not day else next((x for x in pf.briefings if x["date"] == day), None)
        if b is None:
            raise NotFound(f"no briefing for {day}")
        s = w.pnl.compute_summary(pf)
        nu = w.next_update_at()
        return jsonable({"available": True, **b, "weekday": date.fromisoformat(b["date"]).strftime("%A"), "dates": [x["date"] for x in pf.briefings],
                         "live_nav": s["nav"], "next_update": nu.isoformat() if nu else None, "clock_mode": w.clock.mode,
                         "positions_count": sum(1 for p in pf.positions.values() if p.quantity), "level_title": w.careers.level_title(pf),
                         "job": JOBS[pf.job].title if pf.job in JOBS else pf.job})

    def career(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        job = JOBS.get(pf.job)
        s = w.pnl.compute_summary(pf)
        nav = s["nav"]
        peak = max(pf.peak_nav, nav)
        # live metrics since inception
        rets = []
        prev = None
        for snap in pf.nav_history:
            if prev:
                rets.append(float(snap.day_pnl / prev))
            prev = snap.nav
        import math as _m
        mean = sum(rets) / len(rets) if rets else 0.0
        sd = _m.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)) if len(rets) > 1 else 0.0
        sharpe = ((mean - w.market.curve().policy_rate / 252) / sd * _m.sqrt(252)) if sd > 0 else 0.0
        dd_peak, max_dd = D(0), 0.0
        for snap in pf.nav_history:
            dd_peak = max(dd_peak, snap.nav)
            max_dd = max(max_dd, float((dd_peak - snap.nav) / dd_peak) if dd_peak else 0.0)
        bench = w.careers._benchmark_return(pf, pf.created, w.current_date.isoformat()) if pf.nav_history else None
        ret = float((nav - pf.contributed_capital) / pf.contributed_capital) if pf.contributed_capital else 0.0
        return jsonable({"job": {"key": job.key, "title": job.title, "description": job.description, "benchmark": job.benchmark, "allowed_classes": sorted(job.allowed_classes),
                                 "max_gross_leverage": job.max_gross_leverage, "max_position_pct": job.max_position_pct, "max_drawdown": job.max_drawdown,
                                 "ladder": list(job.ladder)} if job else None,
                         "level": pf.level, "level_title": w.careers.level_title(pf), "contributed_capital": pf.contributed_capital,
                         "metrics": {"return_since_inception": ret, "benchmark_return": bench, "alpha": (ret - bench) if bench is not None else None,
                                     "sharpe": sharpe, "max_drawdown": max_dd, "current_drawdown": float((peak - nav) / peak) if peak else 0.0,
                                     "gross_leverage": float(s["leverage"]), "days": len(pf.nav_history), "volatility": sd * _m.sqrt(252)},
                         "limits_status": {"gross_leverage": {"value": float(s["leverage"]), "limit": job.max_gross_leverage if job else None},
                                           "max_position": {"value": max([float(abs(p.notional if p.is_future else p.market_value) / nav) for p in pf.positions.values()] or [0.0]) if nav else 0.0,
                                                            "limit": job.max_position_pct if job else None},
                                           "drawdown": {"value": float((peak - nav) / peak) if peak else 0.0, "limit": job.max_drawdown if job else None}},
                         "reviews": list(reversed(pf.reviews)), "breaches": list(reversed(pf.breaches[-50:])), "career_log": list(reversed(pf.career_log)),
                         "margin_calls": [asdict(m) for m in reversed(pf.margin_calls)], "missions": pf.missions,
                         "scenario": w.scenario, "scenario_log": w.scenario_log})

    # ------------------------------------------------------------------ phase 8/10: the job's desk
    def desk(self, world_id: str, portfolio_id: str) -> Dict:
        """Everything specific to the portfolio's job: investors, client requests, lending desk, corporate treasury, AI-desk oversight."""
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        out: Dict = {"job": pf.job, "title": JOBS[pf.job].title if pf.job in JOBS else pf.job, "missions": pf.missions, "date": w.current_date}
        if w.investors.applies(pf):
            out["investors"] = w.investors.book(pf)
        if w.clients.applies(pf):
            reqs = sorted(pf.client_rfqs.values(), key=lambda r: (r["date"], r["id"]), reverse=True)
            out["clients"] = {"open": [r for r in reqs if r["status"] in ("OPEN", "QUOTED")], "history": [r for r in reqs if r["status"] not in ("OPEN", "QUOTED")][:60],
                              "stats": pf.client_stats}
        if pf.job in ("SEC_LENDING", "SANDBOX", "HEDGE_FUND", "PORTFOLIO_MANAGER", "FIXED_INCOME_PM"):
            out["lending"] = w.lenddesk.book(pf)
        if w.treasury.applies(pf):
            out["treasury"] = w.treasury.dashboard(pf)
        if pf.job == "RISK_MANAGER":
            out["oversight"] = w.institutions.oversight(pf)
        return jsonable(out)

    def quote_client(self, world_id: str, portfolio_id: str, rfq_id: str, level: Optional[float], pass_: bool) -> Dict:
        w = self.world(world_id)
        return jsonable(w.quote_client(portfolio_id, rfq_id, None if level is None else float(level), bool(pass_)))

    def lend_out(self, world_id: str, portfolio_id: str, security_id: str, quantity) -> Dict:
        w = self.world(world_id)
        return jsonable(w.lend_out(portfolio_id, security_id, D(str(quantity))))

    def recall_lent(self, world_id: str, portfolio_id: str, lend_id: str, quantity=None) -> Dict:
        w = self.world(world_id)
        return jsonable(w.recall_lent(portfolio_id, lend_id, None if quantity in (None, "") else D(str(quantity))))

    def decide_request(self, world_id: str, portfolio_id: str, desk_id: str, request_id: str, approve: bool, note: str = "") -> Dict:
        w = self.world(world_id)
        return jsonable(w.decide_request(portfolio_id, desk_id, request_id, bool(approve), note or ""))

    def set_desk_limit(self, world_id: str, portfolio_id: str, desk_id: str, key: str, value) -> Dict:
        w = self.world(world_id)
        return jsonable(w.set_desk_limit(portfolio_id, desk_id, key, float(value)))

    def force_reduce(self, world_id: str, portfolio_id: str, desk_id: str, security_id: str, fraction) -> Dict:
        w = self.world(world_id)
        return jsonable(w.force_reduce(portfolio_id, desk_id, security_id, float(fraction)))

    def news(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        return jsonable([asdict(n) for n in reversed(w.news[-100:])])

    def corporate_actions(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        return jsonable(sorted([asdict(c) for c in w.corporate_actions.values()], key=lambda x: x["ex_date"]))

    # ------------------------------------------------------------------ portfolio
    # ------------------------------------------------------------------ the treasury
    def _treasury_state(self, w) -> Dict:
        tb = w.treasury_book()
        bal = {c: a.balance for c, a in tb.cash.items()} if tb else {}
        for c, v in w.treasury_cash.items():                               # a legacy pool, if the save has one
            bal[c] = bal.get(c, ZERO) + v
        return {"book_id": tb.id if tb else None, "balances": bal, "total_usd": bal.get("USD", ZERO), "spare_usd": (w.spare_cash(tb, "USD") if tb else w.treasury_cash.get("USD", ZERO))}

    def treasury(self, world_id: str) -> Dict:
        w = self.world(world_id)
        books = [{"id": p.id, "name": p.name, "type": p.portfolio_type, "cash": {c: a.balance for c, a in p.cash.items()}, "spare_usd": w.spare_cash(p, "USD")} for p in w.portfolios.values()]
        return jsonable({**self._treasury_state(w), "books": books, "log": w.treasury_log[-40:]})

    def treasury_command(self, world_id: str, action: str, body: Dict) -> Dict:
        w = self.world(world_id)
        ccy = str(body.get("currency", "USD")).upper()
        amt = D(str(body["amount"]))
        if action == "fund":
            w.fund_treasury(ccy, amt, str(body.get("note", "capital into the treasury")))
        elif action == "allocate":
            w.allocate_from_treasury(body["portfolio_id"], ccy, amt)
        elif action == "return":
            w.return_to_treasury(body["portfolio_id"], ccy, amt)
        else:
            raise NotFound(f"unknown treasury action {action}")
        return self.treasury(world_id)

    def delete_portfolio(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        name = pf.name
        w.delete_portfolio(portfolio_id)
        self.log.info("world %s: book %s (%s) deleted", world_id, portfolio_id, name)
        return {"deleted": portfolio_id, "name": name, "portfolios": [{"id": p.id, "name": p.name} for p in w.portfolios.values()], "treasury": jsonable(self._treasury_state(w))}

    def balance_sheet_rows(self, w, pf) -> List[Dict]:
        """A book's balance sheet from the ledger and the marks: assets, liabilities, equity, and the NAV that ties them."""
        s = w.pnl.compute_summary(pf)
        led = w.ledgers[pf.id]
        rows = []
        def add(group, line, amount, note=""):
            v = money(D(str(amount)))
            if v or line in ("Cash", "Contributed capital", "Net asset value"):
                rows.append({"group": group, "line": line, "amount": v, "note": note})
        for c, a in sorted(pf.cash.items()):
            add("Assets", f"Cash {c}", a.base_value, f"{c} {a.balance:,.2f}" if c != pf.base_currency else "")
        add("Assets", "Securities at market (long)", s["market_value"] - s.get("short_market_value", ZERO), "stocks, ETFs, bonds, coins, long options at the mark")
        add("Assets", "Receivables", s["receivables"], "sales awaiting settlement, dividends and coupons due")
        add("Assets", "Margin deposits", s["margin_deposits"], "initial margin at the clearing broker")
        add("Assets", "Collateral posted", s["collateral_posted"], "cash margin against repo, borrows and OTC")
        add("Assets", "Reverse repo", s["reverse_repo"], "cash lent against collateral")
        add("Assets", "FX forwards (MTM)", s["fx_forwards"], "")
        add("Assets", "OTC derivatives (positive PV)", s["otc_assets"], "")
        add("Assets", "Private credit", s.get("private_credit", ZERO), "loans at the mark plus accrued interest")
        add("Assets", "Private equity", s.get("private_equity", ZERO), "portfolio companies at the mark")
        add("Assets", "Underwriting positions", s.get("underwriting", ZERO), "blocks held for resale")
        add("Assets", "Operating assets", s.get("operating_assets", ZERO), "")
        add("Liabilities", "Securities sold short (at market)", -s.get("short_market_value", ZERO), "shorts and written options at the mark")
        add("Liabilities", "Payables", s["payables"], "purchases awaiting settlement, fees, rebates")
        add("Liabilities", "Repo borrowing", s["repo_borrowing"], "")
        add("Liabilities", "Prime-broker loan", s["margin_loan"], "")
        add("Liabilities", "OTC derivatives (negative PV)", s["otc_liabilities"], "")
        add("Liabilities", "Variation margin received", s.get("vm_received", ZERO), "")
        add("Liabilities", "Collateral received", sum((D(str(r["value"])) for r in pf.collateral_received.values()), ZERO), "securities lent")
        add("Equity", "Contributed capital", pf.contributed_capital, "capital put in, net of what went back to the Treasury")
        add("Equity", "Retained P&L", s["nav"] - pf.contributed_capital, "realized and unrealized since inception")
        add("Equity", "Net asset value", s["nav"], "assets less liabilities")
        return rows

    def overall(self, world_id: str) -> Dict:
        """The whole save as one big book: every book's NAV and P&L side by side, positions added up across books
        (with the split by book), cash by currency, and the day's P&L by bucket summed."""
        w = self.world(world_id)
        books, positions, cash, explain = [], {}, {}, {}
        tot = {"nav": ZERO, "day_pnl": ZERO, "mtd_pnl": ZERO, "ytd_pnl": ZERO, "since_inception_pnl": ZERO, "contributed": ZERO, "gross_exposure": ZERO, "net_exposure": ZERO,
               "long_exposure": ZERO, "short_exposure": ZERO, "unrealized": ZERO, "realized": ZERO, "cash_base": ZERO}
        for pf in w.portfolios.values():
            d = self.dashboard(world_id, pf.id)
            s = w.pnl.compute_summary(pf)
            row = {"id": pf.id, "name": pf.name, "type": pf.portfolio_type, "job": pf.job, "level_title": d["level_title"], "benchmark": pf.benchmark, "created": pf.created,
                   "nav": d["nav"], "day_pnl": d["day_pnl"], "mtd_pnl": d["mtd_pnl"], "ytd_pnl": d["ytd_pnl"], "since_inception_pnl": d["since_inception_pnl"],
                   "return_since_inception": d["return_since_inception"], "contributed": pf.contributed_capital, "cash_base": s["cash_base"], "gross_exposure": d["gross_exposure"],
                   "net_exposure": d["net_exposure"], "long_exposure": d["long_exposure"], "short_exposure": d["short_exposure"], "leverage": d["leverage"],
                   "unrealized": d["unrealized"], "realized": d["realized"], "positions": len(d["positions"]), "working_orders": len(d["working_orders"]),
                   "margin_calls": len(d["margin_calls"]), "private_credit": s.get("private_credit", ZERO), "private_equity": s.get("private_equity", ZERO)}
            books.append(row)
            for k in ("nav", "day_pnl", "mtd_pnl", "ytd_pnl", "since_inception_pnl", "gross_exposure", "net_exposure", "long_exposure", "short_exposure", "unrealized", "realized", "cash_base"):
                tot[k] += D(str(row[k]))
            tot["contributed"] += pf.contributed_capital
            for p in d["positions"]:
                sid = p["security_id"]
                agg = positions.setdefault(sid, {"security_id": sid, "name": p.get("name"), "asset_class": p.get("asset_class"), "sector": p.get("sector"), "quantity": ZERO,
                                                 "market_value": ZERO, "unrealized": ZERO, "day_pnl": ZERO, "books": []})
                q, mv, ur, dp = D(str(p.get("quantity", 0))), D(str(p.get("market_value", 0))), D(str(p.get("unrealized_pnl", p.get("unrealized", 0)) or 0)), D(str(p.get("day_pnl", 0) or 0))
                agg["quantity"] += q; agg["market_value"] += mv; agg["unrealized"] += ur; agg["day_pnl"] += dp
                agg["books"].append({"book": pf.name, "portfolio_id": pf.id, "quantity": q, "market_value": mv, "unrealized": ur})
                if p.get("mark") is not None:
                    agg["mark"] = p["mark"]
            for c, a in pf.cash.items():
                cash.setdefault(c, {"settled": ZERO, "base_value": ZERO})
                cash[c]["settled"] += a.balance; cash[c]["base_value"] += a.base_value
            if pf.nav_history:
                for k, v in (pf.nav_history[-1].explain or {}).items():
                    if not k.startswith("_"):
                        explain[k] = explain.get(k, ZERO) + D(str(v))
        rows = sorted(positions.values(), key=lambda r: -abs(float(r["market_value"])))
        by_class: Dict[str, Dict] = {}
        for r in rows:
            g = by_class.setdefault(r["asset_class"] or "OTHER", {"market_value": ZERO, "count": 0})
            g["market_value"] += r["market_value"]; g["count"] += 1
        ts = self._treasury_state(w)
        legacy = sum((v for v in w.treasury_cash.values()), ZERO)
        tot["treasury"] = ts["total_usd"]
        tot["nav_with_treasury"] = tot["nav"] + legacy                     # the Treasury book is already one of the books
        capital_total = tot["contributed"] + legacy
        tot["return_since_inception"] = float(tot["nav_with_treasury"] / capital_total - 1) if capital_total else 0.0
        for b in books:
            b["is_treasury"] = b["id"] == ts["book_id"]
        # the balance sheet: the Treasury book on its own, and every book consolidated, line by line
        tb = w.treasury_book()
        cons: Dict[str, Dict] = {}
        for pf in w.portfolios.values():
            for r in self.balance_sheet_rows(w, pf):
                key = (r["group"], r["line"])
                c = cons.setdefault(key, {"group": r["group"], "line": r["line"], "amount": ZERO, "note": r["note"]})
                c["amount"] += r["amount"]
        treasury_rows = {(r["group"], r["line"]): r for r in (self.balance_sheet_rows(w, tb) if tb else [])}
        order = {"Assets": 0, "Liabilities": 1, "Equity": 2}
        bs = []
        for key, c in sorted(cons.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1] == "Net asset value", not kv[0][1].startswith("Cash"), kv[0][1])):
            bs.append({"group": c["group"], "line": c["line"], "treasury": treasury_rows.get(key, {}).get("amount", ZERO), "all_books": c["amount"], "note": c["note"]})
        allocated = []
        for pf in w.portfolios.values():
            if tb and pf.id != tb.id:
                drawn = sum((l["amount"] for l in w.treasury_log if l.get("portfolio_id") == pf.id and l["kind"] == "TO_BOOK"), ZERO)
                back = sum((l["amount"] for l in w.treasury_log if l.get("portfolio_id") == pf.id and l["kind"] == "TO_TREASURY"), ZERO)
                allocated.append({"book": pf.name, "portfolio_id": pf.id, "drawn": drawn, "returned": back, "net": drawn - back, "nav": next((b["nav"] for b in books if b["id"] == pf.id), ZERO)})
        return jsonable({"world": {"id": w.id, "name": w.name, "date": w.current_date, "market_source": getattr(w, "market_source", "SIMULATED")}, "totals": tot, "books": books,
                         "positions": rows, "cash": cash, "explain": explain, "by_asset_class": by_class, "treasury": ts, "balance_sheet": bs, "allocated": allocated, "treasury_log": w.treasury_log[-40:]})

    def dashboard(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        s = w.pnl.compute_summary(pf)
        last = pf.nav_history[-1] if pf.nav_history else None
        # Once-per-day world: "today" is the last processed session, plus anything that moved NAV since its snapshot.
        live_delta = s["nav"] - (last.nav if last else pf.contributed_capital) - pf.day_capital_flows
        day_pnl_live = (last.day_pnl if last else ZERO) + live_delta
        positions = self._positions(w, pf)
        largest = max(positions, key=lambda p: abs(p["market_value"]), default=None)
        upcoming_settle = [jsonable(asdict(si)) for si in pf.settlements.values() if si.status in ("PENDING", "MATCHED", "FAILED")]
        upcoming_settle.sort(key=lambda x: x["settlement_date"])
        cashflows = self._upcoming_cash_flows(w, pf)
        ytd = mtd = ZERO
        if pf.nav_history:
            y = w.current_date.year
            m = w.current_date.month
            ytd = sum((x.day_pnl for x in pf.nav_history if x.date >= f"{y}-01-01"), ZERO) + live_delta
            mtd = sum((x.day_pnl for x in pf.nav_history if x.date >= f"{y}-{m:02d}-01"), ZERO) + live_delta
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
            "leverage": s["leverage"], "margin_used": s["margin_deposits"] + s["margin_loan"], "available_liquidity": w.prime.financing(pf)["excess_liquidity"],
            "collateral_posted": s["margin_deposits"] + s["collateral_posted"] + sum((w.collateral.market_value(w.securities[p.security_id], p.quantity) for p in pf.pledges), ZERO),
            "collateral_received": sum((D(str(r["value"])) for r in pf.collateral_received.values()), ZERO),
            "margin_calls": [asdict(c) for c in pf.collateral_calls.values() if c.status == "OPEN"], "futures_long": s["futures_long"], "futures_short": s["futures_short"],
            "repo_borrowing": s["repo_borrowing"], "margin_loan": s["margin_loan"], "short_market_value": s["short_market_value"], "collateral_posted_total": s["collateral_posted"],
            "job": pf.job, "level_title": w.careers.level_title(pf), "clock_mode": w.clock.mode,
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
            k = p.get(key) or "—"
            g[k] = g.get(k, 0.0) + float(p["notional"]) * (1 if p["quantity"] >= 0 else -1)
        return g

    def _upcoming_cash_flows(self, w: World, pf) -> List[Dict]:
        out = []
        for si in pf.settlements.values():
            if si.status in ("PENDING", "MATCHED", "FAILED"):
                out.append({"date": si.settlement_date, "kind": "SETTLEMENT", "amount": -si.cash_amount if si.instruction_type == "RVP" else si.cash_amount,
                            "currency": si.currency, "ref": f"{si.id} {si.security_id}"})
        for ca in w.corporate_actions.values():
            ent = ca.entitlements.get(pf.id)
            if ent and ent.get("amount") is not None and not ent.get("paid"):
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
        nav = w.pnl.compute_summary(pf)["nav"]
        last_snap = pf.nav_history[-1] if pf.nav_history else None
        for pos in pf.positions.values():
            if pos.quantity == 0 and pos.settled_quantity == 0 and pos.pending_deliver == 0 and pos.pending_receive == 0 and not pos.trade_ids:
                continue
            sec = w.securities[pos.security_id]
            day_row = last_snap.by_position.get(sec.id, {}) if last_snap else {}
            if pos.is_future:
                bar = w.market.last_bar(sec.id) if w.market.history.get(sec.id) else None
                rows.append({"security_id": sec.id, "name": sec.name, "asset_class": "FUTURE", "sector": sec.sector, "currency": sec.currency, "country": sec.country,
                             "underlying": sec.underlying, "underlying_class": sec.underlying_class, "contract_month": sec.contract_month, "expiry": sec.expiry,
                             "quantity": pos.quantity, "settled_quantity": pos.quantity, "pending_receive": ZERO, "pending_deliver": ZERO,
                             "average_cost": pos.average_cost_future, "cost_basis": ZERO, "mark": pos.settlement_price, "market_value": ZERO, "notional": pos.notional, "multiplier": sec.multiplier,
                             "unrealized_pnl": ZERO, "realized_pnl": pos.variation_margin_total, "day_variation_margin": day_row.get("realized", ZERO),
                             "dividend_income": ZERO, "interest_income": ZERO, "commissions": pos.commissions, "accrued_interest": ZERO,
                             "weight": float(pos.notional / nav) if nav else 0.0, "beta": sec.beta, "risk": {"delta": 1.0, "notional": pos.notional,
                             "initial_margin": pos.initial_margin, "days_to_expiry": w.calendar.business_days_between(w.current_date, date.fromisoformat(sec.expiry)) if sec.expiry else None},
                             "lots": 0, "borrow_status": "n/a (cleared)", "collateral_status": f"initial margin {pos.initial_margin:,.0f} at clearing member",
                             "financing": "variation margin daily", "is_future": True, "multiplier": sec.multiplier})
                continue
            inst = Instrument(sec, pos.mark, w.current_date, w.market.curve())
            rm = inst.risk_metrics(pos.quantity) if pos.quantity else {}
            avg_cost = pos.average_cost * (100 if sec.is_bond else 1)   # bonds: price per 100 face
            if sec.is_option:
                n = float(pos.quantity) * sec.multiplier
                avg_cost = (pos.cost_basis / (pos.quantity * D(str(sec.multiplier)))).quantize(D("0.0001")) if pos.quantity else ZERO
                rm = {**{k: v * n for k, v in pos.greeks.items() if k in ("delta", "gamma", "vega", "theta", "rho")}, "iv": pos.greeks.get("iv"),
                      "underlying": pos.greeks.get("underlying"), "dollar_delta": pos.greeks.get("delta", 0.0) * n * pos.greeks.get("underlying", 0.0),
                      "days_to_expiry": (date.fromisoformat(sec.expiry) - w.current_date).days, "margin": pos.margin_requirement, "covered": pos.covered_by_shares}
            rows.append({"security_id": sec.id, "name": sec.name, "asset_class": sec.asset_class, "sector": sec.sector, "currency": sec.currency,
                         "country": sec.country, "quantity": pos.quantity, "settled_quantity": pos.settled_quantity, "pending_receive": pos.pending_receive,
                         "pending_deliver": pos.pending_deliver, "average_cost": avg_cost, "cost_basis": pos.cost_basis, "mark": pos.mark,
                         "market_value": pos.market_value, "notional": pos.market_value, "unrealized_pnl": pos.unrealized_pnl, "realized_pnl": pos.realized_pnl,
                         "day_pnl": day_row.get("total", ZERO),
                         "dividend_income": pos.dividend_income, "interest_income": pos.interest_income, "commissions": pos.commissions,
                         "accrued_interest": pos.accrued_interest, "weight": float(pos.market_value / nav) if nav else 0.0, "beta": sec.beta, "risk": rm, "lots": len(pos.lots),
                         "borrow_status": (f"short: {pos.borrowed_quantity:,} borrowed" if pos.quantity < 0 else f"{pos.borrowed_quantity:,} borrowed, unsold" if pos.borrowed_quantity else "n/a (long)"),
                         "collateral_status": (f"{pf.pledged_quantity(sec.id):,} pledged" if pf.pledged_quantity(sec.id) else "unencumbered"),
                         "financing": ("borrow fees" if pos.quantity < 0 else "none"), "is_future": False, "pledged": pf.pledged_quantity(sec.id),
                         "borrow_fees": pos.borrow_fees, "manufactured_dividends": pos.manufactured_dividends, "is_option": sec.is_option,
                         **({"option": {"underlying": sec.underlying, "type": sec.option_type, "strike": sec.strike, "expiry": sec.expiry, "style": sec.exercise_style,
                                        "settlement": sec.settlement_style, "multiplier": sec.multiplier, "deliverable": sec.deliverable}} if sec.is_option else {})})
            if sec.is_option:
                rows[-1]["borrow_status"] = "n/a (cleared option)"
                rows[-1]["financing"] = f"margin {pos.margin_requirement:,.0f}" if pos.quantity < 0 else "premium paid in full"
                rows[-1]["collateral_status"] = (f"covered by {pos.covered_by_shares * D(str(sec.multiplier)):,} shares" if pos.covered_by_shares else
                                                 ("margined at clearing member" if pos.quantity < 0 else "unencumbered"))
        rows.sort(key=lambda r: -abs(float(r["notional"])))
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
        if pos.is_future:
            relationships = [{"kind": "MARKET_EXPOSURE", "description": f"{'Long' if pos.quantity > 0 else 'Short'} {abs(pos.quantity):,} {sec.id}: notional {pos.notional:,.0f} ({sec.underlying} {sec.contract_month})"},
                             {"kind": "MARGIN", "description": f"Initial margin {pos.initial_margin:,.0f} posted at the clearing member; variation margin settles daily in cash"},
                             {"kind": "EXPIRY", "description": f"Last trade date {sec.expiry}: auto-closed at settlement unless rolled to a later month"}]
        else:
            relationships = [{"kind": "MARKET_EXPOSURE", "description": f"Long {pos.quantity:,} {sec.id}: beta-adjusted exposure {float(pos.market_value) * sec.beta:,.0f}"}]
        if sec.is_bond:
            relationships.append({"kind": "INTEREST_RATE_RISK", "description": f"DV01 {row['risk'].get('position_dv01', 0):,.2f} per bp (unhedged)"})
            if sec.spread_bps_credit:
                relationships.append({"kind": "CREDIT_RISK", "description": f"Credit spread {sec.spread_bps_credit:.0f}bp, rating {sec.rating}, no CDS hedge"})
        if pos.pending_receive or pos.pending_deliver:
            relationships.append({"kind": "SETTLEMENT", "description": f"Pending receive {pos.pending_receive:,}, pending deliver {pos.pending_deliver:,}"})
        loans = [asdict(l) for l in pf.loans.values() if l.security_id == security_id]
        loan_ids = {l["id"] for l in loans}
        collateral_moves = [asdict(m) for m in pf.cash_movements if m.kind in ("COLLATERAL", "BORROW_FEE", "BUY_IN_PENALTY", "MANUFACTURED_DIVIDEND")
                            and any(lid in m.reference for lid in loan_ids | {security_id})]
        pledges = [asdict(p) for p in pf.pledges if p.security_id == security_id]
        if pos.quantity < 0:
            st = w.market.lending.state.get(security_id)
            open_loans = [l for l in pf.loans.values() if l.security_id == security_id and l.status == "OPEN"]
            relationships = [{"kind": "SHORT_EXPOSURE", "description": f"Short {-pos.quantity:,} {sec.id}: market value {pos.market_value:,.0f}; average sale price {pos.average_cost:,.4f}"},
                             {"kind": "SECURITY_LOAN", "description": f"{sum((l.quantity for l in open_loans), ZERO):,} shares borrowed from {', '.join(sorted({l.lender for l in open_loans})) or '—'} at {(st.rate if st else 0):.2%} ({st.category if st else ''})"},
                             {"kind": "COLLATERAL", "description": f"Collateral posted {sum((l.collateral_amount if l.collateral_type == 'CASH' else l.collateral_value for l in open_loans), ZERO):,.0f} (marked daily at 102%)"},
                             {"kind": "BORROW_FEES", "description": f"Accrued borrow fees {sum((l.accrued_fee for l in open_loans), ZERO):,.2f}; paid to date {sum((l.fees_paid for l in pf.loans.values() if l.security_id == security_id), ZERO):,.2f}"},
                             {"kind": "DIVIDEND_OBLIGATIONS", "description": f"Manufactured dividends owed to date {pos.manufactured_dividends:,.2f}"}] +                             [{"kind": "RECALL", "description": f"RECALL — return {l.recall_quantity:,} shares by {l.recall_due}"} for l in open_loans if l.recall_status == "RECALLED"] +                             [r for r in relationships if r["kind"] == "SETTLEMENT"]
        if sec.is_option:
            g = pos.greeks or {}
            n = float(pos.quantity) * sec.multiplier
            side = "Long" if pos.quantity > 0 else "Short"
            relationships = [{"kind": "UNDERLYING", "description": f"{side} {abs(pos.quantity):,} {sec.underlying} {sec.expiry} {sec.strike:g} {'call' if sec.option_type == 'C' else 'put'} "
                                                                    f"({sec.exercise_style.lower()}, {sec.settlement_style.lower()}): delta {g.get('delta', 0) * n:,.0f} shares, "
                                                                    f"dollar delta {g.get('delta', 0) * n * g.get('underlying', 0):,.0f}, gamma {g.get('gamma', 0) * n:,.2f}, vega {g.get('vega', 0) * n:,.0f}/vol pt, theta {g.get('theta', 0) * n:,.0f}/day"},
                             {"kind": "EXPIRY", "description": f"Expires {sec.expiry} ({(date.fromisoformat(sec.expiry) - w.current_date).days} days): "
                                                               + ("cash-settled at the index level" if sec.settlement_style == "CASH" else f"delivers {sec.deliverable.get('quantity')} shares of {sec.underlying} per contract at {sec.strike:g}")},
                             {"kind": "MARGIN" if pos.quantity < 0 else "PREMIUM", "description": (f"Margin requirement {pos.margin_requirement:,.0f} at the clearing member"
                                                                                                 + (f"; {pos.covered_by_shares} contract(s) covered by long stock" if pos.covered_by_shares else "")) if pos.quantity < 0
                                                                                                 else f"Premium paid {pos.cost_basis:,.2f}; maximum loss is the premium"},
                             ] + [r for r in relationships if r["kind"] == "SETTLEMENT"]
            for st in pf.strategies.values():
                if any(l["security_id"] == sec.id for l in st.legs):
                    relationships.append({"kind": "STRATEGY", "description": f"Leg of {st.id} {st.strategy_type} ({st.status})"})
        if pledges:
            relationships.append({"kind": "ENCUMBRANCE", "description": "; ".join(f"{p['quantity']:,} pledged to {p['reference']} ({p['purpose']})" for p in pledges)})
        return jsonable({"position": row, "lots": [asdict(l) for l in pos.lots], "trades": trades, "settlements": sis, "ledger_entries": entries,
                         "dividends": divs, "custody_movements": custody, "daily_pnl": pnl_days, "relationships": relationships,
                         "loans": loans, "collateral_movements": collateral_moves, "pledges": pledges,
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
                     "pending_deliver": p.pending_deliver, "pledged": pf.pledged_quantity(p.security_id), "borrowed": p.borrowed_quantity,
                     "reserved_for_calls": w.options.covered_shares_needed(pf, p.security_id), "available": w.collateral.available_quantity(pf, p.security_id),
                     "failing": sum((si.quantity for si in pf.settlements.values() if si.security_id == p.security_id and si.status == "FAILED"), ZERO)}
                    for p in pf.positions.values() if p.settled_quantity or p.pending_receive or p.pending_deliver or p.quantity]
        return jsonable({"custodian": "BNY Mellon Asset Servicing", "account": pf.custody_account, "holdings": holdings,
                         "movements": [asdict(c) for c in reversed(pf.custody_movements)]})

    def cash(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        policy = w.market.curve().policy_rate
        accounts = [{"currency": c.currency, "settled_balance": c.balance, "accrued_interest": c.accrued_interest, "projected": w.trading.projected_cash(pf, c.currency),
                     "base_value": c.base_value, "deposit_rate": policy - 0.0025, "overdraft_rate": policy + 0.015} for c in pf.cash.values()]
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
        deltas = {a: led.balance(a) - D(prev_bal.get(a, 0)) for a in ["4000", "4100", "4200", "4300", "4400", "5000", "5100"]}
        rows = [{"account": a, "name": account_name(a), "change": (v if account_type(a) == "INCOME" else -v)} for a, v in deltas.items()]
        entries_since = [self._entry(e) for e in led.entries if (last is None or int(e.event_id[3:]) > int(last.event_id[3:]))
                         and any(l.account in ("4000", "4100", "4200", "4300", "4400", "5000", "5100") for l in e.lines)]
        s = w.pnl.compute_summary(pf)
        return jsonable({"nav": s["nav"], "since": last.date if last else None, "prev_nav": last.nav if last else pf.contributed_capital,
                         "capital_flows": pf.day_capital_flows, "components": rows, "entries": entries_since[-100:],
                         "composition": {"cash": s["cash"], "market_value": s["market_value"], "receivables": s["receivables"], "payables": s["payables"]}})

    # ------------------------------------------------------------------ phase 2: financing desks
    def seclending(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        market = [w.seclending.market_row(sec) for sec in w.securities.values() if sec.shares_outstanding and w.market.lending.state.get(sec.id)]
        for row in market:
            row.pop("history", None)
            mine = [l for l in pf.loans.values() if l.security_id == row["security_id"] and l.status == "OPEN"]
            row["my_borrowed"] = sum((l.quantity for l in mine), ZERO)
            row["my_collateral"] = sum((l.collateral_amount if l.collateral_type == "CASH" else l.collateral_value for l in mine), ZERO)
            row["my_accrued_fee"] = sum((l.accrued_fee for l in mine), ZERO)
            row["recall"] = any(l.recall_status == "RECALLED" for l in mine)
        market.sort(key=lambda r: (-float(r["my_borrowed"]), -r["rate"]))
        loans = [asdict(l) for l in sorted(pf.loans.values(), key=lambda l: l.loan_date, reverse=True)]
        locates = [asdict(l) for l in sorted(pf.locates.values(), key=lambda l: l.date, reverse=True)][:30]
        return jsonable({"market": market, "loans": loans, "locates": locates, "short_book": w.seclending.short_book(pf), "rebate_rate": w.seclending.rebate_rate(),
                         "treasuries": [s.id for s in w.securities.values() if s.asset_class == "GOVT_BOND"]})

    def lending_history(self, world_id: str, security_id: str) -> Dict:
        w = self.world(world_id)
        return jsonable({"security_id": security_id, "history": w.market.lending.history.get(security_id, [])[-260:]})

    def request_locate(self, world_id: str, portfolio_id: str, security_id: str, quantity: float) -> Dict:
        w = self.world(world_id)
        loc = w.request_locate(portfolio_id, security_id.upper(), D(str(quantity)))
        return jsonable(asdict(loc))

    def borrow(self, world_id: str, portfolio_id: str, locate_id: str, quantity: float, collateral_type: str = "CASH") -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        loan = w.borrow_securities(portfolio_id, locate_id, D(str(quantity)), collateral_type)
        return jsonable(asdict(loan))

    def return_loan(self, world_id: str, portfolio_id: str, loan_id: str, quantity: Optional[float] = None) -> Dict:
        w = self.world(world_id)
        loan = w.return_securities(portfolio_id, loan_id, D(str(quantity)) if quantity else None)
        return jsonable(asdict(loan))

    def repo_desk(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        book = w.repo.book(pf)
        eligible = []
        for pos in pf.positions.values():
            sec = w.securities[pos.security_id]
            if pos.quantity > 0 and not pos.is_future and w.collateral.eligible(sec, "REPO"):
                eligible.append({"security_id": sec.id, "name": sec.name, "available": w.collateral.available_quantity(pf, sec.id), "haircut": w.collateral.haircut(sec, "REPO"),
                                 "market_value_available": w.collateral.market_value(sec, w.collateral.available_quantity(pf, sec.id))})
        haircuts = {k: w.collateral.haircut(s, "REPO") for k, s in w.securities.items() if not s.is_future and w.collateral.eligible(s, "REPO")}
        from ..engines.collateral import HAIRCUTS
        return jsonable({**book, "eligible": eligible, "haircut_schedule": {k: v["REPO"] for k, v in HAIRCUTS.items()}, "regime_multiplier": w.collateral.regime_mult(),
                         "policy_rate": w.market.curve().policy_rate, "counterparties": __import__("finsim.engines.repo", fromlist=["COUNTERPARTIES"]).COUNTERPARTIES})

    def repo_quote(self, world_id: str, side: str, security_id: str, quantity: float, term_type: str = "OVERNIGHT", term_days: int = 1) -> Dict:
        w = self.world(world_id)
        return jsonable(w.repo_quote(side, security_id.upper(), D(str(quantity)), term_type, int(term_days)))

    def playbook(self, world_id: str, portfolio_id: str) -> Dict:
        return jsonable(self.world(world_id).playbook.catalogue())

    def playbook_preview(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        return jsonable(w.playbook.preview(w.portfolio(portfolio_id), body.get("key", ""), body.get("params") or {}))

    def playbook_execute(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        try:
            return jsonable(w.playbook.execute(w.portfolio(portfolio_id), body.get("key", ""), body.get("params") or {}))
        finally:
            w.flush()

    def private_credit(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        return jsonable(w.pcredit.book(w.portfolio(portfolio_id)))

    def pc_commit(self, world_id: str, portfolio_id: str, deal_id: str, amount: float) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        loan = w.commit_private_credit(portfolio_id, str(deal_id).upper(), D(str(amount)))
        return jsonable(asdict(loan))

    def pc_sell(self, world_id: str, portfolio_id: str, loan_id: str, amount: float) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        loan = w.sell_private_credit(portfolio_id, str(loan_id).upper(), D(str(amount)))
        return jsonable(asdict(loan))

    # ------------------------------------------------------------------ real estate & housing
    def housing(self, world_id: str) -> Dict:
        """The housing market: listed real estate grouped the way a property desk reads it, plus the housing indicators —
        real (FRED, as known on the save's date) in a career save, derived from the curve and the cycle in a sandbox."""
        from ..engines.realmacro import housing_as_of
        w = self.world(world_id)
        secs = self.securities(world_id)
        by_id = {r["id"]: r for r in secs}
        REIT_SUB = {"PLD": "Industrial", "AMT": "Towers", "CCI": "Towers", "SBAC": "Towers", "EQIX": "Data centres", "DLR": "Data centres", "O": "Net lease", "VICI": "Net lease",
                    "SPG": "Retail", "KIM": "Retail", "REG": "Retail", "PSA": "Storage", "EXR": "Storage", "WELL": "Health care", "VTR": "Health care",
                    "AVB": "Residential", "EQR": "Residential", "ESS": "Residential", "INVH": "Residential", "MAA": "Residential", "ARE": "Office & labs", "BXP": "Office & labs",
                    "IRM": "Specialty", "WY": "Timber", "HST": "Hotels"}
        HOMEBUILDERS = ["DHI", "LEN", "NVR", "PHM", "TOL", "KBH"]
        BUILDING = ["HD", "LOW", "SHW", "MAS", "BLDR", "RKT"]
        MORTGAGE_REITS = ["AGNC", "NLY", "STWD"]
        ETFS = ["VNQ", "IYR", "REZ", "XHB", "ITB"]
        def rows(ids):
            return [by_id[i] for i in ids if i in by_id]
        reits = [dict(r, subsector=REIT_SUB.get(r["id"], "Other")) for r in secs if r.get("asset_class") == "REIT"]
        reits.sort(key=lambda r: (r["subsector"], r["id"]))
        agency = [r for r in secs if str(r["id"]).startswith("AGY-")]
        real = getattr(w, "market_source", "SIMULATED") == "REAL"
        ind: Dict = {"source": "FRED (as known on the save's date)" if real else "derived from the save's curve and cycle"}
        series = w.market.macro.real_series if real else None
        if real and (not series or "mortgage30" not in series) and w.market.real_macro_feed is not None:
            try:                                                        # a save from before the housing series existed: fetch them once (cached six hours)
                w.market.real_macro_feed.refresh()
                w.market.macro.real_series = series = w.market.real_macro_series()
            except Exception:
                pass
        if series:
            ind.update(housing_as_of(series, w.current_date, w.calendar.roll))
        if "mortgage30" not in ind:
            from ..engines.pricing import interp_rate
            curve = w.market.curve()
            ten = interp_rate(curve, 10.0) * 100.0
            regime = w.market.regime().name
            ind["mortgage30"] = round(ten + {"NORMAL_GROWTH": 1.7, "RATE_CUTTING": 1.6, "RATE_HIKING": 1.9, "RECESSION": 2.1, "LIQUIDITY_STRESS": 2.6}.get(regime, 1.8), 2)
            ind["housing_starts"] = round(1350 * {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.06, "RATE_HIKING": 0.93, "RECESSION": 0.78, "LIQUIDITY_STRESS": 0.7}.get(regime, 1.0) * (1 - 0.06 * max(0.0, ind["mortgage30"] - 6.5)))
            ind["permits"] = round(ind["housing_starts"] * 1.04)
            ind["home_prices_yoy"] = round({"NORMAL_GROWTH": 3.5, "RATE_CUTTING": 5.0, "RATE_HIKING": 1.0, "RECESSION": -4.0, "LIQUIDITY_STRESS": -7.0}.get(regime, 2.0) - 0.8 * max(0.0, ind["mortgage30"] - 6.5), 1)
            ind["existing_sales"] = round(4.1e6 * {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.08, "RATE_HIKING": 0.92, "RECESSION": 0.8, "LIQUIDITY_STRESS": 0.72}.get(regime, 1.0) * (1 - 0.05 * max(0.0, ind["mortgage30"] - 6.5)))
            ind["simulated"] = True
        return jsonable({"indicators": ind, "reits": reits, "homebuilders": rows(HOMEBUILDERS), "building_products": rows(BUILDING), "mortgage_reits": rows(MORTGAGE_REITS),
                         "etfs": rows(ETFS), "agency_bonds": agency, "real": real,
                         "notes": {"reits": "equity REITs by property type: rents, occupancy and cap rates against the 10-year yield",
                                   "homebuilders": "new-home demand: mortgage rates, starts and permits drive orders and margins",
                                   "mortgage_reits": "levered holders of agency MBS: earn the spread, live and die by rates volatility and funding",
                                   "agency": "government-sponsored issuers whose guarantees stand behind the mortgage market"}})

    # ------------------------------------------------------------------ investment banking
    def investment_banking(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        return jsonable(w.ibank.book(w.portfolio(portfolio_id)))

    def ib_command(self, world_id: str, portfolio_id: str, action: str, body: Dict) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        if action == "pitch":
            return jsonable(w.ib_pitch(portfolio_id, str(body["mandate_id"]).upper(), float(body["fee_pct"]), float(body.get("promise", 0))))
        if action == "decide":
            e = w.ib_decide(portfolio_id, str(body["engagement_id"]).upper(), body["choice"], body.get("value"))
            return jsonable(asdict(e))
        raise NotFound(f"unknown investment banking action {action}")

    # ------------------------------------------------------------------ private equity
    def private_equity(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        return jsonable(w.pequity.book(w.portfolio(portfolio_id)))

    def pe_structure(self, world_id: str, portfolio_id: str, deal_id: str, multiple: float, leverage: float) -> Dict:
        w = self.world(world_id)
        deal = w.pequity.deal(str(deal_id).upper(), portfolio_id)
        if deal is None:
            raise NotFound(f"deal {deal_id} not found")
        return jsonable(w.pequity.structure(deal, float(multiple), float(leverage)))

    def pe_command(self, world_id: str, portfolio_id: str, action: str, body: Dict) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        if action == "diligence":
            return jsonable(w.pe_diligence(portfolio_id, str(body["deal_id"]).upper()))
        if action == "bid":
            return jsonable(w.pe_bid(portfolio_id, str(body["deal_id"]).upper(), float(body["multiple"]), float(body.get("leverage", 0))))
        cid = str(body.get("company_id", "")).upper()
        if action == "initiative":
            c = w.pe_initiative(portfolio_id, cid, body["kind"], body.get("size_pct"))
        elif action == "recap":
            c = w.pe_recap(portfolio_id, cid, float(body["target_leverage"]))
        elif action == "cure":
            c = w.pe_cure(portfolio_id, cid)
        elif action == "exit":
            c = w.pe_exit(portfolio_id, cid, body.get("route", "SALE"))
        elif action == "selldown":
            c = w.pe_selldown(portfolio_id, cid, float(body.get("fraction", 1.0)))
        else:
            raise NotFound(f"unknown private equity action {action}")
        return jsonable(asdict(c))

    def repo_open(self, world_id: str, portfolio_id: str, side: str, security_id: str, quantity: float, term_type: str = "OVERNIGHT", term_days: int = 1, auto_roll: bool = True) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        r = w.open_repo(portfolio_id, side, security_id.upper(), D(str(quantity)), term_type, int(term_days), bool(auto_roll))
        return jsonable(asdict(r))

    def repo_action(self, world_id: str, portfolio_id: str, repo_id: str, action: str, body: Dict) -> Dict:
        w = self.world(world_id)
        if action == "close":
            r = w.close_repo(portfolio_id, repo_id)
        elif action == "collateral":
            r = w.repo_post_collateral(portfolio_id, repo_id, body["security_id"].upper(), D(str(body["quantity"])))
        elif action == "cash":
            r = w.repo_post_cash(portfolio_id, repo_id, D(str(body["amount"])))
        elif action == "reduce":
            r = w.repo_reduce(portfolio_id, repo_id, D(str(body["amount"])))
        elif action == "substitute":
            r = w.repo_substitute(portfolio_id, repo_id, body["old_security_id"].upper(), D(str(body["old_quantity"])), body["new_security_id"].upper(), D(str(body["new_quantity"])))
        else:
            raise NotFound(f"unknown repo action {action}")
        return jsonable(asdict(r))

    def collateral(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        d = w.collateral.dashboard(pf)
        d["calls"] = [asdict(c) for c in sorted(d["calls"], key=lambda c: c.issued, reverse=True)]
        d["prime"] = {k: v for k, v in w.prime.financing(pf).items() if k != "call"}
        d["prime"]["call"] = asdict(w.prime.financing(pf)["call"]) if w.prime.financing(pf)["call"] else None
        return jsonable(d)

    def financing(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        fin = w.prime.financing(pf)
        fin["call"] = asdict(fin["call"]) if fin["call"] else None
        fxm = w.market.fx
        rates = {c: {"spot": fxm.spot[c], "rate": fxm.rate[c], "display": (1 / fxm.spot[c]) if c == "JPY" else fxm.spot[c]} for c in fxm.spot}
        return jsonable({"prime": fin, "repo": w.repo.book(pf), "fx": {"rates": rates, "exposures": w.fx.exposures(pf),
                         "trades": [asdict(t) for t in sorted(pf.fx_trades.values(), key=lambda t: t.trade_date, reverse=True)],
                         "forwards": [asdict(f) for f in sorted(pf.fx_forwards.values(), key=lambda f: f.trade_date, reverse=True)],
                         "history": {c: h[-120:] for c, h in fxm.history.items() if c != "USD"}}})

    def fx_market(self, world_id: str) -> Dict:
        """The FX market page: each currency against the dollar with its rate differential, forward points, realised vol,
        recent changes, and a decomposition of today's move into the model's drivers (carry, equity-factor beta, mean
        reversion to the long-run level, and the residual flow), plus a cross-rate matrix and 120 sessions of history."""
        import math
        from ..engines.fx_market import SPECS, CURRENCIES
        w = self.world(world_id)
        fxm = w.market.fx
        d = w.current_date
        usd = float(fxm.rate.get("USD", 0.0))
        spy = w.market.history.get("SPY") or []
        spy_ret = float(spy[-1].close / spy[-2].close - 1) if len(spy) > 1 else 0.0
        dt = 1.0 / 252.0
        rows = []
        for c, spec in SPECS.items():
            h = fxm.history.get(c) or []
            spots = [float(x[1]) for x in h]
            cur = float(fxm.spot[c])
            if not spots:
                spots = [cur]
            def chg(n):
                return (cur / spots[-1 - n] - 1.0) if len(spots) > n and spots[-1 - n] else None
            rets = [math.log(spots[i] / spots[i - 1]) for i in range(max(1, len(spots) - 20), len(spots)) if spots[i - 1] and spots[i]]
            if len(rets) > 2:
                m = sum(rets) / len(rets)
                vol = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252)
            else:
                vol = spec.vol
            r = float(fxm.rate[c])
            carry_ann = usd - r
            carry_d = -0.3 * carry_ann * dt
            beta_d = spec.mkt_beta * spy_ret
            rev_d = 0.002 * math.log(spec.spot0 / cur) * dt * 252 / 20 if cur > 0 else 0.0
            total = math.log(cur / spots[-2]) if len(spots) > 1 and spots[-2] else 0.0
            resid = total - carry_d - beta_d - rev_d
            inv = spec.inverse_quote
            disp = (1.0 / cur) if inv else cur
            fwd = {}
            for label, days in (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)):
                F = fxm.forward(c, "USD", d, d + timedelta(days=days))
                Fd = (1.0 / F) if inv else F
                fwd[label] = {"rate": Fd, "points": (Fd - disp) * (100 if inv else 10000)}
            rows.append({"ccy": c, "pair": f"USD/{c}" if inv else f"{c}/USD", "quote": disp, "spot_usd_per_unit": cur, "inverse": inv, "rate": r, "usd_rate": usd,
                         "carry": carry_ann, "day_pct": chg(1), "week_pct": chg(5), "month_pct": chg(21), "realized_vol": vol, "model_vol": spec.vol, "beta": spec.mkt_beta,
                         "spread_bps": spec.spread_bps, "long_run": spec.spot0, "deviation_from_long_run": cur / spec.spot0 - 1.0 if spec.spot0 else 0.0,
                         "drivers": {"carry": carry_d, "equity_factor": beta_d, "mean_reversion": rev_d, "flow": resid, "total": total, "spy_return": spy_ret},
                         "forwards": fwd, "history": [[x[0], (1.0 / float(x[1])) if inv else float(x[1]), float(x[2])] for x in h[-120:]]})
        ccys = [c for c in CURRENCIES]
        cross = {a: {b: (float(fxm.spot[a]) / float(fxm.spot[b])) for b in ccys} for a in ccys}
        real = getattr(w, "market_source", "SIMULATED") == "REAL"
        return jsonable({"date": d.isoformat(), "usd_rate": usd, "rows": rows, "cross": cross, "currencies": ccys, "spy_return": spy_ret,
                         "source": "REAL" if real else "SIMULATED",
                         "model": ("Spots are the real closes (Yahoo) for the sessions this save has advanced into; the drivers below are the model's decomposition of that move."
                                   if real else "Each currency moves with the equity market factor (risk-on currencies AUD, CAD rise with stocks; JPY and CHF fall), drifts against its rate differential to the dollar (carry), pulls slowly toward its long-run level, and carries its own noise. Foreign short rates follow the US policy rate with a lag.")})

    def margin_action(self, world_id: str, portfolio_id: str, action: str, amount: float) -> Dict:
        w = self.world(world_id)
        if action == "draw":
            w.margin_draw(portfolio_id, D(str(amount)))
        elif action == "repay":
            w.margin_repay(portfolio_id, D(str(amount)))
        else:
            raise NotFound(f"unknown margin action {action}")
        return self.financing(world_id, portfolio_id)["prime"]

    def fx_spot(self, world_id: str, portfolio_id: str, buy_ccy: str, sell_ccy: str, amount: float, amount_ccy: str = "BUY", execution: Optional[str] = None,
                tag: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        return jsonable(asdict(w.fx_spot(portfolio_id, buy_ccy, sell_ccy, D(str(amount)), amount_ccy, execution or "NEXT_UPDATE", tag)))

    def fx_forward(self, world_id: str, portfolio_id: str, buy_ccy: str, sell_ccy: str, buy_amount: float, maturity: str, tag: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        return jsonable(asdict(w.fx_forward(portfolio_id, buy_ccy, sell_ccy, D(str(buy_amount)), maturity, tag)))

    def tags(self, world_id: str, portfolio_id: str) -> Dict:
        """Every #tag in the book: the fills, working instructions, OTC trades and FX deals that carry it, with net quantities,
        realised and unrealised P&L. Tags come from the ticket's strategy tag, the playbook (its strategy key), FX deals and OTC RFQs."""
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        out: Dict[str, Dict] = {}

        def bucket(tag: str) -> Dict:
            t = (tag or "").strip().lstrip("#")
            if not t:
                return None
            return out.setdefault(t, {"tag": t, "fills": [], "orders": [], "otc": [], "fx": [], "net": {}, "realized": ZERO, "unrealized": ZERO, "mtm": ZERO, "cash": ZERO})
        tag_of_order = {o.id: o.strategy_tag for o in pf.orders.values() if o.strategy_tag and o.strategy_tag != "SYSTEM"}
        for o in pf.orders.values():
            b = bucket(tag_of_order.get(o.id))
            if b is not None and o.status in ("WORKING", "PARTIALLY_FILLED"):
                b["orders"].append(asdict(o))
        for t in pf.trades.values():
            b = bucket(tag_of_order.get(t.order_id))
            if b is None:
                continue
            b["fills"].append(asdict(t))
            sec = w.securities[t.security_id]
            signed = t.quantity if t.side == "BUY" else -t.quantity
            n = b["net"].setdefault(t.security_id, {"security_id": t.security_id, "name": sec.name, "quantity": ZERO, "cost": ZERO, "realized": ZERO})
            n["quantity"] += signed
            n["cost"] += (t.net_amount if t.side == "BUY" else -t.net_amount) if not sec.is_future else ZERO
            n["realized"] += t.realized_pnl or ZERO
            b["realized"] += t.realized_pnl or ZERO
        for b in out.values():
            for n in b["net"].values():
                sec = w.securities[n["security_id"]]
                if n["quantity"] != 0 and w.market.history.get(n["security_id"]) or (sec.is_option and w.trading._bar(sec)):
                    bar = w.trading._bar(sec)
                    unit = w.trading._unit(sec)
                    mark = money(n["quantity"] * bar.close * unit / (100 if sec.is_bond else 1)) if not sec.is_future else ZERO
                    n["mark_value"] = mark
                    n["unrealized"] = mark - n["cost"] if not sec.is_future else ZERO
                    b["unrealized"] += n["unrealized"]
            b["net"] = [n for n in b["net"].values()]
        for t in pf.otc_trades.values():
            b = bucket((t.terms or {}).get("tag"))
            if b is not None:
                b["otc"].append({"id": t.id, "product": t.product, "counterparty": t.counterparty, "description": w.otc.describe(t), "status": t.status, "mtm": t.mtm, "realized": t.realized,
                                 "notional": t.notional, "maturity": t.maturity})
                if t.is_open:
                    b["mtm"] += t.mtm
                b["cash"] += t.realized
        for f in list(pf.fx_trades.values()) + list(pf.fx_forwards.values()):
            b = bucket(getattr(f, "tag", None))
            if b is not None:
                b["fx"].append(asdict(f))
                b["mtm"] += getattr(f, "mtm", ZERO) if getattr(f, "status", "") == "OPEN" else ZERO
        rows = sorted(out.values(), key=lambda b: b["tag"])
        for b in rows:
            b["total"] = b["realized"] + b["unrealized"] + b["mtm"] + b["cash"]
        return jsonable({"tags": rows, "count": len(rows)})

    def force_regime(self, world_id: str, regime: str) -> Dict:
        w = self.world(world_id)
        ev = w.force_regime(regime)
        return {"event_id": ev.id, "regime": regime}

    def order_preview(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        """What an instruction is worth in cash before it is sent: price used, notional, accrued, commission, margin,
        and the quantity a cash amount buys. Estimates only: the fill happens at the next session."""
        from ..engines.pricing import BondPricer
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        sid = str(body.get("security_id", "")).strip().upper()
        sec = w.securities.get(sid)
        if sec is None:
            raise NotFound(f"unknown security {sid}")
        side = str(body.get("side", "BUY")).upper()
        bar = w.trading._bar(sec)
        if bar is None:
            raise CommandError(f"{sid} has no session data yet")
        limit = body.get("limit_price")
        live = None
        if str(body.get("execution") or "").upper() == "LIVE" and w.live.available():
            try:
                live = w.live.quote(sec)
            except Exception:
                live = None
        if live:
            lp = D(str(live["price"]))
            if sec.is_option:
                lbid, lask = D(str(live["bid"])), D(str(live["ask"]))
            else:
                rel = (bar.ask - bar.bid) / 2 / bar.close if bar.close else ZERO
                lbid, lask = qprice(lp - lp * rel), qprice(lp + lp * rel)
            px = D(str(limit)) if limit not in (None, "") else (lask if side == "BUY" else lbid)
        else:
            px = D(str(limit)) if limit not in (None, "") else (bar.ask if side == "BUY" else bar.bid)
        unit = w.trading._unit(sec)
        lot = D(sec.lot_size) if sec.lot_size > 1 else D(1)
        acc100 = BondPricer.accrued_per_100(sec, w.current_date) if sec.is_bond else ZERO
        per_unit_cash = (px + acc100) * unit                  # cash per one unit of quantity (per share / per 1 face incl. accrued / per contract)
        amount = body.get("amount")
        step = sec.qty_step if sec.qty_step != 1 else D(1)
        if amount not in (None, "") and D(str(amount)) > 0 and per_unit_cash > 0:
            q = (D(str(amount)) / per_unit_cash).quantize(step, rounding="ROUND_DOWN")
            q = q - (q % lot)
        else:
            q = D(str(body.get("quantity") or 0)).quantize(step, rounding="ROUND_DOWN")
        q = max(D(0), q)
        gross = money(q * px * unit)
        accrued = money(q * BondPricer.accrued_per_100(sec, w.current_date) / 100) if sec.is_bond else ZERO
        commission = w.trading._commission(sec, q) if q > 0 else ZERO
        margin = money(w.futures.initial_margin_per_contract(sec) * q) if sec.is_future else ZERO
        if sec.is_future:
            cash_needed = margin + commission
        elif side == "BUY":
            cash_needed = gross + accrued + commission
        else:
            cash_needed = -(gross + accrued - commission)
        ccy = sec.currency or pf.base_currency
        settle = str(body.get("settle_ccy") or ccy).upper()
        fx = None
        if settle != ccy and cash_needed != 0:
            # pay with (or receive in) another currency: the conversion is a spot deal at the pair's rate, settled T+2
            need = abs(cash_needed)
            q_fx = w.fx.quote(ccy, settle, need, "BUY") if cash_needed > 0 else w.fx.quote(settle, ccy, need, "SELL")
            fx = {**q_fx, "direction": "pay" if cash_needed > 0 else "receive",
                  "settle_amount": q_fx["sell_amount"] if cash_needed > 0 else q_fx["buy_amount"],
                  "settle_balance": pf.cash_account(settle).balance, "settle_projected": w.trading.projected_cash(pf, settle)}
        return jsonable({"security_id": sid, "side": side, "quantity": q, "price": px,
                         "price_source": "limit" if limit not in (None, "") else (("live ask" if side == "BUY" else "live bid") if live else ("ask" if side == "BUY" else "bid")),
                         "live": live, "execution": "LIVE" if live else "NEXT_UPDATE",
                         "unit_cash": per_unit_cash, "gross": gross, "accrued_interest": accrued, "commission": commission, "initial_margin": margin,
                         "cash_needed": cash_needed, "cash_settled": pf.cash_account(ccy).balance, "cash_projected": w.trading.projected_cash(pf, ccy),
                         "kind": "future" if sec.is_future else "option" if sec.is_option else "bond" if sec.is_bond else "crypto" if sec.asset_class == "CRYPTO" else "cash",
                         "lot_size": lot, "multiplier": float(sec.multiplier) if (sec.is_future or sec.is_option) else 1.0, "currency": ccy,
                         "settle_ccy": settle, "fx": fx, "cash_by_currency": {c: {"settled": a.balance, "base_value": a.base_value} for c, a in pf.cash.items()}})

    # ------------------------------------------------------------------ phase 9: commodity desk
    def place_spread(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        st = w.place_spread(portfolio_id, body["near"], body["far"], body.get("side", "BUY"), D(str(body["quantity"])),
                            None if body.get("limit_points") in (None, "") else float(body["limit_points"]), body.get("time_in_force", "DAY"))
        return jsonable({"strategy_id": st.id, "status": st.status, "legs": st.legs, "analytics": w.options.strategy_analytics(w.portfolio(portfolio_id), st)})

    def set_physical_delivery(self, world_id: str, portfolio_id: str, on: bool) -> Dict:
        w = self.world(world_id)
        return jsonable(w.set_physical_delivery(portfolio_id, bool(on)))

    def commodity_desk(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        spreads = [{"id": st.id, "status": st.status, "underlying": st.underlying, "legs": st.legs, "quantity": st.quantity, "net_limit": st.net_limit,
                    "net_premium": st.net_premium, "entered": st.entered_date} for st in pf.strategies.values() if st.strategy_type == "FUTURES_CALENDAR"]
        return jsonable({"physical_delivery": pf.physical_delivery, "inventory": w.cdesk.inventory(pf), "deliveries": pf.physical_deliveries[-30:],
                         "spreads": list(reversed(spreads))[:40], "futures_margin": w.futures.required_margin(pf),
                         "options_on_futures": [u for u in w.options.optionable_futures()]})

    def force_rates(self, world_id: str, bp: float) -> Dict:
        w = self.world(world_id)
        ev = w.force_rates(float(bp))
        return {"event_id": ev.id, "bp": float(bp)}

    # ------------------------------------------------------------------ audit
    # ------------------------------------------------------------------ listed options (phase 3)
    def options_underlyings(self, world_id: str) -> List[Dict]:
        w = self.world(world_id)
        out = []
        counts: Dict[str, int] = {}
        for s in w.securities.values():                      # one pass over the (large) option universe, not one per underlying
            if s.is_option and not s.expired:
                counts[s.underlying] = counts.get(s.underlying, 0) + 1
        from ..engines.vol import INDICES, is_index, index_source, index_factor
        index_names = {"SPX": "S&P 500 index", "NDX": "Nasdaq-100 index", "RUT": "Russell 2000 index"}
        for under in w.options.optionable():
            src = w.securities.get(index_source(under))
            st = w.market.vol.state.get(under)
            if src is None or st is None or not w.market.history.get(src.id):
                continue
            rank = w.market.vol.iv_rank(under)
            name = f"{index_names.get(under, under)} ({index_factor(under):g}x {src.id}, cash-settled European)" if is_index(under) else src.name
            out.append({"underlying": under, "name": name, "level": w.options.underlying_level(under),
                        "atm_iv": st.atm, "skew": st.skew, "term": st.term, "realized_20d": w.market.realized_vol(src.id), "iv_rank": rank["iv_rank"] if rank else None,
                        "style": "EUROPEAN/CASH" if is_index(under) else "AMERICAN/PHYSICAL", "dividend_yield": src.dividend_yield,
                        "contracts": counts.get(under, 0), "is_index": is_index(under), "source": src.id if is_index(under) else None})
        return jsonable(out)

    def option_chain(self, world_id: str, underlying: str, expiry: Optional[str] = None) -> Dict:
        w = self.world(world_id)
        if underlying not in w.market.vol.state:
            raise NotFound(f"no option chain for {underlying}")
        ch = w.options.chain(underlying, expiry)
        vs = w.market.vol.state[underlying]
        ch["atm_iv"] = vs.atm
        ch["regime"] = w.market.regime().name
        return jsonable(ch)

    def vol_surface(self, world_id: str, underlying: str) -> Dict:
        w = self.world(world_id)
        if underlying not in w.market.vol.state:
            raise NotFound(f"no vol surface for {underlying}")
        return jsonable(w.options.surface(underlying))

    def option_contract(self, world_id: str, contract_id: str) -> Dict:
        w = self.world(world_id)
        sec = w.securities.get(contract_id)
        if sec is None or not sec.is_option:
            raise NotFound(f"unknown option {contract_id}")
        q = w.options.quote(sec) if not sec.expired else {}
        bar = w.options.option_bar(sec) if not sec.expired else None
        positions = {pf.id: pf.positions[sec.id].quantity for pf in w.portfolios.values() if sec.id in pf.positions and pf.positions[sec.id].quantity}
        return jsonable({"security": asdict(sec), "is_option": True, "quote": q, "bar": asdict(bar) if bar else None, "positions": positions,
                         "days_to_expiry": (date.fromisoformat(sec.expiry) - w.current_date).days, "underlying_level": w.options.underlying_level(sec.underlying),
                         "chain_link": f"#/options/{sec.underlying}/{sec.expiry}"})

    def options_book(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        rows = w.options.position_rows(pf)
        agg = w.options.aggregate_greeks(pf)
        total, detail = w.options.margin_requirement(pf)
        strategies = []
        for st in pf.strategies.values():
            legs = [{**l, "order_status": pf.orders[l["order_id"]].status if l.get("order_id") in pf.orders else None,
                     "fill_price": pf.orders[l["order_id"]].avg_fill_price if l.get("order_id") in pf.orders else None} for l in st.legs]
            strategies.append({"id": st.id, "strategy_type": st.strategy_type, "underlying": st.underlying, "quantity": st.quantity, "net_limit": st.net_limit,
                               "status": st.status, "entered_date": st.entered_date, "net_premium": st.net_premium, "legs": legs, "notes": st.notes})
        expirations = []
        for r in rows:
            bd = w.calendar.business_days_between(w.current_date, date.fromisoformat(r["expiry"]))
            expirations.append({**r, "business_days": bd})
        expirations.sort(key=lambda r: (r["expiry"], r["security_id"]))
        events = []
        for ev in w.events:
            if ev.portfolio_id == pf.id and ev.type in ("OPTION_EXERCISED", "OPTION_ASSIGNED", "OPTION_EXPIRED", "CONTRACT_ADJUSTED"):
                events.append({"event_id": ev.id, "date": ev.sim_date, "kind": ev.type, **{k: v for k, v in ev.payload.items() if k != "portfolio_id"}})
            elif ev.type == "CONTRACT_ADJUSTED" and ev.payload.get("security_id") in pf.positions:
                events.append({"event_id": ev.id, "date": ev.sim_date, "kind": ev.type, **ev.payload})
        snap = pf.nav_history[-1] if pf.nav_history else None
        cash = pf.cash_account(pf.base_currency).balance
        return jsonable({"positions": rows, "greeks": agg, "margin": {"total": total, "detail": detail, "held_at_clearing": w.ledgers[pf.id].balance("1300"),
                         "futures_margin": w.futures.required_margin(pf), "regime_multiplier": REGIME_MARGIN_MULT.get(w.market.state.regime, 1.0),
                         "cash": cash, "excess_liquidity": w.prime.financing(pf)["excess_liquidity"]},
                         "strategies": strategies, "expirations": expirations, "events": events[-100:], "underlyings": w.options.optionable(),
                         "options_pnl_today": snap.explain.get("options") if snap else None, "greek_attribution": snap.greek_attribution if snap else {},
                         "strategy_types": {k: {"legs": [{"type": t, "side": sd, "strike_index": ki, "expiry_index": ei, "ratio": r} for (t, sd, ki, ei, r) in v[0]], "needs_shares": v[1]}
                                            for k, v in STRATEGY_TEMPLATES.items()}})

    def exercise(self, world_id: str, portfolio_id: str, contract_id: str, quantity: Optional[float] = None) -> Dict:
        w = self.world(world_id)
        r = w.exercise_option(portfolio_id, contract_id, quantity)
        return jsonable(r)

    def strategy_preview(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        from ..domain.models import Strategy
        from ..engines.options import STRATEGY_TEMPLATES, contract_id as cid_of
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        st_type = str(body["strategy_type"]).upper()
        if st_type not in STRATEGY_TEMPLATES:
            raise CommandError(f"unknown strategy {st_type}")
        template, needs_shares = STRATEGY_TEMPLATES[st_type]
        strikes = [float(k) for k in body["strikes"]]
        exps = [body["expiry"], body.get("expiry2") or body["expiry"]]
        n = D(str(body.get("quantity", 1)))
        legs = []
        for (ot, side, ki, ei, ratio) in template:
            if ki >= len(strikes):
                raise CommandError(f"{st_type} needs {max(k for _, _, k, _, _ in template) + 1} strike(s)")
            cid = cid_of(body["underlying"], date.fromisoformat(exps[ei]), ot, strikes[ki])
            if cid not in w.securities:
                raise CommandError(f"contract {cid} is not listed")
            legs.append({"security_id": cid, "side": side, "ratio": ratio, "quantity": n * ratio, "order_id": None})
        st = Strategy(id="PREVIEW", portfolio_id=pf.id, strategy_type=st_type, underlying=body["underlying"], legs=legs, quantity=n, net_limit=None, status="PREVIEW",
                      entered_date=w.current_date.isoformat())
        a = w.options.strategy_analytics(pf, st)
        extra = [(w.securities[l["security_id"]], (l["quantity"] if l["side"] == "BUY" else -l["quantity"])) for l in legs]
        base, _ = w.options.margin_requirement(pf)
        after, det = w.options.margin_requirement(pf, extra=extra)
        a["incremental_margin"] = max(ZERO, after - base)
        a["margin_detail"] = [d for d in det if any(d["contract"] == l["security_id"] for l in legs)]
        a["legs"] = [{**l, "quote": w.options.quote(w.securities[l["security_id"]])} for l in legs]
        a["needs_shares"] = n * 100 if needs_shares else ZERO
        return jsonable(a)

    def place_strategy(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        st = w.place_strategy(portfolio_id, body["strategy_type"], body["underlying"], body["expiry"], [float(k) for k in body["strikes"]], body.get("quantity", 1),
                              body.get("net_limit"), body.get("expiry2"), body.get("time_in_force", "DAY"))
        return jsonable({"strategy_id": st.id, "status": st.status, "legs": st.legs, "analytics": w.options.strategy_analytics(w.portfolio(portfolio_id), st)})

    def strategy(self, world_id: str, portfolio_id: str, strategy_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        if strategy_id not in pf.strategies:
            raise NotFound(f"unknown strategy {strategy_id}")
        st = pf.strategies[strategy_id]
        return jsonable({"id": st.id, "strategy_type": st.strategy_type, "underlying": st.underlying, "quantity": st.quantity, "net_limit": st.net_limit, "status": st.status,
                         "entered_date": st.entered_date, "net_premium": st.net_premium, "legs": st.legs, "notes": st.notes, "analytics": w.options.strategy_analytics(pf, st)})

    # ------------------------------------------------------------------ OTC derivatives, dealers, ISDA/CSA (phase 4)
    def otc_dealers(self, world_id: str) -> Dict:
        from ..engines.counterparties import CSA_TERMS, DEALERS, HALF_WIDTH, UNIT, quote_half_width
        from ..domain.otc_models import PRODUCTS
        w = self.world(world_id)
        regime = w.market.state.regime
        rows = []
        for k, spec in DEALERS.items():
            st = w.market.dealers.state[k]
            rows.append({"dealer": k, "name": spec.name, "rating": st.rating, "base_rating": spec.rating, "cds_bps": st.cds, "cds_base": spec.cds0, "stress": st.stress,
                         "defaulted": st.defaulted, "capital_bn": spec.capital_bn, "products": list(spec.products), "style": spec.style,
                         "pd_1y": w.market.dealers.default_probability_1y(k), "csa": CSA_TERMS[k],
                         "half_widths": {p: quote_half_width(p, k, regime, st.stress) for p in spec.products},
                         "history": [{"date": d, "cds": c} for d, c in w.market.dealers.history.get(k, [])[-260:]]})
        return jsonable({"dealers": rows, "products": list(PRODUCTS), "units": UNIT, "standard_half_widths": HALF_WIDTH, "regime": regime,
                         "rate_vol": w.otc.rate_vol(), "market_basis": {c: w.otc.market_basis(c) for c in ("EUR", "GBP", "JPY", "CHF", "CAD", "AUD")}})

    def otc_book(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        return jsonable(w.otc.book(pf))

    def otc_trade(self, world_id: str, portfolio_id: str, trade_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        t = pf.otc_trades.get(trade_id)
        if t is None:
            raise NotFound(f"unknown OTC trade {trade_id}")
        led = w.ledgers[pf.id]
        entries = [self._entry(e) for e in led.entries if any(l.security_id == trade_id for l in e.lines)]
        sched = []
        T = t.terms
        if t.product == "IRS":
            sched = [{"leg": "FIXED", "start": s.isoformat(), "end": e.isoformat(), "pay": p.isoformat()} for s, e, p in w.otc.periods(t.start, t.maturity, T["fixed_months"])] + \
                    [{"leg": "FLOAT", "start": s.isoformat(), "end": e.isoformat(), "pay": p.isoformat(), "fixing": t.fixings.get(s.isoformat())} for s, e, p in w.otc.periods(t.start, t.maturity, T["float_months"])]
        elif t.product in ("CAP", "FLOOR", "XCCY", "COMMODITY_SWAP"):
            def _fix(s_):
                k = s_.isoformat()
                if t.product == "XCCY":
                    u, f_ = t.fixings.get("USD:" + k), t.fixings.get(T["ccy"] + ":" + k)
                    return None if u is None and f_ is None else f"USD {u:.4%} / {T['ccy']} {f_:.4%}" if (u is not None and f_ is not None) else (u if u is not None else f_)
                if t.product == "COMMODITY_SWAP":
                    return (t.state or {}).get("realised", {}).get(k)
                return t.fixings.get(k)
        elif t.product in ("CAP", "FLOOR", "XCCY", "COMMODITY_SWAP"):
            sched = [{"leg": t.product, "start": s.isoformat(), "end": e.isoformat(), "pay": p.isoformat(), "fixing": _fix(s)} for s, e, p in w.otc.periods(t.start, t.maturity, T["months"])]
        elif t.product == "CDS":
            sched = [{"leg": "PREMIUM", "start": s.isoformat(), "end": e.isoformat(), "pay": p.isoformat()} for s, e, p in w.otc.periods(t.start, t.maturity, 3)]
        elif t.product == "TRS":
            sched = [{"leg": "RESET", "start": s.isoformat(), "end": e.isoformat(), "pay": p.isoformat()} for s, e, p in w.otc.periods(t.start, t.maturity, T["reset_months"])]
        row = next(r for r in w.otc.book(pf)["trades"] if r["id"] == trade_id)
        return jsonable({**row, "cashflows": t.cashflows, "schedule": sched, "ledger_entries": entries, "csa": pf.csas.get(t.counterparty),
                         "ledger_balances": {a: led.security_balance(trade_id, a) for a in ("1800", "2800", "4900", "4910")}})

    def otc_rfq(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        r = w.request_quote(portfolio_id, body["product"], body.get("params", {}))
        return jsonable({"id": r.id, "product": r.product, "params": r.params, "quotes": r.quotes, "mid": r.mid, "status": r.status, "expires": r.expires})

    def otc_execute(self, world_id: str, portfolio_id: str, rfq_id: str, dealer: str) -> Dict:
        w = self.world(world_id)
        self._open_for_instructions(w)
        t = w.execute_rfq(portfolio_id, rfq_id, dealer)
        return jsonable({"trade_id": t.id, "product": t.product, "counterparty": t.counterparty, "mtm": t.mtm, "description": w.otc.describe(t)})

    def otc_terminate(self, world_id: str, portfolio_id: str, trade_id: str) -> Dict:
        w = self.world(world_id)
        amt = w.terminate_otc(portfolio_id, trade_id)
        return jsonable({"trade_id": trade_id, "settlement": amt})

    def counterparties(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        ex = w.otc.exposure(pf)
        return jsonable({**ex, "csas": [asdict(c) for c in pf.csas.values()], "calls": [c for c in pf.collateral_calls.values() if c.source == "OTC"],
                         "dealers": self.otc_dealers(world_id)["dealers"]})

    def credit_event(self, world_id: str, reference: str, recovery: Optional[float] = None) -> Dict:
        w = self.world(world_id)
        ev = w.credit_event(reference, recovery)
        return {"event_id": ev.id, "reference": reference}

    def default_counterparty(self, world_id: str, dealer: str, recovery: float = 0.4) -> Dict:
        w = self.world(world_id)
        ev = w.default_counterparty(dealer, recovery)
        return {"event_id": ev.id, "dealer": dealer}

    # ------------------------------------------------------------------ risk (phase 5)
    def risk(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        rep = w.risk.report(pf)
        return jsonable({**rep, "history": pf.risk_history[-120:]})

    def risk_stress(self, world_id: str, portfolio_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        custom = {k: float(v) for k, v in body.items() if k in ("equity", "rates_bp", "spreads_bp", "vol_pts", "commodity", "fx") and v is not None}
        if body.get("commodity_by_code"):
            custom["commodity_by_code"] = {k.upper(): float(v) for k, v in body["commodity_by_code"].items()}
        custom["label"] = body.get("label", "Custom")
        return jsonable(w.risk.stress(pf, custom=custom))

    # ------------------------------------------------------------------ macro world & corporate events (phases 6-7)
    def macro(self, world_id: str) -> Dict:
        w = self.world(world_id)
        cur = w.market.curve()
        real_world = getattr(w, "market_source", "SIMULATED") == "REAL"
        real = real_world and bool(w.market.macro.real_series)          # a career whose FRED series never arrived shows the simulation's macro, and says so
        return jsonable({**w.market.macro.dashboard(w.current_date), "regime": w.market.regime().label, "curve": asdict(cur),
                         "corporate_events": w.market.cevents.announced[-60:], "date": w.current_date, "source": "REAL" if real else ("REAL_PENDING" if real_world else "SIMULATED"),
                         "real": w.market.macro.last_real if real else None,
                         "note": ("Growth, inflation, unemployment and the target rate are the real figures as known on each date (FRED: CPIAUCSL, CPILFESL, UNRATE, PAYEMS, "
                                  "A191RL1Q225SBEA, DFEDTARU); releases show the published number against the previous one (no consensus is fetched); "
                                  "release dates follow the usual calendar; FOMC dates are the published meeting days. Earnings and ratings remain the simulation's." if real else
                                  "The economic series (FRED) have not been fetched yet — the machine may be offline — so growth, inflation and the policy rate shown are the "
                                  "simulation's; they switch to the real figures at the first session after the feed answers." if real_world else
                                  "A seeded economy: growth, inflation and unemployment drift toward the regime's anchors, releases carry a consensus and a surprise, "
                                  "and the central bank follows a reaction function.")})

    def corporate_actions_for(self, world_id: str, portfolio_id: str) -> Dict:
        w = self.world(world_id)
        pf = w.portfolio(portfolio_id)
        rows = []
        for ca in sorted(w.corporate_actions.values(), key=lambda c: c.ex_date, reverse=True):
            ent = ca.entitlements.get(pf.id)
            terms = ca.entitlements.get("_terms", {})
            held = pf.positions[ca.security_id].quantity if ca.security_id in pf.positions else ZERO
            rows.append({"id": ca.id, "kind": ca.action_type, "security_id": ca.security_id, "declared": ca.declared_date, "ex_date": ca.ex_date, "pay_date": ca.pay_date,
                         "amount_per_unit": ca.amount_per_unit, "status": ca.status, "terms": terms, "held": held, "entitlement": ent,
                         "election_open": ca.action_type in ("TENDER_OFFER", "RIGHTS_ISSUE") and ca.status == "DECLARED" and w.current_date.isoformat() <= str(terms.get("deadline", ""))})
        return jsonable({"rows": rows[:200], "fail_charges": pf.fail_charges,
                         "fails": [asdict(si) for si in pf.settlements.values() if si.status == "FAILED"]})

    def elect(self, world_id: str, portfolio_id: str, ca_id: str, quantity: float) -> Dict:
        w = self.world(world_id)
        return jsonable(w.elect(portfolio_id, ca_id, quantity))

    def force_corporate_event(self, world_id: str, body: Dict) -> Dict:
        w = self.world(world_id)
        ev = w.force_corporate_event(body["security_id"], body["kind"], body.get("terms", {}), body["effective"])
        return {"event_id": ev.id, "ca_id": ev.payload["id"]}

    def force_split(self, world_id: str, security_id: str, ratio: float) -> Dict:
        w = self.world(world_id)
        ev = w.force_split(security_id, float(ratio))
        return {"event_id": ev.id, "security_id": security_id, "ratio": float(ratio)}

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

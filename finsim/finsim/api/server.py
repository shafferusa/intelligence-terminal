"""Standard-library HTTP server exposing the Service as a JSON API and serving
the terminal UI. No third-party dependencies."""
from __future__ import annotations

import json
import mimetypes
import secrets
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

from .service import NotFound, Service
from ..log import get_logger
from ..world import CommandError

log = get_logger("server")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class Router:
    def __init__(self, service: Service):
        self.s = service
        self.lock = threading.Lock()
        self.shutdown = None          # set by serve(): stops the HTTP server (the desktop app's uninstall/stop uses it)

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        s = self.s
        parts = [p for p in path.split("/") if p]
        if parts == ["api", "jobs"]:
            return s.jobs()
        if parts == ["api", "health"]:
            return s.health()
        if parts == ["api", "shutdown"] and method == "POST":
            if self.shutdown:
                self.shutdown()
            return {"shutting_down": bool(self.shutdown)}
        # /api/worlds ...
        if parts[:2] == ["api", "worlds"]:
            rest = parts[2:]
            if not rest:
                if method == "GET":
                    return s.list_worlds()
                if method == "POST":
                    return s.create_world(body.get("name", "New world"), body.get("seed", 42), body.get("start_date"),
                                          body.get("capital"), body.get("portfolio_name", "Main Portfolio"), body.get("portfolio_type", "PERSONAL"),
                                          body.get("realism", "PROFESSIONAL"), body.get("mode", "SANDBOX"), body.get("initial_regime", "NORMAL_GROWTH"),
                                          body.get("benchmark", "SPY"), body.get("job", "SANDBOX"), body.get("clock_mode", "SANDBOX"),
                                          body.get("timezone", "America/New_York"), body.get("update_time"), body.get("scenario", "NONE"),
                                          market_source=body.get("market_source"), lock_session=bool(body.get("lock_session", False)))
            if parts[2:] == ["jobs"] if len(parts) > 2 else False:
                return s.jobs()
            wid = rest[0]
            sub = rest[1:]
            if not sub:
                if method == "GET":
                    return s.world_info(wid)
                if method == "DELETE":
                    s.delete_world(wid)
                    return {"deleted": wid}
            if sub == ["advance"] and method == "POST":
                return s.advance(wid, int(body.get("days", 1)))
            if sub == ["securities"]:
                return s.securities(wid)
            if sub[0] == "securities" and len(sub) == 2:
                return s.security(wid, sub[1], query.get("period", ["1Y"])[0])
            if sub == ["yield-curve"]:
                return s.yield_curve(wid)
            if sub == ["force-regime"] and method == "POST":
                return s.force_regime(wid, body["regime"])
            if sub == ["force-rates"] and method == "POST":
                return s.force_rates(wid, body["bp"])
            if sub[0] == "lending-history" and len(sub) == 2:
                return s.lending_history(wid, sub[1])
            if sub == ["repo-quote"] and method == "POST":
                return s.repo_quote(wid, body["side"], body["security_id"], body["quantity"], body.get("term_type", "OVERNIGHT"), body.get("term_days", 1))
            if sub == ["options"]:
                return s.options_underlyings(wid)
            if sub[0] == "options" and len(sub) == 3 and sub[2] == "chain":
                return s.option_chain(wid, sub[1], query.get("expiry", [None])[0])
            if sub[0] == "options" and len(sub) == 3 and sub[2] == "surface":
                return s.vol_surface(wid, sub[1])
            if sub[0] == "options" and len(sub) == 3 and sub[2] == "contract":
                return s.option_contract(wid, sub[1])
            if sub == ["macro"]:
                return s.macro(wid)
            if sub == ["fx"]:
                return s.fx_market(wid)
            if sub == ["housing"]:
                return s.housing(wid)
            if sub == ["overall"]:
                return s.overall(wid)
            if sub == ["clock"] and method == "POST":
                return s.set_clock(wid, body.get("update_time"), body.get("timezone"), body.get("lock_session"))
            if sub == ["live"]:
                ids = [x for x in (query.get("ids", [""])[0] or "").split(",") if x]
                return s.live(wid, ids or None)
            if sub == ["live", "work"] and method == "POST":
                return s.work_live(wid)
            if sub == ["track-real"] and method == "POST":
                return s.switch_to_real(wid)
            if sub == ["force-corporate-event"] and method == "POST":
                return s.force_corporate_event(wid, body)
            if sub == ["otc", "dealers"]:
                return s.otc_dealers(wid)
            if sub == ["credit-event"] and method == "POST":
                return s.credit_event(wid, body["reference"], body.get("recovery"))
            if sub == ["default-counterparty"] and method == "POST":
                return s.default_counterparty(wid, body["dealer"], body.get("recovery", 0.4))
            if sub == ["force-split"] and method == "POST":
                return s.force_split(wid, body["security_id"], body["ratio"])
            if sub == ["commodities"]:
                return s.commodities(wid)
            if sub[0] == "commodities" and len(sub) == 2:
                return s.commodity(wid, sub[1])
            if sub == ["news"]:
                return s.news(wid)
            if sub == ["corporate-actions"]:
                return s.corporate_actions(wid)
            if sub == ["events"]:
                return s.events(wid, int(query.get("limit", [200])[0]), int(query.get("offset", [0])[0]), query.get("type", [None])[0],
                                query.get("portfolio_id", [None])[0], query.get("q", [None])[0])
            if sub[0] == "events" and len(sub) == 2:
                return s.event(wid, sub[1])
            if sub == ["portfolios"] and method == "POST":
                return s.create_portfolio(wid, body.get("name", "Portfolio"), body.get("portfolio_type", "PERSONAL"), body.get("capital", 10_000_000),
                                          body.get("realism", "PROFESSIONAL"), body.get("mode", "SANDBOX"), body.get("benchmark", "SPY"), body.get("job", "SANDBOX"))
            if sub[0] == "portfolios" and len(sub) >= 2:
                pid = sub[1]
                leaf = sub[2:]
                if not leaf and method == "DELETE":
                    return s.delete_portfolio(wid, pid)
                if leaf == ["dashboard"]:
                    return s.dashboard(wid, pid)
                if leaf == ["positions"] and len(leaf) == 1:
                    return s.dashboard(wid, pid)["positions"]
                if leaf[:1] == ["positions"] and len(leaf) == 2:
                    return s.position(wid, pid, leaf[1])
                if leaf == ["orders", "preview"] and method == "POST":
                    return s.order_preview(wid, pid, body)
                if leaf == ["orders"]:
                    if method == "POST":
                        return s.place_order(wid, pid, body["security_id"], body["side"], body["quantity"], body.get("order_type", "MARKET"),
                                             body.get("limit_price"), body.get("stop_price"), body.get("time_in_force", "DAY"), body.get("strategy_tag") or body.get("tag"),
                                             body.get("trail_pct"), body.get("condition"), body.get("settle_ccy"), body.get("execution"))
                    return s.orders(wid, pid)
                if leaf == ["briefing"]:
                    return s.briefing(wid, pid, query.get("date", [None])[0])
                if leaf == ["seclending"]:
                    return s.seclending(wid, pid)
                if leaf == ["locates"] and method == "POST":
                    return s.request_locate(wid, pid, body["security_id"], body["quantity"])
                if leaf == ["loans"] and method == "POST":
                    return s.borrow(wid, pid, body["locate_id"], body["quantity"], body.get("collateral_type", "CASH"))
                if leaf[:1] == ["loans"] and len(leaf) == 3 and leaf[2] == "return" and method == "POST":
                    return s.return_loan(wid, pid, leaf[1], body.get("quantity"))
                if leaf == ["playbook"]:
                    return s.playbook(wid, pid)
                if leaf == ["playbook", "preview"] and method == "POST":
                    return s.playbook_preview(wid, pid, body)
                if leaf == ["playbook", "execute"] and method == "POST":
                    return s.playbook_execute(wid, pid, body)
                if leaf == ["private-credit"]:
                    return s.private_credit(wid, pid)
                if leaf == ["private-credit", "commit"] and method == "POST":
                    return s.pc_commit(wid, pid, body["deal_id"], body["amount"])
                if leaf == ["private-credit", "sell"] and method == "POST":
                    return s.pc_sell(wid, pid, body["loan_id"], body["amount"])
                if leaf == ["investment-banking"]:
                    return s.investment_banking(wid, pid)
                if len(leaf) == 2 and leaf[0] == "investment-banking" and method == "POST":
                    return s.ib_command(wid, pid, leaf[1], body)
                if leaf == ["private-equity"]:
                    return s.private_equity(wid, pid)
                if leaf == ["private-equity", "structure"] and method == "POST":
                    return s.pe_structure(wid, pid, body["deal_id"], body["multiple"], body.get("leverage", 0))
                if len(leaf) == 2 and leaf[0] == "private-equity" and method == "POST":
                    return s.pe_command(wid, pid, leaf[1], body)
                if leaf == ["repo"]:
                    if method == "POST":
                        return s.repo_open(wid, pid, body["side"], body["security_id"], body["quantity"], body.get("term_type", "OVERNIGHT"), body.get("term_days", 1), body.get("auto_roll", True))
                    return s.repo_desk(wid, pid)
                if leaf[:1] == ["repo"] and len(leaf) == 3 and method == "POST":
                    return s.repo_action(wid, pid, leaf[1], leaf[2], body)
                if leaf == ["collateral"]:
                    return s.collateral(wid, pid)
                if leaf == ["financing"]:
                    return s.financing(wid, pid)
                if leaf[:1] == ["margin"] and len(leaf) == 2 and method == "POST":
                    return s.margin_action(wid, pid, leaf[1], body["amount"])
                if leaf == ["fx", "spot"] and method == "POST":
                    return s.fx_spot(wid, pid, body["buy_ccy"], body["sell_ccy"], body["amount"], body.get("amount_ccy", "BUY"), body.get("execution"), body.get("tag"))
                if leaf == ["fx", "forward"] and method == "POST":
                    return s.fx_forward(wid, pid, body["buy_ccy"], body["sell_ccy"], body["buy_amount"], body["maturity"], body.get("tag"))
                if leaf == ["tags"]:
                    return s.tags(wid, pid)
                if leaf == ["corporate-actions"]:
                    return s.corporate_actions_for(wid, pid)
                if leaf == ["elections"] and method == "POST":
                    return s.elect(wid, pid, body["ca_id"], body["quantity"])
                if leaf == ["desk"]:
                    return s.desk(wid, pid)
                if leaf == ["commodity-desk"]:
                    return s.commodity_desk(wid, pid)
                if leaf == ["spreads"] and method == "POST":
                    return s.place_spread(wid, pid, body)
                if leaf == ["physical-delivery"] and method == "POST":
                    return s.set_physical_delivery(wid, pid, body.get("on", True))
                if leaf == ["desk", "quote"] and method == "POST":
                    return s.quote_client(wid, pid, body["rfq_id"], body.get("level"), body.get("pass", False))
                if leaf == ["desk", "lend"] and method == "POST":
                    return s.lend_out(wid, pid, body["security_id"], body["quantity"])
                if leaf == ["desk", "recall"] and method == "POST":
                    return s.recall_lent(wid, pid, body["lend_id"], body.get("quantity"))
                if leaf == ["desk", "decide"] and method == "POST":
                    return s.decide_request(wid, pid, body["desk_id"], body["request_id"], body["approve"], body.get("note", ""))
                if leaf == ["desk", "limit"] and method == "POST":
                    return s.set_desk_limit(wid, pid, body["desk_id"], body["key"], body["value"])
                if leaf == ["desk", "reduce"] and method == "POST":
                    return s.force_reduce(wid, pid, body["desk_id"], body["security_id"], body["fraction"])
                if leaf == ["risk"]:
                    return s.risk(wid, pid)
                if leaf == ["risk", "stress"] and method == "POST":
                    return s.risk_stress(wid, pid, body)
                if leaf == ["otc"]:
                    return s.otc_book(wid, pid)
                if leaf == ["otc", "rfq"] and method == "POST":
                    return s.otc_rfq(wid, pid, body)
                if leaf[:1] == ["otc"] and len(leaf) == 4 and leaf[1] == "rfq" and leaf[3] == "execute" and method == "POST":
                    return s.otc_execute(wid, pid, leaf[2], body["dealer"])
                if leaf[:1] == ["otc"] and len(leaf) == 3 and leaf[2] == "terminate" and method == "POST":
                    return s.otc_terminate(wid, pid, leaf[1])
                if leaf[:1] == ["otc"] and len(leaf) == 2:
                    return s.otc_trade(wid, pid, leaf[1])
                if leaf == ["counterparties"]:
                    return s.counterparties(wid, pid)
                if leaf == ["options"]:
                    return s.options_book(wid, pid)
                if leaf == ["options", "exercise"] and method == "POST":
                    return s.exercise(wid, pid, body["contract_id"], body.get("quantity"))
                if leaf == ["strategies", "preview"] and method == "POST":
                    return s.strategy_preview(wid, pid, body)
                if leaf == ["strategies"]:
                    if method == "POST":
                        return s.place_strategy(wid, pid, body)
                    return s.options_book(wid, pid)["strategies"]
                if leaf[:1] == ["strategies"] and len(leaf) == 2:
                    return s.strategy(wid, pid, leaf[1])
                if leaf == ["career"]:
                    return s.career(wid, pid)
                if leaf[:1] == ["orders"] and len(leaf) == 2:
                    if method == "DELETE":
                        return s.cancel_order(wid, pid, leaf[1])
                    return s.order(wid, pid, leaf[1])
                if leaf == ["trades"]:
                    return s.trades(wid, pid)
                if leaf[:1] == ["trades"] and len(leaf) == 2:
                    return s.trade(wid, pid, leaf[1])
                if leaf == ["settlements"]:
                    return s.settlements(wid, pid)
                if leaf == ["custody"]:
                    return s.custody(wid, pid)
                if leaf == ["cash"]:
                    return s.cash(wid, pid)
                if leaf == ["ledger"]:
                    return s.ledger(wid, pid, int(query.get("limit", [200])[0]), query.get("account", [None])[0], query.get("security_id", [None])[0])
                if leaf == ["balance-sheet"]:
                    return s.balance_sheet(wid, pid)
                if leaf == ["pnl-explain"]:
                    return s.pnl_explain(wid, pid, query.get("date", [None])[0])
                if leaf == ["nav-explain"]:
                    return s.nav_explain(wid, pid)
                if leaf == ["contribute"] and method == "POST":
                    return s.contribute(wid, pid, body["amount"], body.get("currency", "USD"))
        raise NotFound(f"no route for {method} {path}")


LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def make_handler(router: Router, access_key: str = None, trust_loopback: bool = True):
    """`access_key`: when set, every /api call from a non-loopback client must carry it (header X-FinSim-Key or
    ?key=); the page and its assets are served to anyone who can reach the port, the data is not. The desktop
    launcher on the same machine is exempt (`trust_loopback`); /api/shutdown is loopback-only regardless."""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload, ctype: str = "application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path: str):
            if path in ("/", ""):
                path = "/index.html"
            fp = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
            if not fp.startswith(STATIC_DIR) or not os.path.isfile(fp):
                fp = os.path.join(STATIC_DIR, "index.html")
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            with open(fp, "rb") as f:
                self._send(200, f.read(), ctype)

        def _authorised(self, u) -> bool:
            local = self.client_address[0] in LOOPBACK
            if u.path == "/api/shutdown":
                return local
            if not access_key or (local and trust_loopback):
                return True
            supplied = self.headers.get("X-FinSim-Key") or (parse_qs(u.query).get("key") or [""])[0]
            return bool(supplied) and secrets.compare_digest(supplied, access_key)

        def _handle(self, method: str):
            u = urlparse(self.path)
            if not u.path.startswith("/api/"):
                return self._static(u.path)
            if not self._authorised(u):
                log.warning("%s %s from %s refused: access key missing or wrong", method, u.path, self.client_address[0])
                return self._send(401, {"error": "access key required: open the link printed by `python3 -m finsim phone`, or enter the key"})
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "invalid JSON body"})
            t0 = time.perf_counter()
            code = 200
            try:
                if u.path == "/api/health" and method == "GET":
                    out = router.s.health()          # read-only, never waits behind a save being loaded
                else:
                    with router.lock:
                        out = router.dispatch(method, u.path, parse_qs(u.query), body)
                self._send(200, out)
            except NotFound as e:
                code = 404
                self._send(404, {"error": str(e)})
            except CommandError as e:
                code = 400
                log.warning("%s %s rejected: %s", method, u.path, e)
                self._send(400, {"error": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                code = 400
                log.warning("%s %s bad request: %r", method, u.path, e)
                self._send(400, {"error": f"bad request: {e!r}"})
            except Exception as e:  # pragma: no cover
                code = 500
                log.exception("%s %s failed", method, u.path)
                self._send(500, {"error": f"internal error: {e!r}"})
            finally:
                if method != "GET" or code != 200:
                    log.info("%s %s -> %d (%.0f ms)", method, u.path, code, (time.perf_counter() - t0) * 1000)
                else:
                    log.debug("%s %s -> %d (%.0f ms)", method, u.path, code, (time.perf_counter() - t0) * 1000)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_DELETE(self):
            self._handle("DELETE")

        def log_message(self, fmt, *args):
            if os.environ.get("FINSIM_LOG"):
                super().log_message(fmt, *args)
    return Handler


class Scheduler:
    """Processes due days for career worlds. `tick(at)` is the unit of work; the thread loop just calls it on an
    interval with the real clock. Tests drive `tick` with a fake clock (`at`) and a fake sleep."""

    def __init__(self, router: Router, interval: int = 60, clock=None, sleep=None):
        self.router = router
        self.interval = interval
        self.clock = clock          # callable returning an aware datetime; None = real time
        self.sleep = sleep or time.sleep
        self.stop = threading.Event()
        self.thread: threading.Thread = None

    def tick(self, at=None) -> Dict[str, List[str]]:
        at = at or (self.clock() if self.clock else None)
        s = self.router.s
        st = s.scheduler_state
        done: Dict[str, List[str]] = {}
        # one save at a time under the lock: loading a big save must not stall every request (and the launcher's
        # health probe) behind the whole catch-up
        for info in s.store.list_worlds():
            st["busy"] = info["id"]
            try:
                with self.router.lock:
                    closed = s.catch_up_one(info["id"], at)
            except Exception:  # pragma: no cover
                log.exception("scheduler: world %s failed to catch up", info["id"])
                continue
            if closed:
                done[info["id"]] = closed
        st["busy"] = None
        # then the resting live book of every loaded career save: a limit, stop or trailing stop set to fill live is checked
        # against the latest real quote once a minute whether or not a page is open
        try:
            with self.router.lock:
                st["last_live"] = s.work_live_loaded()
        except Exception:  # pragma: no cover
            log.exception("scheduler: live sweep failed")
        st["ticks"] += 1
        st["last_tick"] = (at.isoformat() if at else datetime.now(timezone.utc).isoformat())
        st["last_result"] = done
        for wid, days in done.items():
            log.info("scheduler: world %s processed %s", wid, ", ".join(days))
        return done

    def loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover
                log.exception("scheduler tick failed")
            self.sleep(self.interval)

    def start(self) -> threading.Thread:
        self.thread = threading.Thread(target=self.loop, daemon=True, name="finsim-scheduler")
        self.thread.start()
        return self.thread


def start_scheduler(router: Router, interval: int = 60) -> threading.Thread:
    """Process due days for career worlds every `interval` seconds, whether or not anyone is logged in."""
    return Scheduler(router, interval).start()


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8000, access_key: str = None):
    from ..store import EventStore
    get_logger("finsim", level=os.environ.get("FINSIM_LOG_LEVEL", "INFO"))   # the server logs at INFO by default; libraries stay quiet
    local_only = host in LOOPBACK + ("localhost",)
    if not local_only and not access_key:
        raise SystemExit("refusing to listen beyond this machine without an access key (use `python3 -m finsim phone`, or pass --key)")
    snap = EventStore.snapshot(db_path, os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups"))
    if snap:
        log.info("saves backed up to %s before opening (the newest ten are kept)", snap)
    service = Service(EventStore(db_path))
    router = Router(service)
    start_scheduler(router)
    httpd = ThreadingHTTPServer((host, port), make_handler(router, access_key))
    router.shutdown = lambda: threading.Thread(target=httpd.shutdown, daemon=True).start()
    reach = "this machine only" if local_only else "your network, access key required"
    log.info("finsim terminal: http://%s:%s/  (db: %s; %s) — career worlds update daily at their configured time", host, port, db_path, reach)
    print(f"finsim terminal: http://{host}:{port}/  (db: {db_path}; {reach}) — career worlds update daily at their configured time")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

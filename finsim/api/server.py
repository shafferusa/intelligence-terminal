"""Standard-library HTTP server exposing the Service as a JSON API and serving
the terminal UI. No third-party dependencies."""
from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .service import NotFound, Service
from ..world import CommandError

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class Router:
    def __init__(self, service: Service):
        self.s = service
        self.lock = threading.Lock()

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        s = self.s
        parts = [p for p in path.split("/") if p]
        # /api/worlds ...
        if parts[:2] == ["api", "worlds"]:
            rest = parts[2:]
            if not rest:
                if method == "GET":
                    return s.list_worlds()
                if method == "POST":
                    return s.create_world(body.get("name", "New world"), body.get("seed", 42), body.get("start_date", "2026-01-05"),
                                          body.get("capital", 10_000_000), body.get("portfolio_name", "Main Portfolio"), body.get("portfolio_type", "PERSONAL"),
                                          body.get("realism", "PROFESSIONAL"), body.get("mode", "SANDBOX"), body.get("initial_regime", "NORMAL_GROWTH"),
                                          body.get("benchmark", "SPXE"))
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
                                          body.get("realism", "PROFESSIONAL"), body.get("mode", "SANDBOX"), body.get("benchmark", "SPXE"))
            if sub[0] == "portfolios" and len(sub) >= 2:
                pid = sub[1]
                leaf = sub[2:]
                if leaf == ["dashboard"]:
                    return s.dashboard(wid, pid)
                if leaf == ["positions"] and len(leaf) == 1:
                    return s.dashboard(wid, pid)["positions"]
                if leaf[:1] == ["positions"] and len(leaf) == 2:
                    return s.position(wid, pid, leaf[1])
                if leaf == ["orders"]:
                    if method == "POST":
                        return s.place_order(wid, pid, body["security_id"], body["side"], body["quantity"], body.get("order_type", "MARKET"),
                                             body.get("limit_price"), body.get("stop_price"), body.get("time_in_force", "DAY"), body.get("strategy_tag"))
                    return s.orders(wid, pid)
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


def make_handler(router: Router):
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

        def _handle(self, method: str):
            u = urlparse(self.path)
            if not u.path.startswith("/api/"):
                return self._static(u.path)
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "invalid JSON body"})
            try:
                with router.lock:
                    out = router.dispatch(method, u.path, parse_qs(u.query), body)
                self._send(200, out)
            except NotFound as e:
                self._send(404, {"error": str(e)})
            except CommandError as e:
                self._send(400, {"error": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": f"bad request: {e!r}"})
            except Exception as e:  # pragma: no cover
                import traceback
                traceback.print_exc()
                self._send(500, {"error": f"internal error: {e!r}"})

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


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8000):
    from ..store import EventStore
    service = Service(EventStore(db_path))
    router = Router(service)
    httpd = ThreadingHTTPServer((host, port), make_handler(router))
    print(f"finsim terminal: http://{host}:{port}/  (db: {db_path})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

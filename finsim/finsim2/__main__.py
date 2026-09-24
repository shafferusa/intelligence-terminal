"""CLI: `python -m finsim2 open` (start the server if needed and open the window), `serve`, `status`, `stop`, `phone`,
`refresh` (download the market history) and `research` (compute evidence, optionally train ML models)."""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, DEFAULT_PORT

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def configure() -> None:
    """Point FinSim's launcher at FinSim2's own home, port and database, and make it start `python -m finsim2 serve`.
    FINSIM2_HOME / FINSIM2_PORT / FINSIM2_DB override the defaults (~/.finsim2, 8865, ~/.finsim2/finsim2.db)."""
    home = os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2")
    os.environ["FINSIM_HOME"] = home
    os.environ["FINSIM_PORT"] = os.environ.get("FINSIM2_PORT") or str(DEFAULT_PORT)
    os.environ["FINSIM_DB"] = os.environ.get("FINSIM2_RESEARCH_DB") or os.environ.get("FINSIM2_DB") or os.path.join(home, "research.db")
    from finsim import app
    app.APP_NAME = APP_NAME

    def serve_argv(p=None):
        return [app.python_exe(windowless=True), "-m", "finsim2", "serve", "--db", app.db_path(), "--port", str(p or app.port())]
    app.serve_argv = serve_argv
    app.legacy_db_path = lambda: os.path.join(home, "none.db")     # FinSim2 never adopts FinSim's saves


def main(argv=None) -> int:
    configure()
    from finsim import app
    ap = argparse.ArgumentParser(prog="finsim2", description="FinSim2: the portfolio-manager edition")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API + the FinSim2 interface in this terminal")
    s.add_argument("--db", default=app.db_path())
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=app.port())
    s.add_argument("--key", default=None)
    sub.add_parser("open", help="start the server if needed and open FinSim2 in its own window")
    sub.add_parser("status", help="is FinSim2 running, where are its saves")
    sub.add_parser("stop", help="stop the FinSim2 server")
    ph = sub.add_parser("phone", help="reach FinSim2 from your phone (behind an access key)")
    ph.add_argument("state", nargs="?", choices=["on", "off", "show"], default="on")
    rf = sub.add_parser("refresh", help="download / update the market history in this terminal")
    rf.add_argument("--full", action="store_true", help="re-download every asset's full history")
    rs = sub.add_parser("research", help="compute the evidence for assets (and optionally train their ML models)")
    rs.add_argument("assets", nargs="*", help="asset ids (default: SPY QQQ TLT GOLD)")
    rs.add_argument("--ml", action="store_true", help="also train the walk-forward ML models")
    au = sub.add_parser("audit", help="replay the Shaffer Score and ML over the whole universe and write SHAFFER_AUDIT.md")
    au.add_argument("assets", nargs="*", help="asset ids (default: every asset with six years of prices)")
    au.add_argument("--workers", type=int, default=3)
    au.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_AUDIT.md"))
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .server import serve
        host = args.host or app.resolve_host()
        serve(args.db, host, args.port, args.key or (None if host in app.LOCAL_HOSTS else app.access_key()))
        return 0
    if args.cmd == "refresh":
        from .data.refresh import refresh
        from .data.store import Store
        st = Store(app.db_path())
        res = refresh(st, full=args.full, progress=lambda d, n, m: print(f"[{d}/{n}] {m}", flush=True))
        print(f"done in {res.get('seconds')}s; errors: {res.get('errors') or 'none'}")
        return 0
    if args.cmd == "research":
        from .data.store import Store
        from .engine.research import Research
        from .engine import ml
        r = Research(Store(app.db_path()))
        for a in args.assets or ["SPY", "QQQ", "TLT", "GOLD"]:
            b = r.bundle(a)
            print(a, b["regime"]["description"], {k: (round(v["score"]) if v.get("score") is not None else None) for k, v in b["horizons"].items()})
            if args.ml:
                ml.train_asset(r, a, progress=lambda d, n, m: print("  ", m, flush=True))
        return 0
    if args.cmd == "audit":
        from .engine import audit
        u = audit.run_universe(app.db_path(), assets=args.assets or None, workers=args.workers, progress=print)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(audit.markdown(u, audit.aggregates(u)))
        print("wrote", args.out)
        return 0
    if args.cmd == "open":
        return app.cmd_open()
    if args.cmd == "status":
        return app.cmd_status()
    if args.cmd == "stop":
        if not app.health():
            print("FinSim2 is not running")
            return 0
        app._stop_server()
        print("FinSim2 stopped")
        return 0
    if args.cmd == "phone":
        return app.cmd_phone(args.state)
    return 1


if __name__ == "__main__":
    sys.exit(main())

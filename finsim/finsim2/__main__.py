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
    ic = sub.add_parser("import-chain", help="load an option chain (CSV: asof, underlying, expiry, strike, right, bid, ask, last, iv, delta, gamma, vega, theta, rho, open_interest, volume)")
    ic.add_argument("file")
    ic.add_argument("--source", default="import")
    lb = sub.add_parser("lab", help="ML Lab: research Shaffer weights (hierarchical, walk-forward) and register challengers")
    lb.add_argument("--build", action="store_true", help="first rerun the point-in-time sweeps that produce the research records")
    lb.add_argument("--workers", type=int, default=3)
    lb.add_argument("--weights", action="store_true", help="only the signal-level Shaffer weight research (engine/weights.py)")
    lb.add_argument("--directional", action="store_true", help="only the Shaffer Alpha vs Shaffer Directional research (engine/directional.py)")
    lb.add_argument("--directional-report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_DIRECTIONAL_RESEARCH.md"))
    lb.add_argument("--report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_WEIGHT_RESEARCH.md"))
    ha = sub.add_parser("hedge-audit", help="walk-forward Shaffer Hedge evaluation and ML-adjustment training → HEDGE_AUDIT.md")
    ha.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "HEDGE_AUDIT.md"))
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
    if args.cmd == "import-chain":
        from .data.store import Store
        from .hedge.surface import read_csv
        rows = read_csv(args.file, args.source)
        n = Store(app.db_path()).upsert_option_quotes(rows)
        print(f"stored {n} option quotes for {', '.join(sorted({r['underlying'] for r in rows}))}; hedges now price these contracts from their quotes")
        return 0
    if args.cmd == "lab":
        from .engine import lab
        if args.build:
            from .engine.audit import run_universe
            run_universe(app.db_path(), workers=args.workers, progress=print, skip_ml=True)
        if args.directional:
            from .engine import directional
            dr = directional.run_all(app.db_path(), workers=args.workers, progress=print)
            with open(args.directional_report, "w", encoding="utf-8") as f:
                f.write(directional.markdown(dr))
            print(f"directional research: {dr['seconds']}s; wrote", args.directional_report)
            return 0
        if not args.weights:
            res = lab.run_parallel(app.db_path(), workers=args.workers, progress=print)
            print("family-weight challengers:", ", ".join(res.get("challengers") or []) or "none", f"({res['seconds']}s)")
        from .engine import weights
        wr = weights.run_all(app.db_path(), workers=args.workers, progress=print)
        for lab_, hz in sorted(wr["horizons"].items(), key=lambda kv: dict(lab.LAB_HORIZONS).get(kv[0], 0)):
            b = (hz.get("challengers") or {}).get(hz.get("best") or "", {})
            print(f"{lab_}: best {hz.get('best')} -> {(b.get('gates') or {}).get('status', hz.get('reason', '-'))}")
        print(f"signal-weight research: {wr['seconds']}s")
        from .data.store import Store
        st = Store(app.db_path())
        try:
            live = {v["id"]: lab.live_gate(st, v["id"]) for v in lab.registry(st)["versions"] if v.get("family") == "signal-weights"}
        finally:
            st.close()
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(weights.markdown(wr, live))
        print("wrote", args.report)
        return 0
    if args.cmd == "hedge-audit":
        from .data.store import Store
        from .engine.research import Research
        from .hedge import audit as haudit
        res = haudit.run(Research(Store(app.db_path())), progress=print)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(haudit.markdown(res))
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

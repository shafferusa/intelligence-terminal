"""CLI: `python -m finsim serve` or `python -m finsim demo`."""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="finsim", description="Institutional finance simulation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API + terminal UI")
    s.add_argument("--db", default=os.environ.get("FINSIM_DB", os.path.join("data", "finsim.db")))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    d = sub.add_parser("demo", help="run the vertical-slice walkthrough and print the audit trail")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--start", default="2026-01-05")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .api.server import serve
        serve(args.db, args.host, args.port)
    elif args.cmd == "demo":
        from .demo import run_demo
        run_demo(args.seed, args.start)


if __name__ == "__main__":
    main()

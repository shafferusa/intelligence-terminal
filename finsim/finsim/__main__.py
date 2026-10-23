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
    s.add_argument("--host", default=None, help="default 127.0.0.1; every interface once `finsim phone` is on")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--key", default=None, help="access key for clients beyond this machine (generated and kept by `finsim phone`)")
    d = sub.add_parser("demo", help="run the vertical-slice walkthrough and print the audit trail")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--start", default="2026-01-05")
    i = sub.add_parser("install", help="install FinSim as a local app: background service at login + a launcher (this user only)")
    i.add_argument("--no-open", action="store_true", help="do not open the window after installing")
    sub.add_parser("open", help="start the local server if needed and open the terminal in its own window")
    sub.add_parser("status", help="is the local server running, where is the data, is the login service installed")
    u = sub.add_parser("uninstall", help="remove the login service and launcher (saves are kept unless --purge)")
    u.add_argument("--purge", action="store_true", help="also delete the save database")
    ph = sub.add_parser("phone", help="play from your phone: the server answers on your network behind an access key; prints the link")
    ph.add_argument("state", nargs="?", choices=["on", "off", "show"], default="on")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .api.server import serve
        from .app import resolve_host, access_key, LOCAL_HOSTS
        host = args.host or resolve_host()
        serve(args.db, host, args.port, args.key or (None if host in LOCAL_HOSTS else access_key()))
    elif args.cmd == "demo":
        from .demo import run_demo
        run_demo(args.seed, args.start)
    elif args.cmd == "install":
        from .app import cmd_install
        sys.exit(cmd_install(open_after=not args.no_open))
    elif args.cmd == "open":
        from .app import cmd_open
        sys.exit(cmd_open())
    elif args.cmd == "status":
        from .app import cmd_status
        sys.exit(cmd_status())
    elif args.cmd == "uninstall":
        from .app import cmd_uninstall
        sys.exit(cmd_uninstall(keep_data=not args.purge))
    elif args.cmd == "phone":
        from .app import cmd_phone
        sys.exit(cmd_phone(args.state))


if __name__ == "__main__":
    main()

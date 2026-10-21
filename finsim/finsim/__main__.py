"""``python -m finsim`` — the FinSim Live Terminal CLI (TERMINAL_DESIGN.md §18; WP-14).

Re-pointed on 2026-10-20 from the old simulation's CLI to ``finsim.terminal.app.main``: ``serve``, ``stop``, ``status``,
``install``, ``open`` and ``uninstall`` now mean the terminal (``%USERPROFILE%\\.finsim\\terminal.db``, port 8765).  The
old engine stays reachable as ``python -m finsim legacy [--port 8766] [--refresh]`` — the archive view, which runs the
unchanged ``finsim.api.server.serve`` on a sqlite3-backup COPY of ``finsim.db`` so the live file is never opened.
The old ``demo`` and ``phone`` subcommands are not exposed; ``finsim.app`` and ``finsim.api`` are not edited.
"""
from __future__ import annotations

import sys

from .terminal.app import main

if __name__ == "__main__":
    sys.exit(main())

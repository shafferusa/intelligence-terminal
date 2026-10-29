"""``python -m finsim`` - the FinSim Terminal (FinSim1) CLI (TERMINAL_DESIGN.md §18; WP-14; FINSIM1_PLAN P7).

Re-pointed on 2026-10-20 from the old simulation's CLI to ``finsim.terminal.app.main``: ``serve``, ``stop``, ``status``,
``install``, ``open``, ``uninstall`` and ``import`` mean the terminal (``%USERPROFILE%\\.finsim\\terminal.db``, port 8765).
From the repository root the outer shim ``finsim/__main__.py`` (1.0.6) runs this same entry point.  The old engine's
``legacy`` archive view left the command line in 1.0.6 (owner question Q8; nothing deleted: ``finsim.api``,
``finsim.engines`` and ``finsim.db`` stay as they are); the old ``demo`` / ``phone`` subcommands are not exposed;
``finsim.app`` and ``finsim.api`` are not edited.
"""
from __future__ import annotations

import sys

from .terminal.app import main

if __name__ == "__main__":
    sys.exit(main())

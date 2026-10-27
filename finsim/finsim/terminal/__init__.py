"""FinSim Live Terminal — the ledger-backed paper-trading terminal (TERMINAL_DESIGN.md).

Core package layout (§2): config, units, clock, calendar, sessions, db, ledger, commands, accounting, instruments,
tables, books, treasury and quotes are the core every other package imports; nothing in the core imports api,
server or app.  Standard library only; Python 3.12 and 3.14; zones only through ``finsim.tzfallback.get_zone``.
"""
VERSION = "1.0.4"
APP_ID = "finsim-terminal"
SCHEMA_VERSION = 1

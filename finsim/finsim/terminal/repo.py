"""Repo and reverse repo — Build 2 stub (TERMINAL_DESIGN.md §4.1 ``repo.*``, §12.2 repo row, §13.9; WP-06).

Every handler answers 400 ``NOT_IN_THIS_BUILD`` and posts nothing; the Build 2 body is specified here as data so the
UI's "NOT IN THIS BUILD" rows and ``/api/unsupported`` can state the terms honestly instead of inventing a market.

Build 2 body (§13.9, not built):
- ``repo.open {account_id, side: REPO | REVERSE, instrument_id (bond), face, term_days}``: price = the bond's
  computed mark (never executable in Build 1), ``principal_c = face × px / 100 × (1 − haircut)``; REPO posts
  ``Dr 1010 / Cr 2500`` and flags the bond encumbered (``BOND_ENCUMBERED`` on sells); REVERSE posts ``Dr 1500 / Cr 1010``.
- Rates (``tables.RATES['repo']``): SOFR + 0.05 % (UST) / + 0.35 % (IG corporates); SOFR − 0.05 % when lending cash;
  haircuts UST 2 %, IG 5 %, equities 15 %; nightly accrual ACT/360 (``accr:repo:{repo_id}:{D}``, 5500 / 4500).
- ``repo.close`` at maturity returns principal plus accrued; ``repo.accrue`` is the ACCRUE_DAILY kind.
"""
from __future__ import annotations

from .commands import RejectError, TxContext
from . import tables

BUILD2_SPEC = {
    "build": 2,
    "kinds": ["repo.open", "repo.close", "repo.accrue"],
    "rates": {"ust_spread": str(tables.RATES["repo"]["ust"]), "ig_spread": str(tables.RATES["repo"]["ig"]), "lending_spread": str(tables.RATES["repo"]["lending"]),
              "haircuts": {k: str(v) for k, v in tables.RATES["repo"]["haircuts"].items()}, "basis": "ACT/360", "text": tables.RATES["repo"]["text"]},
    "postings": {"REPO": "Dr 1010 / Cr 2500 (bond flagged encumbered)", "REVERSE": "Dr 1500 / Cr 1010", "accrual": "Dr 5500 / Cr 2500 or Dr 1500 / Cr 4500"},
    "note": "Repo and reverse repo are Build 2 (TERMINAL_DESIGN.md §13.9); nothing is posted in this build.",
}


def _not_built(kind: str):
    def handler(ctx: TxContext, payload: dict) -> dict:
        raise RejectError("NOT_IN_THIS_BUILD", f"{kind}: repo and reverse repo are Build 2 (§13.9); nothing was posted", {"spec": BUILD2_SPEC})
    handler.__name__ = f"h_{kind.replace('.', '_')}"
    return handler


h_open = _not_built("repo.open")
h_close = _not_built("repo.close")
h_accrue = _not_built("repo.accrue")


def register(bus) -> None:
    bus.register("repo.open", h_open)
    bus.register("repo.close", h_close)
    bus.register("repo.accrue", h_accrue)

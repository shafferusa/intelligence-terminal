"""Product modules of the FinSim Live Terminal (TERMINAL_DESIGN.md §2.12, §13).

- ``products.options`` — listed options: margin (§13.3), expiry / exercise / assignment (§5.4, §11.2), the chain view (WP-03).
- ``products.futures`` — futures IM / VM / final settlement (WP-04).
- ``products.swaps`` / ``swaps_trs`` / ``dividends`` / ``private`` / ``private_credit`` — later packages.

Each module registers its command handlers through ``register(bus)`` and is bound to the runtime as ``services.<name>``
(``services.options`` …); other packages reach a product only through ``services`` and treat a missing module as
NOT_IN_THIS_BUILD.  Nothing here is imported eagerly, so a missing product never breaks the core.
"""
from __future__ import annotations

__all__ = ["options"]

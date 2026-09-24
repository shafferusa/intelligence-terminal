"""FinSim2's routes on top of FinSim's API: /api/fs2/... for creating a portfolio-manager save with nothing but an
amount, the analytics workspace and the Shaffer Score slot. Everything else is FinSim's API unchanged."""
from __future__ import annotations

import random
from urllib.parse import unquote

from finsim.api.server import Router
from finsim.api.service import NotFound
from finsim.world import CommandError

from . import APP_NAME, shaffer_score
from .analytics import Analytics

MAX_AMOUNT = 1_000_000_000_000


class Router2(Router):
    def __init__(self, service):
        super().__init__(service)
        self.analytics = Analytics(service)

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        parts = [p for p in path.split("/") if p]
        if parts[:2] == ["api", "fs2"]:
            return self.fs2(method, [unquote(x) for x in parts[2:]], query, body or {})
        return super().dispatch(method, path, query, body)

    def fs2(self, method: str, rest: list, query: dict, body: dict):
        if rest == ["info"]:
            return {"app": APP_NAME, "job": "PORTFOLIO_MANAGER", "shaffer": shaffer_score.status()}
        if rest == ["worlds"] and method == "POST":
            return create_pm_save(self.s, body)
        if rest == ["equations"]:
            from finsim.quant.catalog import catalog
            return catalog()
        if len(rest) == 3 and rest[0] == "equations" and rest[2] == "source":
            from finsim.quant.catalog import EQUATIONS, source
            eq = next((e for e in EQUATIONS if e["id"] == rest[1]), None)
            if eq is None:
                raise NotFound(f"no equation {rest[1]}")
            return {"id": eq["id"], "fn": eq["fn"], "source": source(eq["fn"])}
        if rest == ["shaffer"]:
            return shaffer_score.status()
        if len(rest) >= 3 and rest[0] == "worlds":
            wid, leaf = rest[1], rest[2:]
            if leaf == ["scores"]:
                return self.analytics.scoreboard(wid)
            if leaf[0] == "analytics" and len(leaf) == 2:
                try:
                    return self.analytics.asset(wid, leaf[1])
                except KeyError:
                    raise NotFound(f"no asset {leaf[1]}")
        raise NotFound(f"no route for {method} /api/fs2/{'/'.join(rest)}")


def create_pm_save(s, body: dict) -> dict:
    """A FinSim2 save: one portfolio-manager book with the amount chosen. `mode`: LIVE plays the real market day by day
    (prices after each close, live quotes in the session); PRACTICE is a simulated market you advance yourself."""
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise CommandError("choose an amount")
    if not (amount > 0) or amount > MAX_AMOUNT:
        raise CommandError("the amount must be above zero and at most $1 trillion")
    name = (str(body.get("name") or "").strip() or "My Fund")[:60]
    mode = str(body.get("mode") or "LIVE").upper()
    if mode not in ("LIVE", "PRACTICE"):
        raise CommandError("mode must be LIVE or PRACTICE")
    seed = random.randint(1, 999_999)
    if mode == "LIVE":
        r = s.create_world(name, seed, None, amount, name, "PORTFOLIO_MANAGER", "PROFESSIONAL", "SANDBOX", "NORMAL_GROWTH", "SPY", "PORTFOLIO_MANAGER",
                           "REAL_TIME", str(body.get("timezone") or "America/New_York"), None, "NONE", market_source="REAL")
    else:
        r = s.create_world(name, seed, body.get("start_date"), amount, name, "PORTFOLIO_MANAGER", "PROFESSIONAL", "SANDBOX", "NORMAL_GROWTH", "SPY",
                           "PORTFOLIO_MANAGER", "SANDBOX", "America/New_York", None, "NONE", market_source="SIMULATED")
    return {**r, "mode": mode, "amount": amount, "name": name}

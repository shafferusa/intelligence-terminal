"""FinSim2's HTTP API (/api/fs2/...) and its background work: data refresh after each close, forecast scoring,
research and ML jobs. Standard library only; the page and its assets come from finsim2/static."""
from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import unquote

from finsim.api.server import LOOPBACK, make_handler

from . import APP_NAME, shaffer_score
from .jobs import JobManager

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VERSION = "2.0"


from finsim.api.service import NotFound as _BaseNotFound
from finsim.world import CommandError


class NotFound(_BaseNotFound):
    """404 with a message."""


class App:
    """Everything the routes need: the store, the research engine, the portfolio ledger and the job manager."""

    def __init__(self, store=None):
        from .data.store import Store
        from .engine.research import Research
        self.store = store or Store()
        self.research = Research(self.store)
        self.jobs = JobManager()
        self.started = time.time()
        self.scheduler_state = {"last_check": None, "last_refresh": None}

    # ------------------------------------------------------------------ status
    def health(self) -> dict:
        return {"app": APP_NAME, "ok": True, "engine_version": VERSION, "uptime_s": time.time() - self.started, "worlds_stored": 0, "worlds": []}

    def status(self) -> dict:
        s = self.store
        spy = s.price_count("SPY")
        running = [j.view() for j in self.jobs.running()]
        return {"app": APP_NAME, "version": VERSION, "data_ready": spy > 500, "spy_rows": spy, "last_price_date": s.last_price_date("SPY"),
                "assets": len(s.assets()), "data_version": s.data_version(), "jobs": running, "scheduler": self.scheduler_state,
                "shaffer": shaffer_score.status(), "fetch_log": s.fetch_log(15)}

    def ledger(self):
        from .engine.portfolio import Ledger
        return Ledger(self.store, self.research.panel(), "main")

    # ------------------------------------------------------------------ jobs
    def start_refresh(self, full: bool = False, assets: Optional[List[str]] = None):
        from .data.refresh import refresh

        def run(job):
            ids = assets
            summary = refresh(self.store, assets=[self.store.asset(a) for a in ids] if ids else None,
                              progress=lambda d, n, m: job.progress(d, n, m), full=full)
            self.scheduler_state["last_refresh"] = datetime.now(timezone.utc).isoformat()
            learned = self.daily_learning(job) if not ids else {}
            return {"seconds": summary.get("seconds"), "errors": summary.get("errors"), "data_version": summary.get("data_version"), "learning": learned}
        return self.jobs.start("refresh", ",".join(assets or []) or "all", run)

    CORE = ["SPY", "QQQ", "IWM", "EFA", "UST10Y", "TLT", "HYG", "GOLD", "WTI", "DXY", "EURUSD", "BTC"]

    def tracked(self) -> List[str]:
        """Assets the daily loop keeps current: holdings, the watchlist and the core markets."""
        held = [a for a, p in self.ledger().holdings()["positions"].items() if abs(p["quantity"]) > 1e-12]
        watched = [w["asset_id"] for w in self.store.watchlist()]
        return [a for a in dict.fromkeys(held + watched + self.CORE) if self.store.asset(a) and self.store.price_count(a)]

    def daily_learning(self, job=None) -> dict:
        """After new data: grade matured forecasts (both engines), compute today's Shaffer Scores for tracked assets (they
        enter the append-only ledger), make today's ML forecasts from the saved models, and retrain ML only on schedule
        (once a month, or when an asset has none). Historical predictions are never rewritten."""
        from .engine import ml, tracking
        out = {"graded": 0, "shaffer": [], "ml_forecasts": [], "retrained": [], "errors": []}
        try:
            out["graded"] = tracking.score_matured(self.store, self.research.panel())
            try:                                        # hedge recommendations whose horizon has passed
                from .hedge.service import grade as grade_hedges
                out["hedges_graded"] = grade_hedges(self)
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"hedge grading: {e}")
        except Exception as e:
            out["errors"].append(f"grading: {e}")
        month = self.research.panel().calendar()[-1][:7]
        ids = self.tracked()
        for k, a in enumerate(ids):
            if job:
                job.progress(k, len(ids), f"learning: {a}")
            try:
                self.research.bundle(a)                        # today's Shaffer Score -> ledger
                out["shaffer"].append(a)
            except Exception as e:
                out["errors"].append(f"{a} Shaffer: {e}")
                continue
            try:
                saved = self.store.kv_get(f"mlmodels:{a}")
                if saved is None or str(saved.get("trained_at", ""))[:7] != month or saved.get("version") != ml.VERSION:
                    ml.train_asset(self.research, a)           # the schedule: monthly (records its own forecasts)
                    out["retrained"].append(a)
                else:
                    ml.forecast_today(self.research, a)       # daily forecasts from the saved models
                    out["ml_forecasts"].append(a)
            except Exception as e:
                out["errors"].append(f"{a} ML: {e}")
        self.store.audit("learning.daily", month, {k: v for k, v in out.items()})
        return out

    def start_learning(self):
        return self.jobs.start("learning", "daily", lambda job: self.daily_learning(job))

    def start_ml(self, asset_id: str):
        from .engine import ml

        def run(job):
            return ml.train_asset(self.research, asset_id, progress=lambda d, n, m: job.progress(d, n, m))["summary"]
        return self.jobs.start("ml", asset_id, run)

    def start_pooled(self, level: str, key: str):
        from .engine import ml

        def run(job):
            return ml.train_pooled(self.research, level, key, progress=lambda d, n, m: job.progress(d, n, m))["summary"]
        return self.jobs.start("ml_pooled", f"{level}:{key}", run)

    def start_scan(self, ids: List[str]):
        def run(job):
            out = {}
            for k, a in enumerate(ids):
                job.progress(k, len(ids), f"researching {a}")
                try:
                    self.research.bundle(a)
                    out[a] = "ok"
                except Exception as e:
                    out[a] = f"failed: {e}"
            job.progress(len(ids), len(ids), "done")
            return out
        return self.jobs.start("scan", ",".join(ids[:5]) + (f"+{len(ids) - 5}" if len(ids) > 5 else ""), run)


def _num(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


class Router:
    """Stateless dispatch; heavy work runs in the request thread (no global lock) or as a background job."""

    def __init__(self, app: App):
        self.s = app
        self.lock = contextlib.nullcontext()
        self.shutdown = None

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        from .engine.research import clean
        parts = [unquote(p) for p in path.split("/") if p]
        if parts == ["api", "health"]:
            return self.s.health()
        if parts == ["api", "shutdown"] and method == "POST":
            if self.shutdown:
                self.shutdown()
            return {"shutting_down": bool(self.shutdown)}
        if parts[:2] != ["api", "fs2"]:
            raise NotFound(f"no route for {method} {path}")
        q = {k: v[0] for k, v in (query or {}).items()}
        try:
            return clean(self.route(method, parts[2:], q, body or {}))
        except (NotFound, CommandError):
            raise
        except KeyError as e:
            raise NotFound(f"not found: {e.args[0] if e.args else e}")
        except (ValueError, LookupError) as e:
            raise CommandError(str(e))

    # ------------------------------------------------------------------ routes
    def route(self, method: str, r: List[str], q: dict, b: dict):
        app, store, research = self.s, self.s.store, self.s.research
        if r == ["status"]:
            return app.status()
        if r == ["learning"] and method == "POST":
            return app.start_learning().view()
        if r == ["refresh"] and method == "POST":
            return app.start_refresh(bool(b.get("full")), b.get("assets")).view()
        if r == ["jobs"]:
            return app.jobs.list()
        if len(r) == 2 and r[0] == "jobs":
            j = app.jobs.get(r[1])
            if j is None:
                raise NotFound("no such job")
            return j.view()
        if r == ["shaffer"]:
            return shaffer_score.status()
        if r == ["equations"]:
            from finsim.quant.catalog import catalog
            return catalog()
        if len(r) == 3 and r[0] == "equations" and r[2] == "source":
            from finsim.quant.catalog import EQUATIONS, source
            eq = next((e for e in EQUATIONS if e["id"] == r[1]), None)
            if eq is None:
                raise NotFound(f"no equation {r[1]}")
            return {"id": eq["id"], "fn": eq["fn"], "source": source(eq["fn"])}
        if r == ["features"]:
            from .engine.features import FEATURES
            return {k: {"family": v[0], "label": v[1], "description": v[2]} for k, v in FEATURES.items()}
        # ---------------- assets and markets
        if r == ["assets"] and method == "GET":
            return self.assets(q.get("class"))
        if r == ["assets"] and method == "POST":
            from .data.refresh import add_symbol
            a = add_symbol(store, str(b.get("symbol", "")).strip(), b.get("asset_class"))
            return a
        if r == ["markets"]:
            return self.markets(q.get("class"))
        if r == ["scan"] and method == "POST":
            ids = b.get("assets") or [a["id"] for a in store.assets()]
            return app.start_scan(ids).view()
        if len(r) >= 2 and r[0] == "asset":
            return self.asset_routes(method, r[1], r[2:], q, b)
        # ---------------- Shaffer Hedge
        if r and r[0] == "hedge":
            return self.hedge_routes(method, r[1:], q, b)
        if r == ["markets", "net"]:
            return self.markets_net(q.get("h", "3M"))
        # ---------------- portfolio
        if r and r[0] == "portfolio":
            return self.portfolio_routes(method, r[1:], q, b)
        # ---------------- watchlist
        if r == ["watchlist"] and method == "GET":
            rows = []
            for w in store.watchlist():
                lt = research.light(w["asset_id"]) if research.is_cached(w["asset_id"]) else None
                rows.append({**w, "asset": store.asset(w["asset_id"]), "light": lt, "quote": self.quote(w["asset_id"])})
            return rows
        if r == ["watchlist"] and method == "POST":
            if not store.asset(b.get("asset_id", "")):
                raise NotFound("unknown asset")
            store.watch(b["asset_id"], b.get("note", ""))
            return {"ok": True}
        if len(r) == 2 and r[0] == "watchlist" and method == "DELETE":
            store.unwatch(r[1])
            return {"ok": True}
        # ---------------- backtests
        if r == ["backtest"] and method == "POST":
            return self.backtest(b)
        if r == ["backtests"]:
            return store.backtests(50)
        # ---------------- ML (pooled levels) and forecasts
        if r == ["ml", "pooled"] and method == "GET":
            return {k: store.kv_get(k) for k in store.kv_keys("mlpool:")}
        if r == ["ml", "pooled"] and method == "POST":
            return app.start_pooled(b.get("level", "global"), b.get("key", "all")).view()
        if r == ["predictions"]:
            from .engine import tracking
            return tracking.report(store, q.get("asset"))
        if r == ["settings"] and method == "GET":
            from .engine.portfolio import benchmark_id
            return {"benchmark": benchmark_id(store)}
        if r == ["settings"] and method == "POST":
            if "benchmark" in b:
                if not store.asset(str(b["benchmark"])):
                    raise NotFound(f"unknown asset {b['benchmark']}")
                store.kv_set("settings:benchmark", str(b["benchmark"]))
                store.audit("settings.benchmark", "settings", {"benchmark": b["benchmark"]})
            return {"ok": True}
        if r == ["audit"]:
            return store.audit_log(int(q.get("limit", 200)))
        if r == ["model-runs"]:
            return store.model_runs(q.get("key"), q.get("horizon"), int(q.get("limit", 200)))
        raise NotFound(f"no route for {method} /api/fs2/{'/'.join(r)}")

    # ------------------------------------------------------------------ helpers
    def quote(self, asset_id: str) -> dict:
        p = self.s.research.panel()
        c = p.series(asset_id, "close", max_fill=10)
        last = next((i for i in range(len(c) - 1, -1, -1) if c[i] is not None), None)
        if last is None:
            return {"price": None}
        def ch(k):
            return (c[last] / c[last - k] - 1) if last - k >= 0 and c[last - k] else None
        return {"price": c[last], "date": p.calendar()[last], "d1": ch(1), "w1": ch(5), "m1": ch(21), "y1": ch(252)}

    def assets(self, cls: Optional[str]) -> List[dict]:
        out = []
        for a in self.s.store.assets(cls):
            out.append({"id": a["id"], "name": a["name"], "asset_class": a["asset_class"], "sector": a.get("sector"), "currency": a.get("currency"),
                        "country": a.get("country")})
        return out

    def markets(self, cls: Optional[str]) -> List[dict]:
        from .engine.portfolio import ANALYSIS_ONLY, continuous_series
        research = self.s.research
        rows = []
        for a in self.s.store.assets(cls):
            qd = self.quote(a["id"])
            lt = research.light(a["id"]) if research.is_cached(a["id"]) else None
            rows.append({"id": a["id"], "name": a["name"], "asset_class": a["asset_class"], "sector": a.get("sector"), "currency": a.get("currency"),
                         **qd, "scores": (lt or {}).get("scores"), "ml": (lt or {}).get("ml"), "confidence": (lt or {}).get("confidence"),
                         "primary_horizon": (lt or {}).get("primary_horizon"), "researched": lt is not None,
                         "shaffer": (lt or {}).get("shaffer"), "analysis_only": ANALYSIS_ONLY if continuous_series(a) else None})
        return rows

    def asset_routes(self, method, asset_id, rest, q, b):
        app, store, research = self.s, self.s.store, self.s.research
        if store.asset(asset_id) is None:
            raise NotFound(f"unknown asset {asset_id}")
        if not rest:
            out = research.bundle(asset_id)
            h = app.ledger().holdings()
            pos = h["positions"].get(asset_id)
            out["position"] = pos
            out["watched"] = any(w["asset_id"] == asset_id for w in store.watchlist())
            out["ml_job"] = next((j.view() for j in app.jobs.running("ml") if j.key == asset_id), None)
            out["shaffer"] = research.shaffer(asset_id, out)
            return out
        if rest == ["matrix"]:
            from .engine import regimes as rg
            from .engine.features import FEATURES
            return {"matrix": research.matrix(asset_id), "features": {k: {"family": v[0], "label": v[1]} for k, v in FEATURES.items()},
                    "regimes": rg.STATE_LABEL}
        if len(rest) == 3 and rest[0] == "cell":
            return research.cell(asset_id, rest[1], rest[2])
        if len(rest) == 2 and rest[0] == "signal":
            s = research.signal_series(asset_id, rest[1])
            from .engine.research import downsample
            return {"feature": s["feature"], "label": s["label"], "values": downsample(s["dates"], s["values"], 800), "z": downsample(s["dates"], s["z"], 800)}
        if rest == ["prices"]:
            p = research.panel()
            c = p.series(asset_id, "close")
            first = next((i for i, v in enumerate(c) if v is not None), 0)
            from .engine.research import downsample
            return downsample(p.calendar()[first:], c[first:], int(q.get("points", 1500)))
        if rest == ["equations"]:
            return research.equations(asset_id)
        if rest == ["ml"] and method == "POST":
            return app.start_ml(asset_id).view()
        if rest == ["ml"]:
            return research.ml_result(asset_id) or {}
        if rest == ["leaderboard"]:
            from .engine import horizons as hz
            return hz.leaderboard(research.matrix(asset_id), q.get("horizon", "3M"), int(q.get("top", 30)))
        if rest == ["score-history"] or rest == ["shaffer"]:
            full = research.shaffer_full(asset_id)
            out = dict(full)
            out["custom"] = research.shaffer(asset_id)
            from .engine.research import downsample
            out["history"] = {lab: {**h, "chart": downsample(h.get("dates") or [], h.get("raw") or [], 700)} for lab, h in (full.get("history") or {}).items()}
            return out
        if rest == ["net-scores"]:
            from .hedge.scoring import net_scores
            return net_scores(research, asset_id)
        if rest == ["montecarlo"] and method == "POST":
            from .engine.montecarlo import HORIZON_DAYS, simulate
            from .engine.portfolio import aligned_returns
            r = aligned_returns(research.panel(), [asset_id], 252 * int(b.get("years", 10)))[asset_id]
            return simulate(r, HORIZON_DAYS.get(b.get("horizon", "1Y"), 252), int(b.get("paths", 2000)), b.get("method", "bootstrap"),
                            start_value=_num(b.get("start_value"), 10000.0), dd_threshold=_num(b.get("dd"), 0.2))
        if rest == ["correlations"]:
            from .engine.tracking import correlation_decay
            others = [x for x in (q.get("with") or "SPY,QQQ,TLT,GOLD,DXY").split(",") if x and x != asset_id and store.asset(x)]
            return [correlation_decay(research.panel(), asset_id, o) for o in others]
        raise NotFound("no such asset route")

    def markets_net(self, lab: str) -> Dict[str, dict]:
        """Net long / short Shaffer Scores at one horizon for every researched asset (cached per data version)."""
        from .hedge.scoring import net_scores
        from .hedge.market import Market
        research, store = self.s.research, self.s.store
        key = f"netscores:{research.version()}:{lab}"
        cached = store.kv_get(key)
        if cached is not None:
            return cached
        from .hedge.engine import HORIZON
        from .data.universe import HORIZONS as UH
        h = HORIZON.get(lab) or dict(UH).get(lab, 63)
        m = Market(research)
        out = {}
        for a in store.assets():
            if not research.is_cached(a["id"]):
                continue
            try:
                ns = net_scores(research, a["id"], [(lab, h)], m)
                hz = (ns.get("horizons") or {}).get(lab) or {}
                out[a["id"]] = {"long": hz.get("long"), "short": hz.get("short"), "source": hz.get("expected_source"), "short_note": hz.get("short_note")}
            except Exception as e:  # noqa: BLE001
                out[a["id"]] = {"error": str(e)[:120]}
        store.kv_set(key, out)
        return out

    def hedge_routes(self, method, rest, q, b):
        from .hedge import engine as E
        from .hedge import products as P
        from .hedge import service as SV
        app, store, research = self.s, self.s.store, self.s.research
        if rest == ["registry"]:
            from .hedge.risk import UNITS
            return {"products": P.registry_table(), "objectives": E.OBJECTIVES, "factor_units": UNITS,
                    "futures": {k: {kk: v for kk, v in spec.items() if kk != "tick"} for k, spec in P.FUTURES.items()}}
        if rest == ["analyze"] and method == "POST":
            led = app.ledger()
            positions = b.get("positions") or led.positions_for_hedge()
            if not positions:
                raise ValueError("the portfolio holds nothing to hedge")
            nav = _num(b.get("nav")) or SV.nav_now(led)
            return E.analyze(research, positions, b.get("objective") or "auto", b.get("params") or {}, nav=nav, history=b.get("history", True))
        if rest == ["preview"] and method == "POST":
            qty = _num(b.get("quantity"))
            if qty is None and _num(b.get("amount")):
                from .hedge.market import Market
                from .hedge.risk import RiskModel
                m = Market(research)
                pr = P.Priced(P.parse(b["asset_id"], store), m, RiskModel(m))
                per = pr.unit_notional() if pr.inst.type != "SPOT" else pr.unit_value()
                if not per:
                    raise ValueError(f"no price for {b['asset_id']}")
                qty = _num(b["amount"]) / per
                qty = round(qty) if pr.inst.type in ("FUTURE", "OPTION") else qty
            if not qty:
                raise ValueError("enter a quantity or an amount")
            if qty < 0:
                raise ValueError("the quantity must be positive: the side (Buy, Sell, Short, Cover) sets the direction")
            if str(b.get("side", "BUY")).upper() not in ("BUY", "SELL", "SHORT", "COVER"):
                raise ValueError(f"unknown side {b.get('side')!r}: use BUY, SELL, SHORT or COVER")
            return SV.trade_preview(app, b["asset_id"], b.get("side", "BUY"), qty, b.get("objective"), b.get("params") or {})
        if rest == ["execute"] and method == "POST":
            return SV.execute(app, b.get("primary"), b.get("legs") or [], b.get("proposal"), b.get("mode", "trade_hedge"))
        if rest == ["history"]:
            return store.hedges(int(q.get("limit", 200)))
        if rest == ["audit"]:
            from .hedge.history import VERSION
            m = store.kv_get(f"hedgeml:{VERSION}") or {}
            return {"trained": m.get("trained"), "asof": m.get("asof"), "groups": {g: {k: v for k, v in x.items() if k != "model"} for g, x in (m.get("groups") or {}).items()}}
        if rest == ["grade"] and method == "POST":
            return {"graded": SV.grade(app)}
        raise NotFound("no such hedge route")

    def backtest(self, b: dict) -> dict:
        from .engine.backtest import run
        research = self.s.research
        asset_id = b["asset"]
        panel = research.panel()
        sig = b.get("signal", "mom_12_1")
        from .data.universe import horizon_days
        if sig in ("quant_score", "shaffer", "shaffer_calibrated"):
            # the point-in-time Shaffer Score record (weekly, carried forward), in units of 50 points
            series = research.shaffer_series(asset_id, b.get("horizon", "3M"), calibrated=sig == "shaffer_calibrated")
            direction = "forecast (signed)"
        elif sig.startswith("ml:"):
            series = research.ml_oos_series(asset_id, b.get("horizon", "3M"))         # walk-forward, expanding z-scores
            direction = "forecast (signed)"
        else:
            z = research.zscores(asset_id).get(sig)
            if z is None:
                raise NotFound(f"no signal {sig}")
            # point the signal the way its history said AT EACH DATE (outcomes known by then), not the full-sample sign
            from .engine.features import targets
            from .engine.horizons import expanding_direction
            h = horizon_days(b.get("horizon", "3M")) or 63
            dirs = expanding_direction(z, targets(panel.series(asset_id), h), h)
            series = [v * d if v is not None else None for v, d in zip(z, dirs)]
            direction = "point in time (expanding)"
        lag = int(_num(b.get("lag"), 1.0))
        spec = {"entry": _num(b.get("entry"), 1.0), "exit": _num(b.get("exit"), 0.0), "mode": b.get("mode", "long"),
                "min_hold": int(b.get("min_hold") or horizon_days(b.get("horizon", "1M")) or 1), "start": b.get("start"), "end": b.get("end"),
                "cost_bps": _num(b.get("cost_bps"), 5.0), "invert": bool(b.get("invert")), "direction": direction,
                "lag": max(0, min(21, lag)),
                "regime": b.get("regime"), "signal": sig, "asset": asset_id, "horizon": b.get("horizon", "3M")}
        res = run(panel.calendar(), panel.series(asset_id), series, spec, research.regimes(), panel.series("SPY"))
        self.s.store.add_backtest(spec, {"metrics": res["metrics"]})
        return res

    def portfolio_routes(self, method, rest, q, b):
        from .engine import portfolio as pf
        app, store, research = self.s, self.s.store, self.s.research
        led = app.ledger()
        if not rest:
            scores = {}
            for a in led.holdings()["positions"]:
                if research.is_cached(a):
                    bd = research.bundle(a)
                    ph = bd.get("primary_horizon")
                    hs = bd["horizons"].get(ph or "3M") or {}
                    scores[a] = {"quant": hs.get("score"), "ml": hs.get("ml_score"), "shaffer": hs.get("score"), "calibrated": hs.get("calibrated"), "expected": hs.get("expected"),
                                 "confidence": (hs.get("confidence") or {}).get("value"), "horizon": ph}
            return pf.analytics(store, research.panel(), led, scores)
        if rest == ["transactions"]:
            return sorted(store.transactions("main"), key=lambda t: (t["date"], t["id"]), reverse=True)
        if rest == ["deposit"] and method == "POST":
            return {"id": led.deposit(_num(b.get("amount"), 0.0), b.get("date"), b.get("note", ""))}
        if rest == ["withdraw"] and method == "POST":
            return {"id": led.withdraw(_num(b.get("amount"), 0.0), b.get("date"), b.get("note", ""))}
        if rest == ["trade"] and method == "POST":
            qty = _num(b.get("quantity"))
            if qty is None and _num(b.get("amount")):
                # size a dollar amount at the price and exchange rate of the trade date (the close of that day by default)
                from .engine.portfolio import fx_series
                panel = research.panel()
                i = panel.index_of(b.get("date") or panel.calendar()[-1])
                px = _num(b.get("price")) or next((v for v in reversed(panel.series(b["asset_id"], "close", max_fill=10)[:i + 1]) if v is not None), None)
                ccy = (store.asset(b["asset_id"]) or {}).get("currency") or "USD"
                rate = next((v for v in reversed(fx_series(panel, ccy)[:i + 1]) if v is not None), None)
                if not px or not rate:
                    raise ValueError(f"no {b['asset_id']} price or {ccy} rate on that date")
                qty = _num(b.get("amount")) / (px * rate)
            return {"id": led.trade(b["asset_id"], qty, b.get("side", "BUY"), _num(b.get("price")), b.get("date"), _num(b.get("fee"), 0.0), b.get("note", ""))}
        if len(rest) == 2 and rest[0] == "transactions" and method == "DELETE":
            led.void(int(rest[1]))
            return {"ok": True}
        if rest == ["optimize"] and method == "POST":
            return self.optimize(b)
        if rest == ["montecarlo"] and method == "POST":
            from .engine.montecarlo import HORIZON_DAYS, simulate
            an = pf.analytics(store, research.panel(), led)
            held = [p for p in an["positions"] if abs(p["quantity"]) > 1e-12]
            if not held:
                raise ValueError("the portfolio holds nothing to simulate")
            w = {p["asset_id"]: p["market_value"] / an["nav"] for p in held}
            rets = pf.aligned_returns(research.panel(), list(w), 252 * int(b.get("years", 10)))
            series = pf.portfolio_series(rets, w)
            cash_w = an["cash"] / an["nav"] if an["nav"] else 0.0
            rf_d = (an.get("rf") or 0.0) / 252
            series = [(v + cash_w * rf_d) if v is not None else None for v in series]
            return simulate(series, HORIZON_DAYS.get(b.get("horizon", "1Y"), 252), int(b.get("paths", 2000)), b.get("method", "bootstrap"),
                            start_value=an["nav"], dd_threshold=_num(b.get("dd"), 0.2))
        if rest == ["scenario"] and method == "POST":
            from .engine.scenario import run as run_scn
            an = pf.analytics(store, research.panel(), led)
            held = [{"asset_id": p["asset_id"], "market_value": p["market_value"], "duration": p.get("duration"),
                     "convexity": (store.asset(p["asset_id"]) or {}).get("convexity")} for p in an["positions"] if abs(p["quantity"]) > 1e-12]
            shock = {k: _num(v) for k, v in (b.get("shock") or {}).items() if _num(v) is not None}
            return run_scn(store, research.panel(), held, shock, an["nav"])
        if rest == ["drivers"]:
            an = pf.analytics(store, research.panel(), led)
            from .engine.tracking import correlation_decay
            held = [p["asset_id"] for p in an["positions"] if abs(p["quantity"]) > 1e-12]
            pairs = []
            for i in range(len(held)):
                for j in range(i + 1, len(held)):
                    if len(pairs) < 20:
                        pairs.append(correlation_decay(research.panel(), held[i], held[j]))
            return {"factors": an["factors"], "correlation": an["correlation"], "pairs": pairs, "risk": an["risk"], "nav": an["nav"]}
        raise NotFound("no such portfolio route")

    def optimize(self, b: dict) -> dict:
        from .engine import optimize as op
        from .engine import portfolio as pf
        store, research = self.s.store, self.s.research
        led = self.s.ledger()
        an = pf.analytics(store, research.panel(), led)
        assets = b.get("assets") or [p["asset_id"] for p in an["positions"] if abs(p["quantity"]) > 1e-12]
        assets = [a for a in assets if store.asset(a)]
        if len(assets) < 2:
            raise ValueError("choose at least two assets")
        rets = pf.aligned_returns(research.panel(), assets, 252 * int(b.get("years", 5)))
        cov = pf.covariance(rets, assets)
        src = b.get("returns", "historical")
        mu = []
        for a in assets:
            xs = [x for x in rets[a] if x is not None]
            hist = (sum(xs) / len(xs) * 252) if xs else 0.0
            if src in ("quant", "shaffer", "ml"):
                v = None
                if research.is_cached(a):
                    hs = research.bundle(a)["horizons"].get("12M") or {}
                    v = hs.get("expected") if src in ("quant", "shaffer") else hs.get("ml_expected")
                mu.append(v if v is not None else 0.5 * hist)
            else:
                mu.append(0.5 * hist + 0.5 * 0.06)          # shrink history toward a 6% equity-like prior: raw means are noisy
        current = {p["asset_id"]: p["market_value"] for p in an["positions"] if p["asset_id"] in assets}
        out = op.optimise(assets, mu, cov, current, _num(b.get("cap"), 1.0), an.get("rf") or 0.0)
        out["returns_source"] = src
        return out


def serve(db_path: Optional[str] = None, host: str = "127.0.0.1", port: int = 8865, access_key: Optional[str] = None):
    from finsim.log import get_logger
    from .data.store import Store
    log = get_logger("finsim2", level=os.environ.get("FINSIM_LOG_LEVEL", "INFO"))
    local_only = host in LOOPBACK + ("localhost",)
    if not local_only and not access_key:
        raise SystemExit("refusing to listen beyond this machine without an access key (python3 -m finsim2 phone on)")
    app = App(Store(db_path) if db_path else None)
    router = Router(app)
    handler = make_handler(router, access_key, static_dir=STATIC_DIR)
    httpd = ThreadingHTTPServer((host, port), handler)
    router.shutdown = lambda: threading.Thread(target=httpd.shutdown, daemon=True).start()
    threading.Thread(target=scheduler, args=(app,), daemon=True, name="finsim2-scheduler").start()
    print(f"finsim2: http://{host}:{port}/  (research db: {app.store.path if hasattr(app.store, 'path') else db_path})")
    log.info("finsim2 listening on %s:%s", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def scheduler(app: App, interval: int = 900):
    """After each US close (17:30 New York or later on a weekday) refresh the data once; score matured forecasts."""
    from .data.refresh import stale
    time.sleep(5)
    while True:
        try:
            app.scheduler_state["last_check"] = datetime.now(timezone.utc).isoformat()
            if app.store.price_count("SPY") > 0 and stale(app.store) and not app.jobs.running("refresh"):
                app.start_refresh()
        except Exception:
            pass
        time.sleep(interval)

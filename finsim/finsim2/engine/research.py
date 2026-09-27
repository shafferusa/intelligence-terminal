"""Research orchestration: one cached evidence bundle per asset, and the universe scan.

A bundle is everything the Asset Research and Analytics pages need for one asset: features, standardised signals,
the signal-horizon matrix, the regime, quant scores per horizon with expected returns and confidence, "what
matters", ML results when a model run exists, and headline numbers. Bundles are cached in memory and in the store
keyed by the store's data version, so they are recomputed only after new data arrives.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Dict, List, Optional

from ..data.universe import HORIZONS
from . import horizons as hz
from . import regimes as rg
from . import scores as sc
from .align import Panel
from .features import FEATURES, compute_features, family, label, macro_features
from .signals import percentile_of_last, standardize_all, strength_label, trend

BUNDLE_VERSION = "8"          # bump whenever the engines change what a bundle contains


def clean(o):
    """JSON-safe: NaN/inf -> None, tuples -> lists, recursively."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def downsample(dates: List[str], values: List[Optional[float]], points: int = 520) -> dict:
    n = len(dates)
    step = max(1, n // points)
    idx = list(range(0, n, step))
    if idx and idx[-1] != n - 1:
        idx.append(n - 1)
    return {"dates": [dates[i] for i in idx], "values": [values[i] for i in idx]}


class Research:
    def __init__(self, store):
        self.store = store
        self._panel: Optional[Panel] = None
        self._ver = None
        self._lock = threading.RLock()
        self._mem: Dict[str, object] = {}

    # ------------------------------------------------------------------ data
    def panel(self) -> Panel:
        with self._lock:
            ver = self.store.data_version()
            if self._panel is None or ver != self._ver:
                self._panel = Panel(self.store)
                self._ver = ver
                self._mem.clear()
            return self._panel

    def version(self) -> str:
        self.panel()
        return self._ver

    def _memo(self, key: str, fn):
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        v = fn()
        with self._lock:
            self._mem[key] = v
            if len(self._mem) > 64:                       # a small LRU is plenty for one user
                for k in list(self._mem)[:16]:
                    self._mem.pop(k, None)
        return v

    def features(self, asset_id: str) -> Dict[str, list]:
        return self._memo(f"f:{asset_id}", lambda: compute_features(self.panel(), asset_id))

    def zscores(self, asset_id: str) -> Dict[str, list]:
        return self._memo(f"z:{asset_id}", lambda: standardize_all(self.features(asset_id)))

    def regimes(self) -> Dict[str, list]:
        return self._memo("regimes", lambda: rg.compute(self.panel(), macro_features(self.panel())))

    def matrix(self, asset_id: str) -> Dict[str, Dict[str, dict]]:
        key = f"matrix:{asset_id}:{self.version()}:{BUNDLE_VERSION}"
        cached = self.store.kv_get(key)
        if cached is not None:
            return cached
        def build():
            p = self.panel().series(asset_id)
            return clean(hz.matrix(self.zscores(asset_id), p, self.regimes()))
        m = self._memo(key, build)
        self.store.kv_set(key, m)
        return m

    # ------------------------------------------------------------------ bundles
    def _bkey(self, asset_id: str) -> str:
        from .. import shaffer_score as shs
        return f"bundle:{asset_id}:{self.version()}:{BUNDLE_VERSION}:ss{shs.VERSION}"

    def is_cached(self, asset_id: str) -> bool:
        return self.store.kv_get(self._bkey(asset_id)) is not None

    def shaffer_key(self, asset_id: str) -> str:
        from .. import shaffer_score as shs
        return f"shaffer2:{asset_id}:{self.version()}:{shs.VERSION}:{BUNDLE_VERSION}"

    def shaffer_full(self, asset_id: str) -> dict:
        """The Shaffer Score v2 run for an asset (live breakdown, reconstructed history, calibration, evidence,
        performance), computed by the one point-in-time sweep in engine/shaffer.py and cached per data version."""
        from .shaffer import ShafferRun, summarize
        key = self.shaffer_key(asset_id)
        cached = self.store.kv_get(key)
        if cached is None:
            def build():
                run = ShafferRun(self, asset_id)
                return clean(summarize(run.run(records=False), run))
            cached = self._memo(key, build)
            self.store.kv_set(key, cached)
            try:                                   # today's scores go into the append-only prediction ledger
                from .tracking import record_shaffer
                record_shaffer(self.store, self.panel(), asset_id, cached)
            except Exception:
                pass
        return cached

    def ml_result(self, asset_id: str) -> Optional[dict]:
        return self.store.kv_get(f"ml:{asset_id}")

    def bundle(self, asset_id: str) -> dict:
        key = self._bkey(asset_id)
        cached = self.store.kv_get(key)
        if cached is None:
            cached = self._memo(key, lambda: self._build(asset_id))
            self.store.kv_set(key, cached)
        out = dict(cached)
        out["horizons"] = {k: dict(v) for k, v in cached["horizons"].items()}
        ml = self.ml_result(asset_id)
        if ml:
            out["ml"] = ml
            for lab, hs in out["horizons"].items():
                m = (ml.get("horizons") or {}).get(lab) or {}
                hs["ml_score"] = m.get("score")
                hs["ml_expected"] = m.get("expected")
                hs["ml_verified"] = m.get("verified", (m.get("score") or 0) != 0)
                hs["agreement"] = sc.agreement(hs.get("score"), m.get("score"), hs["ml_verified"], (hs.get("confidence") or {}).get("value"))
        return out

    def _build(self, asset_id: str) -> dict:
        t0 = time.time()
        panel = self.panel()
        asset = self.store.asset(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        cal = panel.calendar()
        close = panel.series(asset_id, "close")
        adj = panel.series(asset_id)
        first = next((i for i, v in enumerate(adj) if v is not None), None)
        if first is None:
            raise LookupError(f"no price history for {asset_id}")
        feats = self.features(asset_id)
        z = self.zscores(asset_id)
        reg = self.regimes()
        mat = self.matrix(asset_id)
        ss = self.shaffer_full(asset_id)
        hs = {}
        for lab, _h in HORIZONS:
            r = ss["horizons"].get(lab) or {}
            conf = r.get("confidence")
            compact = lambda xs: [{k: x.get(k) for k in ("signal", "label", "family", "points", "z", "decay")} for x in (xs or [])[:5]]
            hs[lab] = {"horizon": lab, "score": r.get("raw"), "calibrated": r.get("calibrated"), "expected": r.get("expected"),
                       "expected_typical": r.get("expected_typical"), "expected_edge": r.get("expected_edge"), "range": r.get("range"),
                       "confidence": {"value": conf, "label": r.get("confidence_label")} if conf is not None else None,
                       "evidence": r.get("evidence"), "n_eff": r.get("n_eff"), "reason": r.get("reason"), "oos": r.get("oos"),
                       "families": [{k: f.get(k) for k in ("family", "points", "W", "V", "n_active")} for f in (r.get("families") or [])],
                       "contributors": compact(r.get("contributors")), "contradicting": compact(r.get("contradicting"))}
        state = rg.current(reg)
        last = len(cal) - 1
        while last > 0 and close[last] is None:
            last -= 1
        current = []
        for name in FEATURES:
            s = feats.get(name)
            if not s:
                continue
            v = next((x for x in reversed(s[max(0, last - 5):last + 1]) if x is not None), None)
            row = mat.get(name, {})
            bh = hz.best_horizon(row)
            rec = row.get(bh) if bh else None
            zz = z.get(name, [None])[last] if z.get(name) else None
            current.append({"feature": name, "label": label(name), "family": family(name), "value": v, "z": zz,
                            "percentile": percentile_of_last(s[:last + 1], 1260), "direction": trend(s[:last + 1]),
                            "best_horizon": bh, "usefulness": rec.get("usefulness") if rec else None, "ic": rec.get("ic") if rec else None,
                            "hit_rate": rec.get("hit_rate") if rec else None, "p": rec.get("p") if rec else None, "n_eff": rec.get("n_eff") if rec else None,
                            "signal": strength_label(zz, rec.get("direction", 1)) if rec else None})
        prim = sc.primary_horizon(hs)
        chg = lambda k: (adj[last] / adj[last - k] - 1) if last - k >= 0 and adj[last] and adj[last - k] else None
        dates = cal[first:last + 1]
        out = {
            "asset": asset, "as_of": cal[last], "price": close[last], "change": {"1D": chg(1), "1W": chg(5), "1M": chg(21), "YTD": None, "1Y": chg(252)},
            "history_start": cal[first], "sessions": last - first + 1,
            "regime": {"state": state, "labels": {d: rg.STATE_LABEL.get(s) for d, s in state.items() if s}, "description": rg.describe(state)},
            "horizons": hs, "primary_horizon": prim, "current": current,
            "what_matters_now": sc.family_share(hs, ("1W", "1M")), "what_matters_long": sc.family_share(hs, ("6M", "12M", "3Y")),
            "shaffer_version": ss.get("version"), "history_years": ss.get("history_years"),
            "price_history": downsample(dates, close[first:last + 1]),
            "features_available": sum(1 for c in current if c["value"] is not None), "computed_in": None,
            "data_notes": panel.data_notes(),
        }
        out["computed_in"] = round(time.time() - t0, 2)
        return clean(out)

    def light(self, asset_id: str) -> Optional[dict]:
        """Scores per horizon only (tables of many assets)."""
        try:
            b = self.bundle(asset_id)
        except Exception:
            return None
        hs = b["horizons"]
        return {"asset_id": asset_id, "name": b["asset"]["name"], "asset_class": b["asset"]["asset_class"], "price": b["price"], "as_of": b["as_of"],
                "change": b["change"], "scores": {k: v.get("score") for k, v in hs.items()}, "calibrated": {k: v.get("calibrated") for k, v in hs.items()},
                "ml": {k: v.get("ml_score") for k, v in hs.items()}, "agreement": {k: v.get("agreement") for k, v in hs.items()},
                "confidence": {k: (v.get("confidence") or {}).get("value") for k, v in hs.items()},
                "expected": {k: v.get("expected") for k, v in hs.items()},
                "primary_horizon": b["primary_horizon"], "regime": b["regime"]["description"],
                "shaffer": {k: v.get("score") for k, v in hs.items()}}

    def shaffer_inputs(self, b: dict, mat: Optional[dict] = None, z: Optional[dict] = None, state: Optional[dict] = None) -> dict:
        """The documented inputs of the Shaffer Score (see finsim2/shaffer_score.py): today's z-score and raw value of
        every signal, its evidence at every horizon, and the current regime."""
        asset_id = b["asset"]["id"]
        mat = mat if mat is not None else self.matrix(asset_id)
        if z is None:
            z = self.zscores(asset_id)
        z_today = {k: next((x for x in reversed(v[-5:]) if x is not None), None) for k, v in z.items()}
        raw = {c["feature"]: c["value"] for c in b.get("current") or []}
        signals = {name: {"family": family(name), "label": label(name), "value": raw.get(name), "z": z_today.get(name), "evidence": row}
                   for name, row in mat.items()}
        return {"asset": {**b["asset"], "price": b.get("price"), "as_of": b.get("as_of")}, "regime": state if state is not None else b["regime"]["state"],
                "horizons": [lab for lab, _ in HORIZONS], "signals": signals}

    def shaffer(self, asset_id: str, bundle: Optional[dict] = None) -> Optional[dict]:
        """A live-only custom score from an override file (~/.finsim2/shaffer_score.py), shown alongside the built-in
        Shaffer Score; None without an override."""
        from .. import shaffer_score as shs
        if not shs.is_override():
            return None
        b = bundle or self.bundle(asset_id)
        return shs.custom_score(self.shaffer_inputs(b))

    def signal_series(self, asset_id: str, name: str) -> dict:
        """One feature and its standardised signal over time (charts and backtests)."""
        cal = self.panel().calendar()
        f = self.features(asset_id).get(name)
        z = self.zscores(asset_id).get(name)
        if f is None:
            raise KeyError(name)
        return {"feature": name, "label": label(name), "dates": cal, "values": f, "z": z}

    def cell(self, asset_id: str, signal: str, horizon: str) -> dict:
        """A signal-horizon matrix cell in depth: the record, the return distribution by signal quintile, the
        rolling IC, and a default threshold backtest."""
        from .backtest import run as run_bt
        from .features import targets
        panel = self.panel()
        mat = self.matrix(asset_id)
        rec = (mat.get(signal) or {}).get(horizon)
        if rec is None:
            raise KeyError(f"{signal} / {horizon}")
        h = dict(HORIZONS)[horizon]
        p = panel.series(asset_id)
        z = self.zscores(asset_id)[signal]
        tgt = targets(p, h)
        step = 1 if h <= 5 else max(1, h // 4)
        pairs = [(z[i], tgt[i]) for i in range(0, len(p), step) if z[i] is not None and tgt[i] is not None]
        pairs.sort()
        q = []
        if len(pairs) >= 50:
            k = len(pairs) // 5
            for j in range(5):
                chunk = pairs[j * k:(j + 1) * k] if j < 4 else pairs[4 * k:]
                rs = [t for _, t in chunk]
                q.append({"quintile": j + 1, "mean": sum(rs) / len(rs), "hit": sum(1 for r in rs if r > 0) / len(rs), "n": len(rs) * step})
        # rolling IC over 3-year windows
        from .horizons import pearson_idx, ranks
        sr, tr = ranks(z), ranks(tgt)
        cal = panel.calendar()
        roll = []
        for end in range(756, len(p), 63):
            idx = [i for i in range(end - 756, end - h, step) if z[i] is not None and tgt[i] is not None]
            if len(idx) >= 40:
                roll.append({"date": cal[end], "ic": pearson_idx(sr, tr, idx)})
        # the backtest points the signal the way its history said AT EACH DATE (not the full-sample direction above)
        dirs = hz.expanding_direction(z, tgt, h)
        directed = [v * d if v is not None else None for v, d in zip(z, dirs)]
        bt = run_bt(cal, p, directed, {"entry": 1.0, "exit": 0.0, "min_hold": h, "lag": 1, "direction": "point in time (expanding)"},
                    self.regimes(), panel.series("SPY"))
        return clean({"asset_id": asset_id, "signal": signal, "label": label(signal), "family": family(signal), "horizon": horizon, "record": rec,
                      "quintiles": q, "rolling_ic": roll, "backtest": bt, "regime_labels": rg.STATE_LABEL})

    # ------------------------------------------------------------------ point-in-time quant score history
    def score_history(self, asset_id: str, horizon: str = "3M", years: int = 10, every: int = 63) -> dict:
        """The quant score as it would have read on each date: every `every` sessions the signal weights are
        re-estimated from data whose outcomes were known by then (no lookahead), and the composite runs forward
        with those weights until the next re-estimate. This is also the series the backtester uses for the score."""
        key = f"scorehist:{asset_id}:{horizon}:{self.version()}:{BUNDLE_VERSION}"
        cached = self.store.kv_get(key)
        if cached is not None:
            return cached
        from .features import targets
        from .horizons import evaluate, persistence, ranks
        import math as _m
        panel = self.panel()
        cal = panel.calendar()
        h = dict(HORIZONS)[horizon]
        p = panel.series(asset_id)
        z = self.zscores(asset_id)
        tgt = targets(p, h)
        tr = ranks(tgt)
        n = len(cal)
        start = max(756 + h, n - years * 252)
        comp = [None] * n
        score = [None] * n
        cut_weights = []
        persist = {name: persistence(s[:start]) for name, s in z.items()}      # from data before the first refit only
        cache: dict = {}
        for c in range(start, n, every):
            ws, nes = {}, []
            for name, s in z.items():
                rec = evaluate(s, tgt, h, None, cutoff=c, persist=persist[name], rank_cache=cache)
                if rec and rec.get("usefulness") is not None:
                    ws[name] = rec["usefulness"]
                    nes.append(rec["n_eff"])
            cut_weights.append({"date": cal[c], "n_signals": len(ws), "top": sorted(ws.items(), key=lambda kv: -abs(kv[1]))[:5]})
            den = sum(abs(w) for w in ws.values())
            top5 = sorted((abs(w) for w in ws.values()), reverse=True)[:5]
            strength = min(1.0, (sum(top5) / len(top5)) / sc.STRONG_IC) * sc.sample_factor(nes) if top5 else 0.0
            for t in range(c, min(n, c + every)):
                if den <= 0:
                    continue
                num = dd = 0.0
                for name, w in ws.items():
                    v = z[name][t]
                    if v is not None:
                        num += w * v
                        dd += abs(w)
                if dd > 0:
                    comp[t] = num / dd
                    score[t] = 100.0 * _m.tanh(comp[t]) * strength
        # how the point-in-time score did: IC against the returns that followed
        from .horizons import pearson_idx, evidence
        idx = [i for i in range(start, n, max(1, h // 4)) if comp[i] is not None and tgt[i] is not None]
        ic = pearson_idx(ranks(comp), tr, idx) if len(idx) >= 20 else None
        ev = evidence(ic, max(1.0, len(idx) * max(1, h // 4) / h)) if ic is not None else {"t": None, "p": None}
        first = next((i for i in range(n) if score[i] is not None), n - 1)
        out = clean({"asset_id": asset_id, "horizon": horizon, "every": every, "series": downsample(cal[first:], score[first:], 600),
                     "composite": comp, "oos_ic": ic, "oos_t": ev["t"], "oos_p": ev["p"], "oos_n": len(idx), "refits": cut_weights[-12:],
                     "note": "Weights re-estimated every quarter from outcomes known at the time; the IC is out of sample."})
        self.store.kv_set(key, out)
        return out

    def composite_series(self, asset_id: str, horizon: str) -> list:
        """The point-in-time Shaffer Score (raw) on the calendar: each weekly score held until the next one."""
        return self.shaffer_series(asset_id, horizon)

    def shaffer_series(self, asset_id: str, horizon: str, calibrated: bool = False) -> list:
        ss = self.shaffer_full(asset_id)
        hist = (ss.get("history") or {}).get(horizon) or {}
        cal = self.panel().calendar()
        pos = {d: i for i, d in enumerate(cal)}
        out = [None] * len(cal)
        dates, vals = hist.get("dates") or [], hist.get("calibrated" if calibrated else "raw") or []
        for k, d in enumerate(dates):
            i = pos.get(d)
            if i is None or vals[k] is None:
                continue
            end = pos.get(dates[k + 1], len(cal)) if k + 1 < len(dates) else min(len(cal), i + 5)
            for t in range(i, end):
                out[t] = vals[k] / 50.0            # ±50 -> ±1 so the backtest's thresholds read in "strong evidence" units
        return out

    def ml_oos_series(self, asset_id: str, horizon: str) -> list:
        """Walk-forward (out-of-sample) ensemble forecasts on the calendar, each standardised with the mean and SD of
        the forecasts made before it only (expanding_standardize), carried forward between rows."""
        ml = self.ml_result(asset_id) or {}
        oos = ((ml.get("horizons") or {}).get(horizon) or {}).get("oos") or []
        n = len(self.panel().calendar())
        out = [None] * n
        if not oos:
            raise LookupError(f"no ML model for {asset_id} at {horizon}: train it on the ML Lab first")
        rows = sorted(oos)
        zs = expanding_standardize([i for i, _, _ in rows], [pr for _, pr, _ in rows])
        for k, (i, _pr, _) in enumerate(rows):
            end = rows[k + 1][0] if k + 1 < len(rows) else min(n, i + 21)
            for t in range(i, min(n, end)):
                out[t] = zs[k]
        return out

    # ------------------------------------------------------------------ equations tab
    def equation_context(self, asset_id: str, window: int = 2520) -> dict:
        panel = self.panel()
        cal = panel.calendar()
        n = len(cal)
        lo = max(0, n - window)
        sl = lambda s: s[lo:]
        asset = dict(self.store.asset(asset_id) or {"id": asset_id})
        adj, close = panel.series(asset_id), panel.series(asset_id, "close")
        last = max((i for i in range(n) if adj[i] is not None), default=None)
        if last is not None and last >= 252 and adj[last - 252] and close[last - 252] and close[last]:
            import math as _m
            asset["dividend_yield"] = max(0.0, _m.log(adj[last] / adj[last - 252]) - _m.log(close[last] / close[last - 252]))
        mac = macro_features(panel)
        pct = lambda sid: [v / 100.0 if v is not None else None for v in panel.macro(sid)]
        macro = {"y3m": pct("DGS3MO"), "y2": pct("DGS2"), "y5": pct("DGS5"), "y10": pct("DGS10"), "y30": pct("DGS30"), "baa": pct("DBAA"),
                 "credit_spread": mac.get("credit_spread"), "vix": mac.get("vix"), "cpi_yoy": mac.get("cpi_yoy"), "real_y10": mac.get("real_y10"),
                 "breakeven_10y": mac.get("breakeven_10y"),
                 "dollar": panel.series("DXY") if self.store.asset("DXY") else [None] * n,
                 "oil": panel.series("WTI") if self.store.asset("WTI") else [None] * n,
                 "gold": panel.series("GOLD") if self.store.asset("GOLD") else [None] * n}
        from .features import log_returns
        uni = {}
        for a in ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GOLD", "WTI", "DXY", "BTC", "XLK", "XLF", "XLE", "XLV", asset_id):
            if self.store.asset(a):
                r = log_returns(panel.series(a))[-252:]
                if sum(1 for v in r if v is not None) > 200:
                    uni[a] = r
        rf = next((v for v in reversed(macro["y3m"]) if v is not None), 0.04)
        port = None
        try:
            from .portfolio import aligned_returns
            h = Ledger_holdings(self)
            if h:
                port = {"weights": h, "returns": aligned_returns(panel, list(h), 756)}
        except Exception:
            port = None
        ml = self.ml_result(asset_id)
        mlc = None
        if ml:
            mlc = {"logistic_prob_up": {k: v.get("prob_up") for k, v in (ml.get("horizons") or {}).items()},
                   "predictions": {k: v.get("expected") for k, v in (ml.get("horizons") or {}).items()}, "importance": ml.get("family_importance")}
        return {"asset": asset, "dates": sl(cal), "close": sl(close), "adj": sl(adj), "high": sl(panel.series(asset_id, "high")),
                "low": sl(panel.series(asset_id, "low")), "volume": sl(panel.series(asset_id, "volume")), "market": sl(panel.series("SPY")),
                "macro": {k: sl(v) if v else [None] * (n - lo) for k, v in macro.items()}, "universe_returns": uni,
                "features": {k: sl(v) for k, v in self.features(asset_id).items() if not k.startswith("_")},
                "signals": self.matrix(asset_id), "rf": rf, "portfolio": port, "ml": mlc, "horizons": [list(x) for x in HORIZONS]}

    def equations(self, asset_id: str) -> dict:
        key = f"equations:{asset_id}:{self.version()}:{BUNDLE_VERSION}"
        cached = self.store.kv_get(key)
        if cached is not None:
            return cached
        from . import equations as eq
        ctx = self.equation_context(asset_id)
        t0 = time.time()
        out = clean({"asset_id": asset_id, "catalog": eq.evaluate_catalog(ctx), "tabs": eq.analytics_tabs(ctx), "summary": eq.summary_numbers(ctx),
                     "computed_in": None})
        out["computed_in"] = round(time.time() - t0, 2)
        self.store.kv_set(key, out)
        return out


# ------------------------------------------------------------------ many assets: the serial loop's results, in parallel
class ShafferBatch:
    """`shaffer_full` for many assets in worker processes, giving exactly what calling it on each asset in turn
    (in the order given) would give, with every store write still made here, by one writer, in that order.

    What makes a serial loop order-dependent is the priors: each fresh sweep reads every OTHER asset's stored January
    checkpoints and then stores its own, so an asset's priors include the new checkpoints of the assets swept before
    it and the stored ones of the rest. The checkpoints do not depend on the priors, so each worker first computes its
    asset's new checkpoints and publishes them (a file in a scratch directory), then waits for those of the fresh
    assets before it, and sweeps with the checkpoints the serial loop would have shown it: the stored ones, replaced by
    the new ones where they differ. `take(asset)`, called in order, stores the checkpoints and the result as the loop
    would, after checking that what the worker assumed is what the loop has stored by then; otherwise (an asset
    before it failed after publishing, or a worker was lost) it sweeps the asset here, with the loop's checkpoints.
    Assets whose result is already cached for this data version are not swept (the loop reads the cache). Workers
    are forked: they share this process's code and hash seed (and with it the result's dict orders)."""

    def __init__(self, research: "Research", assets: List[str], workers: Optional[int] = None, progress=None):
        import multiprocessing as mp
        import os
        import tempfile
        from concurrent.futures import ProcessPoolExecutor
        from .shaffer import checkpoint_key, prior_cache
        self.r, st = research, research.store
        self.fresh = [a for a in assets if st.kv_get(research.shaffer_key(a)) is None]
        self.saved: Dict[str, dict] = {}                 # checkpoints stored by take(), per fresh asset
        self.agree = True                                # every asset taken so far stored what the workers assumed
        self.here = 0                                    # assets swept in this process (fallback)
        self.futures: Dict[str, object] = {}
        self.ex, self.dir = None, None
        n = max(1, min(workers or usable_cpus(), len(self.fresh)))
        on_disk = not getattr(st, "_memory", False)          # workers open the store by its path
        self.workers = n if n > 1 and on_disk and "fork" in mp.get_all_start_methods() else 0
        if not self.workers:
            return                                       # take() is shaffer_full, in this process
        self.stored = {a: st.kv_get(checkpoint_key(a)) for a in self.fresh}
        self.base = dict(prior_cache(research)["per"])
        self.dir = tempfile.mkdtemp(prefix="finsim2-batch-")
        self.ex = ProcessPoolExecutor(self.workers, mp_context=mp.get_context("fork"), initializer=_batch_init,
                                      initargs=(st.path, self.base, self.stored, self.fresh, self.dir))
        for a in self.fresh:
            self.futures[a] = self.ex.submit(_batch_sweep, a)
        (progress or (lambda m: None))(f"{len(self.fresh)} of {len(assets)} assets to sweep, on {self.workers} worker processes")

    def take(self, asset_id: str) -> dict:
        """The asset's shaffer_full, with its checkpoints, cache entry and first ledger rows written as the serial
        loop writes them; raises what the serial loop would raise."""
        from .shaffer import checkpoint_key, parse_checkpoints
        r, st = self.r, self.r.store
        if not self.workers or asset_id not in self.futures:
            return r.shaffer_full(asset_id)
        fut = self.futures.pop(asset_id)
        result = None
        if self.agree:
            try:
                result = fut.result()
            except Exception:  # noqa: BLE001  the sweep raised, or a worker was lost: redone here (raises the same)
                result = None
        else:
            fut.cancel()
        if result is None:                                 # sweep it here, with the checkpoints the loop has stored
            published = _published(self.dir, self.fresh.index(asset_id)) if self.agree else None
            changed = {b: parse_checkpoints(p) for b, p in self.saved.items() if p != self.stored.get(b)}
            per = {**self.base, **changed}
            self.here += 1
            try:
                payload, full, err = _sweep(r, asset_id, {a: per[a] for a in sorted(per, key=checkpoint_key)})
            except Exception:
                if published is not None:                  # later workers assumed it stores new checkpoints: it stores none
                    self.agree = False
                raise
        else:
            _, published, payload, full, err = result
        # later assets were swept assuming this one stores `published` (None: its stored checkpoints stay)
        if published != (payload if payload != self.stored.get(asset_id) else None):
            self.agree = False
        st.kv_set(checkpoint_key(asset_id), payload)          # as ShafferRun.run does at the end of a full run
        r._prior_cache = None
        self.saved[asset_id] = payload
        if err is not None:
            raise err
        st.kv_set(r.shaffer_key(asset_id), full)
        try:                                   # today's scores go into the append-only prediction ledger
            from .tracking import record_shaffer
            record_shaffer(st, r.panel(), asset_id, full)
        except Exception:
            pass
        return full

    def close(self):
        import shutil
        if self.ex is not None:
            self.ex.shutdown(wait=True, cancel_futures=True)
            self.ex = None
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None


def usable_cpus() -> int:
    """The CPUs this process may use: its CPU affinity, capped by a cgroup CPU quota when the container sets one
    (os.cpu_count() reports the host's)."""
    import os
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    for path, period in (("/sys/fs/cgroup/cpu.max", None), ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us")):
        try:
            with open(path) as f:
                parts = f.read().split()
            if period:
                with open(period) as f:
                    parts = parts[:1] + f.read().split()[:1]
            if parts and parts[0] not in ("max", "-1") and len(parts) > 1 and float(parts[1]) > 0:
                n = min(n, max(1, int(float(parts[0]) / float(parts[1]))))
            break
        except (OSError, ValueError):
            continue
    return max(1, n)


def _sweep(research: "Research", asset_id: str, checkpoints: Optional[Dict[str, tuple]], run=None):
    """(the new checkpoint payload, clean(summarize(...)), the error summarize raised) of a full sweep that stores
    nothing; `checkpoints` = the priors' source ({asset: parse_checkpoints(...)}, None = the stored ones). An error in
    the sweep itself is raised (the serial loop stores nothing then)."""
    from .shaffer import ShafferRun, aggregate_priors, checkpoint_payload, summarize
    if run is None:
        run = ShafferRun(research, asset_id, prior_checkpoints=checkpoints)
    else:                                                  # a run built without priors (for its checkpoints): add them
        run.priors = aggregate_priors(checkpoints, asset_id, run.cls, run.signals)
        run.yearly, run._prior_ic = {}, {}
    res = run.run(save=False, records=False)
    payload = checkpoint_payload(run.cls, run.yearly, run.signals)
    try:
        return payload, clean(summarize(res, run)), None
    except Exception as e:  # noqa: BLE001
        return payload, None, e


_WORKER: dict = {}


def _batch_init(db_path: str, base: Dict[str, tuple], stored: Dict[str, dict], fresh: List[str], folder: str):
    from ..data.store import Store
    _WORKER.clear()
    _WORKER.update(research=Research(Store(db_path)), base=base, stored=stored, fresh=fresh, dir=folder, parsed={})


def _publish(folder: str, i: int, kind: str, payload: Optional[dict] = None):
    import json
    import os
    tmp = os.path.join(folder, f".{i}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload) if payload is not None else "")
    os.replace(tmp, os.path.join(folder, f"{i}.{kind}"))


def _published(folder: Optional[str], i: int, wait: float = 0.0) -> Optional[dict]:
    """The checkpoints the i-th fresh asset published: the payload if they changed, None if unchanged or failed.
    Waits up to `wait` seconds for them (then raises TimeoutError)."""
    import json
    import os
    t0 = time.time()
    while folder:
        if os.path.exists(os.path.join(folder, f"{i}.new")):
            with open(os.path.join(folder, f"{i}.new"), encoding="utf-8") as f:
                return json.loads(f.read())
        if os.path.exists(os.path.join(folder, f"{i}.same")) or os.path.exists(os.path.join(folder, f"{i}.none")):
            return None
        if time.time() - t0 > wait:
            if wait:
                raise TimeoutError(f"checkpoints of asset {i} not published")
            return None
        time.sleep(0.05)
    return None


def _forget(research: "Research", asset_id: str):
    """Drop an asset's features, z-scores and prices from a worker's caches (a worker sees many assets)."""
    with research._lock:
        for k in (f"f:{asset_id}", f"z:{asset_id}"):
            research._mem.pop(k, None)
    p = research._panel
    if p is not None and asset_id != p.benchmark:
        with p._lock:
            for k in [k for k in p._cache if len(k) > 1 and k[1] == asset_id]:
                del p._cache[k]


def _batch_sweep(asset_id: str):
    """Worker: publish the asset's new checkpoints, wait for those of the fresh assets before it, sweep. Returns
    ("worker", the published payload or None, the payload, clean(summarize(...)), summarize's error)."""
    from .shaffer import ShafferRun, checkpoint_key, checkpoint_payload, parse_checkpoints
    w = _WORKER
    r, fresh, folder = w["research"], w["fresh"], w["dir"]
    i = fresh.index(asset_id)
    try:
        try:
            run = ShafferRun(r, asset_id, use_priors=False)
            run.run(checkpoints_only=True, save=False)
            new = checkpoint_payload(run.cls, run.yearly, run.signals)
        except BaseException:
            _publish(folder, i, "none")
            raise
        changed = new != w["stored"].get(asset_id)
        _publish(folder, i, "new" if changed else "same", new if changed else None)
        per = dict(w["base"])
        for j in range(i):                                 # the loop has stored these by the time it reaches this asset
            b = fresh[j]
            if b not in w["parsed"]:
                pb = _published(folder, j, wait=3600.0)
                w["parsed"][b] = parse_checkpoints(pb) if pb is not None else None
            if w["parsed"][b] is not None:
                per[b] = w["parsed"][b]
        payload, full, err = _sweep(r, asset_id, {a: per[a] for a in sorted(per, key=checkpoint_key)}, run=run)
        return "worker", (new if changed else None), payload, full, err
    finally:
        _forget(r, asset_id)


def expanding_standardize(rows: List[int], values: List[float], min_prior: int = 20) -> List[Optional[float]]:
    """z-score of each value against the values at strictly earlier rows only (expanding mean and sample SD; rows
    ascending). None until `min_prior` earlier values exist, or when their SD is zero. A later value never changes an
    earlier z-score, so thresholding the result carries no look-ahead."""
    out: List[Optional[float]] = [None] * len(values)
    cnt, mean, m2 = 0, 0.0, 0.0
    pending: List[float] = []                   # values at the current row: they join the statistics once the row is past
    prev_row = None
    for k, (r, v) in enumerate(zip(rows, values)):
        if r != prev_row:
            for x in pending:
                cnt += 1
                d = x - mean
                mean += d / cnt
                m2 += d * (x - mean)
            pending, prev_row = [], r
        if v is None:
            continue
        if cnt >= max(2, min_prior) and m2 > 0:
            out[k] = (v - mean) / math.sqrt(m2 / (cnt - 1))
        pending.append(v)
    return out


def Ledger_holdings(research: "Research") -> Dict[str, float]:
    """Current portfolio weights (market value / NAV) for the equations tab's portfolio items."""
    from .portfolio import Ledger, analytics
    led = Ledger(research.store, research.panel(), "main")
    an = analytics(research.store, research.panel(), led)
    return {p["asset_id"]: p["market_value"] / an["nav"] for p in an["positions"] if abs(p["quantity"]) > 1e-12 and an["nav"]}

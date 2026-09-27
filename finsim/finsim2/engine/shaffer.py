"""Shaffer Score v2 engine: one point-in-time forward sweep produces every score, historical and live.

How the sweep works (for one asset):
  * Time runs forward over the calendar. At each session τ, the observation dated τ − h − 1 is added to the
    evidence for horizon h: its forward return (dated t, ending t + h) became known at the close of t + h, so it is
    usable from τ = t + h + 1. Nothing later is ever read.
  * Evidence is refitted at the first session of every month (the "refit"): per signal and horizon, running sums
    give the IC, hit rate, conditional-return spread, stability across thirds, the IC over the last 1, 3 and 5
    years (monthly checkpoints), and the IC inside each regime state; persistence (for the effective sample) and
    the correlations between signals of a family come from their own running sums. Benjamini–Hochberg q-values are
    taken across the signals at each horizon.
  * Scores are computed weekly (every 5th session) and on the last session, always with the evidence of the most
    recent refit, today's signals and today's regime: `score_at` is the only scoring routine.
  * Every score is kept; when its horizon has passed, the realised return is attached and the pair feeds (a) the
    family validation multipliers V_f and (b) the calibration of raw scores, both used only from the next refit.

So `compute_shaffer_score(asset, horizon, as_of)` for any date is: run the sweep to `as_of` and take the last score.
The live score is the same call with as_of = the latest session. See finsim2/shaffer_score.py for the formula,
the families, the priors and the constants.
"""
from __future__ import annotations

import gc
import math
from bisect import bisect_left, bisect_right
from functools import reduce
from itertools import accumulate, compress, repeat
from operator import add, and_, eq, gt, itemgetter, lt, mul
from typing import Dict, List, Optional

from .. import shaffer_score as cfg
from .horizons import t_pvalue

Series = List[Optional[float]]
REG_MAX_H = 252          # regime-conditional evidence only up to 12M (longer horizons have too few windows per state)
PRIOR_N0 = 20.0          # hierarchical shrinkage: the class/global prior counts as this many independent observations
PRIOR_CLASS_N0 = 50.0    # the class prior is itself shrunk toward the global one with this weight
PRIOR_MAX_H = 252        # priors (yearly checkpoints) are kept for horizons up to 12M
MIN_ROWS = 40            # sampled rows before a signal's evidence counts at all
CORR_STEP = 5            # sampling of the co-movement sums used for the correlation penalty
PERSIST_LAG = 21


def _step(h: int) -> int:
    return 1 if h <= 5 else min(21, max(1, h // 5))


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _corr(n, sx, sy, sxx, syy, sxy) -> Optional[float]:
    if n < 3:
        return None
    mx, my = sx / n, sy / n
    vx, vy = sxx / n - mx * mx, syy / n - my * my
    if vx <= 1e-15 or vy <= 1e-15:
        return None
    return _clip((sxy / n - mx * my) / math.sqrt(vx * vy), -1.0, 1.0)


def _tstat(r: float, n_eff: float) -> float:
    if n_eff <= 3:
        return 0.0
    return r * math.sqrt(n_eff - 2) / math.sqrt(max(1e-12, 1 - r * r))


def _bh(pvals: Dict[str, float]) -> Dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, prev = {}, 1.0
    for rank in range(m, 0, -1):
        k, p = items[rank - 1]
        prev = min(prev, p * m / rank)
        out[k] = min(1.0, prev)
    return out


def _median(xs):
    xs = sorted(xs)
    k = len(xs)
    return xs[k // 2] if k % 2 else 0.5 * (xs[k // 2 - 1] + xs[k // 2])


def _quantile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(len(xs) - 1, lo + 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def isotonic(xs: List[float], ys: List[float], ws: List[float]) -> List[float]:
    """Pool-adjacent-violators: the non-decreasing fit to ys (ordered by xs) with weights ws."""
    blocks = []                                   # [sum_wy, sum_w, count]
    for y, w in zip(ys, ws):
        blocks.append([y * w, w, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            a = blocks.pop()
            blocks[-1][0] += a[0]; blocks[-1][1] += a[1]; blocks[-1][2] += a[2]
    out = []
    for b in blocks:
        out += [b[0] / b[1]] * b[2]
    return out


def _pick(seq, idx) -> list:
    """[seq[i] for i in idx] (at C speed)."""
    if len(idx) > 1:
        return list(itemgetter(*idx)(seq))
    return [seq[i] for i in idx]


def _stability(cps: list, ns: list, r: int, base: tuple, ps: float) -> float:
    """The share of the thirds of the matured rows (by count, at refit r) whose IC has the sign of ps."""
    en = base[0]
    third = en / 3.0
    k1, k2 = bisect_left(ns, third, 0, r + 1), bisect_left(ns, 2 * third, 0, r + 1)
    c1 = cps[k1] if k1 <= r else base
    c2 = cps[k2] if k2 <= r else base
    sics = []                                   # (the first third is c1 itself: c1 − 0 is c1, bit for bit)
    if c1[0] >= 20:
        sics.append(_corr(*c1))
    if c2[0] - c1[0] >= 20:
        sics.append(_corr_window(c1, c2))
    if en - c2[0] >= 20:
        sics.append(_corr_window(c2, base))
    sics = [x for x in sics if x is not None]
    return (sum(1 for x in sics if (x > 0) == (ps > 0)) / len(sics)) if sics else 0.0


def _corr_window(a: tuple, b: tuple) -> Optional[float]:
    """The correlation from the sums accumulated between two checkpoints a and b (each (n, Σx, Σy, Σx², Σy², Σxy))."""
    return _corr(b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3], b[4] - a[4], b[5] - a[5])


class _Buckets:
    """One signal's sums per regime state at one horizon, [(state, n, Σx, Σy, Σx², Σy², Σxy)] with each sum a list
    over the needed refits, the states in the order they first occurred (a state's sums take its observations in
    date order, as the step-by-step sweep adds them). Built on first use: a signal never active at a horizon never
    needs them, and the evidence the run returns needs them at its last refit only (`at`)."""
    __slots__ = ("xT", "yT", "states", "jw", "m", "shared", "_rows", "_index")

    def __init__(self, xT: list, yT: list, states: Dict[str, tuple], jw: List[int], m: Optional[int] = None,
                 shared: Optional[dict] = None):
        """`m`: the signal has a value on every observation from the m-th on (and none before), None otherwise; then
        the outcome side of the sums is the same for every such signal and is kept in `shared` (one per horizon)."""
        self.xT, self.yT, self.states, self.jw, self.m, self.shared, self._rows, self._index = xT, yT, states, jw, m, shared, None, None

    def _entries(self):
        """(first occurrence, state, positions with a value) per state that ever has one."""
        xT = self.xT
        out = []
        for st, (pos, dim) in self.states.items():
            first = next(((p, d) for p, d in zip(pos, dim) if xT[p] is not None), None)
            if first is not None:
                out.append((first, st, [p for p in pos if xT[p] is not None]))
        out.sort(key=lambda e: e[0])
        return out

    def _outcomes(self, st: str, pos: List[int], k0: int) -> tuple:
        """(positions, y values, counts at the needed refits, Σy, Σy²) of state st from its k0-th position on."""
        key = (st, k0)
        v = self.shared.get(key)
        if v is None:
            bp = pos[k0:]
            by = _pick(self.yT, bp)
            cs = [bisect_left(bp, j) for j in self.jw]
            v = self.shared[key] = (bp, by, cs, _pick(list(accumulate(by, initial=0.0)), cs), _pick(list(accumulate(map(mul, by, by), initial=0.0)), cs))
        return v

    def rows(self) -> list:
        if self._rows is None:
            xT, yT, jw = self.xT, self.yT, self.jw
            rows = []
            if self.m is not None and self.shared is not None:
                entries = []
                for st, (pos, dim) in self.states.items():
                    k0 = bisect_left(pos, self.m)
                    if k0 < len(pos):
                        entries.append(((pos[k0], dim[k0]), st, pos, k0))
                entries.sort(key=lambda e: e[0])
                for _, st, pos, k0 in entries:
                    bp, by, cs, BY, BYY = self._outcomes(st, pos, k0)
                    bx = _pick(xT, bp)
                    rows.append((st, cs, _pick(list(accumulate(bx, initial=0.0)), cs), BY, _pick(list(accumulate(map(mul, bx, bx), initial=0.0)), cs),
                                 BYY, _pick(list(accumulate(map(mul, bx, by), initial=0.0)), cs)))
            else:
                for _, st, bp in self._entries():
                    bx, by = _pick(xT, bp), _pick(yT, bp)
                    cs = [bisect_left(bp, j) for j in jw]
                    rows.append((st, cs) + tuple(_pick(P, cs) for P in ShafferRun._prefix(bx, by)))
            self._rows = rows
        return self._rows

    def index(self) -> dict:
        if self._index is None:
            self._index = {b[0]: b for b in self.rows()}
        return self._index

    def at(self, k: int) -> list:
        """[(state, (n, Σx, Σy, Σx², Σy², Σxy))] at the k-th needed refit (without building every refit's sums)."""
        if self._rows is not None:
            return [(b[0], (b[1][k], b[2][k], b[3][k], b[4][k], b[5][k], b[6][k])) for b in self._rows]
        xT, yT, j = self.xT, self.yT, self.jw[k]
        out = []
        for _, st, bp in self._entries():
            c = bisect_left(bp, j)
            bx, by = _pick(xT, bp[:c]), _pick(yT, bp[:c])
            out.append((st, (c, reduce(add, bx, 0.0), reduce(add, by, 0.0), reduce(add, map(mul, bx, bx), 0.0),
                             reduce(add, map(mul, by, by), 0.0), reduce(add, map(mul, bx, by), 0.0))))
        return out


class _Regimes:
    """The regime-conditional evidence of one signal at one refit, {state: (IC, n_eff)} for the states with ≥ 20 rows,
    from the signal's `_Buckets` — computed per state when first read."""
    __slots__ = ("buckets", "k", "step", "den", "memo")

    def __init__(self, buckets: "_Buckets", k: int, step: int, den: float):
        self.buckets, self.k, self.step, self.den, self.memo = buckets, k, step, den, {}

    def _value(self, acc: tuple):
        return _corr(*acc), max(1.0, acc[0] * self.step / self.den)

    def full(self) -> dict:
        return {st: self._value(acc) for st, acc in self.buckets.at(self.k) if acc[0] >= 20}

    def at(self, states: tuple) -> dict:
        """The entries for the states in `states` (today's state in each dimension)."""
        out = self.memo.get(states)
        if out is None:
            out = self.memo[states] = self._at(states)
        return out

    def _at(self, states: tuple) -> dict:
        out = {}
        idx, k = self.buckets.index(), self.k
        for st in states:
            if not st or st in out:
                continue
            b = idx.get(st)
            if b is not None and b[1][k] >= 20:
                out[st] = self._value((b[1][k], b[2][k], b[3][k], b[4][k], b[5][k], b[6][k]))
        return out


class ShafferRun:
    """One asset's point-in-time Shaffer Score run. Build with the research object, then `run(until)`."""

    def __init__(self, research, asset_id: str, use_priors: bool = True, prior_checkpoints: Optional[Dict[str, tuple]] = None):
        """`prior_checkpoints` ({asset: parse_checkpoints(...)}, in key order) replaces the stored checkpoints as the
        source of the priors (the weekly panel passes each asset the checkpoints the serial run would have seen)."""
        self.r = research
        self.asset_id = asset_id
        panel = research.panel()
        self.cal = panel.calendar()
        self.n = len(self.cal)
        self.asset = research.store.asset(asset_id) or {"id": asset_id, "asset_class": "EQUITY"}
        self.cls = self.asset.get("asset_class") or "EQUITY"
        self.z = research.zscores(asset_id)
        self.raw = research.features(asset_id)
        self.price = panel.series(asset_id)
        self.vol = self.raw.get("vol_60") or [None] * self.n
        self.regimes = research.regimes()
        # production and candidate signals share the evidence machinery; candidates stay out of the production
        # score (and out of its Benjamini-Hochberg set) unless admitted at that horizon (shaffer_score.ADMITTED)
        self.signals = [s for s in cfg.ALL_FAMILY_OF if s in self.z and any(v is not None for v in self.z[s])]
        self.fam_signals = {f: [s for s, _ in sigs if s in self.signals] for f, sigs in cfg.ALL_FAMILIES.items()}
        self.horizons = [(lab, h) for lab, h in cfg.HORIZONS if h < self.n]
        first = next((i for i, v in enumerate(self.price) if v is not None), 0)
        self.first = first
        # targets: vol-scaled forward log return (comparable over time and across assets) and the raw log return
        self.y: Dict[int, Series] = {}
        self.yr: Dict[int, Series] = {}
        for _, h in self.horizons:
            ys, yrs = [None] * self.n, [None] * self.n
            for t in range(self.n - h):
                a, b, v = self.price[t], self.price[t + h], self.vol[t]
                if a and b and a > 0 and b > 0:
                    lr = math.log(b / a)
                    yrs[t] = lr
                    if v:
                        ys[t] = _clip(lr / (v * math.sqrt(h / 252.0)), -4.0, 4.0)
            self.y[h], self.yr[h] = ys, yrs
        self.refits = [i for i in range(max(1, first), self.n) if self.cal[i][:7] != self.cal[i - 1][:7]]
        if not use_priors:
            self.priors = {}
        elif prior_checkpoints is not None:
            self.priors = aggregate_priors(prior_checkpoints, asset_id, self.cls, self.signals)
        else:
            self.priors = load_priors(research, asset_id, self.cls, self.signals)
        self._prior_ic: Dict[int, tuple] = {}
        self.yearly: Dict[str, dict] = {}
        self._fam_memo: Dict[str, list] = {}
        self.present = {}
        for sgn in self.signals:                        # prefix count of sessions with a value (data quality)
            acc, c = [0] * (self.n + 1), 0
            for i, v in enumerate(self.z[sgn]):
                c += v is not None
                acc[i + 1] = c
            self.present[sgn] = acc

    # ------------------------------------------------------------------ the sweep
    def run(self, until: Optional[int] = None, keep_history: bool = True, checkpoints_only: bool = False, save: bool = True,
            records: bool = True) -> dict:
        """The forward sweep described in the module docstring, computed horizon by horizon. A full run (no `until`)
        stores the asset's January checkpoints unless `save` is False. Without `records`, the research records the
        ML Lab and the audit read (lab_records, sig_records, fam_records, sign_checks, the shadow pairs) are not kept:
        nothing else changes (summarize does not read them).

        Nothing crosses horizons inside the sweep except the refit calendar, signal persistence and the within-family
        correlations, which are computed first. Every running sum is a prefix sum over its observations in the order
        they join (itertools.accumulate adds them one by one, exactly as a running `+=` would), read at each refit.
        Evidence, validation and calibration are only computed at refits whose result is scored before the next
        refit (the others would be overwritten unread), so the output is the step-by-step sweep's, bit for bit."""
        gc_on = gc.isenabled()
        gc.disable()                             # millions of small tuples, no cycles: the collector only costs time
        try:
            return self._run(until, keep_history, checkpoints_only, save, records)
        finally:
            if gc_on:
                gc.enable()

    def _run(self, until: Optional[int], keep_history: bool, checkpoints_only: bool, save: bool, records: bool) -> dict:
        n = self.n if until is None else min(self.n, until + 1)
        cal = self.cal
        refits = [tau for tau in self.refits if tau < n]
        start_hist = max(self.first + cfg.MIN_TRAIN_YEARS * 252, n - cfg.HISTORY_YEARS * 252)
        score_days = [] if checkpoints_only else sorted(set(range(start_hist, n, 5)) | {n - 1})
        need = []                                # a refit's evidence is read iff a score day falls before the next refit
        for r, tr in enumerate(refits):
            nxt = refits[r + 1] if r + 1 < len(refits) else n
            k = bisect_left(score_days, tr)
            need.append(k < len(score_days) and score_days[k] < nxt)
        januaries = [r for r, tr in enumerate(refits) if cal[tr][5:7] == "01"]
        pers_at, corr_at = ({}, {}) if checkpoints_only else self._comovement(n, refits, need)
        history = {lab: [] for lab, _ in self.horizons}
        latest = {}
        oos, ev, calib, vf = {}, {}, {}, {}
        self.fam_records = {lab: [] for lab, _ in self.horizons}
        lab_records: Dict[str, list] = {}
        sig_records: Dict[str, list] = {}
        sign_checks: Dict[str, list] = {}
        first_rec: Dict[str, tuple] = {}         # the dict orders of the step-by-step sweep: first event per horizon
        first_chk: Dict[str, tuple] = {}
        jan_base: Dict[tuple, dict] = {}
        for hi, (lab, h) in enumerate(self.horizons):
            if checkpoints_only and h > PRIOR_MAX_H:
                continue
            sums = self._sums(h, n, refits, need, januaries if h <= PRIOR_MAX_H else (), full=not checkpoints_only)
            for r in (januaries if h <= PRIOR_MAX_H else ()):
                jan_base[(r, h)] = {s: d[0][r] for s, d in sums.items()}
            if checkpoints_only:
                continue
            o = oos[h] = {"pairs": [], "shadow": [], "fam": {f: [0, 0.0, 0.0, 0.0, 0.0, 0.0] for f in cfg.ALL_FAMILIES}}
            y_h, yr_h = self.y[h], self.yr[h]
            matq: List[tuple] = []               # (maturity, record): scores mature in the order they were made
            qi = 0
            ev_h = vf_h = calib_h = corr_now = None
            widx = {r: k for k, r in enumerate(r for r in range(len(refits)) if need[r])}
            events = sorted([(refits[r], 0, r) for r in widx] + [(d, 1, 0) for d in score_days])
            for tau, kind, r in events + [(n, 2, 0)]:
                # 1) matured score records feed the out-of-sample record (validation, calibration)
                while qi < len(matq) and (matq[qi][0] <= tau if kind < 2 else matq[qi][0] < n):
                    mat, rec = matq[qi]
                    qi += 1
                    y, yr = y_h[rec["t"]], yr_h[rec["t"]]
                    if y is None:
                        continue
                    rec["realized"] = yr
                    o["pairs"].append((rec["raw"], y, yr, rec["t"]))
                    for f, fs in rec["fam"].items():
                        a = o["fam"][f]
                        a[0] += 1; a[1] += fs; a[2] += y; a[3] += fs * fs; a[4] += y * y; a[5] += fs * y
                    if not records:
                        continue
                    if rec.get("sh"):
                        o["shadow"].append((rec["t"], y, rec["raw"], rec["all"], rec["sh"]))
                    # the research record the ML Lab learns from: everything known at t, and what happened after
                    first_rec.setdefault(lab, (mat, rec["t"]))
                    lab_records.setdefault(lab, []).append((rec["t"], rec["raw"], y, yr, rec["fam"], rec["c"], rec.get("K")))
                    sig_records.setdefault(lab, []).append((rec["t"], rec["raw"], y, yr, rec.get("sig")))
                if kind == 2:
                    break
                # 4) monthly refit
                if kind == 0:
                    ev_h = self._evidence(sums, h, tau, r, widx[r], pers_at[r], self._prior_at(tau, h), full=r == len(refits) - 1)
                    vf_h = self._validation(o["fam"], h)
                    calib_h = self._calibration(o["pairs"], h)
                    corr_now = corr_at[r]
                    continue
                # 5) scores
                if ev_h is None:
                    continue
                rec = self.score_at(tau, lab, h, ev_h, corr_now, vf_h, calib_h)
                if rec["raw"] is None:
                    if tau == n - 1:
                        latest[lab] = rec
                    continue
                if tau + h + 1 < self.n:
                    shd = rec.get("shadow") or {}
                    fam = {f["family"]: f["score"] for f in rec["families"]}
                    fam.update({f["family"]: f["score"] for f in shd.get("families") or []})
                    if not records:                # what validation and calibration read
                        matq.append((tau + h + 1, {"t": tau, "raw": rec["raw"], "fam": fam}))
                    else:
                        matq.append((tau + h + 1, {"t": tau, "raw": rec["raw"], "fam": fam, "all": shd.get("all"), "lab": lab,
                                                   "c": {f["family"]: f["contribution"] for f in rec["families"]}, "K": rec.get("K"),
                                                   "sig": signal_record(rec),
                                                   "sh": {**{f["family"]: (f["score"], shd["with"][f["family"]]) for f in shd.get("families") or []},
                                                          **{"VARIANT:" + k: (v, v) for k, v in (rec.get("variants") or {}).items() if v is not None}}}))
                if records and rec.get("expected") is not None and rec.get("calibrated") is not None:
                    first_chk.setdefault(lab, (tau, hi))
                    chk = sign_checks.setdefault(lab, [0, 0, 0])   # shown, total differs from calibrated, evidence part differs
                    chk[0] += 1
                    if rec["expected"] * rec["calibrated"] < 0:
                        chk[1] += 1
                    if rec["expected_edge"] * rec["calibrated"] < 0 and abs(rec["calibrated"]) > 1e-9 and abs(rec["expected_edge"]) > 1e-12:
                        chk[2] += 1
                if keep_history:
                    history[lab].append((tau, rec["raw"], rec.get("calibrated"), rec.get("expected")))
                    if records:
                        self.fam_records[lab].append((tau, {f["family"]: (f["score"], f["contribution"], f["V"]) for f in rec["families"]}))
                if tau == n - 1:
                    latest[lab] = rec
            if ev_h is not None:
                for e in ev_h.values():
                    view = e.pop("_reg", None)
                    if view is not None:
                        e["regimes"] = view.full()
                ev[h], vf[h], calib[h] = ev_h, vf_h, calib_h
        for r in januaries:
            for lab, h in self.horizons:
                if (r, h) in jan_base:
                    self.yearly.setdefault(cal[refits[r]][:4], {})[h] = jan_base[(r, h)]
        self.lab_records = {lab: lab_records[lab] for lab in sorted(lab_records, key=first_rec.get)}
        self.sig_records = {lab: sig_records[lab] for lab in sorted(sig_records, key=first_rec.get)}
        self.sign_checks = {lab: sign_checks[lab] for lab in sorted(sign_checks, key=first_chk.get)}
        latest = {lab: latest[lab] for lab, _ in self.horizons if lab in latest}
        if until is None and save:
            save_checkpoints(self.r, self.asset_id, self.cls, self.yearly, self.signals)
        if checkpoints_only:
            return {"asset_id": self.asset_id, "checkpoints": sorted(self.yearly)}
        return {"asset_id": self.asset_id, "as_of": cal[n - 1], "latest": latest, "history": history,
                "evidence": {lab: ev.get(h) for lab, h in self.horizons}, "calibration": {lab: calib.get(h) for lab, h in self.horizons},
                "validation": {lab: vf.get(h) for lab, h in self.horizons}, "oos": {lab: oos[h]["pairs"] for lab, h in self.horizons},
                "shadow": {lab: oos[h]["shadow"] for lab, h in self.horizons}, "n": n}

    def _sums(self, h: int, n: int, refits: List[int], need: List[bool], januaries, full: bool = True) -> Dict[str, tuple]:
        """Per signal, the evidence sums for horizon h at every refit: the observation dated t joins at session
        t + h + 1 (every `_step(h)`-th date with a known outcome). Returns {signal: (cp, cpn, extra, reg)} where cp[r]
        = (n, Σx, Σy, Σx², Σy², Σxy) and cpn[r] = n at refit r; for refits in `need` (when `full`), extra[r] = (hits
        counted, hits, n x>½, Σy x>½, n x<−½, Σy x<−½) and reg = the per-regime-state sums (`_Buckets`, built when first
        read). Only the January refits are read unless `full`."""
        z = self.z
        yh = self.y[h]
        T = [t for t in range(0, n - h - 1, _step(h)) if yh[t] is not None]
        yT = _pick(yh, T)
        joined = [bisect_right(T, tr - h - 1) for tr in refits]           # observations joined by each refit
        at = list(range(len(refits))) if full else list(januaries)
        want = [r for r in range(len(refits)) if need[r]] if full else []
        jw = [joined[r] for r in want]
        states: Dict[str, tuple] = {}                                   # state -> (positions in T, dimension index)
        if want and h <= REG_MAX_H:
            cols = [self.regimes[d] for d in self.regimes]
            for i, t in enumerate(T):
                for di, col in enumerate(cols):
                    st = col[t]
                    if st is not None:
                        e = states.get(st)
                        if e is None:
                            e = states[st] = ([], [])
                        e[0].append(i); e[1].append(di)
        # a signal with a value on every observation date shares the outcome sums (the same numbers, added in order)
        shared: dict = {}
        ja = [joined[r] for r in at]
        y_sums = [_pick(P, ja) for P in (list(accumulate(yT, initial=0.0)), list(accumulate(map(mul, yT, yT), initial=0.0)))]
        out = {}
        for s in self.signals:
            xT = _pick(z[s], T)
            dense = None not in xT
            if dense:
                xs, ys, cnt, m = xT, yT, joined, 0
            else:
                keep = [i for i, x in enumerate(xT) if x is not None]
                xs, ys = _pick(xT, keep), _pick(yT, keep)
                cnt = [bisect_left(keep, j) for j in joined]
                m = keep[0] if keep and keep[0] + len(keep) == len(xT) else None      # a value from the m-th date on
            ca = [cnt[r] for r in at]
            if dense:
                sy, syy = y_sums
            else:
                sy, syy = (_pick(P, ca) for P in (list(accumulate(ys, initial=0.0)), list(accumulate(map(mul, ys, ys), initial=0.0))))
            snap = zip(ca, _pick(list(accumulate(xs, initial=0.0)), ca), sy, _pick(list(accumulate(map(mul, xs, xs), initial=0.0)), ca), syy,
                       _pick(list(accumulate(map(mul, xs, ys), initial=0.0)), ca))
            if full:
                cp = list(snap)
            else:
                cp = [None] * len(refits)
                for r, v in zip(at, snap):
                    cp[r] = v
            extra: list = [None] * len(refits)
            reg = None
            if want:
                cw = [cnt[r] for r in want]
                hit = list(map(gt, map(abs, xs), repeat(0.25)))
                HN = _pick(list(accumulate(hit, initial=0)), cw)
                HH = _pick(list(accumulate(map(and_, hit, map(eq, map(gt, xs, repeat(0)), map(gt, ys, repeat(0)))), initial=0)), cw)
                PN = _pick(list(accumulate(map(gt, xs, repeat(0.5)), initial=0)), cw)
                PPY = _pick(list(accumulate(compress(ys, map(gt, xs, repeat(0.5))), initial=0.0)), PN)
                QN = _pick(list(accumulate(map(lt, xs, repeat(-0.5)), initial=0)), cw)
                QQY = _pick(list(accumulate(compress(ys, map(lt, xs, repeat(-0.5))), initial=0.0)), QN)
                for r, v in zip(want, zip(HN, HH, PN, PPY, QN, QQY)):
                    extra[r] = v
                reg = _Buckets(xT, yT, states, jw, m, shared) if states else None
            out[s] = (cp, cnt, extra, reg)
        return out

    def _comovement(self, n: int, refits: List[int], need: List[bool]):
        """Signal persistence (lag-21 autocorrelation) and the within-family correlations at each refit that is read:
        ({refit: {signal: persistence}}, {refit: {family: {(a, b): correlation}}})."""
        z, sigs = self.z, self.signals
        want = [r for r in range(len(refits)) if need[r]]
        pers_at = {r: {} for r in want}
        for s in sigs:
            zs = z[s]
            taus = [tau for tau in range(PERSIST_LAG, n) if zs[tau] is not None and zs[tau - PERSIST_LAG] is not None]
            A, B = [zs[t] for t in taus], [zs[t - PERSIST_LAG] for t in taus]
            P = self._prefix(A, B)
            for r in want:
                c = bisect_right(taus, refits[r])
                rho = _corr(c, P[0][c], P[1][c], P[2][c], P[3][c], P[4][c])
                pers_at[r][s] = min(2520.0, -PERSIST_LAG / math.log(rho)) if rho is not None and 0.05 < rho < 1 else 1.0
        corr_at = {r: {} for r in want}
        for f, ss in self.fam_signals.items():
            for r in want:
                corr_at[r][f] = {}
            for i, a in enumerate(ss):
                za = z[a]
                for b in ss[i + 1:]:
                    zb = z[b]
                    taus = [tau for tau in range(0, n, CORR_STEP) if za[tau] is not None and zb[tau] is not None]
                    P = self._prefix([za[t] for t in taus], [zb[t] for t in taus])
                    for r in want:
                        c = bisect_right(taus, refits[r])
                        corr_at[r][f][(a, b)] = _corr(c, P[0][c], P[1][c], P[2][c], P[3][c], P[4][c]) or 0.0
        return pers_at, corr_at

    @staticmethod
    def _prefix(xs: list, ys: list) -> tuple:
        """Prefix sums (Σx, Σy, Σx², Σy², Σxy), each a list starting at 0.0, added in order."""
        return (list(accumulate(xs, initial=0.0)), list(accumulate(ys, initial=0.0)), list(accumulate(map(mul, xs, xs), initial=0.0)),
                list(accumulate(map(mul, ys, ys), initial=0.0)), list(accumulate(map(mul, xs, ys), initial=0.0)))

    # ------------------------------------------------------------------ evidence at a refit
    def _prior_at(self, tau: int, h: int) -> Optional[dict]:
        """Class and global evidence sums from other assets' January checkpoints on or before this refit."""
        if not self.priors or h > PRIOR_MAX_H:
            return None
        year = self.cal[tau][:4]
        ys = [y for y in self.priors if y <= year]
        return self.priors[max(ys)].get(h) if ys else None

    def _evidence(self, sums: Dict[str, tuple], h: int, tau: int, r: int, k: int, pers_now: Dict[str, float], hier: Optional[dict] = None,
                  full: bool = True) -> dict:
        """The evidence of every signal at refit r (session tau; the k-th refit that is read), from `_sums`. The
        regime-conditional evidence is a `_Regimes` view under "_reg" ("regimes" holds its place in the record):
        score_at reads today's states from it, and the sweep writes the full dict into the evidence it returns.
        Unless `full`, a muted signal's record is only what score_at reads of it ({"status", "w": 0.0}); its p-value
        still enters the q-values of the others."""
        step = _step(h)
        out, pv = {}, {}
        for s, (cps, ns, extra, reg) in sums.items():
            base = cps[r]
            en = base[0]
            if en < MIN_ROWS:
                out[s] = {"status": "insufficient", "n": en}
                continue
            rho = _corr(*base)
            if rho is None:
                out[s] = {"status": "insufficient", "n": en}
                continue
            n_eff = max(1.0, en * step / max(h, pers_now.get(s, 1.0)))
            t = _tstat(rho, n_eff)
            p = t_pvalue(t, max(1.0, n_eff - 2)) if n_eff > 3 else 1.0
            pv[s] = p
            ests = [rho]
            hn, hh, pn, py, qn, qy = extra[r]
            hit = hh / hn if hn >= 20 else None
            if hit is not None:
                ests.append(math.sin(math.pi * (hit - 0.5)))
            spread = None
            if pn >= 10 and qn >= 10:
                spread = py / pn - qy / qn
                vy = base[4] / en - (base[2] / en) ** 2
                if vy > 1e-12:
                    ests.append(_clip(spread / math.sqrt(vy) / 2.2826, -1.0, 1.0))   # E[x|x>½] − E[x|x<−½] = 2.2826 for N(0,1)
            ps_own = _median(ests)
            ps, ps_prior, prior_n = ps_own, None, 0.0
            pr = (hier or {}).get(s)
            if pr:
                rcg = self._prior_ic.get(id(pr))      # the same sums for every refit of the year
                if rcg is None or rcg[0] is not pr:
                    rcg = self._prior_ic[id(pr)] = (pr, _corr(*pr[0]) if pr[0][0] >= 30 else None, _corr(*pr[1]) if pr[1][0] >= 30 else None)
                rc, rg = rcg[1], rcg[2]
                nc = pr[0][0] * step / max(h, 1)
                if rc is not None or rg is not None:
                    if rc is not None and rg is not None:
                        ps_prior = (nc * rc + PRIOR_CLASS_N0 * rg) / (nc + PRIOR_CLASS_N0)
                    else:
                        ps_prior = rc if rc is not None else rg
                    ps = (n_eff * ps_own + PRIOR_N0 * ps_prior) / (n_eff + PRIOR_N0)
                    prior_n = PRIOR_N0
            # direction from the economic prior and the evidence (stability, below, only when it can matter)
            prior = cfg.PRIOR.get(s, 0)
            sgn = 1 if ps > 0 else -1
            if prior:
                if ps * prior >= 0:
                    delta, status = prior, "active"
                elif abs(t) >= cfg.FLIP_T and n_eff >= cfg.FLIP_N and _stability(cps, ns, r, base, ps) >= 2 / 3:
                    delta, status = -prior, "reversed by evidence"
                else:
                    delta, status = 0, "muted: evidence against the prior"
            elif abs(t) >= cfg.AGNOSTIC_T and n_eff >= cfg.AGNOSTIC_N and _stability(cps, ns, r, base, ps) >= 2 / 3:
                delta, status = sgn, "active: direction from evidence"
            else:
                delta, status = 0, "muted: no reliable direction"
            if not delta and not full:
                out[s] = {"status": status, "w": 0.0}
                continue
            stab = _stability(cps, ns, r, base, ps)
            # windows: last 1, 3, 5 years (12, 36, 60 refits ago)
            win = {}
            for lab_w, back in (("1Y", 12), ("3Y", 36), ("5Y", 60)):
                if r >= back:
                    wa = cps[r - back]
                    wn = en - wa[0]
                    wi = _corr_window(wa, base) if wn >= 10 else None
                    win[lab_w] = (wi, max(1.0, wn * step / max(h, pers_now.get(s, 1.0))))
            # data quality: share of the last year with a value
            pc = self.present[s]
            lo = max(0, tau - 252)
            quality = (pc[tau] - pc[lo]) / (tau - lo) if tau > lo else 0.0
            # decay: recent windows against the full history, in the direction the signal is used
            base_ic = abs(rho) if abs(rho) > 0.01 else 0.01
            d, dclass = 1.0, "INSUFFICIENT DATA"
            w3, w1 = win.get("3Y"), win.get("1Y")
            if w3 and w3[0] is not None and w3[1] >= 10 and delta:
                r3 = _clip(delta * w3[0] / base_ic, -1.0, 1.5)
                r1 = _clip(delta * w1[0] / base_ic, -1.0, 1.5) if w1 and w1[0] is not None else r3
                trend = 0.6 * r3 + 0.4 * r1
                lam = w3[1] / (w3[1] + 30.0)
                d = 1.0 - lam * (1.0 - _clip(0.5 + 0.5 * trend, cfg.D_MIN, 1.0))
                dclass = "HEALTHY" if trend >= 0.6 else "WEAKENING" if trend >= 0.2 else "DECAYING"
            se = 1.0 / math.sqrt(max(1.0, n_eff - 3))
            ci_strength = abs(rho) / (abs(rho) + 1.96 * se)
            rec = {"status": status, "delta": delta, "ps": ps, "ps_own": ps_own, "ps_prior": ps_prior, "ic": rho, "ic_hit": ests[1] if hit is not None else None,
                   "hit": hit, "spread": spread, "n": en, "n_eff": n_eff, "t": t, "p": p, "stability": stab, "quality": quality,
                   "windows": {k2_: v[0] for k2_, v in win.items()}, "decay": dclass, "d": d, "ci_strength": ci_strength}
            if h <= REG_MAX_H:
                if reg is None:
                    rec["regimes"] = {}
                else:
                    rec["regimes"] = None
                    rec["_reg"] = _Regimes(reg, k, step, max(h, pers_now.get(s, 1.0)))
            out[s] = rec
        # q-values: production signals among production signals only (so adding candidates cannot move the production
        # score); candidate signals among all signals (as if admitted)
        q_prod = _bh({s: p for s, p in pv.items() if s in cfg.FAMILY_OF}) if pv else {}
        q_all = _bh(pv) if pv else {}
        for s, rec in out.items():
            if "ps" not in rec:
                continue                        # insufficient, or (unless full) muted: w is already 0
            rec["q"] = (q_prod if s in cfg.FAMILY_OF else q_all).get(s, 1.0)
            rec["c"] = min(1.0, math.sqrt(rec["n_eff"] / cfg.N_FULL)) * rec["ci_strength"] * (1.0 - 0.5 * rec["q"]) * rec["quality"]
            rec["w"] = abs(rec["ps"]) * (0.25 + 0.75 * rec["stability"]) if rec["delta"] else 0.0
        return out

    @staticmethod
    def _variants(families: List[dict], vf: dict) -> dict:
        """Methodology variants, SHADOW ONLY (never shown or used): the same families and evidence, weighted as
            no_prior_H       W·A·F / (κ·ΣA)            — the economic horizon prior H removed from weight and scale
            strict_V         E·V'·A·H·F / (κ·ΣA·H)     — V' = clip(t/2, 0, 1): a family with a non-positive
                                                         out-of-sample record gets zero weight (production: 0.5 + t/4)
            no_H_strict_V    E·V'·A·F / (κ·ΣA)
        Each is judged by the same discovery/confirmation test as a candidate family; none is adopted otherwise."""
        out = {}
        for key, useH, strict in (("no_prior_H", False, False), ("strict_V", True, True), ("no_H_strict_V", False, True)):
            num = den = 0.0
            for f in families:
                A, H = f["A"], (f["H"] if useH else 1.0)
                den += A * H
                if not f.get("n_active"):
                    continue
                t = (vf.get(f["family"]) or {}).get("t")
                V = (_clip(t / 2.0, 0.0, 1.0) if t is not None else 1.0) if strict else f["V"]
                num += f["E"] * V * A * H * f["score"]
            out[key] = 100.0 * math.tanh(num / (cfg.KAPPA * den)) if den > 0 else None
        return out

    def _validation(self, fam_acc: Dict[str, list], h: int) -> dict:
        """V_f from each family score's matured out-of-sample record (scores are weekly: overlap removed via n_eff)."""
        out = {}
        for f, acc in fam_acc.items():
            n_eff = acc[0] * 5.0 / max(5.0, float(h))
            r = _corr(*acc) if acc[0] >= 10 else None
            if r is None or n_eff < 20:
                out[f] = {"V": 1.0, "ic": r, "n_eff": n_eff, "t": None, "state": "not enough history (neutral)"}
                continue
            t = _tstat(r, n_eff)
            out[f] = {"V": _clip(0.5 + 0.25 * t, 0.0, 1.0), "ic": r, "n_eff": n_eff, "t": t,
                      "state": "validated" if t >= 2 else "weak" if t >= 0 else "negative record: reduced"}
        return out

    def _calibration(self, pairs: List[tuple], h: int) -> dict:
        """Monotone map raw score -> vol-scaled forward return, from matured out-of-sample pairs only."""
        m = len(pairs)
        n_eff = m * 5.0 / max(5.0, float(h))
        if m < 30:
            return {"usable": False, "n": m, "n_eff": n_eff}
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        ic = _corr(m, sum(xs), sum(ys), sum(x * x for x in xs), sum(y * y for y in ys), sum(x * y for x, y in zip(xs, ys)))
        t = _tstat(ic, n_eff) if ic is not None else 0.0
        ybar = sum(ys) / m
        order = sorted(range(m), key=lambda i: xs[i])
        size = max(20, m // 10)
        bins = [order[i:i + size] for i in range(0, m, size)]
        if len(bins) > 1 and len(bins[-1]) < size // 2:
            # merge a short last bin into the one before it. (Was `bins[-2] += bins.pop()`: the index was taken before
            # the pop and the list extended in place, so one bin was counted twice and another dropped — fixed in 2.1.)
            tail = bins.pop()
            bins[-1] = bins[-1] + tail
        cx = [sum(xs[i] for i in b) / len(b) for b in bins]
        my = [sum(ys[i] for i in b) / len(b) for b in bins]
        fit = isotonic(cx, my, [len(b) for b in bins])
        table = []
        for b, c, mean_y, g in zip(bins, cx, my, fit):
            yrs = [pairs[i][2] for i in b]
            ne = len(b) * 5.0 / max(5.0, float(h))
            table.append({"lo": xs[b[0]], "hi": xs[b[-1]], "center": c, "n": len(b), "n_eff": ne, "mean_y": mean_y, "fit": g,
                          "edge": (g - ybar) * ne / (ne + 20.0), "mean_ret": sum(yrs) / len(yrs), "median_ret": _quantile(yrs, 0.5),
                          "hit": sum(1 for v in yrs if v > 0) / len(yrs), "q10": _quantile(yrs, 0.1), "q90": _quantile(yrs, 0.9)})
        mono = None
        if len(table) >= 3:
            ranks = sorted(range(len(table)), key=lambda i: table[i]["mean_y"])
            rk = [0] * len(table)
            for pos, i in enumerate(ranks):
                rk[i] = pos
            k = len(table)
            mono = 1 - 6 * sum((i - rk[i]) ** 2 for i in range(k)) / (k * (k * k - 1))
        return {"usable": n_eff >= 30 and ic is not None, "n": m, "n_eff": n_eff, "ic": ic, "t": t, "ybar": ybar,
                "bins": table, "monotonicity": mono}

    # ------------------------------------------------------------------ the one scoring routine
    def _families_at(self, lab: str) -> list:
        """(family, members, in production, A, H) for the families that apply to this asset at this horizon."""
        memo = self._fam_memo
        if lab not in memo:
            memo[lab] = [(f, members, cfg.in_production(f, lab), cfg.applicability(f, self.cls), cfg.horizon_fit(f, lab))
                         for f, members in self.fam_signals.items()]
            memo[lab] = [x for x in memo[lab] if x[3] * x[4] > 0]
        return memo[lab]

    def score_at(self, tau: int, lab: str, h: int, ev: dict, corr_now: dict, vf: dict, calib: dict) -> dict:
        z = self.z
        state = {d: self.regimes[d][tau] for d in self.regimes}
        skey = tuple(state.values())
        families, num, ksum = [], 0.0, 0.0
        shadow, shadow_ah = [], {}
        s_scale, n_regime, r_min, r_max = cfg.S_SCALE, cfg.N_REGIME, cfg.R_MIN, cfg.R_MAX
        for f, members, prod, A, H in self._families_at(lab):
            target = families if prod else shadow
            present, rows = False, []
            for s in members:
                zc = z[s]
                zs = zc[tau]
                if zs is None and tau:
                    zs = zc[tau - 1]
                if zs is None:
                    continue
                present = True
                e = ev.get(s) or {}
                if not e.get("w"):
                    rows.append({"signal": s, "z": zs, "status": e.get("status", "insufficient"), "active": False})
                    continue
                delta = e["delta"]
                x = zs / s_scale
                s_val = delta * (-1.0 if x < -1.0 else 1.0 if x > 1.0 else x)           # _clip(x, -1, 1)
                r = 1.0
                regs = e.get("regimes")
                if regs is None:
                    view = e.get("_reg")
                    regs = view.at(skey) if view is not None else {}
                if regs:
                    parts = []
                    for st in skey:
                        if st and st in regs and regs[st][0] is not None:
                            ric, rne = regs[st]
                            ratio = _clip(delta * ric / max(abs(e["ps"]), 0.01), 0.0, 2.0)
                            parts.append(rne / (rne + n_regime) * (ratio - 1.0))
                    if parts:
                        r = _clip(1.0 + sum(parts) / len(parts), r_min, r_max)
                term = s_val * e["c"] * r * e["d"]
                rows.append({"signal": s, "z": zs, "s": s_val, "delta": e["delta"], "w": e["w"], "c": e["c"], "r": r, "d": e["d"], "term": term,
                             "ps": e["ps"], "ic": e["ic"], "q": e.get("q"), "n_eff": e["n_eff"], "decay": e["decay"], "status": e["status"], "active": True})
            if not present:
                continue
            if prod:
                ksum += A * H
            else:
                shadow_ah[f] = A * H
            act = [x for x in rows if x["active"]]
            if not act:
                target.append({"family": f, "score": 0.0, "W": 0.0, "A": A, "H": H, "E": 0.0, "V": (vf.get(f) or {}).get("V", 1.0),
                                 "contribution": 0.0, "signals": rows, "n_active": 0})
                continue
            cm = corr_now.get(f) or {}
            adj = {}
            for x in act:
                dsum = 1.0
                for y in act:
                    if y is x:
                        continue
                    rho = cm.get((x["signal"], y["signal"]), cm.get((y["signal"], x["signal"]), 0.0))
                    dsum += rho * rho
                adj[x["signal"]] = x["w"] / dsum
            tot = sum(adj.values())
            fs = 0.0
            evid = 0.0
            for x in act:
                x["omega"] = adj[x["signal"]] / tot if tot else 0.0
                fs += x["omega"] * x["term"]
                evid += x["omega"] * abs(x["ps"])            # c is already inside each term: not counted twice
            E = min(1.0, evid / cfg.PS_REF)
            V = (vf.get(f) or {}).get("V", 1.0)
            W = E * V
            contrib = W * A * H * fs
            if prod:
                num += contrib
            target.append({"family": f, "score": fs, "W": W, "E": E, "V": V, "A": A, "H": H, "contribution": contrib, "signals": rows,
                             "n_active": len(act)})
        act_neff = [x["n_eff"] for fam in families for x in fam["signals"] if x.get("active")]
        rec = {"horizon": lab, "date": self.cal[tau], "raw": None, "families": families, "regime": state}
        if not act_neff or ksum <= 0:
            rec["reason"] = "no signal has usable evidence at this horizon yet"
            return rec
        med = sorted(act_neff)[len(act_neff) // 2]
        if med < cfg.MIN_NEFF_SHOW:
            rec["reason"] = f"insufficient evidence: about {med:.0f} independent {lab} observations"
            return rec
        K = cfg.KAPPA * ksum
        raw = 100.0 * math.tanh(num / K)
        rec.update(raw=raw, numerator=num, K=K, n_eff=med)
        rec["variants"] = self._variants(families, vf)
        if shadow:        # what the score would read with each candidate family added (and with all of them)
            add = sum(x["contribution"] for x in shadow)
            add_k = sum(shadow_ah.values())
            rec["shadow"] = {"families": shadow,
                             "with": {x["family"]: 100.0 * math.tanh((num + x["contribution"]) / (cfg.KAPPA * (ksum + shadow_ah[x["family"]]))) for x in shadow},
                             "all": 100.0 * math.tanh((num + add) / (cfg.KAPPA * (ksum + add_k)))}
        # calibration (learned only from matured out-of-sample pairs before the refit)
        vol = self.vol[tau]
        if calib.get("usable"):
            bins = calib["bins"]
            b = min(range(len(bins)), key=lambda i: abs(bins[i]["center"] - raw))
            cx = [x["center"] for x in bins]
            edges = [x["edge"] for x in bins]
            fits = [x["fit"] for x in bins]
            if raw <= cx[0]:
                edge, fit = edges[0], fits[0]
            elif raw >= cx[-1]:
                edge, fit = edges[-1], fits[-1]
            else:
                j = bisect_left(cx, raw)
                u = (raw - cx[j - 1]) / (cx[j] - cx[j - 1]) if cx[j] > cx[j - 1] else 0.0
                edge = edges[j - 1] + u * (edges[j] - edges[j - 1])
                fit = fits[j - 1] + u * (fits[j] - fits[j - 1])
            rec["calibrated"] = 100.0 * math.tanh(edge / cfg.G_SCALE)
            supported = (calib.get("t") or 0) >= 1.0 and calib["n_eff"] >= 30
            if supported and vol:
                # the calibrated score is the evidence RELATIVE to this asset's own average (edge = shrunk g − ȳ); the
                # expected return is TOTAL: the average plus that edge. A strong-drift asset can therefore show a
                # negative calibrated score and a positive expected return; the evidence part has the score's sign.
                scale = vol * math.sqrt(h / 252.0)
                rec["expected"] = math.exp((calib["ybar"] + edge) * scale) - 1
                rec["expected_typical"] = math.exp(calib["ybar"] * scale) - 1
                rec["expected_edge"] = rec["expected"] - rec["expected_typical"]
            bn = bins[b]
            if bn["n"] >= 30 and bn["q10"] is not None:
                rec["range"] = (math.exp(bn["q10"]) - 1, math.exp(bn["q90"]) - 1)
            rec["calibration_bin"] = {k: bn[k] for k in ("lo", "hi", "n", "mean_ret", "median_ret", "hit")}
        # confidence and evidence
        cal_q = _clip((calib.get("t") or 0.0) / 3.0, 0.0, 1.0) if calib.get("usable") else 0.0
        sample = min(1.0, math.sqrt(calib.get("n_eff", 0.0) / cfg.N_FULL))
        ah = [(fam["A"] * fam["H"], fam) for fam in families]
        tot_ah = sum(a for a, _ in ah) or 1.0
        strength = sum(a * fam["W"] for a, fam in ah) / tot_ah
        coverage = sum(a for a, fam in ah if fam["n_active"]) / tot_ah
        conf = sample * (0.45 * cal_q + 0.35 * strength + 0.2 * coverage) if calib.get("usable") else 0.35 * strength * coverage
        rec["confidence"] = conf
        rec["confidence_label"] = "High" if conf >= 0.65 else "Medium" if conf >= 0.4 else "Low"
        oos_t, oos_n = calib.get("t") or 0.0, calib.get("n_eff", 0.0)
        rec["evidence"] = "High" if oos_n >= 60 and oos_t >= 2.5 else "Medium" if oos_n >= 25 and oos_t >= 1.5 else "Low"
        rec["oos"] = {"ic": calib.get("ic"), "t": calib.get("t"), "n_eff": calib.get("n_eff"), "monotonicity": calib.get("monotonicity")}
        return rec


def compute_shaffer_score(research, asset_id: str, horizon: str, as_of: Optional[str] = None) -> dict:
    """THE canonical Shaffer Score: the score of `asset_id` at `horizon` as it would have read on `as_of` (default:
    the latest session), using only information available by then. Live and historical scores are this call."""
    run = ShafferRun(research, asset_id)
    idx = None
    if as_of is not None:
        idx = research.panel().index_of(as_of)
    res = run.run(until=idx, keep_history=False, records=False)
    return res["latest"].get(horizon) or {"horizon": horizon, "raw": None, "reason": "no score"}


# ------------------------------------------------------------------ presentation: what the pages and the audit read
def signal_record(rec: dict) -> Optional[dict]:
    """Everything production applied to each signal on this date, compact, so the ML Lab can re-weight the equation
    inside its own structure: x = clip(z / S_SCALE, −1, 1) for every production signal present; for each active signal
    its PIT direction δ, weight ω, confidence c, regime factor r and decay d; for each family g = W·A·H / K. Then
        raw = 100·tanh( Σ_f g_f Σ_{i∈f active} ω_i · δ_i · x_i · c_i · r_i · d_i )
    reproduces the production score (test: SignalRecords)."""
    K = rec.get("K")
    if not K or rec.get("raw") is None:
        return None
    x, a, g = {}, {}, {}
    for f in rec.get("families") or []:
        g[f["family"]] = round(f["W"] * f["A"] * f["H"] / K, 8)
        for sg in f.get("signals") or []:
            if sg.get("z") is None:
                continue
            x[sg["signal"]] = round(_clip(sg["z"] / cfg.S_SCALE, -1.0, 1.0), 4)
            if sg.get("active"):
                a[sg["signal"]] = [sg.get("delta", 1), round(sg.get("omega", 0.0), 6), round(sg["c"], 5), round(sg["r"], 5), round(sg["d"], 5)]
    return {"x": x, "a": a, "g": g}


def attribute(rec: dict) -> dict:
    """Split a raw score into points per family and per signal that add up to it (proportional to each piece's share
    of the numerator; tanh is monotone, so the split keeps signs and ranks)."""
    num, raw = rec.get("numerator"), rec.get("raw")
    scale = (raw / num) if num else 0.0
    fams, sigs = [], []
    for f in rec.get("families") or []:
        pts = f["contribution"] * scale
        fams.append({"family": f["family"], "points": pts, "score": f["score"], "W": f["W"], "E": f.get("E"), "V": f.get("V"), "A": f["A"], "H": f["H"],
                     "n_active": f["n_active"], "n_signals": len(f["signals"])})
        for x in f["signals"]:
            if x.get("active"):
                p = scale * f["W"] * f["A"] * f["H"] * x.get("omega", 0.0) * x["term"]
                sigs.append({"signal": x["signal"], "family": f["family"], "points": p, "z": x["z"], "s": x["s"], "omega": x.get("omega"), "w": x["w"],
                             "c": x["c"], "r": x["r"], "d": x["d"], "ic": x["ic"], "ps": x["ps"], "q": x.get("q"), "n_eff": x["n_eff"], "decay": x["decay"],
                             "status": x["status"]})
            else:
                sigs.append({"signal": x["signal"], "family": f["family"], "points": 0.0, "z": x["z"], "status": x["status"]})
    fams.sort(key=lambda f: -abs(f["points"]))
    return {"families": fams, "signals": sigs}


def summarize(res: dict, run: "ShafferRun") -> dict:
    """The cached, JSON-ready form of a run: live breakdown per horizon, the reconstructed history with realised
    returns, calibration tables, evidence tables, validation and out-of-sample performance (overall and by regime)."""
    from .features import label as flabel
    cal = run.cal
    out = {"asset_id": run.asset_id, "version": cfg.VERSION, "as_of": res["as_of"], "horizons": {}, "history": {}, "calibration": {},
           "evidence": {}, "validation": res["validation"], "performance": {}, "formula": cfg.FORMULA}
    for lab, h in run.horizons:
        rec = res["latest"].get(lab) or {"raw": None, "reason": "no score"}
        live = {k: rec.get(k) for k in ("raw", "calibrated", "expected", "expected_typical", "expected_edge", "range", "confidence", "confidence_label", "evidence", "n_eff", "reason",
                                        "oos", "numerator", "K", "calibration_bin", "date")}
        live["regime"] = rec.get("regime")
        if rec.get("raw") is not None:
            at = attribute(rec)
            live["families"] = at["families"]
            live["sig"] = signal_record(rec)             # the same pieces the research records keep (signal challengers)
            for x in at["signals"]:
                x["label"] = flabel(x["signal"])
            act = [x for x in at["signals"] if x.get("s") is not None]
            sgn = 1 if rec["raw"] >= 0 else -1
            live["contributors"] = sorted([x for x in act if x["points"] * sgn > 0], key=lambda x: -abs(x["points"]))[:8]
            live["contradicting"] = sorted([x for x in act if x["points"] * sgn < 0], key=lambda x: -abs(x["points"]))[:8]
            live["signals"] = at["signals"]
        shd = rec.get("shadow")
        if shd:           # candidate families: shown, never counted (until admitted)
            live["shadow"] = {"with": shd["with"], "all": shd["all"],
                              "families": [{"family": x["family"], "score": x["score"], "W": x["W"], "A": x["A"], "H": x["H"], "n_active": x["n_active"],
                                            "signals": [{k: y.get(k) for k in ("signal", "z", "s", "status", "active", "ic", "n_eff")} | {"label": flabel(y["signal"])}
                                                        for y in x["signals"]]} for x in shd["families"]]}
        out["horizons"][lab] = live
        # history with realised returns
        realized = {p[3]: p[2] for p in res["oos"][lab]}
        hist = res["history"][lab]
        out["history"][lab] = {"dates": [cal[t] for t, *_ in hist], "raw": [x[1] for x in hist], "calibrated": [x[2] for x in hist],
                               "expected": [x[3] for x in hist], "realized": [realized.get(t) for t, *_ in hist]}
        out["calibration"][lab] = res["calibration"].get(lab)
        evd = res["evidence"].get(lab) or {}
        out["evidence"][lab] = {s: {k: v for k, v in e.items() if k != "regimes"} for s, e in evd.items()}
        # out-of-sample performance of the raw score: overall and by the regime on the score date
        pairs = res["oos"][lab]
        perf = {"n": len(pairs), "n_eff": len(pairs) * 5.0 / max(5.0, float(h))}
        if len(pairs) >= 30:
            xs, ys, yrs = [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs]
            m = len(xs)
            ic = _corr(m, sum(xs), sum(ys), sum(x * x for x in xs), sum(y * y for y in ys), sum(x * y for x, y in zip(xs, ys)))
            lean = [(x, yr) for x, yr in zip(xs, yrs) if abs(x) >= 5]
            perf.update(ic=ic, t=_tstat(ic, perf["n_eff"]) if ic is not None else None,
                        hit=(sum(1 for x, yr in lean if (x > 0) == (yr > 0)) / len(lean)) if len(lean) >= 20 else None, n_lean=len(lean),
                        first=cal[pairs[0][3]], years=(pairs[-1][3] - pairs[0][3]) / 252.0)
            by = {}
            for d, states in run.regimes.items():
                for st in set(states[p[3]] for p in pairs if states[p[3]]):
                    sub = [p for p in pairs if states[p[3]] == st]
                    if len(sub) >= 20:
                        a = [p[0] for p in sub]; b = [p[1] for p in sub]; k = len(sub)
                        ric = _corr(k, sum(a), sum(b), sum(x * x for x in a), sum(y * y for y in b), sum(x * y for x, y in zip(a, b)))
                        lean_s = [(p[0], p[2]) for p in sub if abs(p[0]) >= 5]
                        by[st] = {"ic": ric, "n": k, "n_eff": k * 5.0 / max(5.0, float(h)),
                                  "hit": (sum(1 for x, yr in lean_s if (x > 0) == (yr > 0)) / len(lean_s)) if len(lean_s) >= 10 else None}
            perf["by_regime"] = by
        out["performance"][lab] = perf
    first_px = next((i for i, v in enumerate(run.price) if v is not None), 0)
    out["history_years"] = round((len(cal) - first_px) / 252.0, 1)
    return out


# ------------------------------------------------------------------ hierarchical evidence: yearly checkpoints pooled by class and globally
def _pack(values: List[float]) -> str:
    import base64
    import zlib
    from array import array
    return base64.b64encode(zlib.compress(array("f", values).tobytes(), 6)).decode("ascii")


def checkpoint_key(asset_id: str) -> str:
    return f"shaffer_cp:{asset_id}:{cfg.VERSION}"


def checkpoint_payload(cls: str, yearly: Dict[str, dict], sigs: List[str]) -> dict:
    """The stored form of an asset's January evidence sums (what `save_checkpoints` writes)."""
    years = sorted(yearly)
    hs = sorted({h for y in years for h in yearly[y]})
    flat = []
    for y in years:
        for h in hs:
            row = yearly[y].get(h) or {}
            for sg in sigs:
                flat += list(row.get(sg) or (0, 0.0, 0.0, 0.0, 0.0, 0.0))
    return {"asset_class": cls, "years": years, "horizons": hs, "signals": sigs, "data": _pack(flat)}


def save_checkpoints(research, asset_id: str, cls: str, yearly: Dict[str, dict], sigs: List[str]) -> None:
    """Store this asset's evidence sums at each January (horizons up to 12M) so other assets can use them as priors."""
    research.store.kv_set(checkpoint_key(asset_id), checkpoint_payload(cls, yearly, sigs))
    research._prior_cache = None


def parse_checkpoints(v: dict) -> tuple:
    """(asset class, years, horizons, signals, the sums as float32) of a stored checkpoint payload."""
    import base64
    import zlib
    from array import array
    a = array("f")
    a.frombytes(zlib.decompress(base64.b64decode(v["data"])))
    return v["asset_class"], tuple(v["years"]), tuple(v["horizons"]), tuple(v["signals"]), a


def prior_cache(research) -> dict:
    """Every asset's stored checkpoints, {"keys": [kv keys], "per": {asset: parse_checkpoints(...)}} in key order;
    reread whenever the set of keys changes or this process saved a checkpoint."""
    cache = getattr(research, "_prior_cache", None)
    keys = research.store.kv_keys("shaffer_cp:")
    keys = [k for k in keys if k.endswith(":" + cfg.VERSION)]
    if cache is None or cache.get("keys") != keys:
        per = {}
        for k in keys:
            v = research.store.kv_get(k)
            if not v:
                continue
            per[k.split(":")[1]] = parse_checkpoints(v)
        cache = {"keys": keys, "per": per}
        research._prior_cache = cache
    return cache


def load_priors(research, asset_id: str, cls: str, signals: Optional[List[str]] = None) -> Dict[str, dict]:
    """{year: {h: {signal: (class sums, global sums)}}} from every OTHER asset's checkpoints (point in time: a
    January checkpoint only holds outcomes known by that January); only for `signals` when given."""
    return aggregate_priors(prior_cache(research)["per"], asset_id, cls, signals)


def aggregate_priors(per: Dict[str, tuple], asset_id: str, cls: str, signals: Optional[List[str]] = None) -> Dict[str, dict]:
    """The class and global sums over every asset in `per` but `asset_id`, each added in the order of `per`. With
    `signals` (the ones the asset has), only those signals' sums are added — the others are never read — while the
    years and horizons are the same as without."""
    wanted = None if signals is None else set(signals)
    layouts: Dict[tuple, tuple] = {}
    out: Dict[str, dict] = {}
    agg: Dict[tuple, list] = {}
    for aid, (acls, years, hs, sigs, arr) in per.items():
        if aid == asset_id or not sigs:
            continue
        for y in years:
            yd = out.setdefault(y, {})
            for h in hs:
                yd.setdefault(h, {})
        lay = layouts.get((years, hs, sigs))
        if lay is None:
            keys = [(y, h, sg) for y in years for h in hs for sg in sigs]
            lay = layouts[(years, hs, sigs)] = [(k, 6 * i) for i, k in enumerate(keys) if wanted is None or k[2] in wanted]
        vals = arr.tolist()
        same = acls == cls
        for key, p in lay:
            slot = agg.get(key)
            if slot is None:
                slot = agg[key] = [[0.0] * 6, [0.0] * 6]
            g = slot[1]
            g[0] += vals[p]; g[1] += vals[p + 1]; g[2] += vals[p + 2]; g[3] += vals[p + 3]; g[4] += vals[p + 4]; g[5] += vals[p + 5]
            if same:
                c = slot[0]
                c[0] += vals[p]; c[1] += vals[p + 1]; c[2] += vals[p + 2]; c[3] += vals[p + 3]; c[4] += vals[p + 4]; c[5] += vals[p + 5]
    for (y, h, sg), (c, g) in agg.items():
        out[y][h][sg] = (tuple(c), tuple(g))
    return out

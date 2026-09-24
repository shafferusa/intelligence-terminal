r"""The Shaffer Score: one score per asset, per horizon, from −100 (strongly bearish) to +100 (strongly bullish).

    SS_{a,h,t} = 100 · tanh( Σ_f W_{f,a,h,t} · [ Σ_{i∈f} w_{i,a,h,t} · s_{i,a,t} · c_{i,a,h} · r_{i,t} · d_{i,t} ] · A_{f,a} · H_{f,h}  /  K )

a = asset, h = horizon, t = today, f = signal family (Momentum, Valuation, Rates, …), i = signal in that family.

  s_i   standardised bullish/bearish reading in −1..+1: the signal's capped z-score, halved and clipped, turned
        toward "bullish" by the sign its history gives it (a signal that has predicted *lower* returns when high reads
        bearish when high).
  w_i   historical predictive strength: the signal's usefulness at this horizon (rank IC shrunk by its t-statistic),
        as a share of its family's total, so a family with many signals does not outvote one with few.
  c_i   confidence in that evidence: t² / (t² + T0²) × min(1, n_eff / 30) × (½ + ½ · stability).
        t = the IC's t-statistic on independent observations (T0 = 2, so t = 2 gives ½, t = 1 gives ⅕, t = 4 gives ⅘),
        n_eff = independent observations, stability = share of the history's thirds in which the signal pointed the
        same way. (The false-discovery q-value is shown next to each signal but not multiplied in: with ~60 signals
        tested, q sits near 1 for all of them whenever none is individually decisive, which would zero the score
        rather than grade it.)
  r_i   current-regime adjustment in 0..1.5: how well the signal has worked in today's regimes relative to overall,
        trusted in proportion to how many independent observations those regimes hold (1 when unknown).
  d_i   decay in 0..1: 1 when the most recent third of the history agrees with the whole, ½ when it shows nothing,
        0 when it has reversed.
  W_f   family weight: how much evidence the family has at this horizon, min(1, mean of its three strongest w / 0.08)
        × min(1, median n_eff / 30).
  A_f,a asset applicability (table below: valuation matters for stocks, not for bitcoin the same way).
  H_f,h horizon applicability (table below: technicals for days and weeks, valuation for years).
  K     normalisation/calibration: KAPPA × Σ_f A·H over the families present. KAPPA was calibrated on ten years of
        weekly readings (today's evidence applied to each past week's signals) of SPY, TLT, GOLD, BTC and NVDA at
        horizons 1D–12M: |numerator| / Σ A·H had a median of about 0.003 and a 95th percentile of about 0.038, so with
        KAPPA = 0.04 the median |score| is about 7, one reading in ten exceeds about 60 and one in twenty about 75.
        Long horizons (3Y, 5Y) carry far less evidence (W is small), so their scores stay near zero, as they should.
        Recalibrate when the universe or the evidence changes materially.

The inputs are the same point-in-time evidence the Quant Score uses (the signal-horizon matrix, today's z-scores and
the current regime), so the two can be compared; the Shaffer Score adds the confidence, regime, decay and
applicability terms and aggregates by family.

To replace the algorithm without a code change, put a file at ~/.finsim2/shaffer_score.py (or the path in
FINSIM2_SHAFFER) defining VERSION and either
  score_asset(inputs) -> the same structure `score_asset` below returns, or
  score(metrics, asset) -> float           (the older interface: one value, applied to every horizon).
The file wins when present. `inputs` is documented on `score_asset`.
"""
from __future__ import annotations

import importlib.util
import math
import os
from typing import Dict, List, Optional

NAME = "Shaffer Score"
VERSION: Optional[str] = "1.0"
RANGE = (-100.0, 100.0)
FORMULA = (r"SS_{a,h,t}=100\tanh\left(\frac{\sum_f W_{f,a,h,t}\left[\sum_{i\in f} w_{i,a,h,t}\,s_{i,a,t}\,c_{i,a,h}\,r_{i,t}\,d_{i,t}\right]"
           r"A_{f,a}H_{f,h}}{K}\right)")

HORIZONS = ["1D", "1W", "1M", "3M", "6M", "12M", "3Y", "5Y", "10Y"]
STRONG_U = 0.08          # usefulness at which a family counts as fully evidenced (same scale as the Quant Score)
FULL_SAMPLE = 30.0       # independent observations for full trust
KAPPA = 0.04             # K = KAPPA · Σ A·H (calibrated; see the docstring)
S_SCALE = 2.0            # s = clip(z / S_SCALE, −1, 1)
T0 = 2.0                 # c's evidence part = t² / (t² + T0²)
R_MAX = 1.5

# A_{f,a}: how much each family applies to each asset class (1 = fully). Edit freely; the page shows the values used.
APPLICABILITY: Dict[str, Dict[str, float]] = {
    #               EQUITY  ETF  INDEX TREASURY CORP_BOND COMMODITY  FX  CRYPTO
    "Returns":      dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=1.0, CORP_BOND=1.0, COMMODITY=1.0, FX=1.0, CRYPTO=1.0),
    "Momentum":     dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=1.0, CORP_BOND=1.0, COMMODITY=1.0, FX=1.0, CRYPTO=1.0),
    "Statistics":   dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=1.0, CORP_BOND=1.0, COMMODITY=1.0, FX=1.0, CRYPTO=1.0),
    "Risk":         dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=0.8, CORP_BOND=0.9, COMMODITY=0.9, FX=0.8, CRYPTO=1.0),
    "Volatility":   dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=0.8, CORP_BOND=0.9, COMMODITY=1.0, FX=0.9, CRYPTO=1.0),
    "Technical":    dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=0.7, CORP_BOND=0.7, COMMODITY=1.0, FX=1.0, CRYPTO=1.0),
    "TimeSeries":   dict(EQUITY=1.0, ETF=1.0, INDEX=1.0, TREASURY=1.0, CORP_BOND=1.0, COMMODITY=1.0, FX=1.0, CRYPTO=1.0),
    "Valuation":    dict(EQUITY=1.0, ETF=0.6, INDEX=0.6, TREASURY=0.4, CORP_BOND=0.4, COMMODITY=0.4, FX=0.5, CRYPTO=0.2),
    "Fundamentals": dict(EQUITY=1.0, ETF=0.0, INDEX=0.0, TREASURY=0.0, CORP_BOND=0.0, COMMODITY=0.0, FX=0.0, CRYPTO=0.0),
    "Rates":        dict(EQUITY=0.7, ETF=0.8, INDEX=0.7, TREASURY=1.0, CORP_BOND=1.0, COMMODITY=0.6, FX=0.9, CRYPTO=0.5),
    "Credit":       dict(EQUITY=0.8, ETF=0.8, INDEX=0.8, TREASURY=0.7, CORP_BOND=1.0, COMMODITY=0.5, FX=0.5, CRYPTO=0.5),
    "Macro":        dict(EQUITY=0.8, ETF=0.8, INDEX=0.8, TREASURY=0.8, CORP_BOND=0.8, COMMODITY=0.9, FX=0.9, CRYPTO=0.7),
}
# H_{f,h}: how much each family applies at each horizon (1D 1W 1M 3M 6M 12M 3Y 5Y 10Y).
HORIZON_FIT: Dict[str, List[float]] = {
    "Returns":      [1.0, 1.0, 1.0, 0.8, 0.7, 0.6, 0.4, 0.3, 0.3],
    "Momentum":     [0.5, 0.7, 1.0, 1.0, 1.0, 1.0, 0.6, 0.4, 0.3],
    "Statistics":   [1.0, 1.0, 0.9, 0.8, 0.6, 0.5, 0.4, 0.3, 0.3],
    "Risk":         [1.0, 1.0, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.5],
    "Volatility":   [1.0, 1.0, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.5],
    "Technical":    [1.0, 1.0, 0.9, 0.6, 0.4, 0.3, 0.2, 0.1, 0.1],
    "TimeSeries":   [0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.5, 0.4, 0.4],
    "Valuation":    [0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.0, 1.0, 1.0],
    "Fundamentals": [0.2, 0.3, 0.5, 0.8, 1.0, 1.0, 1.0, 0.9, 0.8],
    "Rates":        [0.4, 0.5, 0.7, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
    "Credit":       [0.4, 0.5, 0.7, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
    "Macro":        [0.4, 0.5, 0.7, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def applicability(family: str, asset_class: str) -> float:
    return APPLICABILITY.get(family, {}).get(asset_class, 0.5)


def horizon_fit(family: str, horizon: str) -> float:
    row = HORIZON_FIT.get(family)
    if row is None or horizon not in HORIZONS:
        return 0.5
    return row[HORIZONS.index(horizon)]


def signal_terms(z: float, rec: dict, regime: Dict[str, Optional[str]]) -> Optional[dict]:
    """s, |u|, c, r, d for one signal at one horizon (None when the evidence is missing)."""
    u = _num(rec.get("usefulness"))
    ic = _num(rec.get("ic"))
    if u is None or u == 0 or ic is None:
        return None
    direction = 1.0 if u > 0 else -1.0
    s = _clip(z / S_SCALE, -1.0, 1.0) * direction
    q = _num(rec.get("q"))
    t = _num(rec.get("t")) or 0.0
    n_eff = _num(rec.get("n_eff")) or 0.0
    stab = _num(rec.get("stability"))
    c = (t * t / (t * t + T0 * T0)) * min(1.0, n_eff / FULL_SAMPLE) * (0.5 + 0.5 * (stab if stab is not None else 0.5))
    # regime: usefulness inside today's regimes relative to overall, trusted by its sample
    by = rec.get("by_regime") or {}
    pairs = [(by[st].get("usefulness"), by[st].get("n_eff") or 0.0) for st in (regime or {}).values() if st and st in by and by[st].get("usefulness") is not None]
    if pairs and sum(n for _, n in pairs) > 0:
        tot = sum(n for _, n in pairs)
        u_reg = sum(x * n for x, n in pairs) / tot
        lam = min(1.0, (tot / len(pairs)) / FULL_SAMPLE)
        r = _clip((1.0 - lam) + lam * (u_reg * direction) / abs(u), 0.0, R_MAX)
    else:
        r = 1.0
    # decay: the recent third against the whole history
    rec_ic = _num(rec.get("ic_recent"))
    d = 1.0 if rec_ic is None or ic == 0 else _clip(0.5 + 0.5 * (rec_ic * (1.0 if ic > 0 else -1.0)) / abs(ic), 0.0, 1.0)
    return {"s": s, "u": abs(u), "c": c, "r": r, "d": d, "z": z, "direction": int(direction), "ic": ic, "t": t, "q": q, "n_eff": n_eff}


def score_asset(inputs: dict) -> dict:
    """The built-in Shaffer Score.

    inputs = {
      "asset":    {"id", "name", "asset_class", "sector", "currency", "price", "as_of"},
      "regime":   {dimension: state or None} for today (e.g. {"market": "bull", "volatility": "low_vol", ...}),
      "horizons": ["1D", ..., "10Y"],
      "signals":  {name: {"family", "label", "value" (today's raw value), "z" (today's capped z, or None),
                          "evidence": {horizon: {"ic", "usefulness", "t", "p", "q", "n_eff", "stability",
                                                 "ic_recent", "hit_rate", "by_regime": {state: {"usefulness", "n_eff", ...}}}}}},
    }
    Returns {"version", "formula", "horizons": {h: {"score", "numerator", "K", "families": [...], "n_signals", "reason"}}}.
    """
    cls = (inputs.get("asset") or {}).get("asset_class") or ""
    regime = inputs.get("regime") or {}
    out = {}
    for h in inputs.get("horizons") or HORIZONS:
        fams: Dict[str, List[dict]] = {}
        for name, sig in (inputs.get("signals") or {}).items():
            z = _num(sig.get("z"))
            rec = (sig.get("evidence") or {}).get(h)
            if z is None or not rec:
                continue
            t = signal_terms(z, rec, regime)
            if t is None:
                continue
            t.update(signal=name, label=sig.get("label") or name)
            fams.setdefault(sig.get("family") or "Other", []).append(t)
        families, num, ksum = [], 0.0, 0.0
        for f, items in fams.items():
            A, H = applicability(f, cls), horizon_fit(f, h)
            tot_u = sum(i["u"] for i in items)
            if tot_u <= 0 or A * H <= 0:
                continue
            for i in items:
                i["w"] = i["u"] / tot_u
                i["term"] = i["w"] * i["s"] * i["c"] * i["r"] * i["d"]
            bracket = sum(i["term"] for i in items)
            top = sorted((i["u"] for i in items), reverse=True)[:3]
            nes = sorted(i["n_eff"] for i in items)
            W = min(1.0, (sum(top) / len(top)) / STRONG_U) * min(1.0, nes[len(nes) // 2] / FULL_SAMPLE)
            contrib = W * bracket * A * H
            num += contrib
            ksum += A * H
            items.sort(key=lambda i: -abs(i["term"]))
            families.append({"family": f, "W": W, "A": A, "H": H, "bracket": bracket, "contribution": contrib, "n_signals": len(items),
                             "signals": [{k: i[k] for k in ("signal", "label", "z", "s", "w", "c", "r", "d", "term", "ic", "t", "q", "n_eff")} for i in items]})
        if not families:
            out[h] = {"score": None, "numerator": None, "K": None, "families": [], "n_signals": 0,
                      "reason": "no signal has evidence at this horizon"}
            continue
        K = KAPPA * ksum
        families.sort(key=lambda x: -abs(x["contribution"]))
        out[h] = {"score": 100.0 * math.tanh(num / K), "numerator": num, "K": K, "families": families,
                  "n_signals": sum(x["n_signals"] for x in families), "reason": None}
    return {"version": VERSION, "formula": FORMULA, "horizons": out}


def score(metrics: Dict, asset: Dict) -> Optional[float]:
    """The older single-value interface; the built-in algorithm uses `score_asset`."""
    return None


# ------------------------------------------------------------------ an override file, so the algorithm can change without a code change
def _override_path() -> str:
    return os.environ.get("FINSIM2_SHAFFER") or os.path.join(os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2"), "shaffer_score.py")


def load():
    """(version, kind, function, source). kind is "asset" (score_asset(inputs)) or "legacy" (score(metrics, asset))."""
    path = _override_path()
    if os.path.isfile(path):
        try:
            spec = importlib.util.spec_from_file_location("finsim2_shaffer_override", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(getattr(mod, "score_asset", None)):
                return getattr(mod, "VERSION", "custom"), "asset", mod.score_asset, path
            if callable(getattr(mod, "score", None)):
                return getattr(mod, "VERSION", "custom"), "legacy", mod.score, path
        except Exception as e:          # a broken override must not take the pages down: fall back to the built-in
            return VERSION, "asset", score_asset, f"built-in (override {path} failed to load: {e!r})"
    return VERSION, "asset", score_asset, "built-in (finsim2/shaffer_score.py)"


def is_override() -> bool:
    return os.path.isfile(_override_path())


def status() -> Dict:
    version, kind, _, source = load()
    return {"name": NAME, "version": version, "enabled": version is not None, "source": source, "range": list(RANGE), "kind": kind,
            "state": "live" if version is not None else "in development", "override_path": _override_path(), "formula": FORMULA,
            "applicability": APPLICABILITY, "horizon_fit": HORIZON_FIT, "horizons": HORIZONS,
            "constants": {"STRONG_U": STRONG_U, "FULL_SAMPLE": FULL_SAMPLE, "KAPPA": KAPPA, "S_SCALE": S_SCALE, "R_MAX": R_MAX, "T0": T0}}


def _clean_score(v) -> Optional[float]:
    v = _num(v)
    return None if v is None else _clip(v, -100.0, 100.0)


def evaluate(inputs: dict, metrics: Dict, asset: Dict) -> Optional[dict]:
    """Run whichever algorithm is loaded, never raising: a failure returns None with the reason in "error"."""
    version, kind, fn, source = load()
    if version is None:
        return None
    try:
        if kind == "legacy":
            v = _clean_score(fn(metrics, asset))
            res = {"version": version, "formula": None, "horizons": {h: {"score": v, "families": [], "reason": None if v is not None else "the custom score returned nothing"} for h in inputs.get("horizons") or HORIZONS}}
        else:
            res = fn(inputs)
            for h, r in (res.get("horizons") or {}).items():
                r["score"] = _clean_score(r.get("score"))
        res.update(source=source, kind=kind, version=res.get("version") or version)
        return res
    except Exception as e:
        return {"version": version, "source": source, "kind": kind, "horizons": {}, "error": f"{type(e).__name__}: {e}"}


def safe_score(fn, metrics: Dict, asset: Dict) -> Optional[float]:
    try:
        return _clean_score(fn(metrics, asset))
    except Exception:
        return None

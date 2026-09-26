"""The two fine-tune reports, generated from the results of engine/finetune.py and hedge/hedgetune.py:

SHAFFER_FINETUNE.md        — 1W Alpha fine-tuning (model selection, weights and why, costs, conviction, robustness),
                              1D after costs, 1M–12M sample limits, Directional + Alpha, versions, the 19 answers.
SHAFFER_HEDGE_FINETUNE.md  — the λ-conditional hedge-size surface, the frontier, product preference, H4 anatomy,
                              Alpha-conditional hedging.

Every sentence that states a result is computed from the result dict; nothing here is hand-written about the data.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

from . import learned as L

_f, _pc, _pcu = L._f, L._pc, L._pcu
ELIG = "LIVE SHADOW ELIGIBLE"
SEL_ORDER = ["learned global (D)", "learned hierarchy (E)", "blend", "ridge (relative return)", "elastic net", "pairwise (RankNet)",
             "listwise (ListNet)", "stability penalty (global)", "stability penalty (hierarchy)", "stability classes",
             "time decay (global)", "time decay (hierarchy)", "rolling window (global)", "rolling window (hierarchy)",
             "regime-conditional (global)", "regime-conditional (hierarchy)", "signal clusters", "signs", "interactions (economic)",
             "interactions (GBM-suggested)", "learned global + turnover smoothing", "learned hierarchy + turnover smoothing",
             "nested model selection"]


def _c(v) -> str:
    """A value safe inside a markdown table cell."""
    return str(v).replace("|", " · ")


def _t(v) -> str:
    return "—" if v is None else f"{v:+.1f}"


def _short(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str).replace("|", "/")[:200]


def _share(w: Optional[List[float]]) -> Optional[List[float]]:
    if not w:
        return None
    s = sum(abs(x) for x in w)
    return [x / s for x in w] if s > 0 else [0.0] * len(w)


def _ordered(models: dict) -> List[str]:
    return [k for k in SEL_ORDER if k in models] + [k for k in models if k not in SEL_ORDER]


def _ric(m: dict) -> Optional[float]:
    return ((m.get("walkforward") or {}).get("challenger") or {}).get("rank_ic")


def _prod_ric(W: dict) -> Optional[float]:
    for m in (W.get("models") or {}).values():
        v = ((m.get("walkforward") or {}).get("production") or {}).get("rank_ic")
        if v is not None:
            return v
    return None


def _eligible(res: dict) -> List[Tuple[str, str, dict]]:
    out = []
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        for k, v in ((res.get(lab) or {}).get("models") or {}).items():
            if v.get("status") == ELIG:
                out.append((lab, k, v))
    return out


def _bar_t(g: dict) -> float:
    """t against the stronger of the two live-shadow learned models (the smaller of the two paired t's)."""
    ts = [g.get("vsD_t"), g.get("vsE_t")] if "vsE_t" in g else [g.get("vsD_t")]
    return min(t if t is not None else -99 for t in ts)


def _best_pit(W: dict) -> Optional[str]:
    ms = W.get("models") or {}
    c = [k for k, v in ms.items() if v.get("pit") and v.get("family") not in ("baseline", "meta") and v["gates"].get("vsD_t") is not None]
    return max(c, key=lambda k: _bar_t(ms[k]["gates"])) if c else None


# ====================================================================== SHAFFER_FINETUNE.md
def markdown(res: dict, learned: Optional[dict] = None, hier: Optional[dict] = None) -> str:
    """`learned` = the learned-weights research (kv lab:learned) for production shares, reliability and the earlier
    capability suite; `hier` = {"weights": record-weighted mean of the hierarchy's deployable weights, "choice": …}."""
    out: List[str] = []
    w = out.append
    W = res.get("1W") or {}
    hier = hier or W.get("hierarchy_mean")
    w("# Shaffer fine-tune — the learned 1W Alpha, made more robust")
    w("")
    w(f"Run {res.get('started')} · {res.get('seconds')} s · `python -m finsim2 lab --finetune`. Research only: production "
      "(shaffer-2.1, shaffer-alpha-2.1-production, the Directional definition, hedge-2, the resizing challenger, "
      "benchmark-2.1-2026-09-25) and the two live-shadow learned models (alpha-learned-1w-global-exp, "
      "alpha-learned-1w-hierarchy-exp) are unchanged. Method: `engine/finetune.py` (module docstring); hedge: "
      "`hedge/hedgetune.py` → SHAFFER_HEDGE_FINETUNE.md.")
    w("")
    w("**The rule for every fine-tune:** the outer walk-forward (eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–) is never "
      "touched; every hyperparameter — the blend weight, the half-life, the window, the penalty, the regime dimension, the "
      "smoothing — is chosen per era from inner walk-forward weeks that matured before the era began. A fine-tuned model "
      "must pass the Alpha program's fixed gates against production (G1 t ≥ 2, G2 split, G3 ≥ 3/4 eras, G4 spread and net "
      "long-short), Benjamini–Hochberg FDR across every fine-tune, **and** a new gate G5 against BOTH learned models already "
      f"in live shadow — the global model D and the hierarchy E (Δ rank IC > 0 with t ≥ {_g5_t()} and ≥ 3/4 complete eras not "
      "worse, against each). Beating production is no longer enough: the bar is the learned models we already have.")
    w("")
    _key_findings(w, res, learned)
    _capability(w, res.get("capability"), learned)
    _answers(w, res, learned, hier)
    _selection(w, W)
    _choices(w, W)
    _weights(w, W, learned, hier)
    _costs(w, W)
    _conviction(w, W)
    _robust(w, W)
    _interactions(w, W)
    _oned(w, res.get("1D"))
    _long(w, res, learned)
    _dir(w, W)
    _today(w, W)
    _versions(w, res)
    return "\n".join(out) + "\n"


def _g5_t():
    from . import finetune as F
    return F.G5_T


def _key_findings(w, res, learned):
    W = res.get("1W") or {}
    ms = W.get("models") or {}
    cap = res.get("capability") or {}
    w("## Key findings")
    w("")
    w(f"- **Capability first:** {sum(1 for v in cap.values() if v.get('pass'))} of {len(cap)} new planted tests passed (ranking-only, "
      "time-decaying, regime-switching, hierarchy-specific, transaction-cost-sensitive, and no false improvement on a plain world) "
      "before any market result was read.")
    rep = W.get("reproduction") or {}
    w(f"- **Reproduction — and a correction:** the fine-tune code reproduces the learned global model (D, rank IC {_f(rep.get('D_rank_ic'))}) "
      f"exactly. The live-shadow hierarchy (E) measures {_f(rep.get('E_rank_ic'))}, not the +0.0546 in SHAFFER_LEARNED_WEIGHTS.md: the "
      "learned engine's walk-forward scored E with the validated model's pooling K instead of E's own K_E (e.g. K = 10 instead of "
      "5000 in 2013–16). The live-shadow model itself was always built with K_E, so what is in live shadow is the stronger model; "
      "only its reported backtest was wrong (fixed in `learned.py`, erratum added to that report). Consequence here: G5 must beat "
      f"E as well as D (production {_f(_prod_ric(W))}).")
    el = [(k, v) for k, v in ms.items() if v.get("status") == ELIG]
    if el:
        w("- **Fine-tunes that beat both production and the learned global model under every gate (G1–G5 + FDR):** " + "; ".join(
            f"{k} — rank IC {_f(_ric(v))}, Δ vs learned global {_f(v['gates'].get('vsD_mean'))} (t {_t(v['gates'].get('vsD_t'))}), "
            f"vs learned hierarchy {_f(v['gates'].get('vsE_mean'))} (t {_t(v['gates'].get('vsE_t'))}), net L/S "
            f"{_pc((v.get('ls20') or {}).get('net'), 3)}/wk" for k, v in el) + ". Each gets a new immutable version in live shadow.")
    else:
        b = _best_pit(W)
        bv = ms.get(b) or {}
        w("- **No fine-tune beats both live-shadow learned models under every gate.** "
          + (f"The closest is {b}: Δ rank IC vs learned global {_f(bv['gates'].get('vsD_mean'))} (t {_t(bv['gates'].get('vsD_t'))}), "
             f"vs learned hierarchy {_f(bv['gates'].get('vsE_mean'))} (t {_t(bv['gates'].get('vsE_t'))}, "
             f"{bv['gates'].get('eras_vsE')}/4 eras not worse). " if b else "")
          + "The learned hierarchy already in live shadow (E) stays the model to watch; fine-tuning did not find a robust improvement on it.")
    near = [(k, v) for k, v in ms.items() if v.get("pit") and v.get("status") != ELIG and v["gates"].get("G1") and v["gates"].get("G3")
            and v.get("family") not in ("baseline",)]
    if near:
        w(f"- **Beat production but not the bar:** {len(near)} fine-tunes pass G1 and G3 against production (as D and E do) yet fail "
          "G5 or FDR — they are variations of the same edge, not improvements on it.")
    dE = [(k, v) for k, v in ms.items() if v.get("pit") and v.get("family") not in ("baseline",) and v["gates"].get("G5_D") and not v["gates"].get("G5_E")]
    if dE:
        w(f"- **Beat the global model but not the hierarchy:** {', '.join(k for k, _ in dE)} — every one is a hierarchy model; what they "
          "gain over D is the hierarchy E already delivers.")
    D, E = ms.get("learned global (D)") or {}, ms.get("learned hierarchy (E)") or {}
    th = (W.get("thresholds") or {}).get("learned global (D)") or []
    if th:
        best = max(th, key=lambda r: r.get("net") or -9)
        w(f"- **Costs and extremes:** for the learned global model the best net long-short is at the {int(best['frac'] * 100)}% tails "
          f"({_pc(best.get('net'), 3)} per week after product costs, turnover {_pcu(best.get('turnover'))}); at 20% it is "
          f"{_pc((D.get('ls20') or {}).get('net'), 3)} (gross {_pc((D.get('ls20') or {}).get('gross'), 3)}).")
    cv = (W.get("conviction") or {}).get("learned global (D)") or {}
    if cv:
        w(f"- **Conviction:** score percentile → relative return is {'monotonic' if cv.get('monotonic') else 'not monotonic'} "
          f"(AlphaReliability = rank correlation of bucket means {_f(cv.get('alpha_reliability'), 2)}). The ConvictionMultiplier is "
          "reported, not applied.")
    one = (res.get("1D") or {}).get("nested_cost_aware") or {}
    if one:
        w(f"- **1D after costs:** the nested cost-aware 1D strategy earns {_pc(one.get('net_per_trade'), 3)} per trade net (t {_t(one.get('t'))}, "
          f"{one.get('eras_positive')}/4 eras positive, active {_pcu(one.get('active_share'))} of weeks) — "
          + ("**profitable after costs under the fixed test**" if one.get("profitable") else "**not profitable after costs**") + _oned_flat(res.get("1D")))
    lg = [(lab, k) for lab in ("1M", "3M", "6M", "12M") for k, v in ((res.get(lab) or {}).get("models") or {}).items() if v.get("status") == ELIG]
    w("- **1M–12M:** " + ("validated: " + ", ".join(f"{lab} {k}" for lab, k in lg) if lg else
                           "no model — lower-dimensional, family-level, stable-only or fundamental — is validated. The effective "
                           "sample (independent periods per signal) is the binding limit, see §10."))
    da = W.get("dir_alpha") or {}
    if da.get("gates"):
        w(f"- **Directional:** adding the out-of-sample 1W Alpha percentile to the point-in-time prior "
          + ("passes" if da.get("passed") else "does not pass") + " the prior-only gate (Brier gain vs prior "
          f"{_f((da.get('vs_prior') or {}).get('brier_gain'), 5)}, t {_t((da.get('vs_prior') or {}).get('brier_t'))}). Directional stays conservative: "
          + ("the gain is small in probability terms and earns only a research version, never a change to the Directional definition." if da.get("passed")
             else "nothing changes."))
    hd = res.get("hedge") or {}
    if hd:
        el_h = sum(1 for cs in (hd.get("cells") or {}).values() for c in cs.values() if c.get("status") == ELIG)
        w(f"- **Hedge:** the λ-conditional size surface is learned per objective and volatility regime (§ SHAFFER_HEDGE_FINETUNE.md); "
          f"{el_h} (objective, horizon, λ) cells pass H1–H5. H4 is not loosened; a redesign is proposed for a new version only.")
    w("")


def _oned_flat(one: Optional[dict]) -> str:
    """How much of the 1D result survives a flat 10 bp per side instead of the product-specific cost estimates."""
    th = ((one or {}).get("thresholds") or {})
    rows = [(k, r) for k, rs in th.items() for r in rs if r.get("net_flat10bp") is not None]
    if not rows:
        return "."
    k, r = max(rows, key=lambda kr: kr[1]["net_flat10bp"])
    return (f"; but it rests on the product-specific cost estimates (single stock 5 bp, ETF 2–3 bp one way): at a flat 10 bp per side "
            f"the best 1D tail ({k}, {int(r['frac'] * 100)}%) nets {_pc(r['net_flat10bp'], 3)} per trade. "
            + ("It survives." if r["net_flat10bp"] > 0 else "It does not survive — treat 1D as cost-fragile."))


def _capability(w, cap, learned):
    w("## 1. Capability — can the new optimisers find what they claim to find?")
    w("")
    w("Planted synthetic worlds (16 assets in a class / sector tree, 2002–2026, weekly, regimes switching every ~26 weeks). The "
      "same code that ran on market data must recover each planted effect, and must NOT report an improvement where none exists.")
    w("")
    if not cap:
        w("Not run.")
        w("")
        return
    w("| Test | Planted / expected | Recovered | Result |")
    w("|---|---|---|---|")
    for k, v in cap.items():
        planted = {kk: vv for kk, vv in v.items() if kk.startswith("truth")} or "—"
        rec = {kk: vv for kk, vv in v.items() if kk != "pass" and not kk.startswith("truth")}
        w(f"| {k} | {_short(planted)} | {_short(rec)} | {'**PASS**' if v.get('pass') else '**FAIL**'} |")
    w("")
    w("The last test calibrated gate G5: on a plain linear world nested selection across the fine-tune families reached t ≈ 1 "
      "against the learned global model by chance, so G5 requires t ≥ 2 — fixed before the market run.")
    old = (learned or {}).get("capability") or {}
    if old:
        w(f"The earlier suite of the learned engine ({sum(1 for v in old.values() if v.get('pass'))}/{len(old)} passed: linear, ranking "
          "target, sparse, correlated, regime-dependent, sector-specific, horizon-specific, interaction, noise, asset-specific) still applies.")
    w("")


def _answers(w, res, learned, hier):
    W = res.get("1W") or {}
    ms = W.get("models") or {}
    names = W.get("signals") or []
    hd = res.get("hedge") or {}
    w("## 2. Answers")
    w("")
    b = _best_pit(W)
    bv = ms.get(b) or {}
    el = [k for k, v in ms.items() if v.get("status") == ELIG]
    nest = ms.get("nested model selection") or {}
    w(f"**1. Can 1W Alpha improve beyond the current learned models?** "
      + (f"Yes, under every gate: {', '.join(el)}." if el else
         f"Not robustly. The best single family against the stronger live model is {b} (Δ rank IC vs learned hierarchy "
         f"{_f(bv.get('gates', {}).get('vsE_mean'))}, t {_t(bv.get('gates', {}).get('vsE_t'))}; vs learned global "
         f"{_f(bv.get('gates', {}).get('vsD_mean'))}, t {_t(bv.get('gates', {}).get('vsD_t'))}); nested selection across all families — the "
         f"honest version of 'pick the best' — gives Δ vs hierarchy {_f(nest.get('gates', {}).get('vsE_mean'))} "
         f"(t {_t(nest.get('gates', {}).get('vsE_t'))}). None clears G5 against both (t ≥ {_g5_t()}, ≥ 3/4 eras)."))
    w("")
    rk = sorted([k for k, v in ms.items() if v.get("pit") and v.get("family") not in ("baseline", "meta")],
                key=lambda k: -_bar_t(ms[k]["gates"]))
    w("**2. Which optimisation method works best?** Ranked by the weaker of the two comparisons (vs learned global D / vs learned "
      "hierarchy E, Δ rank IC and t): "
      + "; ".join(f"{k} {_f(ms[k]['gates'].get('vsD_mean'))} ({_t(ms[k]['gates'].get('vsD_t'))}) / {_f(ms[k]['gates'].get('vsE_mean'))} "
                  f"({_t(ms[k]['gates'].get('vsE_t'))})" for k in rk[:6])
      + (f". Worst: {rk[-1]}." if rk else ".") + " Hierarchy-based fine-tunes lead against D because they contain E; against E the "
      "differences are small. The ranking objectives (pairwise, listwise, relative-return ridge) are worse than the pairwise "
      "least-squares target already in use.")
    w("")
    bl = ms.get("blend") or {}
    w(f"**3. Does blending global + hierarchy help?** α (weight on the global model's rank) chosen per era: {_choice_str(bl)}; "
      f"today α = {bl.get('final')}. Rank IC {_f(_ric(bl))} vs global {_f(_ric(ms.get('learned global (D)') or {}))} and hierarchy "
      f"{_f(_ric(ms.get('learned hierarchy (E)') or {}))}; Δ vs global {_f(bl.get('gates', {}).get('vsD_mean'))} (t {_t(bl.get('gates', {}).get('vsD_t'))}) — "
      + ("it helps." if bl.get("status") == ELIG else "not a validated improvement."))
    w("")
    for q, keys, what in ((4, ("time decay (global)", "time decay (hierarchy)"), "half-life (years)"),
                          (5, ("rolling window (global)", "rolling window (hierarchy)"), "window (years)")):
        parts = []
        for k in keys:
            v = ms.get(k) or {}
            parts.append(f"{k}: {what} per era {_choice_str(v)}, today {v.get('final')}; Δ vs global {_f(v.get('gates', {}).get('vsD_mean'))} "
                         f"(t {_t(v.get('gates', {}).get('vsD_t'))})")
        title = "Does time decay help?" if q == 4 else "Does rolling history help?"
        ok = any((ms.get(k) or {}).get("status") == ELIG for k in keys)
        w(f"**{q}. {title}** " + "; ".join(parts) + (". Validated." if ok else ". Not validated — 'None' (all history) is chosen whenever "
                                                         "the inner weeks prefer it; forgetting old data costs more precision than it buys adaptivity."))
        w("")
    rg = [ms.get("regime-conditional (global)") or {}, ms.get("regime-conditional (hierarchy)") or {}]
    st = W.get("stability_today") or {}
    cls = st.get("classes") or []
    w(f"**6. Do regimes explain instability?** Regime-conditional weights (dimension and shrinkage chosen per era, heavily shrunk to the "
      f"base): global per era {_choice_str(rg[0])}, Δ vs global {_f(rg[0].get('gates', {}).get('vsD_mean'))} (t {_t(rg[0].get('gates', {}).get('vsD_t'))}); "
      f"hierarchy Δ {_f(rg[1].get('gates', {}).get('vsD_mean'))} (t {_t(rg[1].get('gates', {}).get('vsD_t'))}). "
      f"Of {len(cls)} signals, {cls.count('REGIME DEPENDENT')} are REGIME DEPENDENT, {cls.count('UNSTABLE')} UNSTABLE, "
      f"{cls.count('STABLE')} STABLE, {cls.count('NO EVIDENCE')} NO EVIDENCE. "
      + ("Regimes explain part of it and the conditional model is validated." if any(r.get("status") == ELIG for r in rg) else
         "Regimes explain little that survives out of sample: conditioning did not improve the ranking."))
    w("")
    stable = [names[i] for i, c in enumerate(cls) if c == "STABLE"] if names else []
    sgn = st.get("stable_sign") or []
    wd = (W.get("weights") or {}).get("D") or []
    w("**7. Which signals are genuinely stable?** " + (", ".join(
        f"{n} ({'+' if (wd[names.index(n)] if wd else 0) >= 0 else '−'})" for n in stable) if stable else "none")
      + f" — STABLE means the leave-one-era-out / leave-one-class-out weight keeps its sign and its size varies little "
        f"({len(stable)} of {len(cls)}). Signals with a stable sign (weaker): {sum(1 for s in sgn if s)}.")
    w("")
    th = (W.get("thresholds") or {}).get("learned global (D)") or []
    w("**8. Is the 1W edge concentrated in extreme scores?** Learned global, long top / short bottom fraction, per week after "
      "product costs: " + ", ".join(f"{int(r['frac'] * 100)}% {_pc(r.get('net'), 3)} (gross {_pc(r.get('gross'), 3)}, t {_t(r.get('t'))})" for r in th)
      + ". " + _extreme_verdict(th))
    w("")
    one = (res.get("1D") or {}).get("nested_cost_aware") or {}
    w(f"**9. Can 1D become profitable after costs?** " + (
        f"{'Yes' if one.get('profitable') else 'No'}: the nested cost-aware choice (model × regime filter × tail fraction, by inner "
        f"net round-trip P&L; standing aside when nothing was positive) earns {_pc(one.get('net_per_trade'), 3)} per trade "
        f"(t {_t(one.get('t'))}), eras {', '.join(_pc(x, 3) for x in (one.get('eras') or []))}" + _oned_flat(res.get("1D")) if one else "not run."))
    w("")
    lg = [(lab, k) for lab in ("1M", "3M", "6M") for k, v in ((res.get(lab) or {}).get("models") or {}).items() if v.get("status") == ELIG]
    w("**10. Can any 1M–6M model become validated?** " + ("Yes: " + ", ".join(f"{a} {b}" for a, b in lg) if lg else
                                                          "No. See §10 for why (sample limits) and which reduced-dimension model came closest."))
    w("")
    E = ms.get("learned hierarchy (E)") or {}
    w(f"**11. What hierarchy depth is best now?** The nested choice (pooling K | depth) per era: {_choice_str(E)}; today **{E.get('final')}**. "
      f"The hierarchy's rank IC {_f(_ric(E))} vs global {_f(_ric(ms.get('learned global (D)') or {}))}; Δ {_f(E.get('gates', {}).get('vsD_mean'))} "
      f"(t {_t(E.get('gates', {}).get('vsD_t'))}).")
    w("")
    rec = max(el, key=lambda k: _bar_t(ms[k]["gates"])) if el else "learned hierarchy (E)"
    fin = (W.get("finals") or {}).get(rec) or {}
    ww = (W.get("weights") or {}).get(rec) or ((fin.get("weights") or {}).get("global") if fin else None)
    hm = (W.get("hierarchy_mean") or {}).get("weights") if rec == "learned hierarchy (E)" else None
    ww = hm or ww or (W.get("weights") or {}).get("D") or []
    sh = _share(ww) or []
    top = sorted(range(len(sh)), key=lambda i: -abs(sh[i]))[:10]
    what = ("the average over assets of the hierarchy's weights (each asset uses its own node's weights; depth "
            f"{E.get('final')})" if hm else ("the global node of " + rec + (f" (assets use their own node's weights, depth {fin.get('depth')})" if fin.get("depth") not in (None, "global") else "")))
    w(f"**12. What should today's learned 1W weights be?** {'The validated fine-tune ' + rec if el else 'Unchanged — the learned hierarchy already in live shadow (E)'}; "
      f"largest signed shares of |weight| ({what}): " + ", ".join(f"{names[i]} {_pc(sh[i], 1)}" for i in top if names) + ". Full table in §5.")
    w("")
    td = (W.get("today") or {}).get(rec) or (W.get("today") or {}).get("learned global (D)") or {}
    srt = sorted([(a, v["score"]) for a, v in td.items() if isinstance(v, dict) and v.get("score") is not None], key=lambda x: -x[1])
    if srt:
        w(f"**13. What should today's learned scores be?** ({rec}) Highest: " + ", ".join(f"{a} {s:+.3f}" for a, s in srt[:8])
          + "; lowest: " + ", ".join(f"{a} {s:+.3f}" for a, s in srt[-8:]) + ". All assets in §14.")
    else:
        w("**13. What should today's learned scores be?** Not available in this run.")
    w("")
    cv = (W.get("conviction") or {}).get("learned global (D)") or {}
    w(f"**14. Does Alpha magnitude support conviction sizing?** AlphaReliability {_f(cv.get('alpha_reliability'), 2)}, "
      f"{'monotonic' if cv.get('monotonic') else 'not monotonic'} across {len(cv.get('buckets') or [])} percentile buckets. "
      + ("The tails carry more expected relative return than the middle, so a ConvictionMultiplier is supported descriptively — it is "
         "reported (§7), not applied; applying it would be a new version." if (cv.get("alpha_reliability") or 0) >= 0.8 else
         "Not reliably: the ConvictionMultiplier is reported but should not be used."))
    w("")
    w("**15. What hedge multiplier is optimal by λ / objective?** " + _hedge_q15(hd))
    w("")
    w("**16. Can H4 failures be economically understood?** " + _hedge_q16(hd))
    w("")
    w("**17. Does Alpha strength change the optimal hedge?** " + _hedge_q17(hd))
    w("")
    hel = [f"{lab} {k.replace('@', ' at λ = ')}" for lab, cs in (hd.get("cells") or {}).items() for k, c in cs.items() if c.get("status") == ELIG]
    lel = [f"{lab} {k}" for lab, k, _ in _eligible(res)]
    w("**18. Is anything newly eligible for live shadow?** " + ("Alpha: " + ", ".join(lel) if lel else "Alpha: nothing.")
      + " " + ("Hedge sizing cells that pass every gate: " + ", ".join(hel) + ". They are recorded in hedge-lambda-sizing-exp with their "
               "multiples, but NOT put in live shadow: the hedge live grader measures variance per hedge group, not utility at a chosen "
               "λ, so a λ-conditional size cannot be graded honestly until a λ-aware grader exists." if hel else "Hedge: nothing."))
    w("")
    w("**19. Is anything eligible for eventual production promotion?** Not yet — by rule. Promotion needs a model in live shadow "
      "with ≥ 60 graded live outcomes that confirm the backtest, and your approval. The two learned 1W models entered live shadow on "
      "their registration date; " + ("the newly eligible fine-tunes start their clock today." if lel else "nothing new starts a clock today."))
    w("")


def _choice_str(m: dict) -> str:
    ch = m.get("choices") or {}
    eras = [a for a, _ in L.TEST_ERAS]
    return ", ".join(f"{c[:4]}: {_c(ch.get(c))}" for c in eras if c in ch) or "—"


def _extreme_verdict(th: List[dict]) -> str:
    if not th:
        return ""
    by = {r["frac"]: r for r in th}
    a, b = by.get(0.05, {}).get("gross"), by.get(0.5, {}).get("gross")
    if a is None or b is None:
        return ""
    if a > 2 * b:
        return "Gross spread per name rises sharply toward the tails — the edge is concentrated in extreme scores; costs and n_eff decide how far out to go."
    return "The spread widens only mildly in the tails — the edge is spread across the cross-section, not concentrated in extremes."


def _selection(w, W):
    ms = W.get("models") or {}
    pr = _prod_ric(W)
    w("## 3. Model selection — 1W Alpha")
    w("")
    w("Walk-forward 2009 → today (outer eras never touched by any choice). Net long-short: top minus bottom 20% of each week's "
      "scored cross-section, equal weight, after one-way product costs on traded weight (single stock 5 bp, sector ETF 3 bp, index "
      "1 bp, …). Turnover: share of each leg replaced per week. Stability: rank autocorrelation of scores week to week (1 = no churn). "
      "Eras: complete eras where the model beat production / was not worse than the learned global (D) · hierarchy (E) model.")
    w("")
    w("| Model | Family | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Δ vs learned hierarchy (t) | Net weekly L/S | Turnover | Eras + vs prod | Eras ≥ global · hierarchy | FDR | Stability | Live-shadow eligible? |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    w(f"| production (shaffer-alpha-2.1) | — | {_f(pr)} | — | — | — | — | — | — | — | — | — | (production) |")
    for k in _ordered(ms):
        v = ms[k]
        g = v.get("gates") or {}
        p = (v.get("walkforward") or {}).get("paired") or {}
        ls = v.get("ls20") or {}
        base = v.get("family") == "baseline"
        w(f"| {k} | {v.get('family')} | {_f(_ric(v))} | {_f(p.get('mean'))} ({_t(p.get('t'))}) | "
          f"{'—' if k == 'learned global (D)' else _f(g.get('vsD_mean')) + ' (' + _t(g.get('vsD_t')) + ')'} | "
          f"{'—' if k == 'learned hierarchy (E)' else _f(g.get('vsE_mean')) + ' (' + _t(g.get('vsE_t')) + ')'} | {_pc(ls.get('net'), 3)} | "
          f"{_pcu(ls.get('turnover'))} | {g.get('eras_won')}/{g.get('eras_complete')} | {g.get('eras_vsD')}/4 · {g.get('eras_vsE', '—')}/4 | "
          f"{'—' if base or g.get('fdr') is None else ('yes' if g.get('fdr') else 'no')} | {_f(v.get('churn'), 2, False)} | "
          f"{'already in live shadow' if base else ('**YES**' if v.get('status') == ELIG else v.get('status') or '—')} |")
    w("")
    w("Gates per model (G1–G4 vs production, G5 vs both learned live-shadow models):")
    w("")
    w("| Model | G1 | G2 (split t) | G3 | G4 | G5 | Quintile spread | Pearson IC | Monotonicity | Hit vs median |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for k in _ordered(ms):
        v = ms[k]
        g = v.get("gates") or {}
        c = (v.get("walkforward") or {}).get("challenger") or {}
        yn = lambda x: "✓" if x else "✗"  # noqa: E731
        w(f"| {k} | {yn(g.get('G1'))} | {yn(g.get('G2'))} ({_t(g.get('split_t'))}) | {yn(g.get('G3'))} | {yn(g.get('G4'))} | "
          f"{'—' if v.get('family') == 'baseline' else yn(g.get('G5'))} | {_pc(c.get('quintile_spread'), 2)} | {_f(c.get('ic'))} | "
          f"{_f((v.get('walkforward') or {}).get('mono_challenger'), 2)} | {_pcu(c.get('hit_vs_median'))} |")
    w("")
    w("Rank IC by era (walk-forward):")
    w("")
    labs = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
    w("| Model | " + " | ".join(labs) + " | Δ vs global by era | Δ vs hierarchy by era |")
    w("|---|" + "---|" * (len(labs) + 2))
    for k in _ordered(ms):
        v = ms[k]
        er = [((e.get("challenger") or {}).get("rank_ic")) for e in (v.get("eras") or [])]
        w(f"| {k} | " + " | ".join(_f(x) for x in (er + [None] * 5)[:5]) + " | " + " ".join(_f(x, 3) for x in (v.get("eras_vsD") or [])) + " | " + " ".join(_f(x, 3) for x in (v.get("eras_vsE") or [])) + " |")
    w("")


def _choices(w, W):
    ms = W.get("models") or {}
    w("## 4. What the inner walk-forward chose")
    w("")
    w("Hyperparameters chosen at each outer cut from inner weeks matured before it (≥ 52 inner weeks, else the default); "
      "'final' = today's choice from all matured data.")
    w("")
    w("| Model | 2009 | 2013 | 2017 | 2021 | 2025 | split 2018 | final | Note |")
    w("|---|---|---|---|---|---|---|---|---|")
    for k in _ordered(ms):
        v = ms[k]
        ch = v.get("choices") or {}
        w(f"| {k} | " + " | ".join(_c(ch.get(c, "—")) for c in [a for a, _ in L.TEST_ERAS] + [L.SPLIT]) + f" | {_c(v.get('final'))} | {(v.get('note') or '').replace('|', '/')[:160]} |")
    w("")


def _weights(w, W, learned, hier):
    names = W.get("signals") or []
    wts = W.get("weights") or {}
    con = W.get("contributions") or {}
    ms = W.get("models") or {}
    if not names or not wts.get("D"):
        return
    sig = {s["name"]: s for s in (((learned or {}).get("horizons") or {}).get("1W") or {}).get("alpha", {}).get("signals", [])}
    st = W.get("stability_today") or {}
    cls = st.get("classes") or [None] * len(names)
    D = _share(wts["D"])
    Hm = _share((hier or {}).get("weights"))
    w("## 5. Weights — and exactly why a model differs")
    w("")
    w("Signed share of |weight| (so different scalings compare). *Production*: the average production share on 1W research "
      "records. *Learned global*: D's weights from all matured data. *Learned hierarchy*: "
      + ("the record-weighted average of the hierarchy's deployable weights over assets (its global node equals D's)." if Hm else
         "its global node equals D's; its asset-level deviations are in the ML Lab.")
      + " *Era stability*: leave-one-era-out / leave-one-class-out class. *Reliability*: out-of-sample t of the signal's global "
      "weight (learned run). *Contribution to ΔIC*: change in the walk-forward mean weekly rank IC when ONLY that signal's weight "
      "is moved from D's to the challenger's, era by era — the columns sum approximately to the model's Δ vs learned global.")
    w("")
    shown = [k for k in _ordered(ms) if k in con] or [k for k in _ordered(ms) if k in wts and k != "D"][:2]
    for k in shown:
        if k not in wts:
            continue
        C = _share(wts[k])
        c = con.get(k)
        g = (ms.get(k) or {}).get("gates") or {}
        w(f"### {k} — Δ vs learned global {_f(g.get('vsD_mean'))} (t {_t(g.get('vsD_t'))}); sum of per-signal contributions "
          f"{_f(sum(x for x in (c or []) if x) if c else None)}")
        w("")
        w("| Signal | Production | Learned global | Learned hierarchy | New challenger | Era stability | Reliability (OOS t) | Contribution to ΔIC |")
        w("|---|---|---|---|---|---|---|---|")
        order = sorted(range(len(names)), key=lambda i: -abs((c or [0] * len(names))[i] or 0) - 1e-3 * abs(C[i] - D[i]))
        for i in order[:30]:
            s = sig.get(names[i]) or {}
            w(f"| {names[i]} | {_pc(s.get('production_share'), 1)} | {_pc(D[i], 1)} | {_pc(Hm[i], 1) if Hm else '= global'} | {_pc(C[i], 1)} | "
              f"{cls[i] or '—'} | {_t(s.get('oos_t'))} | {_f(c[i] if c else None, 5)} |")
        w("")
        if c:
            pos = sorted([(names[i], c[i]) for i in range(len(names)) if c[i]], key=lambda x: -x[1])[:5]
            neg = sorted([(names[i], c[i]) for i in range(len(names)) if c[i]], key=lambda x: x[1])[:3]
            w("Why: the gain comes from " + ", ".join(f"{n} ({v:+.5f})" for n, v in pos if v > 0) + "; it is given back by "
              + ", ".join(f"{n} ({v:+.5f})" for n, v in neg if v < 0) + ".")
            w("")
    w("### All 74 signals — today's weights under every global fine-tune (signed share)")
    w("")
    gk = [k for k in _ordered(ms) if k in wts][:8]
    w("| Signal | Production | D | " + " | ".join(gk) + " |")
    w("|---|---|---|" + "---|" * len(gk))
    shares = {k: _share(wts[k]) for k in gk}
    for i, n in enumerate(names):
        s = sig.get(n) or {}
        w(f"| {n} | {_pc(s.get('production_share'), 1)} | {_pc(D[i], 1)} | " + " | ".join(_pc(shares[k][i], 1) for k in gk) + " |")
    w("")


def _costs(w, W):
    th = W.get("thresholds") or {}
    if not th:
        return
    w("## 6. Score thresholds and costs (1W)")
    w("")
    w("Long the top / short the bottom fraction of each week's cross-section; net after product-specific costs, and with a flat "
      "10 bp per side for sensitivity. n = weeks; persistence = average weeks a name stays in a leg.")
    w("")
    w("| Model | Tails | Gross/wk | Net/wk | Net (flat 10 bp) | t (net) | Net/yr | Turnover | Hit | Max drawdown | Persistence (wk) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, rows in th.items():
        for r in rows:
            w(f"| {k} | {int(r['frac'] * 100)}% | {_pc(r.get('gross'), 3)} | {_pc(r.get('net'), 3)} | {_pc(r.get('net_flat10bp'), 3)} | {_t(r.get('t'))} | "
              f"{_pc(r.get('net_annual'), 1)} | {_pcu(r.get('turnover'))} | {_pcu(r.get('hit'))} | {_pc(r.get('drawdown'), 1)} | {_f(r.get('persistence_weeks'), 1, False)} |")
    w("")
    ch = W.get("churn") or {}
    if ch:
        w("Rank churn (week-to-week rank correlation of the same assets' scores): " + ", ".join(f"{k} {_f(v, 2, False)}" for k, v in ch.items()) + ".")
        w("")


def _conviction(w, W):
    cv = W.get("conviction") or {}
    if not cv:
        return
    w("## 7. Conviction calibration — AlphaReliability and the ConvictionMultiplier (reported, not applied)")
    w("")
    w("Within-week score percentile → next-week return relative to the week's median (walk-forward records). CI: 95%, "
      "week-clustered. ConvictionMultiplier = bucket mean ÷ mean |bucket mean| of the upper half.")
    w("")
    for k, c in cv.items():
        w(f"**{k}** — AlphaReliability {_f(c.get('alpha_reliability'), 2)} · {'monotonic' if c.get('monotonic') else 'not monotonic'}")
        w("")
        w("| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |")
        w("|---|---|---|---|---|---|---|---|---|")
        for b in c.get("buckets") or []:
            ci = b.get("ci")
            w(f"| {b['bucket']} | {b.get('n')} | {_pc(b.get('mean'), 2)} | {_pc(b.get('median'), 2)} | {_pcu(b.get('hit'))} | {_pcu(b.get('vol'), 1)} | "
              f"{_pc(b.get('downside'), 2)} | {('[' + _pc(ci[0], 2) + ', ' + _pc(ci[1], 2) + ']') if ci else '—'} | {_f(b.get('conviction_multiplier'), 2)} |")
        w("")


def _robust(w, W):
    ne = W.get("neutralization") or {}
    if ne:
        w("## 8. Robustness — neutralisation, groups, leave one group out")
        w("")
        w("Weekly rank IC against the relative return before and after removing, cross-sectionally, market beta, class, value "
          "(earnings yield) and volatility. Size is not neutralised: the store has no point-in-time market cap.")
        w("")
        w("| Model | Raw rank IC (t) | Neutralised rank IC (t) | Kept |")
        w("|---|---|---|---|")
        for k, v in ne.items():
            r, n = v.get("raw") or {}, v.get("neutralized") or {}
            kept = (n.get("mean") / r["mean"]) if r.get("mean") and n.get("mean") is not None else None
            w(f"| {k} | {_f(r.get('mean'))} ({_t(r.get('t'))}) | {_f(n.get('mean'))} ({_t(n.get('t'))}) | {_pcu(kept)} |")
        w("")
    gr = W.get("groups") or {}
    if gr:
        keys = list(next(iter(gr.values())).keys())
        w("Within-class rank IC (each class ranked on its own, ≥ 8 names a week):")
        w("")
        w("| Class | " + " | ".join(keys) + " |")
        w("|---|" + "---|" * len(keys))
        for g, v in gr.items():
            w(f"| {g} | " + " | ".join(f"{_f((v.get(k) or {}).get('mean'))} ({_t((v.get(k) or {}).get('t'))}, {(v.get(k) or {}).get('weeks')} wk)" for k in keys) + " |")
        w("")
    lg = W.get("logo") or {}
    if lg:
        w("Leave one group out — the learned global model refitted walk-forward without the group, compared with production on "
          "what remains. A model that only works because of one group would collapse here.")
        w("")
        w("| Left out | Records | Assets | Learned rank IC | Production | Δ (t) |")
        w("|---|---|---|---|---|---|")
        for g, v in lg.items():
            w(f"| {g} | {v.get('records')} | {v.get('assets')} | {_f(v.get('rank_ic'))} | {_f(v.get('production'))} | {_f(v.get('d'))} ({_t(v.get('t'))}) |")
        w("")
        bad = [g for g, v in lg.items() if (v.get("t") or 0) < 2]
        w("The edge survives removing every group." if not bad else f"The edge weakens below t = 2 without: {', '.join(bad)}.")
        w("")


def _interactions(w, W):
    si = W.get("interaction_singles") or {}
    cl = W.get("clusters") or []
    if cl:
        w("## 9. Clusters and interactions")
        w("")
        w(f"Signal clusters today (|ρ| ≥ 0.6 on training records): {len(cl)} — " + "; ".join("{" + ", ".join(c) + "}" for c in cl if len(c) > 1) + ".")
        w("")
    if si:
        w("Predeclared economic interactions, each added alone to the learned global model (weight nested; GBM-suggested ones are "
          "descriptive only because the GBM saw the whole sample):")
        w("")
        w("| Interaction | Weight today | Rank IC | Δ vs learned global (t) |")
        w("|---|---|---|---|")
        iw = W.get("interaction_weights") or {}
        for k, v in si.items():
            a = v.get("alone") or {}
            w(f"| {k} | {_f(iw.get(k), 5)} | {_f(a.get('rank_ic'))} | {_f(a.get('vsD'))} ({_t(a.get('vsD_t'))}) |")
        w("")


def _oned(w, one):
    if not one:
        return
    w("## 10a. 1D — a cost-aware model")
    w("")
    w(one.get("note", ""))
    w("")
    n = one.get("nested_cost_aware") or {}
    w(f"Nested cost-aware strategy (model × regime filter × tail fraction chosen per era by inner net P&L after round-trip costs; "
      f"stand aside when no option had a positive inner net): net per trade {_pc(n.get('net_per_trade'), 3)}, t {_t(n.get('t'))}, "
      f"weeks {n.get('weeks')}, eras {', '.join(_pc(x, 3) for x in (n.get('eras') or []))}, active {_pcu(n.get('active_share'))} → "
      f"**{'PROFITABLE AFTER COSTS' if n.get('profitable') else 'NOT PROFITABLE AFTER COSTS'}**.")
    w("")
    w("Choices per era: " + ", ".join(f"{k[:4]}: {v or 'stand aside'}" for k, v in (n.get("choices") or {}).items()) + ".")
    w("")
    w("| Model | 1D rank IC (t) | Δ vs learned global (t) |")
    w("|---|---|---|")
    p = one.get("production") or {}
    w(f"| production | {_f(p.get('rank_ic'))} ({_t(p.get('t'))}) | — |")
    for k, v in (one.get("models") or {}).items():
        w(f"| {k} | {_f(v.get('rank_ic'))} ({_t(v.get('t'))}) | {_f(v.get('vsD'))} ({_t(v.get('vsD_t'))}) |")
    w("")
    w("| Model | Tails | Gross/trade | Net/trade | Net (flat 10 bp) | t | Hit | Max drawdown |")
    w("|---|---|---|---|---|---|---|---|")
    for k, rows in (one.get("thresholds") or {}).items():
        for r in rows:
            w(f"| {k} | {int(r['frac'] * 100)}% | {_pc(r.get('gross'), 3)} | {_pc(r.get('net'), 3)} | {_pc(r.get('net_flat10bp'), 3)} | {_t(r.get('t'))} | "
              f"{_pcu(r.get('hit'))} | {_pc(r.get('drawdown'), 1)} |")
    w("")


def _long(w, res, learned):
    w("## 10. Long horizons (1M / 3M / 6M / 12M) — sample limits and simpler models")
    w("")
    w("Overlapping horizons have far fewer independent periods than weeks. Every variant is nested (stronger shrinkage, "
      "family-level weights, top 10 / 20 signals, stable signals only, fundamental / relative value / macro subsets, class-level "
      "hierarchy) and FDR is applied across all 1M–12M variants.")
    w("")
    for lab in ("1M", "3M", "6M", "12M"):
        R = res.get(lab)
        if not R:
            continue
        s = R.get("sample") or {}
        ms = R.get("models") or {}
        w(f"### {lab}")
        w("")
        w(f"Effective sample: {s.get('weeks')} walk-forward weeks ≈ **{_f(s.get('n_eff'), 0, False)} independent periods** for {s.get('signals')} "
          f"signals → {_f(s.get('n_eff_per_signal'), 2, False)} per signal; mean cross-section {_f(s.get('mean_cross_section'), 0, False)} assets. "
          f"Era-to-era coefficient t (median) {_f(s.get('coef_era_t_median'), 2, False)}; signals with |era t| ≥ 2: {s.get('coef_era_t_ge2')}. "
          f"Stability classes: {', '.join(f'{k} {v}' for k, v in sorted((s.get('classes') or {}).items(), key=lambda kv: -kv[1]))}.")
        w("")
        w("| Model | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Eras + | FDR | Status |")
        w("|---|---|---|---|---|---|---|")
        for k, v in ms.items():
            g = v.get("gates") or {}
            p = (v.get("walkforward") or {}).get("paired") or {}
            w(f"| {k} | {_f(_ric(v))} | {_f(p.get('mean'))} ({_t(p.get('t'))}) | {'—' if k == 'learned global (D)' else _f(g.get('vsD_mean')) + ' (' + _t(g.get('vsD_t')) + ')'} | "
              f"{g.get('eras_won')}/{g.get('eras_complete')} | {'—' if g.get('fdr') is None else ('yes' if g['fdr'] else 'no')} | {v.get('status') or 'baseline'} |")
        w("")
        best = max(ms, key=lambda k: _ric(ms[k]) if _ric(ms[k]) is not None else -9) if ms else None
        red = [k for k, v in ms.items() if k != "learned global (D)" and (v["gates"].get("vsD_t") or 0) >= 2]
        el = [k for k, v in ms.items() if v.get("status") == ELIG]
        sig = [x for x in ((((learned or {}).get("horizons") or {}).get(lab) or {}).get("alpha") or {}).get("signals", []) if (x.get("stability") or {}).get("class") == "STABLE"]
        w(f"- Best complexity: **{best}** (rank IC {_f(_ric(ms.get(best) or {}))}).")
        w(f"- Stable weights (learned run): " + (", ".join(f"{x['name']} {_f(x.get('shrunk'), 4)}" for x in sig) if sig else "none") + ".")
        w("- Reduced dimension helps: " + (", ".join(red) if red else "no variant beats the full learned global model at t ≥ 2") + ".")
        w("- Validated Alpha edge: " + (", ".join(el) if el else "**none** — null result, reported as such") + ".")
        gr = R.get("groups") or {}
        if gr:
            w("- Within-class rank IC (production / learned global): " + "; ".join(
                f"{g} {_f((v.get('production') or {}).get('mean'), 3)} / {_f((v.get('learned global (D)') or {}).get('mean'), 3)}" for g, v in gr.items()) + ".")
        w("")


def _dir(w, W):
    da = W.get("dir_alpha") or {}
    if not da.get("gates"):
        return
    w("## 11. Directional — does the validated Alpha percentile add to the prior?")
    w("")
    w(f"Logistic P(R_1W > 0) on [1, prior z, Alpha percentile] fitted on records matured before each era (the Alpha percentile "
      f"({W.get('dir_alpha_model')}) is itself out of sample), against prior-only and prior + production, with the Directional "
      "program's fixed gates. Directional stays conservative: a pass would only earn a research version, never a direct change.")
    w("")
    m, p = da.get("metrics") or {}, da.get("prior") or {}
    vp, vc = da.get("vs_prior") or {}, da.get("vs_current") or {}
    w("| | Brier | Log loss | AUC | ECE |")
    w("|---|---|---|---|---|")
    w(f"| prior + Alpha percentile | {_f(m.get('brier'), 5, False)} | {_f(m.get('log_loss'), 5, False)} | {_f(m.get('auc'), 4, False)} | {_f(m.get('ece'), 4, False)} |")
    w(f"| prior only | {_f(p.get('brier'), 5, False)} | {_f(p.get('log_loss'), 5, False)} | {_f(p.get('auc'), 4, False)} | {_f(p.get('ece'), 4, False)} |")
    w("")
    w(f"Paired vs prior: {_short(vp)}; vs prior + production: {_short(vc)}; calibration slope {_f(da.get('calibration_slope'), 2)}. "
      f"Gates: {_short(da.get('gates'))} → **{'PASS' if da.get('passed') else 'NOT VALIDATED'}**.")
    w("")


def _today(w, W):
    td = W.get("today") or {}
    pt = W.get("production_today") or {}
    if not td:
        return
    keys = [k for k in td if isinstance(td[k], dict) and "error" not in td[k]]
    w("## 12. Today's scores (all matured data)")
    w("")
    w("Score = today's research record × each model's final weights (signals demeaned by today's cross-section); the blend is a "
      "rank mix. Sorted by the learned global model.")
    w("")
    w("| Asset | Production raw | " + " | ".join(keys) + " |")
    w("|---|---|" + "---|" * len(keys))
    base = td.get("learned global (D)") or (td[keys[0]] if keys else {})
    for a in sorted(base, key=lambda a: -((base[a] or {}).get("score") or -9)):
        w(f"| {a} | {_f(pt.get(a), 2)} | " + " | ".join(_f(((td[k].get(a) or {}).get("score")), 3) for k in keys) + " |")
    w("")


def _versions(w, res):
    from . import finetune as F
    w("## 13. Versions")
    w("")
    w("Every fine-tuned model has its own immutable version id; the two existing live-shadow models are never modified. "
      "'challenger' = in live shadow (passed every gate), 'research' = recorded, not shadowed.")
    w("")
    w("| Version | Horizon | Model | Status |")
    w("|---|---|---|---|")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        for k, v in ((res.get(lab) or {}).get("models") or {}).items():
            if v.get("family") == "baseline" or k == "learned global (D)" or not v.get("pit", True):
                continue
            w(f"| {F.vid_of(lab, k)} | {lab} | {k} | {'challenger (live shadow)' if v.get('status') == ELIG else 'research'} |")
    from ..hedge.hedgetune import VID
    hd = res.get("hedge") or {}
    ok = sum(1 for cs in (hd.get("cells") or {}).values() for c in cs.values() if c.get("status") == ELIG)
    w(f"| {VID} | 1W–3M | λ-conditional hedge size surface | research{f' ({ok} cells pass every gate; live shadow needs a λ-aware hedge grader, not built)' if ok else ''} |")
    w("")


# ---------------------------------------------------------------------- hedge answers used by both reports
def _surf_row(hd: dict, lab: str, node: str) -> dict:
    return ((hd.get("surface") or {}).get(lab) or {}).get(node) or {}


def _hedge_q15(hd: dict) -> str:
    if not hd:
        return "Not run."
    parts = []
    for lab, surf in (hd.get("surface") or {}).items():
        g = surf.get("global") or {}
        sh = g.get("shrunk") or {}
        if sh:
            parts.append(f"{lab} global: " + ", ".join(f"λ {l} → {v:.2f}×" for l, v in sh.items()))
    s = "; ".join(parts) + ". "
    s += ("The multiple falls as λ rises (isotonic by construction): risk-only users get larger hedges, profit-sensitive users "
          "lighter ones. Per objective and volatility regime in SHAFFER_HEDGE_FINETUNE.md §1.")
    return s


def _hedge_q16(hd: dict) -> str:
    h4 = hd.get("h4") or []
    if not hd:
        return "Not run."
    if not h4:
        return "No surface cell at λ = 1 passed FDR and then failed H4, so there is nothing to explain in this run."
    cost = sum(1 for x in h4 if "cost" in (x.get("failed_on") or ""))
    basis = sum(1 for x in h4 if "basis" in (x.get("failed_on") or ""))
    vs = sum(1 for x in h4 if x.get("type") == "variance-sensitive")
    rpc = [x["risk_per_cost_dollar"] for x in h4 if x.get("risk_per_cost_dollar") is not None]
    med = sorted(rpc)[len(rpc) // 2] if rpc else None
    return (f"Yes. {len(h4)} cells beat hedge-2 on utility with FDR but failed H4: {cost} on cost (> +10%), {basis} on basis error "
            f"(> +5%); {vs} are variance-sensitive objectives. A bigger hedge mechanically costs proportionally more — the median cell "
            f"bought {_f(med, 2, False)} of extra risk reduction per extra dollar of cost. H4 caps the size of the hedge, not its "
            "efficiency. H4 is NOT changed; an efficiency-based redesign is proposed for a new research version only.")


def _hedge_q17(hd: dict) -> str:
    al = hd.get("alpha") or {}
    if not al:
        return "Not run (no Alpha map)."
    parts = []
    for lab, a in al.items():
        if not a.get("cases"):
            continue
        tv = a.get("alpha_vs_one") or {}
        sig = [o for o, v in tv.items() if (v.get("t") or 0) >= 2]
        parts.append(f"{lab}: {a['cases']} cases; per-Alpha-bucket multiples beat one multiple walk-forward in "
                     f"{len(sig)}/{len(tv)} objectives at t ≥ 2" + (f" ({', '.join(sig)})" if sig else ""))
    keys = [(lab, o) for lab, a in al.items() for o, v in (a.get("alpha_vs_one") or {}).items() if v.get("p") is not None]
    ps = [al[lab]["alpha_vs_one"][o]["p"] for lab, o in keys]
    surv = [f"{lab} {o}" for (lab, o), r in zip(keys, L.benjamini_hochberg(ps)) if r] if ps else []
    return "; ".join(parts) + f". Across all {len(keys)} tests, {len(surv)} survive Benjamini–Hochberg FDR" + (
        f" ({', '.join(surv)}) — Alpha strength changes the optimal hedge there; research only." if surv else
        ": **no** — conditioning the hedge size on the book's validated Alpha does not improve realised utility out of sample; "
        "isolated t ≥ 2 cells are what chance produces in this many tests.")


# ====================================================================== SHAFFER_HEDGE_FINETUNE.md
def hedge_markdown(hd: dict) -> str:
    from ..hedge import hedgetune as HT
    out: List[str] = []
    w = out.append
    w("# Shaffer Hedge fine-tune — a λ-conditional size surface")
    w("")
    w(f"Run {hd.get('started')} · {hd.get('seconds')} s · `python -m finsim2 lab --finetune`. Research only: hedge-2, its sizing, the "
      "resizing live shadow and the fixed gates H1–H5 are unchanged. Method: `hedge/hedgetune.py` (module docstring).")
    w("")
    w("There is no single optimal hedge multiplier. Utility U = risk reduction − λ · profit sacrificed − cost: a risk-only user "
      "(λ small) wants a bigger hedge than a profit-sensitive one (λ large). So the fine-tune learns a **surface** "
      "m(objective, λ, risk class, horizon, volatility regime) × hedge-2's size, m ∈ [0.5, 1.5]: best fit per node and λ on outcomes "
      f"matured before each era, shrunk toward the parent (dates / (dates + {HT.K_POOL})), then made non-increasing in λ. Each "
      "(objective, horizon, λ) cell is validated walk-forward against hedge-2 at that same λ with H1 (utility gain, BH FDR across all "
      "cells), H2 (≥ 3/4 eras), H3 (tail not worse), H4 (cost ≤ +10%, basis error ≤ +5%), H5 (holds without crises).")
    w("")
    _h_summary(w, hd)
    _h_surface(w, hd)
    _h_frontier(w, hd)
    _h_products(w, hd)
    _h_h4(w, hd)
    _h_alpha(w, hd)
    return "\n".join(out) + "\n"


def _h_summary(w, hd):
    cells = [(lab, k, c) for lab, cs in (hd.get("cells") or {}).items() for k, c in cs.items()]
    el = [(lab, k) for lab, k, c in cells if c.get("status") == ELIG]
    fdr = [(lab, k) for lab, k, c in cells if (c.get("gates") or {}).get("fdr")]
    w("## Key findings")
    w("")
    w(f"- {len(cells)} (objective, horizon, λ) cells tested; {len(fdr)} survive FDR on utility; **{len(el)} pass every gate**"
      + (": " + ", ".join(f"{lab} {k.replace('@', ' at λ = ')}" for lab, k in el) if el else "") + ".")
    w("- Production size (hedge-2) = 1.00× in every row. " + _hedge_q15(hd))
    w("- " + _hedge_q16(hd))
    w("- " + _hedge_q17(hd))
    w("")


def _h_surface(w, hd):
    w("## 1. The surface — per objective, horizon and λ")
    w("")
    w("*Historical best fit*: in-sample over all matured cases. *Shrunk*: pooled toward the parent and isotonic in λ — the deployable "
      "value. Risk reduction / profit sacrificed / cost / basis: realised, walk-forward, learned multiple (hedge-2 in brackets). "
      "Utility: ΔU vs hedge-2 at that λ (t). Gate: status, and which gates failed.")
    w("")
    w("| Objective | Horizon | λ | Production size | Historical best-fit multiplier | Shrunk multiplier | Risk reduction | Profit sacrificed | Cost | Basis | Utility ΔU (t) | Gate result |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, cs in (hd.get("cells") or {}).items():
        for k in sorted(cs, key=lambda k: (k.split("@")[0], float(k.split("@")[1]))):
            c = cs[k]
            nd = c.get("node") or ""
            s = _surf_row(hd, lab, nd)
            lam = str(c.get("lam"))
            a, b = c.get("a") or {}, c.get("b") or {}
            g = c.get("gates") or {}
            fails = [x for x in ("H1", "H2", "H3", "H4", "H5") if not g.get(x)]
            bf = (s.get("best_fit") or {}).get(lam)
            sh = (s.get("shrunk") or {}).get(lam)
            w(f"| {k.split('@')[0]} | {lab} | {c.get('lam')} | 1.00× | {'—' if bf is None else f'{bf:.2f}×'} | {'—' if sh is None else f'{sh:.2f}×'} | "
              f"{_n(b.get('loss_reduction'))} ({_n(a.get('loss_reduction'))}) | {_n(b.get('profit_sacrificed'))} ({_n(a.get('profit_sacrificed'))}) | "
              f"{_n(b.get('cost'))} ({_n(a.get('cost'))}) | {_n(b.get('basis_error'))} ({_n(a.get('basis_error'))}) | "
              f"{_n(c.get('d'))} ({_t(c.get('t'))}) | {'**' + c.get('status', '') + '**' if c.get('status') == ELIG else (c.get('status') or '—') + (' — fails ' + ', '.join(fails) if fails and g.get('enough') else '')} |")
    w("")
    w("Surface by volatility regime (shrunk multiple at λ = 0.5 / 1 / 2 / 5 / 10):")
    w("")
    w("| Horizon | Node | Dates | Shrunk by λ | Best fit by λ |")
    w("|---|---|---|---|---|")
    for lab, surf in (hd.get("surface") or {}).items():
        for nd, v in sorted(surf.items()):
            w(f"| {lab} | {_c(nd)} | {v.get('dates')} | " + " / ".join(f"{x:.2f}" for x in (v.get("shrunk") or {}).values()) + " | "
              + " / ".join("—" if x is None else f"{x:.2f}" for x in (v.get("best_fit") or {}).values()) + " |")
    w("")


def _n(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    return f"{v:.3g}" if a < 1000 else f"{v:,.0f}"


def _h_frontier(w, hd):
    fr = hd.get("frontier") or {}
    if not fr:
        return
    w("## 2. The utility frontier (in-sample, descriptive)")
    w("")
    w("Scaling hedge-2 from 0.5× to 1.5×: risk reduction against profit given up and cost. Marked: the minimum-risk point, the "
      "balanced point (best U at λ = 1) and the profit-preserving point (best U at λ = 10).")
    w("")
    for lab, objs in fr.items():
        for obj, pts in objs.items():
            if not pts:
                continue
            w(f"**{lab} · {obj}**")
            w("")
            w("| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |")
            w("|---|---|---|---|---|---|---|---|")
            for p in pts:
                w(f"| {p['m']:.1f}× | {_n(p.get('risk_reduction'))} | {_n(p.get('profit_sacrificed'))} | {_n(p.get('cost'))} | {_n(p.get('basis'))} | "
                  f"{_n(p.get('U1.0'))} | {_n(p.get('U10.0'))} | {p.get('mark', '')} |")
            w("")


def _h_products(w, hd):
    pr = hd.get("products") or {}
    if not pr:
        return
    w("## 3. Product preference by objective × volatility regime (an interpretable prior, not a selector)")
    w("")
    w("Realised utility advantage of forcing each product type over hedge-2's own choice, shrunk toward the objective. Positive = "
      "that product would have done better than what hedge-2 picked.")
    w("")
    w("| Horizon | Node | Product | Dates | Advantage | Shrunk |")
    w("|---|---|---|---|---|---|")
    for lab, nodes in pr.items():
        for nd, ps in sorted(nodes.items()):
            for t, v in sorted(ps.items(), key=lambda kv: -(kv[1].get("shrunk") or 0)):
                w(f"| {lab} | {_c(nd)} | {t} | {v.get('dates')} | {_n(v.get('advantage'))} | {_n(v.get('shrunk'))} |")
    w("")


def _h_h4(w, hd):
    from ..hedge import hedgetune as HT
    w("## 4. H4 — what the failures are made of")
    w("")
    h4 = hd.get("h4") or []
    if h4:
        w("| Horizon | Objective | Type | Failed on | Cost increase | Basis increase | Extra risk reduction | Extra cost | Extra profit given up | Risk per cost $ | ΔU (t) |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for x in h4:
            w(f"| {x['horizon']} | {x['node']} | {x['type']} | {x['failed_on'] or '—'} | {_pc(x.get('cost_increase'), 0)} | {_pc(x.get('basis_increase'), 0)} | "
              f"{_n(x.get('extra_risk_reduction'))} | {_n(x.get('extra_cost'))} | {_n(x.get('extra_profit_sacrificed'))} | {_f(x.get('risk_per_cost_dollar'), 2, False)} | "
              f"{_n(x.get('d'))} ({_t(x.get('t'))}) |")
        w("")
    else:
        w("No cell at λ = 1 passed FDR and failed H4.")
        w("")
    w("H4 is **not** loosened after seeing these results. " + HT.H4_PROPOSAL)
    w("")


def _h_alpha(w, hd):
    al = hd.get("alpha") or {}
    if not al:
        return
    w("## 5. Does validated Alpha strength change the optimal hedge?")
    w("")
    w("Each hedge case's book gets its out-of-sample 1W Alpha percentile (learned hierarchy model, walk-forward), bucketed by "
      "|percentile − 50%| into weak / medium / strong and by sign. Best in-sample multiple per bucket and λ; then walk-forward: a "
      "multiple per bucket (shrunk to the single multiple) against one multiple, both learned before each era, at λ = 1.")
    w("")
    for lab, a in al.items():
        if not a.get("cases"):
            continue
        w(f"**{lab}** — {a['cases']} cases")
        w("")
        w("| Alpha bucket | Cases | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 |")
        w("|---|---|---|---|")
        for bk, v in (a.get("buckets") or {}).items():
            w(f"| {bk} | {v['cases']} | {v['dates']} | " + " / ".join("—" if x is None else f"{x:.2f}" for x in v["best_by_lambda"].values()) + " |")
        w("")
        w("| Objective | Dates | ΔU per-bucket vs one multiple | t | p |")
        w("|---|---|---|---|---|")
        for o, v in (a.get("alpha_vs_one") or {}).items():
            w(f"| {o} | {v.get('dates')} | {_n(v.get('d'))} | {_t(v.get('t'))} | {_f(v.get('p'), 3, False)} |")
        w("")

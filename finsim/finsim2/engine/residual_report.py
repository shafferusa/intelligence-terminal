"""The two residual / meta-learning reports, generated from the result of engine/residual.py (+ hedge/hedgepolicy.py):

SHAFFER_RESIDUAL_ML.md    — the 15 sections of the program (capability, Alpha residual, reliability, D vs E, dynamic
                             hierarchy, tails, pairwise, multi-task, multi-horizon, Directional residual, hedge policy,
                             current predictions, passed, failed, eligible for a live-shadow proposal).
SHAFFER_META_CURRENT.md   — today's research-only view of every asset, with a short explanation per asset.

Every sentence that states a result is computed from the result dict.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import residual as RS

ALPHA_ORDER = ["R1", "R1s", "R2", "R3", "R4", "R5", "MT", "DH", "PW"]
ALPHA_LABEL = {"R1": "R1 ridge residual", "R1s": "R1s uncertainty-shrunk ridge", "R2": "R2 elastic net", "R3": "R3 hierarchical residual",
               "R4": "R4 boosted trees", "R5": "R5 ensemble", "MT": "MT multi-task (economic groups)", "DH": "DH dynamic hierarchy",
               "PW": "PW pairwise → per-asset score"}


def _f(v, d=4, sign=True) -> str:
    if v is None:
        return "—"
    try:
        return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _t(v) -> str:
    return "—" if v is None else f"{v:+.1f}"


def _pc(v, d=1) -> str:
    return "—" if v is None else f"{100 * v:.{d}f}%"


def _yn(b) -> str:
    return "✓" if b else "✗"


def _c(v) -> str:
    return str(v).replace("|", " · ")


def _alpha_items(W: dict) -> List[str]:
    return [k for k in ALPHA_ORDER if k in (W.get("alpha") or {})]


def verdict(res: dict) -> List[str]:
    """The headline answers, computed."""
    W = res.get("1W") or {}
    A = W.get("alpha") or {}
    out = []
    passed_a = [k for k in _alpha_items(W) if A[k].get("passed")]
    best = max(_alpha_items(W), key=lambda k: (A[k].get("delta") or {}).get("mean") or -9, default=None)
    bd = (A.get(best) or {}).get("delta") or {}
    if passed_a:
        out.append(f"**A residual model improves E out of sample and passed every gate (A1–A7 + FDR): {', '.join(ALPHA_LABEL[k] for k in passed_a)}.**")
    else:
        sig = [k for k in _alpha_items(W) if ((A[k].get("delta") or {}).get("t") or 0) >= 2]
        if sig:
            out.append(f"**No Alpha residual model passed all gates.** {', '.join(ALPHA_LABEL[k] for k in sig)} "
                       f"{'has' if len(sig) == 1 else 'have'} a walk-forward Δ rank IC vs E with t ≥ 2, but failed "
                       f"{'; '.join(ALPHA_LABEL[k] + ': ' + ', '.join(x for x in ('A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7') if not A[k]['gates'].get(x)) for k in sig)}.")
        else:
            out.append(f"**E's residuals behave like noise to every pre-registered model.** The best Alpha residual ({ALPHA_LABEL.get(best, best)}) "
                       f"changes the weekly rank IC by {_f(bd.get('mean'))} (t {_t(bd.get('t'))}) on 2013–2024; none reaches t ≥ 2.")
    rel = W.get("rel") or {}
    bc = rel.get("beyond_conviction") or {}
    out.append(f"E's reliability is predictable (REL AUC − ½ = {_f((rel.get('auc_minus_half') or {}).get('mean'), 3)}, t {_t((rel.get('auc_minus_half') or {}).get('t'))}; "
               f"gates {'passed' if rel.get('passed') else 'not all passed'}), but beyond E's own conviction the gain is "
               f"{_f(bc.get('mean'), 4)} (t {_t(bc.get('t'))}) — "
               + ("context adds real information about when E is right." if (bc.get("t") or 0) >= 2 else
                  "reliability is almost entirely E's own conviction, which E already uses."))
    de = W.get("de") or {}
    dd = de.get("always_D_minus_E") or {}
    out.append(f"D vs E: always-D minus always-E = {_f(dd.get('mean'))} (t {_t(dd.get('t'))}); the selector DE-A {'passed' if (de.get('DE-A') or {}).get('passed') else 'did not pass'} "
               f"(Δ {_f(((de.get('DE-A') or {}).get('delta_vs_E') or {}).get('mean'))}), the blend DE-B {'passed' if (de.get('DE-B') or {}).get('passed') else 'did not pass'} "
               f"(Δ {_f(((de.get('DE-B') or {}).get('delta_vs_E') or {}).get('mean'))}).")
    T = W.get("tails") or {}
    tp = [f"{k} {side}" for side in ("top", "bottom") for k in ("T2", "T3", "T4") if ((T.get(side) or {}).get(k) or {}).get("passed")]
    if tp:
        out.append(f"Tail probabilities improve on E's percentile alone ({', '.join(tp)}): mostly by learning which assets move a lot "
                   "(asset class, signal disagreement) — magnitude, symmetric across both tails, not direction.")
    H = res.get("horizons") or {}
    other = [k for k in ("MH-1M", "MH-3M", "DR-1M") if (H.get(k) or {}).get("passed")] + (["DR-1W"] if (W.get("directional_1W") or {}).get("passed") else [])
    hp = (res.get("hedge") or {}).get("passed") or []
    out.append(f"Multi-horizon transfer: {'passed ' + ', '.join(k for k in other if k.startswith('MH')) if any(k.startswith('MH') for k in other) else 'no gain'}; "
               f"Directional residual: {'passed ' + ', '.join(k for k in other if k.startswith('DR')) if any(k.startswith('DR') for k in other) else 'no gain over prior-only'}; "
               f"hedge action policy: {len(hp)} of {len((res.get('hedge') or {}).get('cells') or {})} cells passed H1–H5.")
    if not [k for k in _alpha_items(W) if (A[k] or {}).get("passed")] and not any(((W.get("de") or {}).get(k) or {}).get("passed") for k in ("DE-A", "DE-B")):
        out.append("**Conclusion: with the information in these 74 signals and context features, E is close to the limit of what this "
                   "program's models can extract for 1W ranking.** What remains predictable is how big moves will be and how reliable E is "
                   "given its own conviction — not which way E is wrong.")
    el = res.get("eligible") or []
    out.append(f"Candidates eligible for a live-shadow proposal: {', '.join(e['candidate'] for e in el) if el else 'none'}. "
               "Nothing was put into live shadow; D and E, production, the Directional prior and hedge-2 are unchanged.")
    return out


def markdown(res: dict) -> str:
    lines: List[str] = []
    w = lines.append
    W = res.get("1W") or {}
    H = res.get("horizons") or {}
    w("# FinSim2 — Residual ML / Meta-Learning Program")
    w("")
    w(f"Run {res.get('started')} · protocol `SHAFFER_RESIDUAL_PROTOCOL.md` (fixed before any market result; amendments listed there were made "
      "after the synthetic capability suite and before market data) · research only.")
    w("")
    w("**Frozen and untouched:** production (shaffer-2.1, shaffer-alpha-2.1-production), Directional prior-only, hedge-2, "
      "`alpha-learned-1w-global-exp` (D) and `alpha-learned-1w-hierarchy-exp` (E) — formulas, backtests, live predictions, G3-XS and clocks — "
      "and the λ-hedge live shadow. Every candidate below has its own immutable `resid-…-exp` research version.")
    w("")
    rep = W.get("reproduction") or {}
    rs = W.get("residual") or {}
    w(f"Baselines reproduced: E weekly rank IC {_f(rep.get('E_rank_ic'))}, D {_f(rep.get('D_rank_ic'))} (walk-forward, all eras). "
      f"Residual table: {rs.get('records', '—')} records from {rs.get('from', '—')} to {rs.get('to', '—')} with an out-of-sample E; "
      f"β (slope of u on zE, 2009–2012) = {_f(rs.get('beta'))}. Evaluation 2013–2024 (eras 2013–16, 2017–20, 2021–24); 2025– untouched (A7).")
    w("")
    w("## Verdict")
    w("")
    for v in verdict(res):
        w(f"- {v}")
    w("")
    _capability(w, res.get("capability") or {})
    _alpha(w, W)
    _reliability(w, W)
    _dve(w, W)
    _dh(w, W)
    _tails(w, W)
    _pairwise(w, W)
    _mt(w, W)
    _horizons(w, H)
    _directional(w, W, H)
    _hedge(w, res.get("hedge") or {})
    _current(w, W)
    _passed_failed(w, res)
    return "\n".join(lines) + "\n"


def _capability(w, cap: dict):
    w("## 1. Synthetic capability tests")
    w("")
    w("Worlds with an intentionally incomplete baseline, run through exactly the same code as the market data, before it.")
    w("")
    w("| World | Planted | Result | Checks |")
    w("|---|---|---|---|")
    for k, v in cap.items():
        checks = "; ".join(f"{_yn(ok)} {nm}" for nm, ok in (v.get("checks") or {}).items())
        w(f"| {k} | {_c(v.get('world'))} | {'PASS' if v.get('pass') else 'FAIL'} | {_c(checks)} |")
    w("")
    for k in ("A", "C", "D"):
        v = cap.get(k) or {}
        if v:
            best = max((v.get("alpha") or {}).items(), key=lambda kv: kv[1].get("delta") or -9, default=(None, {}))
            w(f"- World {k}: best residual {best[0]} Δ {_f(best[1].get('delta'))} (t {_t(best[1].get('t'))}); R1's largest missing weight "
              f"`{v.get('r1_top_signal')}`; R4's first splits {', '.join(f'`{s}`' for s in v.get('r4_top_splits') or [])}.")
    e = cap.get("E") or {}
    if e:
        w(f"- World E (optimal E): REL AUC − ½ t {_t((e.get('rel') or {}).get('t'))} — REL {'passes' if (e.get('rel') or {}).get('passed') else 'does not pass'} its gates "
          f"even here, because E's conviction predicts its hits; beyond conviction t {_t((e.get('rel') or {}).get('beyond_conviction_t'))}. "
          "This is why the reliability section reports the beyond-conviction test next to the gates.")
    w("")


def _alpha(w, W: dict):
    A = W.get("alpha") or {}
    w("## 2. Alpha residual (what E gets wrong)")
    w("")
    w("Score = β·zE + γ·r̂ (a correction of E, never a refit; MT and DH are refits and PW a pairwise score, all judged on "
      "the same records). Δ = weekly rank IC(challenger) − IC(E), 2013–2024, week-clustered t.")
    w("")
    w("| Model | Δ IC | t | 2013–16 | 2017–20 | 2021–24 | 2025– | LS10 net vs E | A1 | A2 | A3 | A4 | A5 | A6 | A7 | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in _alpha_items(W):
        v = A[k]
        g = v.get("gates") or {}
        er = v.get("eras") or {}
        ls, lse = v.get("ls10") or {}, v.get("ls10_E") or {}
        lsd = (ls.get("net") - lse.get("net")) if ls.get("net") is not None and lse.get("net") is not None else None
        w(f"| {ALPHA_LABEL[k]} | {_f((v.get('delta') or {}).get('mean'))} | {_t((v.get('delta') or {}).get('t'))} | {_f(er.get('2013–16'))} | "
          f"{_f(er.get('2017–20'))} | {_f(er.get('2021–24'))} | {_f(er.get('2025–'))} | {_f(lsd, 5)} | "
          + " | ".join(_yn(g.get(x)) for x in ("A1", "A2", "A3", "A4", "A5", "A6", "A7")) + f" | {v.get('status', '—')} |")
    w("")
    ch = W.get("alpha_models") or {}
    if ch:
        w("Nested choices (per era cut → option; the 2013 cut uses the pre-registered default):")
        w("")
        for k in ("R1", "R2", "R3", "R4", "R5"):
            c = (ch.get(k) or {}).get("choices") or {}
            w(f"- {ALPHA_LABEL[k]}: " + ", ".join(f"{cut[:4]}→`{opt}`" for cut, opt in c.items() if cut != "2009-01-01"))
        w("")
    wts = W.get("r1_final_weights") or {}
    if wts:
        top = sorted(wts.items(), key=lambda kv: -abs(kv[1]))[:10]
        w("What R1 would correct today (largest standardised residual weights, all matured data): "
          + ", ".join(f"`{k}` {_f(v, 4)}" for k, v in top) + ".")
        w("")
    sp = W.get("r4_splits") or {}
    for n, info in sp.items():
        w(f"R4 ({n} rounds) most-used splits: " + ", ".join(f"`{a}` ×{b}" for a, b in (info.get("top_splits") or [])[:8]) + ".")
    w("")
    lo = {k: (A[k].get("loco") or {}) for k in _alpha_items(W)}
    if lo:
        w("A5 detail — Δ with each asset class removed from the cross-section, and outside the crisis windows:")
        w("")
        classes = sorted({c for v in lo.values() for c in v})
        w("| Model | " + " | ".join(c.split(":")[-1] for c in classes) + " | no crises |")
        w("|---|" + "---|" * (len(classes) + 1))
        for k in _alpha_items(W):
            w(f"| {k} | " + " | ".join(_f(lo[k].get(c)) for c in classes) + f" | {_f(A[k].get('no_crisis'))} |")
        w("")


def _reliability(w, W: dict):
    r = W.get("rel") or {}
    w("## 3. E reliability (REL)")
    w("")
    w("Label: E and the realised return on the same side of the week's median. Model: ridge logistic on the context features, "
      "walk-forward. Research display only (0–1).")
    w("")
    a = r.get("auc_minus_half") or {}
    g = r.get("gates") or {}
    ic = r.get("ic_terciles") or [None, None, None]
    w(f"- AUC − ½: {_f(a.get('mean'), 4)} (t {_t(a.get('t'))}, {a.get('weeks', '—')} weeks); by era "
      + ", ".join(f"{k} {_f(v, 4)}" for k, v in (r.get("auc_eras") or {}).items()) + ".")
    w(f"- E's rank IC inside reliability terciles (low / mid / high): {_f(ic[0])} / {_f(ic[1])} / {_f(ic[2])} "
      f"({'monotone' if g.get('monotone') else 'not monotone'}); top − bottom by era "
      + ", ".join(f"{k} {_f(v)}" for k, v in (r.get("top_minus_bottom_eras") or {}).items()) + ".")
    bc = r.get("beyond_conviction") or {}
    w(f"- Beyond E's own conviction (REL AUC − conviction-only AUC, descriptive): {_f(bc.get('mean'), 4)} (t {_t(bc.get('t'))}).")
    w(f"- Mean reliability by class: " + ", ".join(f"{k.split(':')[-1]} {_f(v, 3, False)}" for k, v in (r.get("by_class") or {}).items()) + ".")
    w(f"- Largest coefficients (final fit, standardised): " + ", ".join(f"`{k}` {_f(v, 3)}" for k, v in (r.get("coefficients") or [])[:8]) + ".")
    w(f"- Gates: AUC > ½ {_yn(g.get('auc'))}, t ≥ 2 {_yn(g.get('t_ok'))}, monotone terciles {_yn(g.get('monotone'))}, ≥ 2 of 3 eras {_yn(g.get('eras'))}, "
      f"FDR {_yn(g.get('fdr'))} → **{r.get('status', '—')}**.")
    w("")


def _dve(w, W: dict):
    de = W.get("de") or {}
    w("## 4. D versus E")
    w("")
    a, d = de.get("always_E") or {}, de.get("always_D") or {}
    dd = de.get("always_D_minus_E") or {}
    w(f"Benchmarks on the residual-period records: always-E rank IC {_f(a.get('mean'))}, always-D {_f(d.get('mean'))}; D − E {_f(dd.get('mean'))} "
      f"(t {_t(dd.get('t'))}); by era " + ", ".join(f"{k} {_f(v)}" for k, v in (de.get("always_D_minus_E_eras") or {}).items()) + ".")
    w("")
    w("| Model | Δ vs always-E | t | eras (13–16 / 17–20 / 21–24 / 25–) | churn vs E | gates | Status |")
    w("|---|---|---|---|---|---|---|")
    for k in ("DE-A", "DE-B"):
        v = de.get(k) or {}
        g = v.get("gates") or {}
        er = v.get("eras") or {}
        w(f"| {k} {'(choose)' if k == 'DE-A' else '(blend)'} | {_f((v.get('delta_vs_E') or {}).get('mean'))} | {_t((v.get('delta_vs_E') or {}).get('t'))} | "
          f"{' / '.join(_f(er.get(x)) for x in ('2013–16', '2017–20', '2021–24', '2025–'))} | {_f(v.get('churn'), 3, False)} vs {_f(v.get('churn_E'), 3, False)} | "
          f"beats {_yn(g.get('beats_E'))} t {_yn(g.get('t_ok'))} eras {_yn(g.get('eras'))} churn {_yn(g.get('churn'))} FDR {_yn(g.get('fdr'))} | {v.get('status', '—')} |")
    w("")
    pb = de.get("P_by_state") or {}
    w("What the selector learned — mean P(E closer to the outcome than D), 2013–2024: " + ", ".join(f"{k} {_f(v, 3, False)}" for k, v in pb.items()) + ".")
    w("Largest coefficients: " + ", ".join(f"`{k}` {_f(v, 3)}" for k, v in (de.get("coefficients") or [])[:8]) + ".")
    w("")


def _dh(w, W: dict):
    v = (W.get("alpha") or {}).get("DH") or {}
    d = W.get("dh") or {}
    w("## 5. Dynamic hierarchy (DH)")
    w("")
    if not v:
        w("Not run.")
        w("")
        return
    tr = d.get("trust") or {}
    w(f"E's tree with each node's deviation scaled by the James–Stein factor of its own out-of-sample evidence. K choices: "
      + ", ".join(f"{c[:4]}→{o}" for c, o in (d.get("choices") or {}).items() if c != "2009-01-01") + f". Final K {d.get('final')}: "
      f"{tr.get('trusted', '—')} of {tr.get('nodes_x_signals', '—')} node × signal deviations keep any trust; mean trust by level "
      + ", ".join(f"{k} {_f(x, 3, False)}" for k, x in (tr.get("mean_trust_by_level") or {}).items()) + ".")
    w(f"Δ vs E {_f((v.get('delta') or {}).get('mean'))} (t {_t((v.get('delta') or {}).get('t'))}) → **{v.get('status', '—')}** (full gate row in §2).")
    w("")


def _tails(w, W: dict):
    T = W.get("tails") or {}
    w("## 6. Tail probabilities (top and bottom decile of the week)")
    w("")
    w("T1 (benchmark) = logistic on E's percentile (cubic). Gains are Brier(T1) − Brier(model) per record, weekly-clustered.")
    w("")
    w("| Side | Model | Brier gain | t | log loss (model / T1) | eras 13–16 / 17–20 / 21–24 / 25– | Status |")
    w("|---|---|---|---|---|---|---|")
    for side in ("top", "bottom"):
        for k in ("T2", "T3", "T4"):
            v = (T.get(side) or {}).get(k) or {}
            er = v.get("eras") or {}
            w(f"| {side} | {v.get('name', k)} | {_f((v.get('brier_gain') or {}).get('mean'), 6)} | {_t((v.get('brier_gain') or {}).get('t'))} | "
              f"{_f(v.get('logloss'), 4, False)} / {_f(v.get('logloss_T1'), 4, False)} | {' / '.join(_f(er.get(x), 6) for x in ('2013–16', '2017–20', '2021–24', '2025–'))} | {v.get('status', '—')} |")
    for side in ("top", "bottom"):
        sp = ((T.get(side) or {}).get("T4") or {}).get("top_splits") or []
        if sp:
            w("")
            w(f"T4 ({side}) most-used splits: " + ", ".join(f"`{a}` ×{b}" for a, b in sp) + ".")
    for side in ("top", "bottom"):
        t1 = (T.get(side) or {}).get("T1") or {}
        if t1.get("base_rate") is not None:
            w(f"")
            w(f"Base rate ({side} decile): {_pc(t1.get('base_rate'))}.")
    w("")


def _pairwise(w, W: dict):
    p = W.get("pairwise") or {}
    w("## 7. Pairwise (A beats B?)")
    w("")
    a, b = p.get("accuracy") or {}, p.get("accuracy_E") or {}
    d = p.get("delta") or {}
    g = p.get("gates") or {}
    w(f"400 random same-week pairs per week. Pair accuracy: PW-L {_pc(a.get('mean'), 2)} vs sign(E_A − E_B) {_pc(b.get('mean'), 2)}; "
      f"gain {_f(d.get('mean'), 4)} (t {_t(d.get('t'))}); by era " + ", ".join(f"{k} {_f(v, 4)}" for k, v in (p.get("eras") or {}).items()) + ".")
    w(f"Gates: gain > 0 {_yn(g.get('accuracy_gain'))}, t ≥ 2 {_yn(g.get('t_ok'))}, eras {_yn(g.get('eras'))}, FDR {_yn(g.get('fdr'))} → **{p.get('status', '—')}**. "
      f"Largest coefficients: " + ", ".join(f"`{k}` {_f(v, 3)}" for k, v in (p.get("coefficients") or [])[:6]) + ".")
    v = (W.get("alpha") or {}).get("PW") or {}
    w(f"Aggregated to a per-asset score (mean win probability vs 40 opponents) and judged as an Alpha model: Δ {_f((v.get('delta') or {}).get('mean'))} "
      f"(t {_t((v.get('delta') or {}).get('t'))}) → {v.get('status', '—')}.")
    w("")


def _mt(w, W: dict):
    v = (W.get("alpha") or {}).get("MT") or {}
    m = W.get("mt") or {}
    w("## 8. Multi-task learning across economic groups (MT)")
    w("")
    if not v:
        w("Not run.")
        w("")
        return
    gr = m.get("groups") or {}
    w(f"Tree global → economic group → asset ({len(gr)} groups): " + "; ".join(f"**{g}** ({len(a)})" for g, a in sorted(gr.items(), key=lambda kv: -len(kv[1]))[:14]) + ".")
    w(f"Choices: " + ", ".join(f"{c[:4]}→{o}" for c, o in (m.get("choices") or {}).items() if c != "2009-01-01") + f". Δ vs E {_f((v.get('delta') or {}).get('mean'))} "
      f"(t {_t((v.get('delta') or {}).get('t'))}) → **{v.get('status', '—')}**.")
    w("")


def _horizons(w, H: dict):
    w("## 9. Multi-horizon transfer (1W informs 1M / 3M)")
    w("")
    w("1M / 3M global ridge shrunk toward c × the 1W weights fitted before the same cut; judged only on its own horizon's outcomes.")
    w("")
    w("| Horizon | choice | G1 | G2 | G3 | G4 | vs learned global (Δ, t) | Status |")
    w("|---|---|---|---|---|---|---|---|")
    for k in ("MH-1M", "MH-3M"):
        v = H.get(k) or {}
        if not v:
            continue
        g = v.get("gates") or {}
        vd = v.get("vsD") or {}
        w(f"| {k[3:]} | {v.get('final')} | {_yn(g.get('G1'))} | {_yn(g.get('G2'))} | {_yn(g.get('G3'))} | {_yn(g.get('G4'))} | "
          f"{_f(vd.get('mean'))} (t {_t(vd.get('t'))}) | {v.get('status', '—')} |")
    w("")
    if not any((H.get(k) or {}).get("passed") for k in ("MH-1M", "MH-3M")) and H:
        w("Transfer did not pass; as pre-registered, nothing else is tried at the long horizons in this program.")
        w("")


def _directional(w, W: dict, H: dict):
    w("## 10. Directional residual (logit P = logit P_prior + capped adjustment)")
    w("")
    w("| Horizon | λ \\| cap | Brier gain vs prior (t) | log loss gain | balanced acc (model / prior) | ECE (model / prior) | slope | eras won | mean \\|adj\\| | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for lab, v in (("1W", W.get("directional_1W") or {}), ("1M", H.get("DR-1M") or {})):
        if not v:
            continue
        vp = v.get("vs_prior") or {}
        m, mp = v.get("metrics") or {}, v.get("metrics_prior") or {}
        g = v.get("gates") or {}
        w(f"| {lab} | {v.get('final')} | {_f(vp.get('brier_gain'), 6)} ({_t(vp.get('brier_t'))}) | {_f(vp.get('logloss_gain'), 6)} | "
          f"{_pc(m.get('balanced_accuracy'), 2)} / {_pc(mp.get('balanced_accuracy'), 2)} | {_f(m.get('ece'), 4, False)} / {_f(mp.get('ece'), 4, False)} | "
          f"{_f(v.get('calibration_slope'), 2, False)} | {g.get('eras_won', '—')}/3 | {_pc(v.get('mean_abs_adjustment'), 2)} | {v.get('status', '—')} |")
    w("")
    v = W.get("directional_1W") or {}
    if v.get("coefficients"):
        w("1W final adjustment coefficients: " + ", ".join(f"`{k}` {_f(x, 3)}" for k, x in v["coefficients"][:8]) + ".")
        w("")


def _hedge(w, hd: dict):
    w("## 11. Hedge residual / action policy")
    w("")
    if not hd:
        w("Not run.")
        w("")
        return
    cells = hd.get("cells") or {}
    w(f"{len(cells)} objective cells judged (sizing and product policies × 1W / 1M × λ ∈ {{1, 5}}); passed H1–H5 with FDR: "
      f"**{', '.join(hd.get('passed') or []) or 'none'}**.")
    w("")
    w("| Cell | cases (dates) | departures | ΔU vs hedge-2 | t | H1 | H2 (eras) | H3 | H4 | H5 | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    order = sorted(cells, key=lambda k: -((cells[k].get("t") or -99)))
    for k in order[:40]:
        c = cells[k]
        g = c.get("gates") or {}
        w(f"| {_c(k)} | {c.get('dates')} | {_pc(c.get('departure_share'), 0)} | {_f(c.get('d'), 0)} | {_t(c.get('t'))} | {_yn(g.get('H1'))} | "
          f"{_yn(g.get('H2'))} ({g.get('eras_won', '—')}/{g.get('eras_complete', '—')}) | {_yn(g.get('H3'))} | {_yn(g.get('H4'))} | {_yn(g.get('H5'))} | {c.get('status')} |")
    if len(order) > 40:
        w(f"| … {len(order) - 40} more cells (all in the result) | | | | | | | | | | |")
    w("")
    ch = hd.get("choices") or {}
    if ch:
        w("How often each action was chosen out of sample (A = hedge-2): " + "; ".join(f"{k}: " + ", ".join(f"{a} {n}" for a, n in v.items()) for k, v in ch.items()) + ".")
        w("")
    cf = (hd.get("counterfactual") or {}).get("1W") or []
    if cf:
        w("Counterfactual table (1W, descriptive, all eras; $ per $1M book over the week):")
        w("")
        w("| Objective | Volatility | Action | Risk reduction | Profit given up | Cost | Basis error | U(λ=1) | U(λ=5) |")
        w("|---|---|---|---|---|---|---|---|---|")
        for r in cf[:60]:
            w(f"| {r['objective']} | {r['volatility']} | {r['action']} | {_f(r.get('risk_reduction'), 0)} | {_f(r.get('profit_given_up'), 0)} | "
              f"{_f(r.get('cost'), 0, False)} | {_f(r.get('basis_error'), 0, False)} | {_f(r.get('U@1'), 0)} | {_f(r.get('U@5'), 0)} |")
        w("")


def _current(w, W: dict):
    w("## 12. Current predictions and uncertainty")
    w("")
    bk = (W.get("buckets") or {})
    for period in ("2013–2024", "2025–"):
        b = bk.get(period) or {}
        if not b:
            continue
        w(f"E percentile buckets → the asset's 1W log return minus the week's mean ({period}; monotonicity ρ {_f(b.get('monotonicity_rho'), 2, False)}, "
          f"{b.get('violations', '—')} adjacent violations):")
        w("")
        w("| Bucket | n | mean | median | hit | vol | downside | net long | net short | 95% CI of mean | 95% of single outcomes |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in b.get("rows") or []:
            if r.get("mean") is None:
                w(f"| {r['bucket']} | {r.get('n')} | | | | | | | | | |")
                continue
            ci, rg = r.get("ci95") or (None, None), r.get("range95") or (None, None)
            w(f"| {r['bucket']} | {r['n']} | {_pc(r['mean'], 2)} | {_pc(r['median'], 2)} | {_pc(r['hit'])} | {_pc(r['vol'], 2)} | {_pc(r['downside'], 2)} | "
              f"{_pc(r['net_long'], 2)} | {_pc(r['net_short'], 2)} | [{_pc(ci[0], 2)}, {_pc(ci[1], 2)}] | [{_pc(rg[0], 1)}, {_pc(rg[1], 1)}] |")
        w("")
    today = W.get("today") or []
    w(f"Today's research-only view of {len(today)} assets is in `SHAFFER_META_CURRENT.md` and in the ML Lab (Residual / Meta ML → "
      "Current predictions). The interval shown for one asset is the bucket's 95% range of single weekly outcomes, not the narrow CI of the mean. "
      "No sizing is derived from any of it.")
    w("")


def _all_candidates(res: dict) -> List[tuple]:
    W = res.get("1W") or {}
    H = res.get("horizons") or {}
    out = []
    for k in _alpha_items(W):
        v = W["alpha"][k]
        out.append(("alpha", ALPHA_LABEL[k], v.get("passed"), [x for x in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "fdr") if not (v.get("gates") or {}).get(x)]))
    r = W.get("rel") or {}
    out.append(("meta", "REL E reliability", r.get("passed"), [x for x in ("auc", "t_ok", "monotone", "eras", "fdr") if not (r.get("gates") or {}).get(x)]))
    for k in ("DE-A", "DE-B"):
        v = (W.get("de") or {}).get(k) or {}
        out.append(("meta", k, v.get("passed"), [x for x in ("beats_E", "t_ok", "eras", "churn", "fdr") if not (v.get("gates") or {}).get(x)]))
    for side in ("top", "bottom"):
        for k in ("T2", "T3", "T4"):
            v = ((W.get("tails") or {}).get(side) or {}).get(k) or {}
            out.append(("tail", f"{k} {side}", v.get("passed"), [x for x in ("brier_gain", "t_ok", "eras", "logloss", "fdr") if not (v.get("gates") or {}).get(x)]))
    p = W.get("pairwise") or {}
    out.append(("pair", "PW-L", p.get("passed"), [x for x in ("accuracy_gain", "t_ok", "eras", "fdr") if not (p.get("gates") or {}).get(x)]))
    for k in ("MH-1M", "MH-3M"):
        v = H.get(k) or {}
        if v:
            out.append(("horizon", k, v.get("passed"), [x for x in ("G1", "G2", "G3", "G4", "vs_global", "fdr") if not (v.get("gates") or {}).get(x)]))
    for k, v in (("DR-1W", W.get("directional_1W") or {}), ("DR-1M", H.get("DR-1M") or {})):
        if v:
            out.append(("directional", k, v.get("passed"), [x for x in ("brier", "logloss", "balanced", "slope", "ece", "eras", "fdr") if not (v.get("gates") or {}).get(x)]))
    hd = res.get("hedge") or {}
    ncell = len(hd.get("cells") or {})
    if ncell:
        out.append(("hedge", f"{ncell} policy cells", bool(hd.get("passed")), [] if hd.get("passed") else ["no cell passed H1–H5 + FDR"]))
    return out


def _passed_failed(w, res: dict):
    cands = _all_candidates(res)
    w("## 13. What passed")
    w("")
    ok = [c for c in cands if c[2]]
    if ok:
        for fam, nm, _, _ in ok:
            w(f"- [{fam}] {nm}")
    else:
        w("Nothing passed its pre-registered gates.")
    hd = res.get("hedge") or {}
    for k in hd.get("passed") or []:
        w(f"- [hedge] {k}")
    w("")
    w("## 14. What failed")
    w("")
    w("| Family | Candidate | Failed gates |")
    w("|---|---|---|")
    for fam, nm, p, fails in cands:
        if not p:
            w(f"| {fam} | {nm} | {', '.join(fails) or '—'} |")
    w("")
    w("## 15. Eligible for a live-shadow proposal")
    w("")
    el = res.get("eligible") or []
    if el:
        w("Passed every pre-registered gate — eligible to be PROPOSED for live shadow (not activated; a proposal needs a decision, "
          "its own frozen version and a live expectation):")
        w("")
        W = res.get("1W") or {}
        for e in el:
            w(f"- [{e['family']}] {e['candidate']} — `{RS.vid_of(e['candidate'])}`. {_assess(e, W)}")
    else:
        w("None. Every candidate stays a research version; the live-shadow set (D, E, the λ-hedge shadow) is unchanged.")
    w("")


def _corr(a: List[float], b: List[float]) -> Optional[float]:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a) ** 0.5
    sb = sum((y - mb) ** 2 for y in b) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else None


def _assess(e: dict, W: dict) -> str:
    """What passing actually means for each eligible candidate (computed)."""
    c = e["candidate"]
    if c == "REL":
        bc = (W.get("rel") or {}).get("beyond_conviction") or {}
        if (bc.get("t") or 0) < 2:
            return (f"It passed, but its AUC beyond a conviction-only model is {_f(bc.get('mean'), 4)} (t {_t(bc.get('t'))}): it re-expresses E's own "
                    "|score| — the capability world with an optimal E passes the same gates. Useful as a display of how confident E is; not a "
                    "reason to change anything. A live-shadow proposal is not recommended.")
        return "Its AUC beyond E's own conviction is significant: context adds information about when E is right."
    if c.startswith("T"):
        side = c.split()[-1]
        k = c.split()[0]
        v = ((W.get("tails") or {}).get(side) or {}).get(k) or {}
        sp = ", ".join(f"`{a}`" for a, _ in (v.get("top_splits") or [])[:4])
        if k == "T4":
            td = [r for r in (W.get("today") or []) if r.get("p_top_decile") is not None and r.get("p_bottom_decile") is not None]
            rho = _corr([r["p_top_decile"] for r in td], [r["p_bottom_decile"] for r in td]) if len(td) > 3 else None
            return (f"Today P(top decile) and P(bottom decile) correlate {_f(rho, 2)} across {len(td)} assets. Brier gain {_f((v.get('brier_gain') or {}).get('mean'), 5)} (t {_t((v.get('brier_gain') or {}).get('t'))}) in every era; it splits "
                    f"mostly on {sp} — which assets make large moves, a magnitude effect present in both tails, not a view on direction. "
                    "It improves tail probabilities for risk display; it is not an Alpha improvement.")
        return (f"Brier gain {_f((v.get('brier_gain') or {}).get('mean'), 5)} (t {_t((v.get('brier_gain') or {}).get('t'))}), era 2013–16 "
                f"{_f((v.get('eras') or {}).get('2013–16'), 5)}: a borderline pass, dominated by T4 on the same target.")
    return ""


# ------------------------------------------------------------------ SHAFFER_META_CURRENT.md
def _num(v, fmt: str) -> str:
    return "—" if v is None else fmt.format(v)


def _pp(v) -> str:
    return "—" if v is None else f"{100 * v:.0f}"


def explain(r: dict) -> str:
    parts = [f"E ranks {r['asset']} at the {_pp(r.get('E_pct') / 100 if r.get('E_pct') is not None else None)}th percentile"
             f" (D: {_pp(r.get('D_pct') / 100 if r.get('D_pct') is not None else None)}th)."]
    if r.get("reliability") is not None:
        dr = ", ".join(f"{k.replace('ctx:', '')} {v:+.2f}" for k, v in (r.get("reliability_drivers") or []))
        parts.append(f"Reliability {r['reliability']:.2f}" + (f" (largest pushes: {dr})" if dr else "") + ".")
    if r.get("p_E_better") is not None:
        parts.append(f"P(E closer than D) {r['p_E_better']:.2f} → {r.get('preferred')}.")
    if r.get("residual") is not None:
        mat = "material" if r.get("residual_material") else "not distinguishable from zero"
        drv = ", ".join(f"{k.replace('ctx:', '')} {v:+.3f}" for k, v in (r.get("residual_drivers") or [])[:2])
        parts.append(f"Residual correction {r['residual']:+.3f} (se {r['residual_se']:.3f}, {mat}; drivers {drv})." if r.get("residual_se") is not None
                     else f"Residual correction {r['residual']:+.3f} (no se).")
    if r.get("exp_rel_return") is not None:
        rg = r.get("range95") or (None, None)
        parts.append(f"Bucket {r.get('bucket')}: mean relative 1W return {100 * r['exp_rel_return']:+.2f}%, 95% of single weeks in "
                     f"[{100 * rg[0]:+.1f}%, {100 * rg[1]:+.1f}%].")
    if r.get("p_top_decile") is not None:
        parts.append(f"Tail: P(top decile) {100 * r['p_top_decile']:.0f}%, P(bottom decile) {100 * r['p_bottom_decile']:.0f}%.")
    if r.get("dir_prior") is not None:
        parts.append(f"Directional prior P(up) {100 * r['dir_prior']:.1f}%" + (f", residual {100 * r['dir_adjustment']:+.2f} pp." if r.get("dir_adjustment") is not None else "."))
    return " ".join(parts)


def current_markdown(res: dict) -> str:
    W = res.get("1W") or {}
    today = W.get("today") or []
    A = W.get("alpha") or {}
    passed_res = [k for k in ("R1", "R1s", "R2", "R3", "R4", "R5", "MT", "DH", "PW") if (A.get(k) or {}).get("passed")]
    by = W.get("adjusted_by")
    lines: List[str] = []
    w = lines.append
    w("# FinSim2 — Residual / Meta ML: current research-only view")
    w("")
    w(f"Built {res.get('started')} from the frozen E and D (final fits on all matured data) and the residual program's final fits. "
      "**Research only** — nothing here changes production, the live-shadow models, the Directional prior or any hedge.")
    w("")
    w("- **E / D %**: within-today percentile of E's and D's score (100 = most attractive).")
    w("- **Residual**: R1's correction r̂ with its between-era se; `*` = material (|r̂| > 1.96·se), otherwise not distinguishable from zero. "
      + (f"**Adj. Alpha** = within-today percentile of β·zE + γ·r̂ of {by}, which passed its gates." if by else
         (f"{', '.join(passed_res)} passed but has no closed-form correction for today; **adjusted Alpha = E**." if passed_res else
          "No residual model passed its gates, so **adjusted Alpha = E**.")))
    w("- **REL**: E reliability (0–1, research display). **P(E)**: probability E is closer to the outcome than D.")
    w("- **Exp. rel.**: the mean 1W return relative to the cross-section in E's percentile bucket (2013–2024); **range** = 95% of single weekly outcomes.")
    tm = (today[0].get("tail_model") if today else None) or "T1/T1"
    w(f"- **Top / Bottom**: probability of finishing in the week's top / bottom decile — model {tm.split('/')[0]} (top) and "
      f"{tm.split('/')[1]} (bottom): the benchmark T1 (E's percentile) unless a richer tail model passed its gates. A passing T4 "
      "learns mostly which assets move a lot (asset class, signal disagreement), so it raises both tails for volatile assets.")
    w("- **Prior / Adj.**: Directional prior-only P(up) and the Directional residual's capped adjustment (research; the Directional model stays prior-only).")
    de = W.get("de") or {}
    unval = []
    if not any((A.get(k) or {}).get("passed") for k in ("R1", "R1s")):
        unval.append("**Residual** (R1 did not pass A1–A7: the correction is shown for transparency, not applied)")
    if not (de.get("DE-A") or {}).get("passed"):
        unval.append("**P(E) / Pref.** (the D-vs-E selector did not pass; it prefers whichever model is less extreme, because an extreme "
                     "percentile is usually farther from the realised one — historically always-E beats always-D "
                     f"by {_f(-((de.get('always_D_minus_E') or {}).get('mean') or 0))})")
    if not (W.get("directional_1W") or {}).get("passed"):
        unval.append("**Adj.** (the Directional residual did not pass; the Directional model stays prior-only)")
    if unval:
        w("- **Not validated — display only:** " + "; ".join(unval) + ".")
    w("")
    w("| Asset | Class | E % | D % | Residual | Adj. Alpha | REL | P(E) | Pref. | Exp. rel. | range (95%) | Top | Bottom | Prior | Adj. |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in today:
        rg = r.get("range95") or (None, None)
        res_s = "—" if r.get("residual") is None else f"{r['residual']:+.3f}{'*' if r.get('residual_material') else ''}"
        adj = _num(r.get("adjusted_pct", r.get("E_pct")), "{:.0f}")
        cells = [r["asset"], r.get("class"), _num(r.get("E_pct"), "{:.0f}"), _num(r.get("D_pct"), "{:.0f}"), res_s, adj,
                 _num(r.get("reliability"), "{:.2f}"), _num(r.get("p_E_better"), "{:.2f}"), r.get("preferred") or "—",
                 _num(r.get("exp_rel_return"), "{:+.2%}"), "—" if rg[0] is None else f"[{100 * rg[0]:+.1f}, {100 * rg[1]:+.1f}]%",
                 _num(r.get("p_top_decile"), "{:.0%}"), _num(r.get("p_bottom_decile"), "{:.0%}"), _num(r.get("dir_prior"), "{:.1%}"),
                 "—" if r.get("dir_adjustment") is None else f"{100 * r['dir_adjustment']:+.2f}pp"]
        w("| " + " | ".join(str(c) for c in cells) + " |")
    w("")
    w("## Per-asset explanation")
    w("")
    for r in today:
        w(f"- **{r['asset']}** — {explain(r)}")
    w("")
    return "\n".join(lines) + "\n"

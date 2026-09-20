"""Offline unit tests for company_scoring.py.

Pure stdlib, no network, no third-party packages:

    python3 test_company_scoring.py
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import company_scoring as C
from company_scoring import CompanyFinancials as F

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

def mk(t, ind="Semis", sec="Technology", **kw):
    base = dict(name=f"{t} Inc", industry=ind, sector=sec, price=100.0,
                shares_outstanding=1e9, market_cap=100e9, total_debt=10e9,
                cash=5e9, ebitda=10e9, revenue=50e9, net_income=5e9,
                total_assets=100e9, revenue_annual=[40e9,45e9,50e9],
                ebitda_annual=[8e9,9e9,10e9])
    base.update(kw)
    return F(ticker=t, **base)

print("== weights are exactly the spec ==")
check("major 40/25/20/15", C.MAJOR_WEIGHTS ==
      {"valuation":.40,"growth":.25,"profitability":.20,"debt":.15})
check("growth 45/35/20", C.GROWTH_WEIGHTS ==
      {"revenue_growth":.45,"revenue_acceleration":.35,"ebitda_growth":.20})
check("profitability 65/35", C.PROFITABILITY_WEIGHTS == {"ebitda_margin":.65,"roa":.35})
check("debt 60/40", C.DEBT_WEIGHTS == {"net_debt_ebitda":.60,"debt_market_cap":.40})
for w in (C.MAJOR_WEIGHTS, C.GROWTH_WEIGHTS, C.PROFITABILITY_WEIGHTS, C.DEBT_WEIGHTS):
    check(f"sums to 1.0 {list(w)[0]}", abs(sum(w.values())-1.0)<1e-12)
check("only ND and DM are lower-is-better",
      sorted(k for k,v in C.COMPONENT_HIGHER_IS_BETTER.items() if not v)
      == ["debt_market_cap","net_debt_ebitda"])

print("== market cap and enterprise value ==")
c = mk("T", market_cap=None, price=20.0, shares_outstanding=5e9)
check("MktCap = price x shares", C.company_market_cap(c) == 100e9)
check("Yahoo mcap preferred", C.company_market_cap(mk("T", market_cap=7e9)) == 7e9)
check("no inputs -> None",
      C.company_market_cap(mk("T", market_cap=None, price=None)) is None)
check("EV = mcap + debt - cash",
      C.enterprise_value(mk("T", market_cap=100e9, total_debt=10e9, cash=5e9)) == 105e9)
check("EV with net cash < mcap",
      C.enterprise_value(mk("T", market_cap=100e9, total_debt=1e9, cash=30e9)) == 71e9)

print("== EV/EBITDA validity gate ==")
check("valid multiple", abs(C._valid_ev_ebitda(mk("T"))-10.5)<1e-9, C._valid_ev_ebitda(mk("T")))
check("EBITDA <= 0 rejected", C._valid_ev_ebitda(mk("T", ebitda=-1e9)) is None)
check("EBITDA zero rejected", C._valid_ev_ebitda(mk("T", ebitda=0)) is None)
check("absurd multiple rejected", C._valid_ev_ebitda(mk("T", ebitda=1.0)) is None)
check("negative EV rejected",
      C._valid_ev_ebitda(mk("T", market_cap=1e9, total_debt=0, cash=50e9)) is None)

print("== STEP 1: industry peer set ==")
uni = [mk(f"S{i}") for i in range(8)] + [mk(f"E{i}", ind="Oil", sec="Energy") for i in range(6)]
target = mk("TGT")
peers, level, notes = C.build_industry_peer_set(target, uni)
check("industry level used", level == C.INDUSTRY)
check("only same-industry peers", all(p.industry=="Semis" for p in peers))
check("target excluded", "TGT" not in [p.ticker for p in peers])
thin = [mk("A"), mk("B")] + [mk(f"X{i}", ind="Other") for i in range(9)]
peers2, level2, notes2 = C.build_industry_peer_set(mk("TGT"), thin)
check("falls back to sector", level2 == C.SECTOR_FALLBACK)
check("fallback labelled, not silent", any("instead" in n for n in notes2), notes2)
check("sector fallback keeps whole sector", len(peers2) == 11, len(peers2))

print("== STEP 2: EBITDA cohort is the 50-75 percentile band ==")
# EBITDA 1..9 -> p50=5, p75=7 -> cohort {5,6,7}
band = [mk(f"P{i}", ebitda=float(i)*1e9, market_cap=float(i)*10e9,
           total_debt=0.0, cash=0.0) for i in range(1,10)]
cohort, cnotes = C.build_ebitda_peer_cohort(band)
got = sorted(p.ebitda/1e9 for p in cohort)
check("cohort = 50th-75th pct only", got == [5.0,6.0,7.0], got)
check("excludes the top quartile", 9.0 not in got and 8.0 not in got)
check("excludes the bottom half", 1.0 not in got and 4.0 not in got)
check("cohort range reported", any("50th-75th" in n for n in cnotes), cnotes)
bad = [mk(f"B{i}", ebitda=-1e9) for i in range(6)]
cohort2, notes3 = C.build_ebitda_peer_cohort(bad)
check("no valid peers -> empty cohort", cohort2 == [])
check("reason given", any("usable EBITDA" in n for n in notes3), notes3)

print("== STEP 3: benchmark EV/EBITDA ==")
# EVs 100,110,120 on EBITDA 10 -> multiples 10,11,12 -> winsorized mean = 11
coh = [mk("A", market_cap=100e9, total_debt=0, cash=0, ebitda=10e9),
       mk("B", market_cap=110e9, total_debt=0, cash=0, ebitda=10e9),
       mk("C", market_cap=120e9, total_debt=0, cash=0, ebitda=10e9)]
bench, rows, n = C.calculate_peer_ev_ebitda(coh)
check("benchmark = mean multiple", abs(bench-11.0)<1e-9, bench)
check("all peers reported", len(rows)==3 and n==3)
check("peer rows carry inputs", rows[0].ev_ebitda is not None and rows[0].ebitda is not None)
check("peers sorted by ticker", [r.ticker for r in rows]==["A","B","C"])
outlier = coh + [mk("Z", market_cap=2000e9, total_debt=0, cash=0, ebitda=10e9)]
bench2, _, _ = C.calculate_peer_ev_ebitda(outlier)
check("extreme peer multiple winsorized", bench2 < 20, bench2)
check("too few observations -> None", C.calculate_peer_ev_ebitda(coh[:1])[0] is None)

print("== STEP 4: implied value chain ==")
ev, eq = C.calculate_implied_equity_value(10e9, 11.0, 20e9, 5e9)
check("ImpliedEV = EBITDA x benchmark", ev == 110e9)
check("ImpliedEquity = EV - debt + cash", eq == 95e9)
check("missing EBITDA -> (None,None)", C.calculate_implied_equity_value(None,11.,0,0)==(None,None))
check("implied price = equity / shares",
      C.calculate_implied_share_price(95e9, 1e9) == 95.0)
check("zero shares -> None", C.calculate_implied_share_price(95e9, 0) is None)

print("== STEP 5: valuation gap and score ==")
check("gap = (implied-current)/current",
      abs(C.calculate_valuation_gap(150.0, 100.0)-0.50)<1e-12)
check("negative gap when overvalued",
      abs(C.calculate_valuation_gap(50.0, 100.0)+0.50)<1e-12)
check("zero price -> None", C.calculate_valuation_gap(50.0, 0.0) is None)
check("V = 100 tanh(2 gap)",
      abs(C.calculate_valuation_score(0.5)-100*math.tanh(1.0))<1e-12)
check("gap 0 -> V 0", C.calculate_valuation_score(0.0) == 0.0)
v50, v25, vm25, vm50 = (C.calculate_valuation_score(x) for x in (0.5,0.25,-0.25,-0.5))
check("+50% strongly positive", v50 > 70, v50)
check("+25% moderately positive", 40 < v25 < 60, v25)
check("-25% moderately negative", -60 < vm25 < -40, vm25)
check("-50% strongly negative", vm50 < -70, vm50)
check("monotone increasing in gap", vm50 < vm25 < 0 < v25 < v50)
check("symmetric about zero", abs(v50+vm50)<1e-12)
check("saturates, never exceeds bounds",
      all(-100 <= C.calculate_valuation_score(g) <= 100 for g in (-50,-5,-1,0,1,5,50)))
check("huge gap saturates near +100", C.calculate_valuation_score(50) > 99.9)

print("== GROWTH components ==")
check("revenue growth", abs(C.calculate_revenue_growth([100.,110.])-0.10)<1e-12)
check("needs 2 periods", C.calculate_revenue_growth([100.]) is None)
check("empty -> None", C.calculate_revenue_growth([]) is None)
# 5% -> 15% is positive acceleration
check("acceleration 5%->15% positive",
      abs(C.calculate_revenue_acceleration([100.,105.,120.75])-0.10)<1e-9,
      C.calculate_revenue_acceleration([100.,105.,120.75]))
# 30% -> 15% is NEGATIVE acceleration despite still growing
acc = C.calculate_revenue_acceleration([100.,130.,149.5])
check("acceleration 30%->15% NEGATIVE while still growing", acc < 0, acc)
check("needs 3 periods", C.calculate_revenue_acceleration([100.,110.]) is None)
check("ebitda growth", abs(C.calculate_ebitda_growth([8.,10.])-0.25)<1e-12)
check("zero base -> None", C.calculate_revenue_growth([0.,10.]) is None)
check("loss shrinking counts as growth", C._growth(-50.0, -100.0) == 0.5)

print("== GROWTH factor blending ==")
peers = [mk(f"P{i}", revenue_annual=[100.,100.,100.], ebitda_annual=[10.,10.,10.])
         for i in range(6)]
fast = mk("FAST", revenue_annual=[100.,120.,160.], ebitda_annual=[10.,12.,16.])
g = C.calculate_growth_score(fast, peers, C.INDUSTRY)
check("fast grower scores high", g.score > 50, g.score)
check("all three components available", all(c.available for c in g.components.values()))
check("weights are 45/35/20", abs(g.weights_used["revenue_growth"]-0.45)<1e-12
      and abs(g.weights_used["revenue_acceleration"]-0.35)<1e-12
      and abs(g.weights_used["ebitda_growth"]-0.20)<1e-12)
check("G = weighted sum",
      abs(g.score - sum(g.contributions.values()))<1e-9)
slow = mk("SLOW", revenue_annual=[100.,95.,85.], ebitda_annual=[10.,9.,7.])
check("shrinking company scores low",
      C.calculate_growth_score(slow, peers, C.INDUSTRY).score < 0)

print("== GROWTH renormalisation when RA is missing ==")
no_accel = mk("NA", revenue_annual=[100.,120.], ebitda_annual=[10.,12.])
g2 = C.calculate_growth_score(no_accel, peers, C.INDUSTRY)
check("RA unavailable", not g2.components["revenue_acceleration"].available)
check("RG weight = .45/.65", abs(g2.weights_used["revenue_growth"]-0.45/0.65)<1e-12,
      g2.weights_used)
check("EG weight = .20/.65", abs(g2.weights_used["ebitda_growth"]-0.20/0.65)<1e-12)
check("renormalised weights sum to 1", abs(sum(g2.weights_used.values())-1.0)<1e-12)
check("RA did not become a silent zero", "revenue_acceleration" not in g2.contributions)
check("missing component named", any("Revenue acceleration" in n for n in g2.notes), g2.notes)

print("== PROFITABILITY ==")
check("EBITDA margin", abs(C.calculate_ebitda_margin(25.,100.)-0.25)<1e-12)
check("zero revenue -> None", C.calculate_ebitda_margin(25.,0.) is None)
check("ROA = NI/assets", abs(C.calculate_roa(mk("T", net_income=5e9, total_assets=100e9))-0.05)<1e-12)
check("supplied ROA preferred", C.calculate_roa(mk("T", roa_supplied=0.33))==0.33)
check("zero assets -> None", C.calculate_roa(mk("T", total_assets=0)) is None)
ppeers=[mk(f"P{i}", ebitda=5e9, revenue=100e9, net_income=1e9, total_assets=100e9)
        for i in range(6)]
rich = mk("RICH", ebitda=50e9, revenue=100e9, net_income=20e9, total_assets=100e9)
p = C.calculate_profitability_score(rich, ppeers, C.INDUSTRY)
check("high margin + high ROA scores high", p.score > 50, p.score)
check("weights 65/35", abs(p.weights_used["ebitda_margin"]-0.65)<1e-12
      and abs(p.weights_used["roa"]-0.35)<1e-12)
check("P = weighted sum", abs(p.score-sum(p.contributions.values()))<1e-9)

print("== DEBT (both LOWER is better) ==")
check("net debt = debt - cash", C.calculate_net_debt(10e9, 4e9) == 6e9)
check("net cash is negative net debt", C.calculate_net_debt(1e9, 5e9) == -4e9)
check("ND/EBITDA", abs(C.calculate_net_debt_ebitda(10e9,4e9,3e9)-2.0)<1e-12)
check("negative ND/EBITDA allowed (net cash)",
      C.calculate_net_debt_ebitda(1e9,5e9,2e9) == -2.0)
check("non-positive EBITDA -> None", C.calculate_net_debt_ebitda(10e9,0,-1e9) is None)
check("zero EBITDA -> None", C.calculate_net_debt_ebitda(10e9,0,0) is None)
check("debt/mktcap", abs(C.calculate_debt_market_cap(mk("T", total_debt=25e9, market_cap=100e9))-0.25)<1e-12)
check("negative debt rejected", C.calculate_debt_market_cap(mk("T", total_debt=-1.0)) is None)

dpeers=[mk(f"P{i}", total_debt=50e9, cash=0.0, ebitda=10e9, market_cap=100e9)
        for i in range(6)]
clean = mk("CLEAN", total_debt=1e9, cash=20e9, ebitda=10e9, market_cap=100e9)
d = C.calculate_debt_score(clean, dpeers, C.INDUSTRY)
check("net-cash company scores HIGH on debt", d.score > 50, d.score)
check("LOWER debt -> HIGHER score", d.components["debt_market_cap"].score > 0)
levered = mk("LEV", total_debt=200e9, cash=0.0, ebitda=10e9, market_cap=100e9)
d2 = C.calculate_debt_score(levered, dpeers, C.INDUSTRY)
check("heavily levered scores LOW", d2.score < 0, d2.score)
check("weights 60/40", abs(d.weights_used["net_debt_ebitda"]-0.60)<1e-12
      and abs(d.weights_used["debt_market_cap"]-0.40)<1e-12)

print("== MASTER equation ==")
def factor(name, score):
    return C.FactorResult(name=name, score=score, status=C.OK)
fs = {"valuation":factor("valuation",60.0), "growth":factor("growth",40.0),
      "profitability":factor("profitability",20.0), "debt":factor("debt",-20.0)}
raw, w, contrib, notes = C.calculate_company_raw_score(fs)
expect = .40*60 + .25*40 + .20*20 + .15*-20
check("raw = 0.40V+0.25G+0.20P+0.15D", abs(raw-expect)<1e-12, f"{raw} vs {expect}")
check("contributions correct", abs(contrib["valuation"]-24.0)<1e-12
      and abs(contrib["growth"]-10.0)<1e-12 and abs(contrib["debt"]+3.0)<1e-12)
check("weights sum to 1", abs(sum(w.values())-1.0)<1e-12)
check("CompanyScore = 0.75 x raw", abs(C.calculate_company_score(raw)-0.75*expect)<1e-12)
check("company score clamps at +75", C.calculate_company_score(100.0) == 75.0)
check("company score clamps at -75", C.calculate_company_score(-100.0) == -75.0)
check("None -> None", C.calculate_company_score(None) is None)

print("== FinalEquityScore = CompanyScore + SectorOverlay ==")
check("adds overlay", C.calculate_final_equity_score(27.0, 15.04) == 42.04)
check("clamps at +100", C.calculate_final_equity_score(75.0, 25.0) == 100.0)
check("clamps at -100", C.calculate_final_equity_score(-75.0, -25.0) == -100.0)
check("missing overlay contributes nothing",
      C.calculate_final_equity_score(27.0, None) == 27.0)
check("no company score -> None", C.calculate_final_equity_score(None, 10.0) is None)
for cs in [x/2 for x in range(-150,151)]:
    for ov in (-25.0, -12.5, 0.0, 12.5, 25.0):
        f = C.calculate_final_equity_score(cs, ov)
        if not (-100 <= f <= 100):
            check("final always within [-100,+100]", False, (cs,ov,f)); break
    else:
        continue
    break
else:
    check("final always within [-100,+100] across the whole domain", True)

print("== major-factor renormalisation ==")
fs2 = dict(fs); fs2["growth"] = C.FactorResult(name="growth")   # unavailable
raw2, w2, c2, n2 = C.calculate_company_raw_score(fs2)
check("growth dropped", "growth" not in w2)
check("V weight = .40/.75", abs(w2["valuation"]-0.40/0.75)<1e-12, w2)
check("P weight = .20/.75", abs(w2["profitability"]-0.20/0.75)<1e-12)
check("D weight = .15/.75", abs(w2["debt"]-0.15/0.75)<1e-12)
check("renormalised weights sum to 1", abs(sum(w2.values())-1.0)<1e-12)
check("growth not a silent zero", abs(raw2-(.40*60+.20*20+.15*-20)/0.75)<1e-9, raw2)
check("dropped factor named", any("Growth" in x for x in n2), n2)
allmissing = {k:C.FactorResult(name=k) for k in C.MAJOR_WEIGHTS}
check("nothing available -> None", C.calculate_company_raw_score(allmissing)[0] is None)
check("raw clamps at +100",
      C.calculate_company_raw_score({k:factor(k,100.0) for k in C.MAJOR_WEIGHTS})[0]==100.0)

print("== build_company_score end to end ==")
# The 50th-75th percentile band is structurally ~25% of the peer set, so a
# usable cohort needs roughly 12+ valid peers. 16 here.
universe = [mk(f"S{i}", ebitda=(i+1)*1e9, market_cap=(i+1)*12e9, total_debt=2e9, cash=1e9,
               revenue=(i+1)*5e9, revenue_annual=[(i+1)*4e9,(i+1)*4.5e9,(i+1)*5e9],
               ebitda_annual=[(i+1)*.8e9,(i+1)*.9e9,(i+1)*1e9],
               net_income=(i+1)*.5e9, total_assets=(i+1)*10e9,
               shares_outstanding=1e9, price=(i+1)*12.0) for i in range(16)]
tgt = mk("TGT", ebitda=5e9, market_cap=40e9, total_debt=2e9, cash=10e9,
         revenue=25e9, revenue_annual=[15e9,20e9,25e9], ebitda_annual=[3e9,4e9,5e9],
         net_income=4e9, total_assets=30e9, shares_outstanding=1e9, price=40.0)
res = C.build_company_score(tgt, universe + [tgt], sector_overlay=10.0)
check("scored", res.scored and res.final_score is not None)
check("final = company + overlay",
      abs(res.final_score-(res.company_score+10.0))<1e-9)
check("company = 0.75 x raw", abs(res.company_score-0.75*res.raw_score)<1e-9)
check("benchmark level labelled", res.benchmark_level in (C.INDUSTRY, C.SECTOR_FALLBACK))
check("valuation detail populated",
      res.valuation.benchmark_ev_ebitda is not None
      and res.valuation.implied_price is not None
      and res.valuation.valuation_gap is not None)
check("cohort peers listed", len(res.valuation.cohort) >= C.MIN_EBITDA_COHORT)
check("all four factors present", set(res.factors)==set(C.MAJOR_WEIGHTS))
check("implied price consistent with chain",
      abs(res.valuation.implied_price
          - res.valuation.implied_equity_value/tgt.shares_outstanding)<1e-6)
check("gap consistent with prices",
      abs(res.valuation.valuation_gap
          - (res.valuation.implied_price-tgt.price)/tgt.price)<1e-12)
print(f"   TGT raw={res.raw_score:+.2f} company={res.company_score:+.2f} "
      f"overlay=+10.00 final={res.final_score:+.2f} level={res.benchmark_level}")

print("== thin EBITDA cohort degrades honestly ==")
# 6 peers -> 50-75 band holds ~2 -> below MIN_EBITDA_COHORT, and here the
# sector is the same set so widening cannot rescue it.
thin_uni = [mk(f"T{i}", ebitda=(i+1)*1e9, market_cap=(i+1)*12e9, total_debt=2e9,
               cash=1e9, shares_outstanding=1e9, price=(i+1)*12.0) for i in range(6)]
thin_tgt = mk("TT", ebitda=3e9, market_cap=36e9, shares_outstanding=1e9, price=36.0)
thin_res = C.build_company_score(thin_tgt, thin_uni+[thin_tgt], sector_overlay=0.0)
check("thin cohort -> valuation unavailable",
      not thin_res.factors["valuation"].available)
check("reason recorded", any("cohort" in n.lower() or "benchmark" in n.lower()
      for n in thin_res.factors["valuation"].notes), thin_res.factors["valuation"].notes)
check("other factors still scored", thin_res.company_score is not None)
check("valuation weight redistributed, not zeroed",
      "valuation" not in thin_res.weights_used
      and abs(sum(thin_res.weights_used.values())-1.0)<1e-12)
check("widening to sector was attempted and reported",
      any("widened" in n.lower() or "cohort" in n.lower()
          for n in thin_res.valuation.notes), thin_res.valuation.notes)

print("== no overlay available ==")
res2 = C.build_company_score(tgt, universe + [tgt], sector_overlay=None)
check("final equals company score", res2.final_score == res2.company_score)
check("absence noted", any("no sector overlay" in n.lower() for n in res2.notes), res2.notes)

print("== company with no peers at all ==")
lonely = mk("ALONE", ind="Nobody", sec="Nowhere")
res3 = C.build_company_score(lonely, [lonely], sector_overlay=5.0)
check("no crash", isinstance(res3, C.CompanyScoreResult))
check("valuation unavailable", not res3.factors["valuation"].available)
check("growth unavailable", not res3.factors["growth"].available)
check("no score rather than a fabricated one", res3.company_score is None, res3.company_score)

print("== company missing EBITDA entirely ==")
no_ebitda = mk("NOE", ebitda=None, ebitda_annual=[])
res4 = C.build_company_score(no_ebitda, universe + [no_ebitda], sector_overlay=0.0)
check("valuation unavailable without EBITDA", not res4.factors["valuation"].available)
check("ND/EBITDA unavailable", not res4.factors["debt"].components["net_debt_ebitda"].available)
check("debt still scored on Debt/MktCap alone",
      res4.factors["debt"].available
      and abs(res4.factors["debt"].weights_used["debt_market_cap"]-1.0)<1e-12)
check("still produces a score from remaining factors", res4.company_score is not None)
check("valuation weight redistributed", "valuation" not in res4.weights_used)

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

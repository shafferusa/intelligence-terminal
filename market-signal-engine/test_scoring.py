"""Offline unit tests for scoring.py.

Pure stdlib, no network, no third-party packages:

    python3 test_scoring.py
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scoring as S

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}"); fails.append(name)

print("== classify_score boundaries ==")
for v, exp in [(100,S.BULLISH),(40,S.BULLISH),(39.9,S.SEMI_BULLISH),(0,S.SEMI_BULLISH),
               (-0.1,S.SEMI_BEARISH),(-40,S.SEMI_BEARISH),(-40.1,S.BEARISH),(-100,S.BEARISH)]:
    check(f"classify({v}) == {exp}", S.classify_score(v)==exp, f"got {S.classify_score(v)}")
check("classify(None) is None", S.classify_score(None) is None)
check("classify(nan) is None", S.classify_score(float('nan')) is None)

print("== calculate_debt_revenue ==")
r,n = S.calculate_debt_revenue(50e9, 200e9); check("50/200 = 0.25", abs(r-0.25)<1e-12)
r,n = S.calculate_debt_revenue(0, 200e9);    check("zero debt -> 0.0", r==0.0)
r,n = S.calculate_debt_revenue(50e9, 0);     check("zero revenue -> None", r is None and n)
r,n = S.calculate_debt_revenue(50e9, None);  check("no revenue -> None", r is None)
r,n = S.calculate_debt_revenue(None, 200e9); check("no debt -> None", r is None)
r,n = S.calculate_debt_revenue(float('nan'), 200e9); check("nan debt -> None", r is None)

print("== calculate_leverage_score ==")
f = S.calculate_leverage_score(0.25, 0.25)
check("equal ratio -> 0", abs(f.score)<1e-12 and f.status==S.OK)
f = S.calculate_leverage_score(0.124, 0.317)
check("lower leverage -> positive", f.score > 0, f.score)
check("matches -50*ln(a/b)", abs(f.score - (-50*math.log(0.124/0.317))) < 1e-9)
f = S.calculate_leverage_score(0.60, 0.30)
check("higher leverage -> negative", f.score < 0, f.score)
f = S.calculate_leverage_score(0.0, 0.30)
check("zero debt -> +100 capped", f.score == 100.0 and f.status==S.OK)
check("zero debt note present", any("no total debt" in x for x in f.notes))
f = S.calculate_leverage_score(None, 0.30)
check("no company ratio -> unavailable", f.status==S.UNAVAILABLE and f.score is None)
f = S.calculate_leverage_score(0.25, None)
check("no sector ratio -> unavailable", f.status==S.UNAVAILABLE)
f = S.calculate_leverage_score(0.0001, 0.30)
check("extreme ratio clamped to +100", f.score == 100.0)
f = S.calculate_leverage_score(30.0, 0.30)
check("extreme ratio clamped to -100", f.score == -100.0)

print("== calculate_valuation_score ==")
f = S.calculate_valuation_score(29.8, 24.2)
check("rich vs peers -> negative", f.score < 0 and f.status==S.OK, f.score)
check("matches -50*ln(a/b)", abs(f.score - (-50*math.log(29.8/24.2))) < 1e-9)
f = S.calculate_valuation_score(12.0, 24.0)
check("cheap vs peers -> positive", f.score > 0)
for bad in (None, 0, -5.0, float('nan'), float('inf'), "x", 9999.0):
    f = S.calculate_valuation_score(bad, 24.2)
    ok = f.score == -50.0 and f.status == S.FALLBACK
    check(f"invalid PE {bad!r} -> -50 fallback", ok, f"{f.score}/{f.status}")
f = S.calculate_valuation_score(None, 24.2)
check("fallback message shown", any("unavailable/invalid" in x for x in f.notes))
f = S.calculate_valuation_score(20.0, None)
check("no sector PE -> unavailable", f.status==S.UNAVAILABLE)

print("== median helpers ==")
m,n = S.median_forward_pe([10, 20, 30, None, -5, 0, float('nan'), 9999])
check("PE median excludes junk", m==20 and n==3, f"{m}/{n}")
m,n = S.median_forward_pe([None, -1, 0])
check("all junk -> None", m is None and n==0)
m,n = S.median_of([0.1, 0.2, 0.3, None, float('nan'), -0.5])
check("ratio median excludes junk/negative", abs(m-0.2)<1e-12 and n==3, f"{m}/{n}")
m,n = S.median_of([1.0, 100.0])
check("median not mean (2 vals)", m==50.5 and n==2)
m,n = S.median_of([1,1,1,1,1000])
check("outlier does not move median", m==1)

print("== calculate_equity_score (bare formula) ==")
v = S.calculate_equity_score(46.9, -10.4)
check("0.55*46.9+0.45*-10.4 = 21.1", abs(v - (0.55*46.9+0.45*-10.4)) < 1e-9, v)
check("rounds to +21.1", round(v,1)==21.1, v)
check("both None -> None", S.calculate_equity_score(None,None) is None)
check("clamped high", S.calculate_equity_score(100,100)==100)
check("clamped low", S.calculate_equity_score(-100,-100)==-100)

print("== build_equity_score ==")
lev = S.calculate_leverage_score(0.124, 0.317)
val = S.calculate_valuation_score(29.8, 24.2)
res = S.build_equity_score([lev, val])
check("weights are 0.55/0.45", abs(res.weights_used["leverage"]-0.55)<1e-9
      and abs(res.weights_used["valuation"]-0.45)<1e-9)
check("score equals bare formula",
      abs(res.score - S.calculate_equity_score(lev.score, val.score)) < 1e-9)
check("label set", res.label in (S.BULLISH,S.SEMI_BULLISH,S.SEMI_BEARISH,S.BEARISH))
check("calculation text present", "=" in res.calculation_text)

# renormalisation when leverage drops out
lev_na = S.calculate_leverage_score(None, 0.317)
res2 = S.build_equity_score([lev_na, val])
check("leverage dropped -> valuation weight 1.0", abs(res2.weights_used["valuation"]-1.0)<1e-9)
check("score == valuation score", abs(res2.score - val.score) < 1e-9)
check("renorm note present", any("renormalised" in x for x in res2.notes))

val_na = S.calculate_valuation_score(20.0, None)
res3 = S.build_equity_score([lev_na, val_na])
check("both unavailable -> None score", res3.score is None and res3.label is None)

# fallback valuation still counts
val_fb = S.calculate_valuation_score(None, 24.2)
res4 = S.build_equity_score([lev, val_fb])
check("fallback valuation counted", abs(res4.weights_used["valuation"]-0.45)<1e-9)

print("== explain_score ==")
txt = S.explain_score("NVDA", res, "Semiconductors")
check("names ticker", txt.startswith("NVDA receives"))
check("mentions label", "Semi-Bullish" in txt or "Bullish" in txt)
check("mentions peer group", "Semiconductors" in txt)
check("deterministic", txt == S.explain_score("NVDA", res, "Semiconductors"))
print("   >", txt)
txt2 = S.explain_score("XYZ", res3, "Technology")
check("unscoreable explained", "could not be scored" in txt2)
txt3 = S.explain_score("XYZ", res4, "Technology")
check("fallback explained", "fixed" in txt3 and "-50" in txt3)
res5 = S.build_equity_score([S.calculate_leverage_score(0.0, 0.3), val])
txt4 = S.explain_score("ZERO", res5, "Technology")
check("zero-debt explained without % of None", "no total debt" in txt4)

bm = S.SectorBenchmark("Tech","sector",0.3,20.0,2,2,[])
check("low confidence flagged", bm.low_confidence)
check("low conf appears in explanation", "LOW" in S.explain_score("T", res, "Tech", bm))

print()
print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

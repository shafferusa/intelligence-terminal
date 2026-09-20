"""Offline unit tests for sector_scoring.py.

Pure stdlib, no network, no third-party packages:

    python3 test_sector_scoring.py
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sector_scoring as SS
from sector_scoring import CompanyObservation as CO

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

def prices(start, recent_ret, prev_ret, n=SS.MIN_PRICE_HISTORY):
    """Series where P[-1]/P[-64]-1 == recent_ret and P[-64]/P[-127]-1 == prev_ret."""
    p = [0.0] * n
    t = n - 1
    p[t - 126] = start
    p[t - 63] = start * (1 + prev_ret)
    p[t] = p[t - 63] * (1 + recent_ret)
    for i in range(n):                       # fill the gaps with anything positive
        if p[i] == 0.0: p[i] = start
    return p

print("== percentile ==")
check("p0 = min", SS.percentile([1,2,3,4], 0.0) == 1)
check("p1 = max", SS.percentile([1,2,3,4], 1.0) == 4)
check("p50 interpolates", SS.percentile([1,2,3,4], 0.5) == 2.5, SS.percentile([1,2,3,4],0.5))
check("single value", SS.percentile([7], 0.05) == 7)
check("empty -> None", SS.percentile([], 0.5) is None)

print("== winsorize / winsorized_mean ==")
vals = list(range(1, 21))                    # 1..20
w = SS.winsorize(vals)
check("winsorize keeps count", len(w) == 20)
check("low tail pulled up", min(w) > 1, min(w))
check("high tail pulled down", max(w) < 20, max(w))
check("middle untouched", w[10] == vals[10])

extreme = [0.10]*19 + [500.0]               # one absurd ROE
mean_raw = sum(extreme)/len(extreme)
mean_w, n = SS.winsorized_mean(extreme)
check("extreme outlier winsorized", mean_w < 1.0 and n == 20, f"raw={mean_raw:.2f} w={mean_w:.4f}")
check("winsorized mean near the body", abs(mean_w - 0.10) < 0.5, mean_w)
neg = [-500.0] + [0.10]*19
mean_neg, _ = SS.winsorized_mean(neg)
check("extreme NEGATIVE outlier winsorized", mean_neg > -1.0, mean_neg)
check("junk dropped", SS.winsorized_mean([1.0, None, float('nan'), float('inf'), 2.0])[1] == 2)
check("all junk -> None", SS.winsorized_mean([None, float('nan')])[0] is None)

print("== average_ranks (ties) ==")
check("no ties", SS.average_ranks([10,20,30]) == [1.0,2.0,3.0])
check("two-way tie averages", SS.average_ranks([10,20,20,30]) == [1.0,2.5,2.5,4.0],
      SS.average_ranks([10,20,20,30]))
check("all tied", SS.average_ranks([5,5,5]) == [2.0,2.0,2.0], SS.average_ranks([5,5,5]))

print("== percentile_factor_scores: directions ==")
vals = {"A":1.0, "B":2.0, "C":3.0, "D":4.0, "E":5.0}
hi = SS.percentile_factor_scores(vals, higher_is_better=True)
check("highest -> +100", hi["E"] == 100.0, hi)
check("lowest  -> -100", hi["A"] == -100.0, hi)
check("median  -> 0",   abs(hi["C"]) < 1e-9, hi)
check("monotone increasing", hi["A"]<hi["B"]<hi["C"]<hi["D"]<hi["E"])
lo = SS.percentile_factor_scores(vals, higher_is_better=False)
check("LOWER-is-better: lowest -> +100", lo["A"] == 100.0, lo)
check("LOWER-is-better: highest -> -100", lo["E"] == -100.0, lo)
check("LOWER-is-better: median -> 0", abs(lo["C"]) < 1e-9)
check("directions are mirror images", all(abs(hi[k]+lo[k])<1e-9 for k in vals))

print("== percentile_factor_scores: formula + edges ==")
four = SS.percentile_factor_scores({"A":1.0,"B":2.0,"C":3.0,"D":4.0}, True)
for k, rank in (("A",1),("B",2),("C",3),("D",4)):
    expect = 200*((rank-1)/3) - 100
    check(f"{k}: 200p-100 = {expect:+.2f}", abs(four[k]-expect)<1e-9, four[k])
tied = SS.percentile_factor_scores({"A":1.0,"B":2.0,"C":2.0,"D":3.0}, True)
check("tied sectors share a score", abs(tied["B"]-tied["C"])<1e-9, tied)
check("tied score uses average rank", abs(tied["B"] - (200*((2.5-1)/3)-100))<1e-9, tied["B"])
check("single sector -> 0", SS.percentile_factor_scores({"A":5.0}, True) == {"A":0.0})
check("all sectors tied -> all 0", all(abs(v)<1e-9 for v in
      SS.percentile_factor_scores({"A":2.0,"B":2.0,"C":2.0}, True).values()))
check("empty -> empty", SS.percentile_factor_scores({}, True) == {})
check("None values skipped", set(SS.percentile_factor_scores({"A":1.0,"B":None}, True)) == {"A"})

print("== calculate_returns ==")
r, p = SS.calculate_returns(prices(100.0, 0.10, 0.20))
check("recent return", abs(r-0.10)<1e-9, r)
check("previous return", abs(p-0.20)<1e-9, p)
check("too little history -> (None,None)", SS.calculate_returns([1.0]*126) == (None,None))
check("exactly enough history works", SS.calculate_returns([1.0]*127) == (0.0,0.0))
check("empty -> (None,None)", SS.calculate_returns([]) == (None,None))
check("None -> (None,None)", SS.calculate_returns(None) == (None,None))

print("== calculate_sector_returns: market-cap weighting ==")
big  = CO("BIG", market_cap=900.0, prices=prices(100.0, 0.10, 0.0))
small= CO("SML", market_cap=100.0, prices=prices(100.0, 0.00, 0.0))
recent, previous, n = SS.calculate_sector_returns([big, small])
check("weighted by market cap", abs(recent-0.09)<1e-9, recent)
check("n_used counted", n==2)
nohist = CO("NOH", market_cap=1000.0, prices=[1.0]*10)
recent2, _, n2 = SS.calculate_sector_returns([big, small, nohist])
check("short history excluded + weights renormalised", abs(recent2-0.09)<1e-9, recent2)
check("excluded company not counted", n2==2)
check("zero market cap excluded",
      SS.calculate_sector_returns([CO("Z", market_cap=0.0, prices=prices(100.,0.5,0.))])[2]==0)
check("no usable company -> None", SS.calculate_sector_returns([])[0] is None)

print("== calculate_growth_acceleration ==")
g, sa, va = SS.calculate_growth_acceleration(0.10, 0.02, 0.05, 0.03)
check("sector accel = .10-.02", abs(sa-0.08)<1e-9)
check("vti accel = .05-.03", abs(va-0.02)<1e-9)
check("differential = .08-.02", abs(g-0.06)<1e-9, g)
check("deteriorating sector -> negative",
      SS.calculate_growth_acceleration(0.01,0.10,0.05,0.03)[0] < 0)
check("no VTI -> raw None but sector accel kept",
      SS.calculate_growth_acceleration(0.1,0.02,None,None) == (None,0.08,None))
check("no sector -> all None", SS.calculate_growth_acceleration(None,None,0.05,0.03)[0] is None)

print("== company ratios ==")
c = CO("X", net_income=200.0, shareholders_equity=1000.0, total_assets=4000.0,
       total_debt=500.0, market_cap=2000.0)
check("ROE = NI/equity", abs(SS.company_roe(c)-0.20)<1e-12)
check("ROA = NI/assets", abs(SS.company_roa(c)-0.05)<1e-12)
check("Debt/MktCap", abs(SS.company_debt_market_cap(c)-0.25)<1e-12)
check("negative equity rejected",
      SS.company_roe(CO("N", net_income=10.0, shareholders_equity=-50.0)) is None)
check("zero assets rejected",
      SS.company_roa(CO("N", net_income=10.0, total_assets=0.0)) is None)
check("supplied ROE preferred",
      SS.company_roe(CO("S", net_income=1.0, shareholders_equity=1.0, roe_supplied=0.33))==0.33)
check("negative debt rejected",
      SS.company_debt_market_cap(CO("N", total_debt=-5.0, market_cap=100.0)) is None)
check("zero market cap rejected",
      SS.company_debt_market_cap(CO("N", total_debt=5.0, market_cap=0.0)) is None)
check("market cap falls back to price x shares",
      SS.company_market_cap(CO("F", price=10.0, shares_outstanding=50.0))==500.0)
check("Yahoo market cap preferred over price x shares",
      SS.company_market_cap(CO("F", market_cap=999.0, price=10.0, shares_outstanding=50.0))==999.0)

print("== factor coverage floor ==")
few = [CO(f"C{i}", net_income=10.0, shareholders_equity=100.0) for i in range(SS.MIN_COMPANIES_PER_FACTOR-1)]
f = SS.calculate_sector_roe(few, n_eligible=len(few))
check("too few observations -> unavailable", not f.available and f.status==SS.UNAVAILABLE)
check("reason recorded", any("need" in n for n in f.notes), f.notes)
enough = [CO(f"C{i}", net_income=10.0, shareholders_equity=100.0) for i in range(SS.MIN_COMPANIES_PER_FACTOR)]
f2 = SS.calculate_sector_roe(enough, n_eligible=len(enough))
check("enough observations -> raw value", abs(f2.raw_value-0.10)<1e-12 and f2.status==SS.OK)
check("coverage computed", abs(f2.coverage-1.0)<1e-9, f2.coverage)

print("== calculate_sector_raw_score: the worked example ==")
def factor(name, score):
    return SS.SectorFactor(name=name, raw_value=1.0, score=score, status=SS.OK,
                           n_used=10, n_eligible=10)
fs = {"growth":factor("growth",70.0), "roe":factor("roe",55.0),
      "roa":factor("roa",40.0), "debt_market_cap":factor("debt_market_cap",60.0)}
raw, weights, contributions, notes = SS.calculate_sector_raw_score(fs)
check("raw = 60.15", abs(raw-60.15)<1e-9, raw)
check("contribution G = 28.0", abs(contributions["growth"]-28.0)<1e-9)
check("contribution ROE = 11.55", abs(contributions["roe"]-11.55)<1e-9)
check("contribution ROA = 5.60", abs(contributions["roa"]-5.60)<1e-9)
check("contribution D = 15.00", abs(contributions["debt_market_cap"]-15.0)<1e-9)
check("weights sum to 1", abs(sum(weights.values())-1.0)<1e-9)
check("overlay = 15.0375", abs(SS.calculate_sector_overlay(raw)-15.0375)<1e-9,
      SS.calculate_sector_overlay(raw))
check("overlay rounds to +15.04", round(SS.calculate_sector_overlay(raw),2)==15.04)

print("== missing-factor renormalisation ==")
fs_no_roa = {k:v for k,v in fs.items() if k!="roa"}
fs_no_roa["roa"] = SS.SectorFactor(name="roa", n_eligible=10)     # unavailable
raw2, w2, c2, n2 = SS.calculate_sector_raw_score(fs_no_roa)
check("ROA weight redistributed", "roa" not in w2, w2)
check("G weight = .40/.86", abs(w2["growth"]-0.40/0.86)<1e-12, w2["growth"])
check("ROE weight = .21/.86", abs(w2["roe"]-0.21/0.86)<1e-12)
check("D weight = .25/.86", abs(w2["debt_market_cap"]-0.25/0.86)<1e-12)
check("renormalised weights sum to 1", abs(sum(w2.values())-1.0)<1e-12)
expect = (0.40*70 + 0.21*55 + 0.25*60)/0.86
check("score matches renormalised blend", abs(raw2-expect)<1e-9, f"{raw2} vs {expect}")
check("missing factor named", any("Mean ROA" in x for x in n2), n2)
check("ROA did NOT become a silent zero", raw2 > raw, f"{raw2} vs {raw}")

only_one = {"growth":factor("growth",70.0)}
for k in ("roe","roa","debt_market_cap"):
    only_one[k] = SS.SectorFactor(name=k)
raw3, _, _, n3 = SS.calculate_sector_raw_score(only_one)
check("one factor -> no score", raw3 is None, raw3)
check("withholding explained", any("no sector score" in x for x in n3), n3)

print("== clamping ==")
allmax = {k:factor(k,100.0) for k in SS.SECTOR_FACTOR_WEIGHTS}
check("raw clamps at +100", SS.calculate_sector_raw_score(allmax)[0] == 100.0)
allmin = {k:factor(k,-100.0) for k in SS.SECTOR_FACTOR_WEIGHTS}
check("raw clamps at -100", SS.calculate_sector_raw_score(allmin)[0] == -100.0)
check("overlay bound +25", SS.calculate_sector_overlay(100.0) == 25.0)
check("overlay bound -25", SS.calculate_sector_overlay(-100.0) == -25.0)
check("overlay None-safe", SS.calculate_sector_overlay(None) is None)
for v in range(-100, 101):
    o = SS.calculate_sector_overlay(float(v))
    if not (SS.OVERLAY_MIN <= o <= SS.OVERLAY_MAX):
        check(f"overlay in range for raw={v}", False, o); break
else:
    check("overlay within [-25,+25] for every raw score in [-100,+100]", True)

print("== build_all_sector_scores (end to end) ==")
def make_sector(n, roe, roa, debt, recent, prev, cap=1e9):
    return [CO(f"S{i}", name=f"Co {i}", market_cap=cap,
               net_income=roe*100.0, shareholders_equity=100.0,
               total_assets=(roe*100.0)/roa if roa else None,
               total_debt=debt*cap, prices=prices(100.0, recent, prev))
            for i in range(n)]

vti = prices(100.0, 0.02, 0.01)      # VTI accel = +0.01
universe = {
    "Strong":  make_sector(8, 0.30, 0.15, 0.10, 0.12, 0.02),   # accel +0.10
    "Middle":  make_sector(8, 0.15, 0.08, 0.30, 0.05, 0.03),   # accel +0.02
    "Weak":    make_sector(8, 0.05, 0.02, 0.90, 0.01, 0.09),   # accel -0.08
}
scores = SS.build_all_sector_scores(universe, vti)
check("all sectors scored", all(s.scored for s in scores.values()))
check("Strong beats Middle beats Weak",
      scores["Strong"].raw_score > scores["Middle"].raw_score > scores["Weak"].raw_score,
      {k:round(v.raw_score,2) for k,v in scores.items()})
check("Strong is top on growth", scores["Strong"].factors["growth"].score == 100.0)
check("Weak is bottom on growth", scores["Weak"].factors["growth"].score == -100.0)
check("LOW debt sector scores POSITIVE on debt",
      scores["Strong"].factors["debt_market_cap"].score == 100.0,
      scores["Strong"].factors["debt_market_cap"].score)
check("HIGH debt sector scores NEGATIVE on debt",
      scores["Weak"].factors["debt_market_cap"].score == -100.0)
check("VTI acceleration recorded", abs(scores["Strong"].vti_acceleration-0.01)<1e-9)
check("sector acceleration recorded", abs(scores["Strong"].sector_acceleration-0.10)<1e-9)
check("growth raw = sector - vti accel",
      abs(scores["Strong"].factors["growth"].raw_value-0.09)<1e-9,
      scores["Strong"].factors["growth"].raw_value)
check("overlay = raw/4",
      all(abs(s.overlay - s.raw_score/4) < 1e-9 for s in scores.values()))
check("overlay in bounds",
      all(-25 <= s.overlay <= 25 for s in scores.values()))
check("confidence assigned", all(s.confidence in (SS.HIGH,SS.MEDIUM,SS.LOW)
                                 for s in scores.values()))

print("== sector with too few companies ==")
sparse = dict(universe); sparse["Tiny"] = make_sector(2, 0.2, 0.1, 0.2, 0.05, 0.01)
s2 = SS.build_all_sector_scores(sparse, vti)
check("tiny sector not scored", not s2["Tiny"].scored, s2["Tiny"].raw_score)
check("tiny sector LOW confidence", s2["Tiny"].confidence == SS.LOW)
check("tiny sector explains itself", any("eligible" in n for n in s2["Tiny"].notes), s2["Tiny"].notes)
check("other sectors still scored", all(s2[k].scored for k in ("Strong","Middle","Weak")))

print("== missing VTI history ==")
s3 = SS.build_all_sector_scores(universe, None)
check("growth unavailable without VTI",
      not s3["Strong"].factors["growth"].available)
check("VTI absence explained",
      any("VTI" in n for n in s3["Strong"].factors["growth"].notes),
      s3["Strong"].factors["growth"].notes)
check("still scored on remaining 3 factors", s3["Strong"].scored)
check("growth weight redistributed", "growth" not in s3["Strong"].weights_used)
check("remaining weights sum to 1", abs(sum(s3["Strong"].weights_used.values())-1)<1e-12)
check("short VTI history also unavailable",
      not SS.build_all_sector_scores(universe, [1.0]*50)["Strong"].factors["growth"].available)

print("== sector with companies lacking price history ==")
nohist_universe = {
    "NoPrices": [CO(f"N{i}", market_cap=1e9, net_income=10.0, shareholders_equity=100.0,
                    total_assets=200.0, total_debt=1e8, prices=None) for i in range(8)],
    "Fine": make_sector(8, 0.20, 0.10, 0.20, 0.06, 0.02),
    "Also": make_sector(8, 0.10, 0.05, 0.40, 0.03, 0.02),
}
s4 = SS.build_all_sector_scores(nohist_universe, vti)
check("no-price sector: growth unavailable", not s4["NoPrices"].factors["growth"].available)
check("no-price sector: other factors fine", s4["NoPrices"].factors["roe"].available)
check("no-price sector still scored", s4["NoPrices"].scored)
check("no-price sector drops growth weight", "growth" not in s4["NoPrices"].weights_used)
check("price-bearing sectors keep growth", s4["Fine"].factors["growth"].available)

print("== extreme ROE does not dominate a sector ==")
normal = make_sector(19, 0.10, 0.05, 0.20, 0.05, 0.02)
blown = CO("BLOWN", market_cap=1e9, net_income=1e12, shareholders_equity=1.0,
           total_assets=200.0, total_debt=1e8, prices=prices(100.0,0.05,0.02))
with_outlier = SS.calculate_sector_roe(normal+[blown], 20)
without = SS.calculate_sector_roe(normal, 19)
check("1e12 ROE outlier barely moves the mean",
      abs(with_outlier.raw_value - without.raw_value) < 0.10,
      f"{with_outlier.raw_value:.4f} vs {without.raw_value:.4f}")
check("outlier still counted in n_used", with_outlier.n_used == 20)

print("== get_sector_score_for_ticker ==")
check("resolves by sector", SS.get_sector_score_for_ticker("X","Strong",scores) is scores["Strong"])
check("unknown sector -> None", SS.get_sector_score_for_ticker("X","Nope",scores) is None)
check("no sector -> None", SS.get_sector_score_for_ticker("X",None,scores) is None)

print("== sector_table_rows ==")
rows = SS.sector_table_rows(s2)
check("one row per sector", len(rows) == len(s2))
scored_rows = [r for r in rows if r["raw_score"] is not None]
check("sorted by raw score desc",
      all(scored_rows[i]["raw_score"] >= scored_rows[i+1]["raw_score"]
          for i in range(len(scored_rows)-1)), [r["raw_score"] for r in scored_rows])
check("unscored sectors sort last", rows[-1]["raw_score"] is None)
check("carries every column",
      {"sector","growth_raw","growth_score","roe_raw","roe_score","roa_raw","roa_score",
       "debt_raw","debt_score","raw_score","overlay","confidence"} <= set(rows[0]))

print("== weights are exactly the spec ==")
check("0.40 / 0.21 / 0.14 / 0.25", SS.SECTOR_FACTOR_WEIGHTS ==
      {"growth":0.40,"roe":0.21,"roa":0.14,"debt_market_cap":0.25})
check("weights sum to 1.0", abs(sum(SS.SECTOR_FACTOR_WEIGHTS.values())-1.0)<1e-12)
check("debt is the only lower-is-better factor",
      [k for k,v in SS.FACTOR_HIGHER_IS_BETTER.items() if not v] == ["debt_market_cap"])

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

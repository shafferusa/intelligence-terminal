"""Offline unit tests for scoring.py and statlib.py.

Pure stdlib, no network, no third-party packages:

    python3 test_scoring.py
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scoring as S
import statlib as L

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

print("== classify_score boundaries ==")
for v, exp in [(100,S.BULLISH),(40,S.BULLISH),(39.9,S.SEMI_BULLISH),(0,S.SEMI_BULLISH),
               (-0.1,S.SEMI_BEARISH),(-40,S.SEMI_BEARISH),(-40.1,S.BEARISH),(-100,S.BEARISH)]:
    check(f"classify({v}) == {exp}", S.classify_score(v)==exp, S.classify_score(v))
for bad in (None, float('nan'), float('inf'), "x", True):
    check(f"classify({bad!r}) is None", S.classify_score(bad) is None)

print("== clamp ==")
check("clamps high", L.clamp(150, -100, 100) == 100)
check("clamps low", L.clamp(-150, -100, 100) == -100)
check("passes through", L.clamp(12.5, -100, 100) == 12.5)

print("== is_finite ==")
for good in (0, 1, -1, 1.5, 1e12):
    check(f"is_finite({good})", L.is_finite(good))
for bad in (None, float('nan'), float('inf'), float('-inf'), "1", True, False):
    check(f"not is_finite({bad!r})", not L.is_finite(bad))

print("== percentile ==")
check("p0 = min", L.percentile([1,2,3,4], 0.0) == 1)
check("p1 = max", L.percentile([1,2,3,4], 1.0) == 4)
check("p50 interpolates", L.percentile([1,2,3,4], 0.5) == 2.5)
check("p75", L.percentile([1,2,3,4], 0.75) == 3.25, L.percentile([1,2,3,4],0.75))
check("single value", L.percentile([7], 0.05) == 7)
check("empty -> None", L.percentile([], 0.5) is None)

print("== median ==")
check("odd", L.median([3,1,2]) == 2)
check("even interpolates", L.median([1,2,3,4]) == 2.5)
check("junk dropped", L.median([1.0, None, float('nan'), 3.0]) == 2.0)
check("all junk -> None", L.median([None, float('nan')]) is None)

print("== winsorize (count-based) ==")
check("keeps count", len(L.winsorize(list(range(1,21)))) == 20)
check("pulls in both tails", min(L.winsorize(list(range(1,21))))==2.0
      and max(L.winsorize(list(range(1,21))))==19.0)
check("huge positive outlier neutralised",
      abs(L.winsorized_mean([0.10]*19+[500.0])[0]-0.10)<1e-9,
      L.winsorized_mean([0.10]*19+[500.0])[0])
check("huge negative outlier neutralised",
      abs(L.winsorized_mean([-500.0]+[0.10]*19)[0]-0.10)<1e-9)
check("small sample still protected (n=8)",
      abs(L.winsorized_mean([0.1]*7+[1e12])[0]-0.10)<1e-9)
check("n<3 untouched", L.winsorize([5.0, 1e9]) == [5.0, 1e9])
check("junk dropped", L.winsorized_mean([1.0,None,float('nan'),3.0])[1]==2)
check("empty -> None", L.winsorized_mean([])[0] is None)

print("== average_ranks ==")
check("no ties", L.average_ranks([10,20,30]) == [1.0,2.0,3.0])
check("tie averages", L.average_ranks([10,20,20,30]) == [1.0,2.5,2.5,4.0])
check("all tied", L.average_ranks([5,5,5]) == [2.0,2.0,2.0])

print("== percentile_rank_within ==")
check("highest in group -> 1.0", L.percentile_rank_within(100, [1,2,3]) == 1.0)
check("lowest in group -> 0.0", L.percentile_rank_within(0, [1,2,3]) == 0.0)
check("middle -> 0.5", abs(L.percentile_rank_within(2, [1,3])-0.5)<1e-9)
# all four values tied -> average rank 2.5 -> p = (2.5-1)/3 = 0.5, the middle
check("all tied -> exactly mid", abs(L.percentile_rank_within(2, [2,2,2])-0.5)<1e-9,
      L.percentile_rank_within(2,[2,2,2]))
check("tied below a higher peer", abs(L.percentile_rank_within(2, [2,2,9])-(1.0/3))<1e-9,
      L.percentile_rank_within(2,[2,2,9]))
check("no peers -> None", L.percentile_rank_within(5, []) is None)
check("junk value -> None", L.percentile_rank_within(None, [1,2,3]) is None)
check("junk peers dropped", L.percentile_rank_within(3, [1,None,float('nan'),2])==1.0)

print("== percentile_to_score ==")
check("p=1 higher-better -> +100", L.percentile_to_score(1.0, True) == 100.0)
check("p=0 higher-better -> -100", L.percentile_to_score(0.0, True) == -100.0)
check("p=0.5 -> 0", L.percentile_to_score(0.5, True) == 0.0)
check("p=0 lower-better -> +100", L.percentile_to_score(0.0, False) == 100.0)
check("p=1 lower-better -> -100", L.percentile_to_score(1.0, False) == -100.0)
check("directions mirror",
      all(abs(L.percentile_to_score(p/10,True)+L.percentile_to_score(p/10,False))<1e-9
          for p in range(11)))
check("None -> None", L.percentile_to_score(None) is None)

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

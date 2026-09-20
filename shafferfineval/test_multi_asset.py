"""Offline tests for asset_models.py, political.py and multi-asset routing.

Pure stdlib, no network:   python3 test_multi_asset.py
"""
import datetime as dt, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_models as A
import political as G
import routers as R
import universe as uni

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

print("== 1. every equation's weights sum correctly ==")
for key, model in A.MODELS.items():
    check(f"{key} sums to 1.0", abs(model.weight_total - 1.0) < 1e-9,
          model.weight_total)
    for spec in model.factors:
        if spec.children:
            total = sum(c.weight for c in spec.children)
            check(f"{key}.{spec.key} sub-equation sums to 1.0",
                  abs(total - 1.0) < 1e-9, total)
check("19 asset models defined", len(A.MODELS) == 19, len(A.MODELS))
check("equity scale is 0.75", A.EQUITY.scale == 0.75)
check("equity weights are 40/25/20/15",
      [f.weight for f in A.EQUITY.factors] == [0.40, 0.25, 0.20, 0.15])
check("REIT weights are 35/25/20/20",
      [f.weight for f in A.REIT.factors] == [0.35, 0.25, 0.20, 0.20])
check("preferred weights are 35/30/20/15",
      [f.weight for f in A.PREFERRED.factors] == [0.35, 0.30, 0.20, 0.15])
check("ETF weights are 80/20",
      [f.weight for f in A.ETF.factors] == [0.80, 0.20])
check("futures weights are 85/10/5",
      [f.weight for f in A.FUTURES.factors] == [0.85, 0.10, 0.05])
check("option weights are 55/25/10/10",
      [f.weight for f in A.OPTION.factors] == [0.55, 0.25, 0.10, 0.10])
check("vol weights are 30/25/20/15/10",
      [f.weight for f in A.VOLATILITY.factors] == [0.30, 0.25, 0.20, 0.15, 0.10])

print("== 2. missing factor renormalization ==")
full = A.blend(A.EQUITY, {"valuation": 60, "growth": 40, "profitability": 20, "debt": -20})
expected = 0.75 * (0.40*60 + 0.25*40 + 0.20*20 + 0.15*-20)
check("full equity blend", abs(full.score - expected) < 1e-9, full.score)
check("all four available", full.n_available == 4 and full.coverage == 1.0)
partial = A.blend(A.EQUITY, {"valuation": 60, "profitability": 20, "debt": -20})
check("growth dropped", "Growth" in partial.missing)
check("weights renormalized over 0.75",
      abs(partial.weights_used["valuation"] - 0.40/0.75) < 1e-12,
      partial.weights_used)
check("renormalized weights sum to 1",
      abs(sum(partial.weights_used.values()) - 1.0) < 1e-12)
check("missing factor is NOT a silent zero", partial.score != A.blend(
    A.EQUITY, {"valuation": 60, "growth": 0, "profitability": 20, "debt": -20}).score)
check("coverage reported", abs(partial.coverage - 0.75) < 1e-9)
check("renormalization explained", any("renormalized" in n for n in partial.notes))
empty = A.blend(A.EQUITY, {})
check("nothing available -> no score", empty.score is None)
check("confidence LOW with nothing", empty.confidence == "LOW")
check("confidence HIGH with full coverage", full.confidence == "HIGH")

print("== nested sub-equations ==")
reit = A.blend(A.REIT, {
    "valuation": {"p_affo": 40, "nav": 60, "cap_rate": 20},
    "growth": {"noi_growth": 30, "affo_growth": 10},
    "quality": {"occupancy": 50, "affo_margin": 40},
    "debt": {"net_debt_ebitda": -30, "interest_coverage": 20, "debt_market_cap": -10},
})
check("REIT blends nested factors", reit.score is not None)
val = next(f for f in reit.factors if f.key == "valuation")
check("valuation sub-score computed",
      abs(val.score - (0.45*40 + 0.30*60 + 0.25*20)) < 1e-9, val.score)
check("children recorded", len(val.children) == 3)
partial_reit = A.blend(A.REIT, {"valuation": {"p_affo": 40}, "growth": {}})
check("empty sub-equation marks the parent missing",
      "Growth" in partial_reit.missing)

print("== 3. factor direction ==")
check("only the documented factors are lower-is-better",
      [f.key for f in A.MBS.factors if not f.higher_is_better]
      == ["prepayment", "volatility"])
check("oil inventories are lower-is-better",
      not next(f for f in A.OIL.factors if f.key == "inventories").higher_is_better)
check("oil demand is higher-is-better",
      next(f for f in A.OIL.factors if f.key == "demand").higher_is_better)
check("gold real yield is lower-is-better",
      not next(f for f in A.GOLD.factors if f.key == "real_yield").higher_is_better)
check("ag stocks-to-use is lower-is-better",
      not next(f for f in A.AGRICULTURE.factors if f.key == "stocks_use").higher_is_better)
check("vol value is lower-is-better (cheap vol is attractive to buy)",
      not next(f for f in A.VOLATILITY.factors if f.key == "vol_value").higher_is_better)
check("higher score -> better", A.normalize_cross_sectional(90, [10,20,30,40], True) >
      A.normalize_cross_sectional(15, [10,20,30,40], True))
check("lower-is-better inverts", A.normalize_cross_sectional(90, [10,20,30,40], False) <
      A.normalize_cross_sectional(15, [10,20,30,40], False))
check("top of group -> +100", A.normalize_cross_sectional(999, [1,2,3], True) == 100.0)
check("bottom of group -> -100", A.normalize_cross_sectional(-999, [1,2,3], True) == -100.0)
check("no peers -> None, never 0", A.normalize_cross_sectional(5, [], True) is None)
check("z-score normalization stays in band",
      all(-100 <= A.normalize_time_series(v, [1,2,3,4,5,6,7,8,9,10], True, "zscore") <= 100
          for v in (-50, 0, 5, 50)))
check("linear anchors map correctly",
      A.normalize_linear(50, 0, 100) == 0.0 and A.normalize_linear(100, 0, 100) == 100.0)

print("== direction labels are never ambiguous ==")
check("bonds say total return not yield", "TOTAL RETURN" in A.RATES.direction)
check("vol says long volatility", "LONG volatility" in A.VOLATILITY.direction)
check("options say buy this option", "BUY this option" in A.OPTION.direction)
check("FX names base and quote",
      "BASE" in A.FX.direction and "QUOTE" in A.FX.direction)
for key, model in A.MODELS.items():
    check(f"{key} has a direction label", bool(model.direction))

print("== 4. political event severity ==")
components = {"conflict": 80, "sanctions": 60, "regulation": 40,
              "fiscal": 20, "capital_control": 10, "uncertainty": 100}
severity, detail = G.event_severity(components)
expected = (.30*80 + .20*60 + .20*40 + .15*20 + .10*10 + .05*100)
check("GPI = .30C+.20S+.20R+.15F+.10K+.05U", abs(severity - expected) < 1e-9, severity)
check("severity weights sum to 1", abs(sum(G.SEVERITY_WEIGHTS.values()) - 1.0) < 1e-12)
check("severity in [0,100]", 0 <= severity <= 100)
check("full coverage", detail.coverage == 1.0)
partial_sev, partial_detail = G.event_severity({"conflict": 80, "sanctions": 60})
check("missing components renormalize",
      abs(partial_sev - (.30*80 + .20*60) / 0.50) < 1e-9, partial_sev)
check("missing components named", len(partial_detail.missing) == 4)
check("no components -> None", G.event_severity({})[0] is None)
check("severity clamps at 100", G.event_severity({"conflict": 500})[0] == 100.0)

print("== 5. asset-specific political sign ==")
event = G.PoliticalEvent(event_type="conflict", start_time="2026-09-01",
                         status=G.ACTIVE, confidence=0.8, components=components)
today = dt.date(2026, 9, 20)
oil = G.political_impact(event, {"revenue": 0.9, "production": 0.8,
                                 "supply_chain": 0.5, "regulatory": 0.2}, today)
airline = G.political_impact(event, {"revenue": -0.7, "production": -0.3,
                                     "supply_chain": -0.8, "regulatory": 0.0}, today)
software = G.political_impact(event, {"revenue": 0.0, "production": 0.0,
                                      "supply_chain": -0.05, "regulatory": 0.0}, today)
check("same event helps oil", oil.impact > 0, oil.impact)
check("same event hurts airlines", airline.impact < 0, airline.impact)
check("same severity for both", oil.severity == airline.severity)
check("sign comes from exposure, not severity",
      oil.exposure > 0 > airline.exposure)
check("unexposed asset is near zero", abs(software.impact) < 2, software.impact)
check("impact = severity x exposure x confidence",
      abs(oil.impact - oil.decayed_severity * oil.exposure * oil.confidence) < 1e-9)
check("exposure weights sum to 1", abs(sum(G.EXPOSURE_WEIGHTS.values()) - 1.0) < 1e-12)
check("exposure clamps to [-1,1]",
      G.asset_exposure({"revenue": 5.0}) == 1.0)
check("no exposure mapping -> no impact, not zero impact",
      G.political_impact(event, {}, today).impact is None)
check("that distinction is explained",
      any("different from an impact of zero" in n
          for n in G.political_impact(event, {}, today).notes))

print("== 6. political decay ==")
check("headline decays fast", G.decay_factor("headline", 5) < 0.55)
check("headline nearly gone after a month", G.decay_factor("headline", 30) < 0.02)
check("proposed regulation decays slowly", G.decay_factor("proposed_regulation", 30) > 0.7)
check("enacted law persists", G.decay_factor("enacted_law", 365) > 0.65)
check("regime change is very long", G.decay_factor("regime_change", 365) > 0.85)
check("active sanctions do NOT decay",
      G.decay_factor("sanctions", 1000, G.ACTIVE) == 1.0)
check("resolved sanctions do decay",
      G.decay_factor("sanctions", 400, G.RESOLVED) < 1.0)
check("active conflict does not decay",
      G.decay_factor("conflict", 500, G.ACTIVE) == 1.0)
check("decay is monotonic",
      G.decay_factor("headline", 1) > G.decay_factor("headline", 10))
check("half-life is exact",
      abs(G.decay_factor("proposed_regulation", 90) - 0.5) < 1e-9)
check("age computed from start", G.age_in_days("2026-09-01", today) == 19)
check("no start time -> None", G.age_in_days(None) is None)

print("== 7. equity political overlay cap ==")
check("overlay default cap is 10", G.OVERLAY_DEFAULT_CAP == 10.0)
check("overlay max cap is 15", G.OVERLAY_MAX_CAP == 15.0)
check("+100 impact -> +10 overlay", G.political_overlay(100) == 10.0)
check("-100 impact -> -10 overlay", G.political_overlay(-100) == -10.0)
check("half impact -> half overlay", G.political_overlay(50) == 5.0)
check("configurable to 15", G.political_overlay(100, 15) == 15.0)
check("cannot exceed the hard max", G.political_overlay(100, 999) == 15.0)
check("no impact -> None", G.political_overlay(None) is None)
aggregate = G.aggregate_impacts([oil, software])
check("multiple events aggregate", aggregate is not None)
check("aggregate saturates, never exceeds 100",
      all(abs(G.aggregate_impacts([G.ImpactResult(impact=90)] * n)) <= 100
          for n in (1, 5, 20)))
check("no impacts -> None", G.aggregate_impacts([]) is None)

print("== 8. FX relative GPI ==")
check("GPI_pair = base - quote", G.fx_relative_gpi(40, -20) == 60.0)
check("symmetric", G.fx_relative_gpi(-20, 40) == -60.0)
check("equal impacts cancel", G.fx_relative_gpi(30, 30) == 0.0)
check("one side missing still works", G.fx_relative_gpi(40, None) == 40.0)
check("both missing -> None", G.fx_relative_gpi(None, None) is None)
check("clamped", G.fx_relative_gpi(100, -100) == 100.0)

print("== 9. futures inherit the underlying ==")
bullish = A.blend(A.FUTURES, {"underlying": 60, "carry_curve": 20, "liquidity": 80})
bearish = A.blend(A.FUTURES, {"underlying": -60, "carry_curve": 20, "liquidity": 80})
check("futures follow the underlying's sign", bullish.score > 0 > bearish.score)
check("underlying dominates at 85%",
      abs(bullish.weights_used["underlying"] - 0.85) < 1e-12)
check("future cannot contradict its underlying",
      abs(bullish.score - 60) < 25, bullish.score)
check("no underlying -> renormalizes onto carry/liquidity",
      "Underlying Shaffer score" in
      A.blend(A.FUTURES, {"carry_curve": 20, "liquidity": 80}).missing)

print("== 10. options inherit underlying direction ==")
check("call on bullish is positive", A.option_direction_score(60, "C") == 60.0)
check("put on bullish is negative", A.option_direction_score(60, "P") == -60.0)
check("call on bearish is negative", A.option_direction_score(-60, "C") == -60.0)
check("put on bearish is positive", A.option_direction_score(-60, "P") == 60.0)
check("no underlying -> None", A.option_direction_score(None, "C") is None)
call = A.blend(A.OPTION, {"direction": A.option_direction_score(60, "C"),
                          "vol_value": -40, "theta": -20, "liquidity": 70})
put = A.blend(A.OPTION, {"direction": A.option_direction_score(60, "P"),
                         "vol_value": -40, "theta": -20, "liquidity": 70})
check("call beats put on a bullish underlying", call.score > put.score)
check("direction carries 55%", abs(call.weights_used["direction"] - 0.55) < 1e-12)

print("== 11. CDS protection direction ==")
healthy = A.score_cds(70, 0)
weak = A.score_cds(-70, 0)
check("healthy issuer -> do NOT buy protection", healthy.score < 0, healthy.score)
check("weak issuer -> BUY protection", weak.score > 0, weak.score)
check("buy score is the negative of credit strength",
      abs(healthy.score + 70) < 1e-9)
check("relative value shifts the buy score", A.score_cds(0, 20).score == 20.0)
sell = A.score_cds(-70, 0, carry_overlay=10, protection_side="sell")
check("selling protection inverts the buy side", sell.score < 0)
check("sell side adds carry rather than pure negation",
      abs(sell.score - (-weak.score + 10)) < 1e-9)
check("sell direction labelled", "SELL protection" in sell.direction)
check("buy direction labelled", "BUY protection" in healthy.direction)
check("no credit score -> no CDS score", A.score_cds(None).score is None)

print("== 12. TRS receive/pay direction ==")
receive = A.score_trs(50, 5, "receive")
pay = A.score_trs(50, 5, "pay")
check("receive follows the underlying", receive.score == 45.0, receive.score)
check("pay inverts the underlying", pay.score == -55.0, pay.score)
check("carry cost is paid on BOTH sides",
      receive.score < 50 and pay.score < -50)
check("receive labelled", "RECEIVE total return" in receive.direction)
check("pay labelled", "PAY total return" in pay.direction)
check("no underlying -> no TRS score", A.score_trs(None).score is None)
check("missing carry recorded", "carry / financing cost" in
      A.score_trs(50, None).missing)

print("== 13. IRS receive/pay direction ==")
rec_fixed = A.score_irs(40, 0, "receive")
pay_fixed = A.score_irs(40, 0, "pay")
check("receive fixed follows bond-bullish rates", rec_fixed.score == 40.0)
check("pay fixed inverts it", pay_fixed.score == -40.0)
check("receive fixed benefits from falling rates",
      "RECEIVE fixed" in rec_fixed.direction and "bullish bonds" in rec_fixed.direction)
check("pay fixed benefits from rising rates",
      "bearish bonds" in pay_fixed.direction)
check("overlay applies", A.score_irs(40, 10, "receive").score == 50.0)
check("payer swaption inverts rates",
      A.score_swaption(40, 0, 0, 0, "payer").underlying_score == -40.0)
check("receiver swaption follows rates",
      A.score_swaption(40, 0, 0, 0, "receiver").underlying_score == 40.0)

print("== FX forward and ETF breadth ==")
check("FX forward = .85 fx + .15 forward",
      abs(A.score_fx_forward(50, 10).score - (0.85*50 + 0.15*10)) < 1e-9)
check("missing forward renormalizes onto FX",
      A.score_fx_forward(50, None).score == 50.0)
breadth = A.etf_breadth([60, 40, -20, 80, 10, -50, 30])
check("breadth built from constituents", set(breadth) ==
      {"percent_positive", "median_score", "dispersion", "participation"})
check("all-positive basket scores high on breadth",
      A.etf_breadth([50]*8)["percent_positive"] == 100.0)
check("all-negative basket scores low",
      A.etf_breadth([-50]*8)["percent_positive"] == -100.0)
check("too few constituents -> empty", A.etf_breadth([1, 2]) == {})
check("weighted underlying renormalizes over scored names",
      abs(A.weighted_underlying([50, None, 100], [0.5, 0.3, 0.5]) - 75.0) < 1e-9)
check("no constituents -> None", A.weighted_underlying([], []) is None)
check("ETF breadth is not a copy of the cap-weighted score",
      A.etf_breadth([100, -90, 95, -85, 90, -80])["median_score"] != 100)

print("== duration / convexity ==")
check("falling yields raise bond price",
      A.bond_price_change(7.0, -0.01) > 0)
check("rising yields lower bond price", A.bond_price_change(7.0, 0.01) < 0)
check("convexity helps both ways",
      A.bond_price_change(7.0, 0.01, 60) > A.bond_price_change(7.0, 0.01))
check("no duration -> None", A.bond_price_change(None, 0.01) is None)

print("== asset router ==")
check("every class with arithmetic is routed",
      set(R.SCORE_ENGINES) >= {uni.EQUITY, uni.ETF, uni.BOND, uni.FX,
                               uni.COMMODITY, uni.CRYPTO, uni.PREFERRED})
for cls, sub, sym, expected in [
        (uni.EQUITY, "Stock", None, A.EQUITY), (uni.EQUITY, "REIT", None, A.REIT),
        (uni.COMMODITY, "Commodity Energy", "CL", A.OIL),
        (uni.COMMODITY, "Commodity Energy", "NG", A.NATGAS),
        (uni.COMMODITY, "Commodity Metal", "GC", A.GOLD),
        (uni.COMMODITY, "Commodity Metal", "HG", A.INDUSTRIAL_METAL),
        (uni.COMMODITY, "Commodity Ag", None, A.AGRICULTURE),
        (uni.COMMODITY, "Commodity Livestock", None, A.LIVESTOCK),
        (uni.BOND, "US Treasury", None, A.RATES),
        (uni.BOND, "Corporate / sovereign bond", None, A.CORP_CREDIT),
        (uni.BOND, "Agency MBS (TBA / pool)", None, A.MBS),
        (uni.BOND, "Structured credit (CLO / CMBS / RMBS / ABS)", None, A.STRUCTURED),
        (uni.FX, "deliverable", None, A.FX),
        (uni.ETF, "ETF", None, A.ETF),
        (uni.CRYPTO, "Crypto (spot coin)", None, A.CRYPTO)]:
    check(f"{cls}/{sub or ''}/{sym or ''} -> {expected.version}",
          R.model_for(cls, sub, sym) is expected)

print("== awaiting-inputs is not a fabricated score ==")
for cls, sub, sym in [(uni.COMMODITY, "Commodity Energy", "CL"),
                      (uni.BOND, "US Treasury", None), (uni.FX, "deliverable", None)]:
    result = R.score_asset(cls, sym or "X", subclass=sub)
    check(f"{cls} with no inputs -> awaiting_inputs",
          result.status == R.AWAITING_DATA, result.status)
    check(f"{cls} invents no score", result.shaffer_score is None)
    check(f"{cls} names the missing inputs", len(result.message) > 40)
oil = R.score_asset(uni.COMMODITY, "CL", subclass="Commodity Energy",
                    factor_values={"inventories": 60, "supply": 20, "demand": 40,
                                   "curve": 30, "gpi": 50})
check("supplied inputs produce a real score", oil.status == R.IMPLEMENTED)
check("score in band", -100 <= oil.shaffer_score <= 100)
check("model version recorded", oil.model_version == "oil_shaffer_v1")
check("direction labelled", bool(oil.message))
check("partial coverage flagged", oil.confidence in ("LOW", "MEDIUM", "HIGH"))

print("== political overlay applied as a SEPARATE layer ==")
base = R.score_asset(uni.COMMODITY, "CL", subclass="Commodity Energy",
                     factor_values={"inventories": 60, "supply": 20, "demand": 40})
with_pol = R.score_asset(uni.COMMODITY, "CL", subclass="Commodity Energy",
                         factor_values={"inventories": 60, "supply": 20, "demand": 40},
                         political_overlay=8.0)
check("overlay adds on top", abs(with_pol.shaffer_score - (base.shaffer_score + 8)) < 1e-9)
check("overlay recorded as its own factor",
      with_pol.factor_scores.get("political_overlay") == 8.0)
check("underlying arithmetic unchanged by the overlay",
      abs(base.shaffer_score - with_pol.detail.score) < 1e-9)

print("== hedge router does not force the equity engine elsewhere ==")
check("only equities have a hedge engine", set(R.HEDGE_ENGINES) == {uni.EQUITY})
for cls in (uni.BOND, uni.FX, uni.COMMODITY, uni.CDS, uni.CRYPTO):
    check(f"{cls} gets no equity hedge",
          R.recommend_hedge_for(cls, None, None, []) is None)
    check(f"{cls} roadmap stated", "hedge engine" in R.hedge_engine_status(cls))
check("roadmap names the instruments", "swap" in R.hedge_roadmap(uni.BOND).lower())

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

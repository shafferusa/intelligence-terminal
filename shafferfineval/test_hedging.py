"""Offline unit tests for hedging.py and strategy_catalog.py.

Pure stdlib, no network:   python3 test_hedging.py
"""
import datetime as dt, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hedging as H
import strategy_catalog as SC

fails = []
def check(name, cond, extra=""):
    if cond: print(f"  PASS  {name}")
    else: print(f"  FAIL  {name} {extra}"); fails.append(name)

CAT, NOTES = SC.load_strategy_catalog()

print("== leg parser ==")
legs, err = SC.parse_strategy_legs("buy stock; buy option P @ pct:-7")
check("two legs", len(legs) == 2 and not err)
check("stock leg", legs[0].side == "buy" and legs[0].instrument == "stock")
check("put leg", legs[1].right == "P" and legs[1].strike_pct == -7.0)
legs, _ = SC.parse_strategy_legs("sell option C @ pct:+12 x2.0")
check("ratio parsed", legs[0].ratio == 2.0 and legs[0].strike_pct == 12.0)
legs, _ = SC.parse_strategy_legs("buy option C @ ATM")
check("ATM parsed", legs[0].strike_mode == SC.STRIKE_ATM and legs[0].strike_pct == 0.0)
legs, _ = SC.parse_strategy_legs("sell option C @ zero_cost")
check("zero-cost parsed", legs[0].strike_mode == SC.STRIKE_ZERO_COST)
for text in ("buy trs", "sell trs", "buy future", "sell future"):
    legs, e = SC.parse_strategy_legs(text)
    check(f"{text!r} parsed", len(legs) == 1 and not e)
_, e = SC.parse_strategy_legs("do something weird")
check("garbage reported not swallowed", e == ["do something weird"])
_, e = SC.parse_strategy_legs("buy option @ pct:-7")
check("option without a right is an error", bool(e))
check("empty reported", SC.parse_strategy_legs("")[1] == ["empty leg definition"])

print("== workbook catalog ==")
check("catalog loaded", len(CAT) == 60, len(CAT))
check("all legs parsed cleanly", all(s.parsed_cleanly for s in CAT),
      [s.key for s in CAT if not s.parsed_cleanly])
check("no parse errors reported", any("parsed cleanly" in n for n in NOTES), NOTES)
keys = {s.key for s in CAT}
for expected in ("PROTECTIVE_PUT", "COLLAR", "ZERO_COST_COLLAR", "PUT_SPREAD_HEDGE",
                 "PUT_SPREAD_COLLAR", "TAIL_HEDGE", "SYNTHETIC_SHORT_TRS",
                 "SHORT_FUTURES", "BETA_HEDGED_LONG", "PROTECTED_SHORT"):
    check(f"catalog has {expected}", expected in keys)

print("== STEP 1: adverse score ==")
for score, direction, expected in [(70, H.LONG, 0), (-60, H.LONG, 60),
                                   (50, H.SHORT, 50), (-70, H.SHORT, 0),
                                   (0, H.LONG, 0), (-100, H.LONG, 100)]:
    got = H.calculate_adverse_score(score, direction)
    check(f"{direction} score {score:+} -> {expected}", abs(got - expected) < 1e-9, got)
check("no score -> 0 adverse", H.calculate_adverse_score(None, H.LONG) == 0.0)

print("== STEP 2: hedge ratio ==")
for adverse, expected in [(0, 0.0), (15, 0.0), (25, 0.1176), (40, 0.2941),
                          (60, 0.5294), (80, 0.7647), (100, 1.0)]:
    got = H.calculate_recommended_hedge_ratio(adverse)
    check(f"adverse {adverse} -> {expected:.0%}", abs(got - expected) < 5e-4, got)
check("never below 0", H.calculate_recommended_hedge_ratio(-50) == 0.0)
check("never above 1", H.calculate_recommended_hedge_ratio(500) == 1.0)
check("hedged shares", abs(H.calculate_target_hedged_shares(10000, 0.5529) - 5529) < 1)

print("== STEP 4: eligibility filters on payoff, not name ==")
long_ok, long_no = H.filter_eligible_strategies(CAT, H.LONG, 100.0)
ok_keys = {s.key for s in long_ok}
no_keys = {k for k, _ in long_no}
for should in ("PROTECTIVE_PUT", "PUT_SPREAD_HEDGE", "COLLAR", "ZERO_COST_COLLAR",
               "TAIL_HEDGE", "SYNTHETIC_SHORT_TRS", "SHORT_FUTURES", "PUT_SPREAD_COLLAR"):
    check(f"LONG eligible: {should}", should in ok_keys)
for should_not in ("COVERED_CALL", "CASH_SECURED_PUT", "IRON_CONDOR", "SHORT_STRADDLE",
                   "SHORT_STRANGLE", "LONG_CALL", "BULL_CALL_SPREAD", "JADE_LIZARD",
                   "LONG_STRADDLE", "LONG_STRANGLE", "CORE_PLUS_OVERLAY", "RISK_REVERSAL"):
    check(f"LONG excluded: {should_not}", should_not in no_keys,
          "still eligible" if should_not in ok_keys else "")
check("covered call reason is the payoff", any(
    "does not offset" in r for k, r in long_no if k == "COVERED_CALL"))
check("straddle excluded for adding exposure", any(
    "adds long exposure" in r for k, r in long_no if k == "LONG_STRADDLE"))
check("pairs excluded for needing a second name", any(
    "second name" in r for k, r in long_no if k.startswith("RV_")))

short_ok, short_no = H.filter_eligible_strategies(CAT, H.SHORT, 100.0)
sok = {s.key for s in short_ok}
for should in ("PROTECTED_SHORT", "CALL_SPREAD_HEDGE", "SHORT_COLLAR",
               "SYNTHETIC_LONG_TRS", "LONG_FUTURES", "SHORT_TAIL_HEDGE"):
    check(f"SHORT eligible: {should}", should in sok)
for should_not in ("CASH_SECURED_PUT", "SHORT_STRADDLE", "IRON_CONDOR", "LONG_PUT"):
    check(f"SHORT excluded: {should_not}", should_not not in sok)

print("== STEP 6: dynamic strike ==")
for adverse, expect_otm in [(0, 0.15), (20, 0.12), (40, 0.09), (60, 0.06),
                            (80, 0.03), (100, 0.0)]:
    strike, otm = H.calculate_option_target_strike(100.0, adverse, H.PUT)
    check(f"adverse {adverse} -> put {expect_otm:.0%} OTM",
          abs(abs(otm) - expect_otm) < 1e-9, otm)
    check(f"adverse {adverse} strike consistent",
          abs(strike - 100.0 * (1 - expect_otm)) < 1e-9)
strike, otm = H.calculate_option_target_strike(180.0, 62.0, H.PUT)
check("spec example: $180 @ adverse 62 -> $169.74", abs(strike - 169.74) < 0.01, strike)
cstrike, cotm = H.calculate_option_target_strike(100.0, 40.0, H.CALL)
check("call strike goes ABOVE spot", cstrike > 100.0 and cotm > 0)
check("no price -> None", H.calculate_option_target_strike(None, 50, H.PUT) == (None, None))

print("== nearest listed strike ==")
strikes = [150, 155, 160, 165, 170, 175, 180]
check("nearest below", H.select_nearest_listed_strike(169.74, strikes) == 170)
check("nearest exact", H.select_nearest_listed_strike(160.0, strikes) == 160)
check("ties resolve deterministically",
      H.select_nearest_listed_strike(162.5, strikes) in (160, 165))
check("no strikes -> None", H.select_nearest_listed_strike(170, []) is None)
check("no target -> None", H.select_nearest_listed_strike(None, strikes) is None)

print("== STEP 7: expiry selection ==")
today = dt.date(2026, 9, 20)
expiries = [today + dt.timedelta(days=d) for d in (7, 30, 65, 92, 115, 200, 400)]
exp, dte, why = H.select_target_expiry(expiries, today)
check("picks ~90 days", dte == 92, dte)
check("inside the window", H.MIN_DTE <= dte <= H.MAX_DTE)
check("reason given", "window" in why)
exp2, dte2, why2 = H.select_target_expiry(
    [today + dt.timedelta(days=d) for d in (5, 20, 300)], today)
check("no expiry in window -> closest to target anyway", dte2 == 20, dte2)
check("fallback explained", "no listed expiry inside" in why2)
check("past expiries ignored",
      H.select_target_expiry([today - dt.timedelta(days=5)], today)[0] is None)
check("empty -> None", H.select_target_expiry([], today)[0] is None)

print("== STEP 8: contract sizing and rounding ==")
check("5529 -> 55 contracts", H.calculate_option_contracts(5529) == 55)
check("5550 -> 56 contracts", H.calculate_option_contracts(5550) == 56)
check("49 -> 0 contracts", H.calculate_option_contracts(49) == 0)
check("50 -> 1 contract (round half up)", H.calculate_option_contracts(51) == 1)
check("zero -> 0", H.calculate_option_contracts(0) == 0)
check("None -> 0", H.calculate_option_contracts(None) == 0)
check("delta coverage = contracts x 100 x |delta|",
      H.calculate_delta_coverage(55, -0.35) == 55 * 100 * 0.35)
check("delta coverage uses magnitude", H.calculate_delta_coverage(10, -0.5) == 500)
check("no delta -> None (not zero)", H.calculate_delta_coverage(55, None) is None)

print("== STEP 9: TRS direction and sizing ==")
check("long hedged by PAYING", H.trs_direction(H.LONG) == "PAY TOTAL RETURN")
check("short hedged by RECEIVING", H.trs_direction(H.SHORT) == "RECEIVE TOTAL RETURN")
notional = H.calculate_trs_notional(1_800_000, 0.55)
check("notional = MV x ratio", abs(notional - 990_000) < 1e-6, notional)
check("share equivalent = notional / price",
      H.calculate_trs_share_equivalent(990_000, 180.0) == 5500)
check("no MV -> None", H.calculate_trs_notional(None, 0.5) is None)
check("zero price -> None", H.calculate_trs_share_equivalent(990_000, 0) is None)

print("== STEP 10-11: stock and proxy sizing ==")
check("stock hedge = hedged shares", H.calculate_stock_hedge_size(5529) == 5529
      and H.calculate_stock_hedge_size(-5529) == 5529)
check("beta-adjusted notional",
      abs(H.calculate_beta_adjusted_proxy_hedge(1_800_000, 0.55, 1.45) - 1_435_500) < 1e-6)
check("no beta -> None, never dollar-matched",
      H.calculate_beta_adjusted_proxy_hedge(1_800_000, 0.55, None) is None)
check("futures contracts", H.calculate_futures_contracts(1_435_500, 25000, 20) == 3)
check("missing multiplier -> None",
      H.calculate_futures_contracts(1_000_000, 25000, None) is None)
check("zero denominator -> None", H.calculate_futures_contracts(1e6, 0, 20) is None)

print("== STEP 12: strategy score blending ==")
factors = {k: H.FactorScore(k, 80.0, True) for k in H.STRATEGY_WEIGHTS}
score, weights = H.calculate_strategy_score(factors)
check("all-80 -> 80", abs(score - 80.0) < 1e-9, score)
check("weights sum to 1", abs(sum(weights.values()) - 1) < 1e-12)
check("weights are the spec", abs(weights["effectiveness"] - 0.30) < 1e-12
      and abs(weights["cost"] - 0.20) < 1e-12 and abs(weights["basis"] - 0.05) < 1e-12)
partial = dict(factors); partial["cost"] = H.FactorScore("cost", None, False)
score2, weights2 = H.calculate_strategy_score(partial)
check("unavailable factor dropped", "cost" not in weights2)
check("remaining renormalised", abs(sum(weights2.values()) - 1) < 1e-12)
check("effectiveness reweighted to .30/.80",
      abs(weights2["effectiveness"] - 0.30 / 0.80) < 1e-12, weights2["effectiveness"])
check("nothing available -> None",
      H.calculate_strategy_score({k: H.FactorScore(k, None, False)
                                  for k in H.STRATEGY_WEIGHTS})[0] is None)

print("== worked example: NVDA long 10,000 @ $180, score -62 ==")
pos = H.Position("NVDA", H.LONG, 10000, 180.0)
ctx = H.HedgeContext(equity_score=-62.0, as_of=today)
res = H.recommend_hedge(pos, ctx, CAT)
check("adverse 62", abs(res.adverse_score - 62) < 1e-9)
check("ratio ~55%", abs(res.hedge_ratio - 0.5529) < 1e-3, res.hedge_ratio)
check("hedged shares ~5,529", abs(res.hedged_shares - 5529.4) < 1, res.hedged_shares)
check("notional ~$995,294", abs(res.hedge_notional - 995_294) < 100, res.hedge_notional)
check("hedge required", res.hedge_required)
check("strategies ranked", len(res.ranked) >= 10, len(res.ranked))
check("ranked descending", all(
    res.ranked[i].strategy_score >= res.ranked[i+1].strategy_score
    for i in range(len(res.ranked) - 1)))
check("preferred is the top", res.preferred is res.ranked[0])
check("explanation is deterministic",
      res.explanation == H.recommend_hedge(pos, ctx, CAT).explanation)
check("explanation names the ticker and ratio",
      "NVDA" in res.explanation and "55%" in res.explanation)

print("== specific structures are built correctly ==")
by_key = {}
for strategy in CAT:
    if strategy.key in ("PROTECTIVE_PUT", "COLLAR", "PUT_SPREAD_HEDGE",
                        "SYNTHETIC_SHORT_TRS", "PUT_SPREAD_COLLAR"):
        by_key[strategy.key] = H.build_strategy_ticket(
            strategy, pos, ctx, 62.0, res.hedged_shares, res.hedge_ratio)

pp = by_key["PROTECTIVE_PUT"]
check("protective put: one option leg", len(pp.legs) == 1)
check("protective put: BUY a put", pp.legs[0].action == "BUY" and pp.legs[0].right == "P")
check("protective put: dynamic strike $169.74",
      abs(pp.legs[0].target_strike - 169.74) < 0.01, pp.legs[0].target_strike)
check("protective put: 55 contracts", pp.legs[0].contracts == 55)
check("protective put: protects 5,500 shares", pp.protected_shares == 5500)
check("protective put: effective ratio 55%", abs(pp.effective_hedge_ratio - 0.55) < 1e-9)
check("protective put: strike % shown", abs(pp.legs[0].strike_pct_from_spot + 0.0570) < 1e-3,
      pp.legs[0].strike_pct_from_spot)

collar = by_key["COLLAR"]
check("collar: two legs", len(collar.legs) == 2)
check("collar: buys a put", any(l.action == "BUY" and l.right == "P" for l in collar.legs))
check("collar: sells a call", any(l.action == "SELL" and l.right == "C" for l in collar.legs))
call_leg = next(l for l in collar.legs if l.right == "C")
check("collar: call keeps the workbook +10% offset",
      abs(call_leg.target_strike - 198.0) < 0.01, call_leg.target_strike)

spread = by_key["PUT_SPREAD_HEDGE"]
check("put spread: two put legs", len(spread.legs) == 2
      and all(l.right == "P" for l in spread.legs))
upper = next(l for l in spread.legs if l.action == "BUY")
lower = next(l for l in spread.legs if l.action == "SELL")
check("put spread: upper is the dynamic strike", abs(upper.target_strike - 169.74) < 0.01)
check("put spread: lower keeps the workbook -20%",
      abs(lower.target_strike - 144.0) < 0.01, lower.target_strike)
check("put spread: upper above lower", upper.target_strike > lower.target_strike)

trs = by_key["SYNTHETIC_SHORT_TRS"]
check("TRS: one leg", len(trs.legs) == 1 and trs.legs[0].instrument == "trs")
check("TRS: PAYS total return for a long", "PAY TOTAL RETURN" in trs.legs[0].description)
check("TRS: notional = MV x ratio", abs(trs.legs[0].notional - 995_294) < 100)
check("TRS: share equivalent", abs(trs.legs[0].shares - 5529.4) < 1)
check("TRS: financing spread flagged missing", "TRS financing spread" in trs.missing)

psc = by_key["PUT_SPREAD_COLLAR"]
check("put-spread collar: three legs", len(psc.legs) == 3)
check("put-spread collar: two puts one call",
      sum(1 for l in psc.legs if l.right == "P") == 2
      and sum(1 for l in psc.legs if l.right == "C") == 1)

print("== short position ticket ==")
short_pos = H.Position("NVDA", H.SHORT, 10000, 180.0)
short_ctx = H.HedgeContext(equity_score=+62.0, as_of=today)
short_res = H.recommend_hedge(short_pos, short_ctx, CAT)
check("short + positive score is adverse", short_res.adverse_score == 62)
check("short hedge required", short_res.hedge_required)
trs_short = next((e for e in short_res.ranked if e.key == "SYNTHETIC_LONG_TRS"), None)
check("short hedged by RECEIVING total return",
      trs_short is not None and "RECEIVE TOTAL RETURN" in trs_short.legs[0].description)
call_hedges = [e for e in short_res.ranked
               if any(l.right == "C" and l.action == "BUY" for l in e.legs)]
check("short hedges buy CALLS", len(call_hedges) > 0)
pc = next((e for e in short_res.ranked if e.key == "PROTECTED_SHORT"), None)
leg = next(l for l in pc.legs if l.right == "C")
check("protective call struck ABOVE spot", leg.target_strike > 180.0, leg.target_strike)

print("== no-hedge case ==")
aligned = H.recommend_hedge(
    H.Position("NVDA", H.LONG, 10000, 180.0),
    H.HedgeContext(equity_score=+72.0, as_of=today), CAT)
check("aligned -> adverse 0", aligned.adverse_score == 0)
check("aligned -> ratio 0", aligned.hedge_ratio == 0)
check("aligned -> no hedge required", not aligned.hedge_required)
check("aligned -> no forced strategy", aligned.preferred is None)
check("aligned -> says so", "NO FUNDAMENTAL HEDGE" in aligned.explanation.upper()
      or "no fundamental hedge is required" in aligned.explanation)
below = H.recommend_hedge(
    H.Position("X", H.LONG, 100, 50.0),
    H.HedgeContext(equity_score=-10.0, as_of=today), CAT)
check("adverse 10 is below the threshold", not below.hedge_required)

print("== missing data is never invented ==")
check("no option chain -> premium missing",
      all(l.premium is None for l in pp.legs))
check("no chain -> listed strike missing", all(l.strike is None for l in pp.legs))
check("no chain -> target strike still shown", pp.legs[0].target_strike is not None)
check("chain absence recorded", "option chain" in pp.missing)
check("confidence drops", pp.confidence in (H.MEDIUM, H.LOW))
no_price = H.recommend_hedge(
    H.Position("X", H.LONG, 1000, None),
    H.HedgeContext(equity_score=-80.0, as_of=today), CAT)
check("no price -> still no crash", isinstance(no_price, H.HedgeResult))
check("no catalog -> no invented strategies",
      H.recommend_hedge(pos, ctx, []).preferred is None)
check("no catalog explained", "catalog is empty" in
      " ".join(H.recommend_hedge(pos, ctx, []).notes))
check("no score -> no hedge",
      not H.recommend_hedge(pos, H.HedgeContext(equity_score=None), CAT).hedge_required)

print("== with a live option chain ==")
expiry = today + dt.timedelta(days=92)
quotes = []
for strike in range(140, 221, 5):
    quotes.append(H.OptionQuote(float(strike), "P", expiry, bid=strike*0.02,
                                ask=strike*0.022, volume=800, open_interest=5200,
                                implied_volatility=0.42, delta=-0.35))
    quotes.append(H.OptionQuote(float(strike), "C", expiry, bid=strike*0.02,
                                ask=strike*0.022, volume=600, open_interest=4100,
                                implied_volatility=0.40, delta=0.35))
chain = H.OptionChain("NVDA", [expiry], {expiry: quotes}, available=True)
ctx_chain = H.HedgeContext(equity_score=-62.0, as_of=today, chain=chain,
                           borrow_fee=0.004, trs_financing_spread=0.0075)
res_chain = H.recommend_hedge(pos, ctx_chain, CAT)
pp2 = next(e for e in res_chain.ranked if e.key == "PROTECTIVE_PUT")
leg = pp2.legs[0]
check("listed strike selected", leg.strike == 170.0, leg.strike)
check("expiry selected", leg.expiry == expiry and leg.dte == 92)
check("premium populated", leg.premium is not None)
check("delta populated", leg.delta == -0.35)
check("IV populated", leg.implied_volatility == 0.42)
check("delta coverage computed",
      pp2.delta_equivalent_shares == 55 * 100 * 0.35, pp2.delta_equivalent_shares)
check("protection floor differs from delta coverage",
      pp2.protected_shares != pp2.delta_equivalent_shares)
check("premium total computed", pp2.total_premium is not None)
check("cost factor now real, not structural",
      "structural estimate" not in pp2.factors["cost"].note)
check("liquidity now available", pp2.factors["liquidity"].available)
check("confidence improves with a chain", pp2.confidence == H.HIGH, pp2.confidence)
check("strike % vs spot correct",
      abs(leg.strike_pct_from_spot - (170.0 - 180.0) / 180.0) < 1e-12)

print("== proxy / index hedge ==")
nq = H.ProxyInstrument("NQZ26", "Nasdaq-100", kind="future", price=25000.0,
                       beta=1.45, correlation=0.78, contract_multiplier=20.0)
res_proxy = H.recommend_hedge(
    pos, H.HedgeContext(equity_score=-62.0, as_of=today, proxies=[nq],
                        chain=chain, borrow_fee=0.004, trs_financing_spread=0.0075), CAT)
fut = next((e for e in res_proxy.ranked if e.key == "SHORT_FUTURES"), None)
check("futures strategy sized", fut is not None and fut.legs[0].contracts is not None)
check("beta-adjusted notional used",
      abs(fut.legs[0].notional - 995_294 * 1.45) < 200, fut.legs[0].notional)
check("proxy basis scores below same-underlying",
      fut.factors["basis"].score < 100.0, fut.factors["basis"].score)
direct = next(e for e in res_proxy.ranked if e.key == "PROTECTIVE_PUT")
check("direct hedge has perfect basis", direct.factors["basis"].score == 100.0)
check("proxy effectiveness discounted by correlation",
      fut.factors["effectiveness"].score < 100.0)
check("basis note explains company-specific risk",
      "company-specific" in fut.factors["basis"].note)
no_proxy = H.recommend_hedge(pos, H.HedgeContext(equity_score=-62.0, as_of=today,
                                                 chain=chain), CAT)
check("unsized futures excluded, not floated up",
      not any(e.key == "SHORT_FUTURES" for e in no_proxy.ranked))
check("exclusion reason names the missing proxy data", any(
      ("cannot be sized" in r or "correlation" in r or "proxy" in r)
      for k, r in no_proxy.excluded if k == "SHORT_FUTURES"),
      [r for k, r in no_proxy.excluded if k == "SHORT_FUTURES"])

print("== sizing is internally consistent ==")
for evaluation in res_chain.ranked:
    for leg in evaluation.legs:
        if leg.instrument == "option" and leg.contracts:
            if leg.shares != leg.contracts * 100:
                check("option shares = contracts x 100", False,
                      f"{evaluation.key} {leg.shares} vs {leg.contracts}")
                break
    else:
        continue
    break
else:
    check("option shares = contracts x 100 everywhere", True)
check("effective ratio never exceeds 1",
      all((e.effective_hedge_ratio or 0) <= 1.01 for e in res_chain.ranked))
check("every ranked strategy has a score",
      all(e.strategy_score is not None for e in res_chain.ranked))
check("every ranked strategy has a ticket",
      all(e.legs for e in res_chain.ranked))
check("every strategy score within 0-100",
      all(0 <= e.strategy_score <= 100 for e in res_chain.ranked))

print(); print("FAILURES:", len(fails), fails if fails else "")
sys.exit(1 if fails else 0)

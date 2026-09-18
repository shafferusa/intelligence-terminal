"""The playbook: named multi-leg strategies (stock, listed options, futures, total return swaps) that preview as one
package and execute as one click.

Each strategy is a list of legs with rules rather than numbers: a strike as a percentage from spot ("7% below"),
an expiry as a tenor, a size relative to the base position. `preview` resolves the rules against today's chain
and quotes every leg (bid for sells, ask for buys), adds up cash, premium, delta and incremental margin, and
computes the payoff at expiry on a price grid so the page can draw it and state max gain, max loss and
breakevens. `execute` places every leg: stock and futures orders, option orders tagged with the strategy, a
locate-and-borrow before a short sale, an RFQ dealt with the best dealer for a swap. Legs are independent
instructions (a short sale that cannot be borrowed fails alone), so the result reports each leg's outcome.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.models import Portfolio
from ..money import D, money, ZERO
from .vol import is_index

# leg: kind (STOCK | OPTION | FUTURE | TRS), side, and rules
# option rules: type C/P, strike "ATM" | "pct:<n>" (n% from spot, negative below) | "zero_cost" (call strike whose bid pays for the put)
# size: multiple of the base units (stock/TRS shares; options: contracts = units × size / 100; futures: notional-matched contracts × size)
GROUPS = [("long_hedge", "Long stock + hedge"), ("short_hedge", "Short stock + hedge"), ("directional", "Outright bullish / bearish"), ("income", "Income"),
          ("bull", "Bullish spreads"), ("bear", "Bearish spreads"), ("vol", "Volatility & range"), ("calendar", "Calendar & diagonal"), ("trs", "Total return swaps"),
          ("futures", "Futures"), ("rv", "Relative value"), ("adv", "Advanced & repair")]

S = lambda side, size=1.0: {"kind": "STOCK", "side": side, "size": size}
O = lambda side, typ, strike, size=1.0, tenor=None: {"kind": "OPTION", "side": side, "type": typ, "strike": strike, "size": size, **({"tenor": tenor} if tenor else {})}
F = lambda side, size=1.0: {"kind": "FUTURE", "side": side, "size": size}
T = lambda side, size=1.0: {"kind": "TRS", "side": side, "size": size}       # BUY = receive total return
OTHER = lambda leg: {**leg, "other": True}

# Each strategy: what you are trading in one sentence (simple), why (thesis), when it fits (best_when), the risk in words (risk),
# and the legs. Strikes are rules — ATM, pct:<n> (% from spot), zero_cost — that the preview resolves on the listed chain.
STRATEGIES: List[Dict] = [
    # ---- long stock + hedge
    {"key": "PROTECTIVE_PUT", "group": "long_hedge", "title": "Protective put", "simple": "Buy the stock and buy a put 7% below it: insurance that pays if the stock falls through the strike.",
     "thesis": "Bullish, but you want a floor under the position.", "best_when": "you expect gains but cannot afford a large drawdown (a review, a limit, a mandate).",
     "risk": "Loss limited to the fall to the strike plus the premium; upside unlimited less the premium.", "legs": [S("BUY"), O("BUY", "P", "pct:-7")],
     "notes": "Closer strikes protect more and cost more: 1–2% below is strong, 7% moderate, 15% cheap crash insurance."},
    {"key": "PROTECTIVE_PUT_TIGHT", "group": "long_hedge", "title": "Protective put, tight (2% below)", "simple": "Buy the stock and a put just 2% below it: strong protection at a high price.",
     "thesis": "You want almost no downside and will pay for it.", "best_when": "an event is close (earnings, a decision) and the stock is a large part of the book.",
     "risk": "Loss capped near 2% plus a large premium; the premium is the cost of sleeping.", "legs": [S("BUY"), O("BUY", "P", "pct:-2")]},
    {"key": "PROTECTIVE_PUT_ATM", "group": "long_hedge", "title": "Protective put, at the money", "simple": "Buy the stock and a put right at the market.",
     "thesis": "Full protection from today's price.", "best_when": "you must hold the stock but want none of the downside for a while.",
     "risk": "Loss limited to the premium; the stock must rise more than the premium before you profit.", "legs": [S("BUY"), O("BUY", "P", "ATM")]},
    {"key": "TAIL_HEDGE", "group": "long_hedge", "title": "Tail-risk hedge (17% below)", "simple": "Buy the stock and a put far below it: cheap insurance against a crash only.",
     "thesis": "Normal declines are acceptable; a catastrophe is not.", "best_when": "a long-term holding in a fragile market; the premium must stay small.",
     "risk": "You absorb the first ~17% of any fall; the put only helps beyond it.", "legs": [S("BUY"), O("BUY", "P", "pct:-17")]},
    {"key": "COLLAR", "group": "long_hedge", "title": "Collar", "simple": "Buy the stock, buy a put below, sell a call above: protection paid for by giving up upside beyond the call.",
     "thesis": "Bullish inside a band; you accept a cap to make the floor cheaper.", "best_when": "you want to hold through a risky period at little cost.",
     "risk": "Loss limited to the put strike; gains capped at the call strike; net premium small either way.", "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "C", "pct:+10")]},
    {"key": "ZERO_COST_COLLAR", "group": "long_hedge", "title": "Zero-cost collar", "simple": "A collar whose sold call pays exactly for the bought put.",
     "thesis": "Free protection in exchange for a cap.", "best_when": "you refuse to pay premium and can live with limited upside.",
     "risk": "Same as a collar: floor at the put, ceiling at the call, no cash today.", "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "C", "zero_cost")]},
    {"key": "PUT_SPREAD_HEDGE", "group": "long_hedge", "title": "Put-spread hedge", "simple": "Buy the stock, buy a put below and sell one farther below: cheaper protection that stops at the lower strike.",
     "thesis": "You want protection against a moderate fall, not a total collapse.", "best_when": "puts are expensive (high implied vol) and you expect a 5–15% correction at worst.",
     "risk": "Protected only between the two strikes; below the lower one you lose again.", "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "P", "pct:-20")]},
    {"key": "PUT_SPREAD_COLLAR", "group": "adv", "title": "Put-spread collar", "simple": "Stock, a put spread below and a short call above: a collar with cheaper, capped protection.",
     "thesis": "Trade some crash protection for a higher call strike.", "best_when": "you want more upside than a plain collar allows.",
     "risk": "Protection stops at the lower put; upside capped at the call.", "legs": [S("BUY"), O("BUY", "P", "pct:-7"), O("SELL", "P", "pct:-20"), O("SELL", "C", "pct:+15")]},
    {"key": "LONG_PLUS_CALL", "group": "directional", "title": "Long stock + call (extra upside)", "simple": "Buy the stock and a call above it: more exposure if it rallies.",
     "thesis": "Very bullish: leverage the upside.", "best_when": "you expect a sharp move up soon.",
     "risk": "Full stock downside plus the premium; not a hedge.", "legs": [S("BUY"), O("BUY", "C", "pct:+7")]},
    {"key": "LONG_FUTURES_HEDGE", "group": "futures", "title": "Long stock + temporary futures hedge", "simple": "Keep the stock and short index futures for a while: market risk off, stock-specific risk on.",
     "thesis": "Long-term bullish, short-term worried about the market.", "best_when": "you want to keep the position (tax, mandate) but cut beta for a month.",
     "risk": "You still lose if the stock underperforms the index; you miss a market rally while hedged.", "legs": [S("BUY"), F("SELL")]},
    # ---- short stock + hedge
    {"key": "PROTECTED_SHORT", "group": "short_hedge", "title": "Protected short", "simple": "Short the stock and buy a call 10% above it: the call caps the loss if the stock rallies.",
     "thesis": "Bearish, with a ceiling on the pain.", "best_when": "shorting a name that could squeeze.",
     "risk": "Loss limited to the rise to the call strike plus premium; profit is the fall less the premium.", "legs": [S("SELL"), O("BUY", "C", "pct:+10")]},
    {"key": "PROTECTED_SHORT_ATM", "group": "short_hedge", "title": "Strongly protected short", "simple": "Short the stock and buy a call just 2% above it.",
     "thesis": "Bearish with tight protection.", "best_when": "an event could gap the stock up.",
     "risk": "Loss capped near 2% plus a large premium.", "legs": [S("SELL"), O("BUY", "C", "pct:+2")]},
    {"key": "SHORT_TAIL_HEDGE", "group": "short_hedge", "title": "Short catastrophe hedge", "simple": "Short the stock and buy a call 20% above it: cheap protection against a monster rally only.",
     "thesis": "Bearish; only a squeeze would hurt.", "best_when": "the borrow is cheap and a takeover is the only real risk.",
     "risk": "You absorb the first ~20% rally.", "legs": [S("SELL"), O("BUY", "C", "pct:+20")]},
    {"key": "SHORT_COLLAR", "group": "short_hedge", "title": "Short collar", "simple": "Short the stock, buy a call above, sell a put below: protection paid for by giving up profit below the put.",
     "thesis": "Bearish inside a band.", "best_when": "you expect a moderate fall and want cheap protection.",
     "risk": "Loss limited to the call strike; profit capped at the put strike.", "legs": [S("SELL"), O("BUY", "C", "pct:+10"), O("SELL", "P", "pct:-10")]},
    {"key": "CALL_SPREAD_HEDGE", "group": "short_hedge", "title": "Call-spread hedge for a short", "simple": "Short the stock; buy a call above and sell one farther above: cheaper protection that stops at the higher strike.",
     "thesis": "Protect against a moderate rally, not a moonshot.", "best_when": "calls are expensive and a huge squeeze is unlikely.",
     "risk": "Protection only between the strikes.", "legs": [S("SELL"), O("BUY", "C", "pct:+7"), O("SELL", "C", "pct:+20")]},
    {"key": "SHORT_PLUS_PUT", "group": "directional", "title": "Short stock + put (extra downside)", "simple": "Short the stock and buy a put below it: more exposure if it falls.",
     "thesis": "Very bearish.", "best_when": "you expect a sharp move down soon.",
     "risk": "Full short-squeeze risk plus the premium; not a hedge.", "legs": [S("SELL"), O("BUY", "P", "pct:-7")]},
    # ---- outright bullish / bearish with options
    {"key": "LONG_CALL", "group": "directional", "title": "Long call", "simple": "Buy a call: the right to buy the stock at the strike. Profit if it rallies past the strike plus premium.",
     "thesis": "Bullish with a known maximum loss.", "best_when": "you want upside without funding the shares, and expect a move before expiry.",
     "risk": "Lose the premium if the stock stays below the strike; time decay works against you.", "legs": [O("BUY", "C", "pct:+3")]},
    {"key": "LONG_PUT", "group": "directional", "title": "Long put", "simple": "Buy a put: the right to sell at the strike. Profit if the stock falls below the strike minus premium.",
     "thesis": "Bearish with a known maximum loss.", "best_when": "you expect a fall and do not want the borrow of a short.",
     "risk": "Lose the premium if the stock stays above the strike.", "legs": [O("BUY", "P", "pct:-3")]},
    {"key": "SYNTHETIC_LONG_OPTIONS", "group": "directional", "title": "Synthetic long (call + short put)", "simple": "Buy a call and sell a put at the same strike: behaves like owning the stock, for little cash.",
     "thesis": "Long exposure with margin instead of capital.", "best_when": "you want stock-like exposure without the shares.",
     "risk": "Same as long stock: unlimited downside below the strike.", "legs": [O("BUY", "C", "ATM"), O("SELL", "P", "ATM")]},
    {"key": "SYNTHETIC_SHORT_OPTIONS", "group": "directional", "title": "Synthetic short (put + short call)", "simple": "Buy a put and sell a call at the same strike: behaves like a short, without borrowing.",
     "thesis": "Short exposure without a locate.", "best_when": "the stock is hard to borrow.",
     "risk": "Same as a short: unlimited loss on a rally.", "legs": [O("BUY", "P", "ATM"), O("SELL", "C", "ATM")]},
    {"key": "RISK_REVERSAL", "group": "directional", "title": "Risk reversal (bullish)", "simple": "Sell a put below and buy a call above: cheap or free bullish exposure with downside if the stock falls.",
     "thesis": "Bullish; the put you sell pays for the call.", "best_when": "puts are dear relative to calls (skew is steep).",
     "risk": "Below the put strike you own the stock, effectively; above the call you profit.", "legs": [O("SELL", "P", "pct:-7"), O("BUY", "C", "pct:+7")]},
    # ---- income
    {"key": "COVERED_CALL", "group": "income", "title": "Covered call", "simple": "Own the stock and sell a call 10% above it: collect the premium; above the strike you give up the gain.",
     "thesis": "Moderately bullish or flat: harvest premium.", "best_when": "implied vol is high and you would be happy to sell at the strike.",
     "risk": "Not a downside hedge: you keep the full fall, less the premium.", "legs": [S("BUY"), O("SELL", "C", "pct:+10")]},
    {"key": "PARTIAL_COVERED_CALL", "group": "income", "title": "Partial covered call", "simple": "Own the stock and write calls on only half of it.",
     "thesis": "Some premium, some uncapped upside.", "best_when": "you like the stock but want a little income.",
     "risk": "Half the position is capped; the whole position keeps the downside.", "legs": [S("BUY"), O("SELL", "C", "pct:+10", 0.5)]},
    {"key": "CASH_SECURED_PUT", "group": "income", "title": "Cash-secured put", "simple": "Sell a put below the market with cash set aside: get paid to wait to buy the stock lower.",
     "thesis": "You would own the stock, but only cheaper.", "best_when": "you want to buy on a dip and vol is high.",
     "risk": "If it falls hard you buy at the strike and keep falling with it, less the premium.", "legs": [O("SELL", "P", "pct:-7")]},
    {"key": "COVERED_STRANGLE", "group": "income", "title": "Covered strangle", "simple": "Own the stock, sell a call above and a put below: double premium; you may be assigned more stock lower.",
     "thesis": "Range-bound stock you want more of if it dips.", "best_when": "high vol, a name you are happy to add to.",
     "risk": "Large exposure below the put (you would own twice as much); capped above the call.", "legs": [S("BUY"), O("SELL", "C", "pct:+10"), O("SELL", "P", "pct:-10")]},
    {"key": "JADE_LIZARD", "group": "income", "title": "Jade lizard", "simple": "Sell a put below and a call spread above: premium with no risk on the upside if the credit exceeds the call-spread width.",
     "thesis": "Neutral to bullish premium collection.", "best_when": "vol is rich and you do not fear a rally.",
     "risk": "Downside like a short put; the upside risk is bounded by the spread width less the credit.", "legs": [O("SELL", "P", "pct:-7"), O("SELL", "C", "pct:+7"), O("BUY", "C", "pct:+12")]},
    # ---- bullish spreads
    {"key": "BULL_CALL_SPREAD", "group": "bull", "title": "Bull call spread", "simple": "Buy a call near the market and sell one farther above: cheaper than a call; profit capped at the higher strike.",
     "thesis": "Bullish to a target.", "best_when": "you expect a move up to a level, not beyond.",
     "risk": "Lose the net premium if the stock stays below the lower strike.", "legs": [O("BUY", "C", "pct:+3"), O("SELL", "C", "pct:+13")]},
    {"key": "BULL_PUT_SPREAD", "group": "bull", "title": "Bull put spread", "simple": "Sell a put below and buy one farther below: a credit if the stock stays up; the bought put caps the damage.",
     "thesis": "Bullish or neutral, paid to wait.", "best_when": "you expect support to hold.",
     "risk": "Max loss is the width between strikes less the credit.", "legs": [O("SELL", "P", "pct:-7"), O("BUY", "P", "pct:-13")]},
    {"key": "CALL_RATIO_SPREAD", "group": "adv", "title": "Call ratio spread (1×2)", "simple": "Buy one call and sell two farther above: cheap or free upside to the short strike, danger beyond it.",
     "thesis": "Mildly bullish: a drift up, not a rocket.", "best_when": "you expect a grind higher and want it for free.",
     "risk": "Above the short strike losses grow without limit (naked call).", "legs": [O("BUY", "C", "pct:+3"), O("SELL", "C", "pct:+12", 2.0)]},
    # ---- bearish spreads
    {"key": "BEAR_PUT_SPREAD", "group": "bear", "title": "Bear put spread", "simple": "Buy a put near the market and sell one farther below: cheaper than a put; profit capped at the lower strike.",
     "thesis": "Bearish to a target.", "best_when": "you expect a fall to a level.",
     "risk": "Lose the net premium if the stock stays above the upper strike.", "legs": [O("BUY", "P", "pct:-3"), O("SELL", "P", "pct:-13")]},
    {"key": "BEAR_CALL_SPREAD", "group": "bear", "title": "Bear call spread", "simple": "Sell a call above and buy one farther above: a credit if the stock stays below the sold strike.",
     "thesis": "Bearish or neutral, paid to wait.", "best_when": "you expect resistance to hold.",
     "risk": "Max loss is the width less the credit.", "legs": [O("SELL", "C", "pct:+7"), O("BUY", "C", "pct:+13")]},
    # ---- volatility and range
    {"key": "LONG_STRADDLE", "group": "vol", "title": "Long straddle", "simple": "Buy a call and a put at the money: profit from a big move either way.",
     "thesis": "A large move is coming; direction unknown.", "best_when": "before an event, when implied vol is still cheap.",
     "risk": "Both premiums decay if nothing happens; needs a move bigger than the combined premium.", "legs": [O("BUY", "C", "ATM"), O("BUY", "P", "ATM")]},
    {"key": "LONG_STRANGLE", "group": "vol", "title": "Long strangle", "simple": "Buy a call above and a put below: a cheaper straddle that needs a bigger move.",
     "thesis": "Big move coming, cheaply.", "best_when": "same as the straddle with less premium at risk.",
     "risk": "Both premiums lost inside the strikes.", "legs": [O("BUY", "C", "pct:+7"), O("BUY", "P", "pct:-7")]},
    {"key": "SHORT_STRADDLE", "group": "vol", "title": "Short straddle", "simple": "Sell a call and a put at the money: collect a lot of premium; lose if the stock moves far either way.",
     "thesis": "Nothing will happen.", "best_when": "implied vol is far above realised and no event is due.",
     "risk": "Unlimited loss on a big move: a learning exercise best defined with wings (iron butterfly).", "legs": [O("SELL", "C", "ATM"), O("SELL", "P", "ATM")]},
    {"key": "SHORT_STRANGLE", "group": "vol", "title": "Short strangle", "simple": "Sell a call above and a put below: premium with a wider comfort zone than the straddle.",
     "thesis": "The stock stays in a range.", "best_when": "rich vol, quiet calendar.",
     "risk": "Unlimited beyond either strike.", "legs": [O("SELL", "C", "pct:+8"), O("SELL", "P", "pct:-8")]},
    {"key": "IRON_CONDOR", "group": "vol", "title": "Iron condor", "simple": "Sell a put and a call around the market, buy a put and a call farther out: premium with capped loss.",
     "thesis": "Range-bound, defined risk.", "best_when": "you want the short strangle's income with a known worst case.",
     "risk": "Max loss is the wing width less the credit, on either side.", "legs": [O("BUY", "P", "pct:-13"), O("SELL", "P", "pct:-7"), O("SELL", "C", "pct:+7"), O("BUY", "C", "pct:+13")]},
    {"key": "REVERSE_IRON_CONDOR", "group": "vol", "title": "Reverse iron condor", "simple": "Buy a put and a call near the market, sell one farther out on each side: a cheaper bet on a big move, capped.",
     "thesis": "A move is coming; you want it defined.", "best_when": "a strangle is too expensive.",
     "risk": "Lose the net premium if the stock stays in the middle; gains capped at the outer strikes.", "legs": [O("SELL", "P", "pct:-13"), O("BUY", "P", "pct:-5"), O("BUY", "C", "pct:+5"), O("SELL", "C", "pct:+13")]},
    {"key": "IRON_BUTTERFLY", "group": "vol", "title": "Iron butterfly", "simple": "Sell a call and a put at the money, buy a call and a put farther out: a short straddle with wings.",
     "thesis": "The stock pins near today's price.", "best_when": "rich vol, a range you trust.",
     "risk": "Max loss is the wing width less the (large) credit.", "legs": [O("BUY", "P", "pct:-8"), O("SELL", "P", "ATM"), O("SELL", "C", "ATM"), O("BUY", "C", "pct:+8")]},
    {"key": "LONG_CALL_BUTTERFLY", "group": "vol", "title": "Long call butterfly", "simple": "Buy one call below, sell two at the target, buy one above: a cheap bet that the stock lands near the middle strike.",
     "thesis": "A specific price target with small premium at risk.", "best_when": "you have a target and time.",
     "risk": "Lose the small debit outside the wings; max gain at the middle strike.", "legs": [O("BUY", "C", "pct:-5"), O("SELL", "C", "ATM", 2.0), O("BUY", "C", "pct:+5")]},
    {"key": "BROKEN_WING_BUTTERFLY", "group": "vol", "title": "Broken-wing put butterfly", "simple": "A put butterfly with the far wing moved farther out: often a credit, with risk only on a large fall.",
     "thesis": "Mildly bearish to neutral, paid to enter.", "best_when": "you expect a drift down toward the middle strike.",
     "risk": "Beyond the far wing the loss equals the extra width less the credit.", "legs": [O("BUY", "P", "pct:-3"), O("SELL", "P", "pct:-8", 2.0), O("BUY", "P", "pct:-16")]},
    # ---- calendar & diagonal (two expiries)
    {"key": "CALENDAR_SPREAD", "group": "calendar", "title": "Calendar spread", "simple": "Sell a near-dated call and buy a later-dated call at the same strike: profit from time decay on the short leg while the stock sits near the strike.",
     "thesis": "Quiet now, movement later.", "best_when": "near-term vol is rich versus later months.",
     "risk": "Lose the net debit if the stock runs far from the strike before the near expiry.", "legs": [O("SELL", "C", "ATM", 1.0, 1), O("BUY", "C", "ATM", 1.0, 3)]},
    {"key": "DIAGONAL_PMCC", "group": "calendar", "title": "Poor man's covered call (diagonal)", "simple": "Buy a long-dated in-the-money call instead of the stock and sell short-dated calls against it.",
     "thesis": "A covered call with less capital.", "best_when": "you want covered-call income on a stock you cannot afford in size.",
     "risk": "The long call can lose most of its value if the stock falls; short calls cap the upside each cycle.", "legs": [O("BUY", "C", "pct:-10", 1.0, 12), O("SELL", "C", "pct:+7", 1.0, 1)]},
    {"key": "PUT_CALENDAR", "group": "calendar", "title": "Put calendar", "simple": "Sell a near put and buy a later put at the same strike below the market: a cheap way to own longer-dated protection.",
     "thesis": "Protection later, financed by decay now.", "best_when": "you want a put for next quarter but not this month.",
     "risk": "A fast drop before the near expiry hurts; net debit at risk.", "legs": [O("SELL", "P", "pct:-7", 1.0, 1), O("BUY", "P", "pct:-7", 1.0, 3)]},
    # ---- TRS
    {"key": "SYNTHETIC_LONG_TRS", "group": "trs", "title": "Synthetic long (receive TRS)", "simple": "Receive the stock's total return from a dealer in exchange for funding plus a spread: long without owning the shares.",
     "thesis": "Long exposure with financing built in.", "best_when": "you want leverage or cannot hold the shares directly.",
     "risk": "Full stock downside plus the spread; the dealer's credit.", "legs": [T("BUY")]},
    {"key": "SYNTHETIC_SHORT_TRS", "group": "trs", "title": "Synthetic short (pay TRS)", "simple": "Pay the stock's total return: short without borrowing the shares yourself.",
     "thesis": "Short exposure via a dealer.", "best_when": "the borrow is scarce or you want one line for a short.",
     "risk": "Unlimited on a rally, like any short; the dealer's credit.", "legs": [T("SELL")]},
    {"key": "PROTECTED_SYNTHETIC_LONG", "group": "trs", "title": "Protected synthetic long", "simple": "Receive the TRS and buy a put below: stock plus put, without the shares.",
     "thesis": "Leveraged long with a floor.", "best_when": "you want the synthetic long but with insurance.",
     "risk": "Loss limited to the put strike plus premium and spread.", "legs": [T("BUY"), O("BUY", "P", "pct:-7")]},
    {"key": "PROTECTED_SYNTHETIC_SHORT", "group": "trs", "title": "Protected synthetic short", "simple": "Pay the TRS and buy a call above: short stock plus call, without the borrow.",
     "thesis": "Synthetic short with a ceiling on losses.", "best_when": "you short via swap and fear a squeeze.",
     "risk": "Loss limited to the call strike.", "legs": [T("SELL"), O("BUY", "C", "pct:+10")]},
    {"key": "CORE_PLUS_OVERLAY", "group": "trs", "title": "Long stock, protected, plus TRS overlay", "simple": "Own the stock with a put, and add extra bullish exposure through a received TRS.",
     "thesis": "Core position protected, extra conviction on top.", "best_when": "high conviction, limited cash.",
     "risk": "The overlay is unprotected: it carries full downside.", "legs": [S("BUY"), O("BUY", "P", "pct:-7"), T("BUY")]},
    {"key": "DELTA_NEUTRAL_TRS", "group": "trs", "title": "Delta-neutral TRS (financing basis)", "simple": "Receive the TRS and short the same shares: direction cancels; what is left is financing and dividends.",
     "thesis": "Earn or pay the basis, not the direction.", "best_when": "you want to learn what a swap really costs.",
     "risk": "Small: spread, borrow cost and dividend timing.", "legs": [T("BUY"), S("SELL")]},
    # ---- futures
    {"key": "LONG_FUTURES", "group": "futures", "title": "Bullish through futures", "simple": "Buy index futures: linear market exposure on margin, no strike, no premium.",
     "thesis": "Bullish on the market.", "best_when": "you want beta quickly and cheaply.",
     "risk": "Full downside, settled daily as variation margin.", "legs": [F("BUY")]},
    {"key": "SHORT_FUTURES", "group": "futures", "title": "Bearish through futures", "simple": "Sell index futures: linear short exposure on margin.",
     "thesis": "Bearish on the market.", "best_when": "you want to hedge a book or bet against the index.",
     "risk": "Unlimited on a rally, settled daily.", "legs": [F("SELL")]},
    {"key": "LONG_FUTURES_PROTECTED", "group": "futures", "title": "Long futures with crash protection", "simple": "Buy index futures and a put on the index below the level you can tolerate.",
     "thesis": "Leveraged long with a floor.", "best_when": "you want beta with insurance.",
     "risk": "Loss limited to the put strike plus premium.", "legs": [F("BUY"), O("BUY", "P", "pct:-7")]},
    {"key": "SHORT_FUTURES_PROTECTED", "group": "futures", "title": "Short futures with rally protection", "simple": "Sell index futures and buy a call above.",
     "thesis": "Short beta with a ceiling.", "best_when": "hedging a book without unlimited squeeze risk.",
     "risk": "Loss limited to the call strike plus premium.", "legs": [F("SELL"), O("BUY", "C", "pct:+10")]},
    {"key": "BETA_HEDGED_LONG", "group": "futures", "title": "Beta-hedged long (stock vs index)", "simple": "Buy the stock and short index futures in notional: you keep only the stock's own performance.",
     "thesis": "The stock beats the market; the market itself is not the bet.", "best_when": "a stock-specific view in an uncertain market.",
     "risk": "You lose if the stock lags the index, whatever the index does.", "legs": [S("BUY"), F("SELL")]},
    # ---- relative value
    {"key": "RV_LONG_OTHER_SHORT_UNDER", "group": "rv", "title": "Pair: long the other name, short this one", "simple": "Long the second name, short the underlying, same notional: profit from the spread between them, not the market.",
     "thesis": "The second name outperforms the first (QQQ vs SPY, MSFT vs SPY).", "best_when": "you have a relative view and want no beta.",
     "risk": "The spread can widen against you without limit; a short needs a borrow.", "legs": [OTHER(S("BUY")), S("SELL")]},
    {"key": "RV_LONG_UNDER_SHORT_OTHER", "group": "rv", "title": "Pair: long this one, short the other name", "simple": "Long the underlying, short the second name.",
     "thesis": "The underlying outperforms the second name.", "best_when": "the opposite relative view.",
     "risk": "As above.", "legs": [S("BUY"), OTHER(S("SELL"))]},
    {"key": "RV_PROTECTED", "group": "rv", "title": "Protected single-stock pair", "simple": "Long the second name with a put on it, short the underlying for market beta.",
     "thesis": "A stock-specific long with company risk insured and market risk hedged.", "best_when": "the long is a single stock that could gap.",
     "risk": "Spread risk plus the premium.", "legs": [OTHER(S("BUY")), OTHER(O("BUY", "P", "pct:-7")), S("SELL")]},
    # ---- advanced & repair
    {"key": "STOCK_REPAIR", "group": "adv", "title": "Stock repair", "simple": "On a stock you own that has fallen: buy one call near the market and sell two above it, for about zero cost — you break even sooner, and give up gains above the short strike.",
     "thesis": "Get back to break-even faster on a loser.", "best_when": "you are underwater and expect a partial recovery, not a full rally.",
     "risk": "No extra downside; upside capped at the short strike (the second short call is covered by the stock).", "legs": [S("BUY"), O("BUY", "C", "ATM"), O("SELL", "C", "pct:+10", 2.0)]},
    {"key": "SEAGULL", "group": "adv", "title": "Seagull (bullish)", "simple": "Buy a call spread and sell a put below: cheap or free upside to the call spread's top, with downside below the put.",
     "thesis": "Bullish to a target, financed by taking the downside.", "best_when": "you would buy the stock lower anyway.",
     "risk": "Below the put you are effectively long the stock; gains capped at the upper call.", "legs": [O("SELL", "P", "pct:-8"), O("BUY", "C", "pct:+3"), O("SELL", "C", "pct:+12")]},
    {"key": "STRAP", "group": "adv", "title": "Strap (bullish straddle)", "simple": "Buy two calls and one put at the money: a straddle that leans bullish.",
     "thesis": "Big move likely, more likely up.", "best_when": "an event with an upside skew of outcomes.",
     "risk": "All premiums lost if nothing moves.", "legs": [O("BUY", "C", "ATM", 2.0), O("BUY", "P", "ATM")]},
]
BY_KEY = {s["key"]: s for s in STRATEGIES}


def _f(x) -> float:
    return float(x) if x is not None else 0.0


class Playbook:
    def __init__(self, world):
        self.w = world

    # ------------------------------------------------------------------ catalogue
    def catalogue(self) -> Dict:
        return {"groups": GROUPS, "strategies": [{k: v for k, v in s.items()} for s in STRATEGIES]}

    # ------------------------------------------------------------------ resolution
    def _chain(self, under: str, tenor_months: int) -> Dict:
        from ..engines import otc_pricing as px
        w = self.w
        want = w.calendar.roll(px._add_months(w.current_date, tenor_months)).isoformat()
        return w.options.chain(under, want)

    def _strike_for(self, rule: str, S: float, strikes: List[float]) -> float:
        if not strikes:
            raise ValueError("no strikes listed")
        if rule == "ATM":
            target = S
        elif rule.startswith("pct:"):
            target = S * (1 + float(rule[4:]) / 100.0)
        else:
            target = S
        return min(strikes, key=lambda k: (abs(k - target), k))

    def _zero_cost_call(self, chain: Dict, put_premium: float, S: float) -> Optional[float]:
        """The call strike above spot whose bid comes closest to the put's ask."""
        best = None
        for r in chain["rows"]:
            if r["strike"] <= S or not r.get("C"):
                continue
            d = abs(_f(r["C"]["bid"]) - put_premium)
            if best is None or d < best[0]:
                best = (d, r["strike"])
        return best[1] if best else None

    def preview(self, pf: Portfolio, key: str, params: Dict) -> Dict:
        from ..world import CommandError
        from ..engines.options import contract_id as cid_of
        w = self.w
        strat = BY_KEY.get(str(key).upper())
        if strat is None:
            raise CommandError(f"unknown strategy {key}")
        under = str(params.get("underlying", "SPY")).upper()
        other = str(params.get("other", "QQQ")).upper()
        units = int(_f(params.get("units", 1000)))
        if units <= 0:
            raise CommandError("units must be positive")
        tenor = max(1, int(_f(params.get("tenor_months", 3))))
        overrides = params.get("strikes") or {}            # leg index -> pct or absolute strike
        legs_out: List[Dict] = []
        needs_option = any(l["kind"] == "OPTION" for l in strat["legs"])
        chains: Dict[tuple, Dict] = {}
        levels: Dict[str, float] = {}
        tenors_used = set()

        def level(sym: str) -> float:
            if sym not in levels:
                levels[sym] = w.options.underlying_level(sym) if (is_index(sym) or sym in w.securities) else _f(w.market.last_bar(sym).close)
            return levels[sym]

        def chain(sym: str, months: Optional[int] = None) -> Dict:
            key = (sym, int(months or tenor))
            if key not in chains:
                chains[key] = self._chain(sym, key[1])
            return chains[key]

        def bar(sym: str):
            return w.market.last_bar(sym)

        fut = w.market.front_contract("ES")
        put_premium_for_zero_cost = 0.0
        expiry = None
        for i, leg in enumerate(strat["legs"]):
            sym = other if leg.get("other") else under
            row: Dict = {"index": i, "kind": leg["kind"], "side": leg["side"], "symbol": sym}
            if leg["kind"] == "STOCK":
                b = bar(sym)
                qty = int(round(units * leg.get("size", 1.0)))
                px_ = _f(b.ask if leg["side"] == "BUY" else b.bid)
                row.update({"security_id": sym, "quantity": qty, "price": px_, "cash": -qty * px_ if leg["side"] == "BUY" else qty * px_,
                            "delta_shares": qty if leg["side"] == "BUY" else -qty, "what": f"{'buy' if leg['side'] == 'BUY' else 'short'} {qty:,} {sym} at {px_:,.2f}"})
                if leg["side"] == "SELL":
                    row["what"] += " (locate and borrow first)"
            elif leg["kind"] == "TRS":
                px_ = level(sym)
                qty = int(round(units * leg.get("size", 1.0)))
                fair = w.otc.fair("TRS", {"security_id": sym, "units": qty, "tenor_years": max(1, round(tenor / 12)), "receiver": leg["side"] == "BUY"})
                row.update({"security_id": sym, "quantity": qty, "price": px_, "cash": 0.0, "notional": qty * px_, "spread_bps": fair["mid"],
                            "delta_shares": qty if leg["side"] == "BUY" else -qty,
                            "what": f"{'receive' if leg['side'] == 'BUY' else 'pay'} total return on {qty:,} {sym} (notional {qty * px_:,.0f}) vs policy {'+' if leg['side'] == 'BUY' else '−'} ~{fair['mid']:.0f}bp; dealers quote on execute"})
            elif leg["kind"] == "FUTURE":
                if fut is None:
                    raise CommandError("no index future listed")
                fb = bar(fut.id)
                spy = level("SPY")
                n = max(1, int(round(units * leg.get("size", 1.0) * level(under) / (_f(fb.close) * fut.multiplier))))
                row.update({"security_id": fut.id, "quantity": n, "price": _f(fb.close), "cash": 0.0, "margin": _f(w.securities[fut.id].initial_margin if hasattr(w.securities[fut.id], 'initial_margin') else 0) * n,
                            "notional": n * _f(fb.close) * fut.multiplier, "delta_shares": (n * fut.multiplier * _f(fb.close) / spy) * (1 if leg["side"] == "BUY" else -1),
                            "what": f"{'buy' if leg['side'] == 'BUY' else 'sell'} {n} {fut.id} ({fut.multiplier} × {_f(fb.close):,.2f} = {n * fut.multiplier * _f(fb.close):,.0f} notional, margin instead of cash)"})
            elif leg["kind"] == "OPTION":
                leg_tenor = int(leg["tenor"]) if leg.get("tenor") else tenor
                tenors_used.add(leg_tenor)
                ch = chain(sym, leg_tenor)
                S_ = _f(ch["level"])
                strikes = [r["strike"] for r in ch["rows"] if r.get(leg["type"])]
                rule = leg["strike"]
                ov = overrides.get(str(i))
                if ov not in (None, ""):
                    rule = f"pct:{float(ov)}" if abs(float(ov)) < 60 else None
                    K = self._strike_for(rule, S_, strikes) if rule else min(strikes, key=lambda k: abs(k - float(ov)))
                elif rule == "zero_cost":
                    K = self._zero_cost_call(ch, put_premium_for_zero_cost, S_) or self._strike_for("pct:+10", S_, strikes)
                else:
                    K = self._strike_for(rule, S_, strikes)
                cid = cid_of(sym, date.fromisoformat(ch["expiry"]), leg["type"], K)
                if cid not in w.securities:
                    raise CommandError(f"contract {cid} is not listed")
                q = w.options.quote(w.securities[cid])
                contracts = max(1, int(round(units * leg.get("size", 1.0) / 100)))
                px_ = _f(q["ask"] if leg["side"] == "BUY" else q["bid"])
                mult = 100
                cash = -px_ * mult * contracts if leg["side"] == "BUY" else px_ * mult * contracts
                if leg["side"] == "BUY" and leg["type"] == "P":
                    put_premium_for_zero_cost = px_
                sign = 1 if leg["side"] == "BUY" else -1
                row.update({"security_id": cid, "quantity": contracts, "price": px_, "cash": cash, "strike": K, "expiry": ch["expiry"], "type": leg["type"], "tenor_months": leg_tenor,
                            "pct_from_spot": (K / S_ - 1) * 100, "iv": q["iv"], "delta": q["delta"], "delta_shares": sign * contracts * mult * _f(q["delta"]),
                            "vega": sign * contracts * mult * _f(q["vega"]), "theta": sign * contracts * mult * _f(q["theta"]),
                            "what": f"{'buy' if leg['side'] == 'BUY' else 'sell'} {contracts} {sym} {ch['expiry']} {K:g} {'call' if leg['type'] == 'C' else 'put'} at {px_:.2f} ({(K / S_ - 1) * 100:+.1f}% from spot)"})
                expiry = ch["expiry"]
            legs_out.append(row)
        # option margin for the package
        extra = [(w.securities[l["security_id"]], D(l["quantity"] if l["side"] == "BUY" else -l["quantity"])) for l in legs_out if l["kind"] == "OPTION"]
        inc_margin = ZERO
        if extra:
            base, _ = w.options.margin_requirement(pf)
            after, _ = w.options.margin_requirement(pf, extra=extra)
            inc_margin = max(ZERO, after - base)
        cash = sum(l["cash"] for l in legs_out)
        premium = sum(l["cash"] for l in legs_out if l["kind"] == "OPTION")
        S0 = level(under)
        payoff = self._payoff(legs_out, S0, level, other)
        totals = {"net_cash_today": cash, "net_option_premium": premium, "delta_shares": sum(l.get("delta_shares", 0.0) for l in legs_out),
                  "dollar_delta": sum(l.get("delta_shares", 0.0) for l in legs_out) * S0, "vega": sum(l.get("vega", 0.0) for l in legs_out),
                  "theta": sum(l.get("theta", 0.0) for l in legs_out), "incremental_option_margin": inc_margin,
                  "futures_margin": sum(l.get("margin", 0.0) for l in legs_out if l["kind"] == "FUTURE"), "spot": S0, "expiry": expiry, "underlying": under, "units": units}
        if len(tenors_used) > 1:
            payoff["grid_note"] = payoff.get("grid_note", "") + " · legs expire on different dates: every leg is valued at intrinsic on the near expiry, so this understates the later leg's remaining value"
        return {"strategy": {k: v for k, v in strat.items() if k != "legs"}, "params": {"underlying": under, "other": other, "units": units, "tenor_months": tenor},
                "legs": legs_out, "totals": totals, **payoff}

    def _payoff(self, legs: List[Dict], S0: float, level, other: str) -> Dict:
        """P&L at expiry versus the underlying's price, on a ±35% grid; the second name is assumed to move with it."""
        grid = [S0 * (0.65 + 0.7 * i / 70) for i in range(71)]
        pts = []
        for ST in grid:
            pnl = 0.0
            for l in legs:
                sign = 1 if l["side"] == "BUY" else -1
                if l["kind"] in ("STOCK", "TRS"):
                    base = level(l["symbol"])
                    pnl += sign * l["quantity"] * (ST / S0 * base - base)
                elif l["kind"] == "FUTURE":
                    pnl += sign * l["quantity"] * 50 * (ST / S0 * l["price"] - l["price"])
                elif l["kind"] == "OPTION":
                    base = level(l["symbol"])
                    ST_sym = ST / S0 * base
                    intrinsic = max(0.0, ST_sym - l["strike"]) if l["type"] == "C" else max(0.0, l["strike"] - ST_sym)
                    pnl += sign * l["quantity"] * 100 * intrinsic + l["cash"]
            pts.append([round(ST, 2), round(pnl, 2)])
        vals = [p[1] for p in pts]
        unlimited_up = legs and any(l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "BUY" for l in legs) and not any(l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "SELL" for l in legs)
        unlimited_down = legs and any(l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "SELL" for l in legs) and not any(l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "BUY" for l in legs)
        # naked short options are unlimited too
        if any(l["kind"] == "OPTION" and l["side"] == "SELL" and l["type"] == "C" for l in legs) and not any((l["kind"] == "OPTION" and l["type"] == "C" and l["side"] == "BUY") or (l["kind"] in ("STOCK", "TRS", "FUTURE") and l["side"] == "BUY") for l in legs):
            unlimited_down = True
        breakevens = []
        for (a, pa), (b, pb) in zip(pts, pts[1:]):
            if (pa < 0 <= pb) or (pa >= 0 > pb):
                breakevens.append(round(a + (b - a) * (0 - pa) / (pb - pa) if pb != pa else a, 2))
        return {"payoff": pts, "max_gain": None if unlimited_up else max(vals), "max_loss": None if unlimited_down else min(vals), "breakevens": breakevens,
                "grid_note": "P&L at expiry versus the underlying's price (±35%); swaps and futures treated as linear, a second name assumed to move with the underlying"}

    # ------------------------------------------------------------------ execution
    def execute(self, pf: Portfolio, key: str, params: Dict) -> Dict:
        from ..world import CommandError
        w = self.w
        pv = self.preview(pf, key, params)
        results = []
        tag = pv["strategy"]["key"]
        for l in pv["legs"]:
            try:
                if l["kind"] == "STOCK":
                    if l["side"] == "SELL" and (l["symbol"] not in pf.positions or pf.positions[l["symbol"]].quantity < l["quantity"]):
                        need = l["quantity"] - int(max(0, pf.positions[l["symbol"]].quantity) if l["symbol"] in pf.positions else 0)
                        loc = w.request_locate(pf.id, l["symbol"], need)
                        got = int(min(need, _f(loc.available)))
                        if got <= 0:
                            raise CommandError(f"no {l['symbol']} available to borrow")
                        loan = w.borrow_securities(pf.id, loc.id, got)
                        qty = got + (l["quantity"] - need)
                        o = w.place_order(pf.id, l["symbol"], "SELL", qty, "MARKET", None, None, "GTC", tag)
                        results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [loc.id, loan.id, o.id], "note": f"borrowed {got:,}, sold {qty:,}" + ("" if got == need else f" (only {got:,} of {need:,} could be borrowed)")})
                    else:
                        o = w.place_order(pf.id, l["symbol"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                        results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "FUTURE":
                    o = w.place_order(pf.id, l["security_id"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "OPTION":
                    o = w.place_order(pf.id, l["security_id"], l["side"], l["quantity"], "MARKET", None, None, "GTC", tag)
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [o.id], "note": o.status})
                elif l["kind"] == "TRS":
                    r = w.request_quote(pf.id, "TRS", {"security_id": l["symbol"], "units": l["quantity"], "tenor_years": max(1, round(pv["params"]["tenor_months"] / 12)), "receiver": l["side"] == "BUY"})
                    live = [q for q in r.quotes if not q.get("declined")]
                    if not live:
                        raise CommandError("no dealer quoted the swap")
                    best = min(live, key=lambda q: q["cost_vs_mid"])
                    t = w.execute_rfq(pf.id, r.id, best["dealer"])
                    results.append({"leg": l["index"], "ok": True, "what": l["what"], "ids": [r.id, t.id], "note": f"dealt with {best['dealer']} at {best['level']:.1f}bp (cost vs mid {best['cost_vs_mid']:,.0f})"})
            except CommandError as e:
                results.append({"leg": l["index"], "ok": False, "what": l["what"], "ids": [], "note": str(e)})
        w.flush()
        return {"strategy": pv["strategy"], "results": results, "all_ok": all(r["ok"] for r in results), "preview": pv}

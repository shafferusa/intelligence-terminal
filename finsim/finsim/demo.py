"""A week in the life of a once-per-day book, runnable from the CLI.

Every evening the player leaves instructions; every morning the daily process
runs them against a new session and writes a briefing. This walks the spec's
17-step first build under that cycle and adds a futures position, then proves
the ledger balances, NAV explains, and the event store replays identically.
"""
from __future__ import annotations

from datetime import date

from .domain.events import E
from .engines.ledger import account_name
from .money import D
from .store import EventStore
from .world import World


def _p(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _entries(w: World, pid: str, since_seq: int, limit: int = 12) -> None:
    led = w.ledgers[pid]
    n = 0
    for e in led.entries:
        if int(e.event_id[3:]) <= since_seq:
            continue
        n += 1
        if n > limit:
            print(f"  ... ({len([x for x in led.entries if int(x.event_id[3:]) > since_seq]) - limit} more)")
            break
        print(f"  {e.id}  {e.date}  {e.memo}   [cause {e.cause_id}]")
        for l in e.lines:
            side = f"Dr {l.debit:>14,.2f}" if l.debit else f"{'':17}Cr {l.credit:>14,.2f}"
            print(f"      {l.account:<9} {account_name(l.account):<46} {side}   {l.security_id or ''}")


def _briefing(pf) -> None:
    b = pf.briefings[-1]
    print(f"  BRIEFING {b['date']}: NAV {b['nav']:,.2f}  today {b['day_pnl']:+,.2f} ({b['day_return']:+.2%})")
    print("   P&L: " + ", ".join(f"{k} {v:+,.0f}" for k, v in b["pnl_buckets"].items() if v))
    print("   market: " + "; ".join(f"{m['label']} {m['value']*100:+.2f}%" if m["fmt"] == "pct" else f"{m['label']} {m['value']:+.1f}bp" for m in b["market"][:5]))
    for a in b["attention"][:5]:
        print(f"   [{a['severity']}] {a['text']}")
    for n in b["news"][:3]:
        print(f"   news: {n['headline']}")


def run_demo(seed: int = 42, start: str = "2026-01-05") -> World:
    store = EventStore(":memory:")
    w = World.create("demo", "Once-per-day walkthrough", seed, date.fromisoformat(start), store=store)
    _p("1. A $10,000,000 sandbox book (career saves run on the real clock; sandbox advances on demand)")
    pf = w.create_portfolio("Demo Book", "PERSONAL", D(10_000_000))
    pid = pf.id
    print(f"  {pid}: cash {pf.cash['USD'].balance:,.2f}, date {w.current_date}, {len(w.securities)} securities incl. "
          f"{sum(1 for s in w.securities.values() if s.is_future and not s.expired)} listed futures")
    _briefing(pf)

    _p("2. The market on the evening of day 1")
    cas = sorted((ca for ca in w.corporate_actions.values() if ca.status == "DECLARED"), key=lambda c: c.ex_date)
    ca = cas[0]
    tkr = ca.security_id
    for t in [tkr, "SPY", "UST-10Y"]:
        b = w.market.last_bar(t)
        print(f"  {t:<8} last {b.close:>10}  bid {b.bid:>10}  ask {b.ask:>10}  vol {b.volume:>12,}")
    front = w.market.front_contract("CL")
    cl = sorted((s for s in w.securities.values() if s.underlying == "CL" and not s.expired), key=lambda s: s.contract_month)[3]
    print(f"  WTI spot {w.market.spot('CL'):.2f}; front {front.id} {w.market.last_bar(front.id).close} (expires {front.expiry}; we trade {cl.id}); curve " +
          ", ".join(f"{m[2:]} {p}" for m, p in list(w.market.curve_for('CL').items())[:4]))
    print(f"  regime {w.market.regime().label}; nearest dividend {tkr} ex {ca.ex_date} pay {ca.pay_date} ${ca.amount_per_unit}/sh")

    _p(f"3. Evening instructions: buy 10,000 {tkr}; buy 20 {cl.id}; a limit and a conditional")
    o = w.place_order(pid, tkr, "BUY", 10000)
    of = w.place_order(pid, cl.id, "BUY", 20, time_in_force="GTC")
    lim = w.place_order(pid, "SPY", "BUY", 1000, order_type="LIMIT", limit_price=w.market.last_bar("SPY").bid * D("0.97"), time_in_force="GTC")
    cond = w.place_order(pid, "UST-10Y", "BUY", 1_000_000, time_in_force="GTC", condition={"ref": "CURVE:10Y", "op": ">=", "value": 99})
    for x in (o, of, lim, cond):
        print(f"  {x.id} {x.side} {x.quantity:,} {x.security_id} {x.order_type} {x.time_in_force} -> {x.status}")

    _p("4. Next morning: the daily process runs the instructions against the new session")
    seq0 = len(w.events)
    w.advance(1)
    t = pf.trades[o.trade_ids[0]]
    ed = t.execution_detail
    print(f"  {o.id} {o.status}: {t.id} {t.side} {t.quantity:,} @ {t.price} at the open (session O/H/L/C {ed['reference_open']}/{ed['reference_high']}/"
          f"{ed['reference_low']}/{ed['reference_close']}; impact {ed['impact_bps']}bp)")
    print(f"     gross {t.gross_amount:,.2f} + commission {t.commission:,.2f} = {t.net_amount:,.2f} payable {t.settlement_date}; status {t.status}")
    tf = pf.trades[of.trade_ids[0]]
    pos_f = pf.positions[cl.id]
    print(f"  {of.id} {of.status}: {tf.id} {tf.quantity:,} {cl.id} @ {tf.price}; settled {pos_f.settlement_price}; variation margin "
          f"{pos_f.variation_margin_total:+,.2f}; initial margin {pos_f.initial_margin:,.2f} posted")
    print(f"  {lim.id} {lim.status}: {lim.reason}")
    print(f"  {cond.id} {cond.status}: {cond.reason}")
    print("  entries generated by the equity buy:")
    _entries(w, pid, seq0, limit=3)
    _briefing(pf)

    _p("5. Settlement obligation")
    si = pf.settlements[t.settlement_instruction_id]
    pos = pf.positions[tkr]
    print(f"  {si.id} {si.instruction_type} {si.quantity:,} {si.security_id} vs {si.currency} {si.cash_amount:,.2f} on {si.settlement_date}; status {si.status}")
    print(f"  position: trade-date {pos.quantity:,}, settled {pos.settled_quantity:,}, pending receive {pos.pending_receive:,}; "
          f"cash settled {pf.cash['USD'].balance:,.2f}, projected {w.trading.projected_cash(pf, 'USD'):,.2f}")

    _p("6-9. Next morning: securities settle into custody, cash leaves")
    while t.status != "SETTLED":
        w.advance(1)
    print(f"  {t.id}: " + " -> ".join(h['status'] for h in t.status_history))
    print(f"  custody {pos.settled_quantity:,}; cash {pf.cash['USD'].balance:,.2f}; movements: " + "; ".join(f"{m.kind} {m.amount:+,.0f}" for m in pf.cash_movements))

    _p("10-11. Prices move, P&L explains, futures settle daily")
    for s in pf.nav_history[-3:]:
        print(f"  {s.date} NAV {s.nav:,.2f}  day {s.day_pnl:+,.2f}  " + ", ".join(f"{k} {v:+,.0f}" for k, v in s.explain.items() if not k.startswith('_') and v))
    print(f"  {tkr}: mark {pos.mark}, MV {pos.market_value:,.2f}, unrealized {pos.unrealized_pnl:+,.2f}; "
          f"{cl.id}: settle {pos_f.settlement_price}, cumulative VM {pos_f.variation_margin_total:+,.2f} (already in cash)")

    _p("12-13. Dividend: entitlement on ex-date, cash on pay date")
    while ca.status != "PAID":
        w.advance(1)
    ent = ca.entitlements[pid]
    div = [m for m in pf.cash_movements if m.kind == "DIVIDEND"][0]
    print(f"  {ca.id}: {ent['quantity']:,} x {ca.amount_per_unit} = {ent['amount']:,.2f}; paid {div.date} -> cash {div.balance_after:,.2f}")

    _p("14-16. Sell 4,000 shares and close the futures: realized P&L, every entry")
    seq2 = len(w.events)
    o2 = w.place_order(pid, tkr, "SELL", 4000)
    o3 = w.place_order(pid, cl.id, "SELL", 20)
    w.advance(1)
    t2 = pf.trades[o2.trade_ids[0]]
    print(f"  {t2.id} SELL {t2.quantity:,} @ {t2.price}: realized {t2.realized_pnl:+,.2f} (FIFO lots " +
          "; ".join(f"{l['lot_id']} {l['quantity']:,} @ {l['cost_per_unit']}" for l in t2.lots_relieved) + ")")
    print(f"  {cl.id}: contracts {pos_f.quantity}, total variation margin {pos_f.variation_margin_total:+,.2f}, initial margin {pos_f.initial_margin:,.2f}")
    _entries(w, pid, seq2, limit=8)
    w.advance(1)
    _briefing(pf)

    _p("17. Audit trail: the causal chain of the equity buy")
    ev = next(e for e in w.events if e.type == E.TRADE_EXECUTED and e.payload["trade_id"] == t.id)
    chain = w.audit_chain(ev.id)
    print("  ancestors: " + " -> ".join(f"{a.id} {a.type}" for a in chain["ancestors"]))
    def show(node, depth=0):
        e = node["event"]
        print("  " + "    " * depth + f"{e.id} {e.type} " + (e.payload.get("memo") or e.payload.get("si_id") or ""))
        for c in node["children"]:
            show(c, depth + 1)
    show(chain["tree"])
    led = w.ledgers[pid]
    summ = w.pnl.compute_summary(pf)
    ok = all(sum((v for k, v in s.explain.items() if not k.startswith("_")), D(0)) == s.day_pnl for s in pf.nav_history)
    print(f"\n  events {len(w.events)}; entries {len(led.entries)}; trial balance balanced {led.trial_balance()['balanced']}; "
          f"ledger NAV {led.nav():,.2f} == economic NAV {summ['nav']:,.2f}: {led.nav() == summ['nav']}; every day's explain reconciles: {ok}")
    w2 = World.load(store, "demo")
    print(f"  replay from event store: NAV {w2.ledgers[pid].nav():,.2f}, events {len(w2.events)}, briefings {len(w2.portfolios[pid].briefings)}, "
          f"identical: {w2.ledgers[pid].nav() == led.nav() and len(w2.events) == len(w.events)}")
    return w


if __name__ == "__main__":
    run_demo()

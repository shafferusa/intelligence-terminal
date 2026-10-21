"""Manual network probe: ``py -3.12 -m finsim.terminal.smoke [--spx] [--no-events]`` (TERMINAL_DESIGN.md §8.3, §7.1; WP-01).

Prints, for the integrator to paste into ``docs/design_proposals/BUILD_NOTES.md``:
1. ten quotes with provenance (symbol, last, provider time, fetched time, delay label, completed bar, provider session);
2. the Cboe verdict for the honest ``TERMINAL_UA`` (``ok`` or ``refused_ua``) on the AAPL chain, with the reduction counts;
3. the per-contract futures symbol check (``ESZ26.CME``, ``ZNZ26.CBT``, ``6EZ26.CME``, ``GCZ26.CMX``, ``ZCZ26.CBT``);
4. one dividend / split events fetch (AAPL, 3 months);
5. the Treasury par curve (as-of and tenors), FRED SOFR / HY OAS, the last FINRA file and Frankfurter;
6. with ``--spx``: the peak memory of a full ``_SPX`` chain parse (``tracemalloc``) — the acceptance asks for < 150 MB.

Never touches a database; never sends anything; every request uses the UA policy of ``quotes.http.ua_for``.
"""
from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from datetime import timedelta

from .clock import RealClock, ny_str
from .quotes.http import FetchError
from .quotes.providers import ProviderSet

QUOTES = ["SPY", "AAPL", "EURUSD=X", "JPY=X", "ES=F", "^IRX", "^TNX", "CLX26.NYM", "^GSPC", "BRK-B"]
FUTURES = ["ESZ26.CME", "ZNZ26.CBT", "6EZ26.CME", "GCZ26.CMX", "ZCZ26.CBT"]


def _t(epoch):
    return ny_str(int(epoch)) if epoch else "-"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FinSim Terminal quote-provider smoke probe (manual, network)")
    ap.add_argument("--spx", action="store_true", help="also parse the 13 MB _SPX chain and report peak memory")
    ap.add_argument("--no-events", action="store_true")
    args = ap.parse_args(argv)
    clock = RealClock()
    ps = ProviderSet.live(clock)
    print(f"smoke probe at {ny_str(clock.epoch())}")
    print("\n== 1. quotes (Yahoo v8 chart, BROWSER_UA, up to 15 min delayed) ==")
    for sym in QUOTES:
        t0 = time.monotonic()
        try:
            q = ps.yahoo.intraday(sym)
        except FetchError as e:
            print(f"{sym:12s} ERROR {e} (status {e.status})")
            continue
        bar = f"bar t={_t(q.bar.t)} c={q.bar.c} v={q.bar.v}" if q.bar else "bar -"
        ps_ = f"session {_t(q.provider_session[0])} .. {_t(q.provider_session[1])}" if q.provider_session else "session -"
        print(f"{sym:12s} last={q.last} ccy={q.currency} quote_time={_t(q.quote_time)} fetched={_t(q.fetched_at)} prev_close={q.prev_close} "
              f"{bar} {ps_} tz={q.exchange_tz} ({time.monotonic() - t0:.2f}s)")
    print("\n== 2. Cboe delayed chain with the honest TERMINAL_UA ==")
    try:
        snap = ps.cboe.chain("AAPL", "AAPL")
        print(f"AAPL: ok — underlying last={snap.spot} bid/ask={snap.underlying.bid}/{snap.underlying.ask} last_trade={_t(snap.quote_time)} "
              f"iv30={snap.iv30} raw contracts={snap.n_raw} kept={snap.n_kept()} expiries={len(snap.expiries())}")
        verdict = "ok"
    except FetchError as e:
        verdict = "refused_ua" if e.status == 403 else f"error: {e}"
        print(f"AAPL: {verdict} (status {e.status})")
    print(f"CBOE VERDICT: {verdict}; health.refused_ua={ps.health.refused_ua('cboe')}")
    print("\n== 3. per-contract futures symbols ==")
    for sym in FUTURES:
        try:
            q = ps.yahoo.intraday(sym)
            print(f"{sym:12s} 200 last={q.last} quote_time={_t(q.quote_time)}")
        except FetchError as e:
            print(f"{sym:12s} {e.status or 'ERR'} {e}")
    if not args.no_events:
        print("\n== 4. chart events (AAPL, 3mo) ==")
        try:
            ev = ps.yahoo.events("AAPL")
            print(f"dividends: {[(d.ex_date.isoformat(), str(d.amount)) for d in ev.dividends]}")
            print(f"splits: {[(s.date.isoformat(), s.ratio) for s in ev.splits]}")
        except FetchError as e:
            print(f"events ERROR {e}")
    print("\n== 5. rates ==")
    try:
        t0 = time.monotonic()
        curve = ps.treasury.curve()
        if curve is None:
            print("treasury: no curve this month or last")
        else:
            print(f"treasury par curve as_of={curve.as_of} ({time.monotonic() - t0:.1f}s): " + ", ".join(f"{k}={v}" for k, v in curve.labels().items()))
    except FetchError as e:
        print(f"treasury ERROR {e}")
    for sid in ("SOFR", "BAMLH0A0HYM2", "VIXCLS"):
        try:
            obs = ps.fred.latest(sid, 30)
            print(f"FRED {sid}: {len(obs)} obs, latest {obs[-1][0]} = {obs[-1][1]}" if obs else f"FRED {sid}: no observations")
        except FetchError as e:
            print(f"FRED {sid} ERROR {e}")
    try:
        fx = ps.frankfurter.fx_latest()
        print(f"Frankfurter {fx.as_of}: EUR={fx.per_usd.get('EUR')} JPY={fx.per_usd.get('JPY')} GBP={fx.per_usd.get('GBP')} ({len(fx.per_usd)} currencies)")
    except FetchError as e:
        print(f"Frankfurter ERROR {e}")
    d = clock.today()
    for back in range(0, 5):
        day = d - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        try:
            rows = ps.finra.finra_daily(day, ["AAPL", "SPY", "A"])
        except FetchError as e:
            print(f"FINRA {day} ERROR {e}")
            break
        if rows is None:
            print(f"FINRA {day}: not published")
            continue
        print(f"FINRA {day}: " + ", ".join(f"{k} {v[0]}/{v[1]} ratio {v[0] / v[1]:.3f}" for k, v in sorted(rows.items())))
        break
    if args.spx:
        print("\n== 6. _SPX chain parse memory ==")
        tracemalloc.start()
        t0 = time.monotonic()
        try:
            snap = ps.cboe.chain("_SPX", "SPX")
            cur, peak = tracemalloc.get_traced_memory()
            print(f"_SPX: raw={snap.n_raw} kept={snap.n_kept()} expiries={len(snap.expiries())} in {time.monotonic() - t0:.1f}s; "
                  f"peak traced memory {peak / 1e6:.0f} MB (acceptance < 150 MB), resident after parse {cur / 1e6:.0f} MB")
        except FetchError as e:
            print(f"_SPX ERROR {e}")
        finally:
            tracemalloc.stop()
    print("\nprovider health:")
    for name, h in ps.health.snapshot().items():
        print(f"  {name:12s} ok={h['ok']} errors={h['consecutive_errors']} last_error={h['last_error']} refused_ua={h['refused_ua']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI: `python -m finsim2 open` (start the server if needed and open the window), `serve`, `status`, `stop`, `phone`,
`refresh` (download the market history) and `research` (compute evidence, optionally train ML models)."""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, DEFAULT_PORT

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def configure() -> None:
    """Point FinSim's launcher at FinSim2's own home, port and database, and make it start `python -m finsim2 serve`.
    FINSIM2_HOME / FINSIM2_PORT / FINSIM2_DB override the defaults (~/.finsim2, 8865, ~/.finsim2/finsim2.db)."""
    home = os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2")
    os.environ["FINSIM_HOME"] = home
    os.environ["FINSIM_PORT"] = os.environ.get("FINSIM2_PORT") or str(DEFAULT_PORT)
    os.environ["FINSIM_DB"] = os.environ.get("FINSIM2_RESEARCH_DB") or os.environ.get("FINSIM2_DB") or os.path.join(home, "research.db")
    from finsim import app
    app.APP_NAME = APP_NAME
    # the desktop-app installer (finsim/app.py) with FinSim2's own names, so both apps can be installed side by side
    app.MODULE, app.SLUG, app.HOME_ENV = "finsim2", "finsim2", "FINSIM2_HOME"
    app.BUNDLE_ID = "io.finsim2.terminal"
    app.DESCRIPTION = "FinSim2 — portfolio and quant analytics (local)"
    app.ICON_SMALL = (os.path.join(STATIC_DIR, "icon-192.png"), 192)
    app.ICON_LARGE = os.path.join(STATIC_DIR, "icon-512.png")

    def serve_argv(p=None):
        return [app.python_exe(windowless=True), "-m", "finsim2", "serve", "--db", app.db_path(), "--port", str(p or app.port())]
    app.serve_argv = serve_argv
    app.legacy_db_path = lambda: os.path.join(home, "none.db")     # FinSim2 never adopts FinSim's saves


def _data_cmd(args) -> int:
    from finsim import app
    from .data.store import Store
    st = Store(app.db_path())
    try:
        if args.cmd == "data":
            from .data import newdata
            if args.status:
                for r in newdata.status(st):
                    print(f"{r['dataset']:<18} {r['rows']:>9} rows  {r['series']:>5} series  {r['first'] or '—'} → {r['last'] or '—'}  (last published {r['last_published'] or '—'})")
                print("due now:", ", ".join(newdata.due(st)) or "nothing")
                return 0
            names = args.sources or (list(newdata.SOURCES) if args.force else newdata.due(st))
            bad = [n for n in names if n not in newdata.SOURCES]
            if bad:
                print("unknown source(s):", ", ".join(bad), "— choose from", ", ".join(newdata.SOURCES))
                return 2
            if not names:
                print("every source is up to date (use --force to refresh anyway)")
                return 0
            res = newdata.refresh(st, names, print)
            return 0 if all(v.get("state") in ("ok", "partial", "skipped", "deferred") for v in res.values()) else 1
        if args.cmd == "universe":
            from .data import expand
            if args.expand is None:
                fixed = expand.reclassify_funds(st, args.dry_run)
                for f in fixed:
                    print(f"reclassified {f['id']} as {f['asset_class']} ({f['reason']})")
                print(f"{len(fixed)} expanded assets reclassified as funds" + (" (dry run)" if args.dry_run else ""))
                if not args.dry_run:
                    print(f"{len(expand.fill_industry(st, print))} stocks given their SEC industry (the Shaffer System hierarchy)")
                return 0
            res = expand.expand(st, args.expand, print, dry_run=args.dry_run, allow_partial=args.allow_partial)
            if res.get("note"):
                print(res["note"])
            print(f"added {len(res['added'])} equities" + (" (dry run)" if args.dry_run else "") + "; next: python -m finsim2 refresh, then python -m finsim2 data sec --force")
            for a in res["added"][:50]:
                print(f"  {a['id']:<7} {a['sector']:<24} ${a['liquidity_usd'] / 1e6:,.0f}M / day")
            return 0
        if args.cmd == "import-options-dump":
            from .data import optionsdump as OD
            if args.probe:
                for t in OD.probe(args.file):
                    print(f"{t['table']}: {t['rows']} rows\n  columns: {', '.join(t['columns'])}\n  mapping: {t['mapping']}")
                    for r in t["sample"]:
                        print("   ", r)
                return 0
            if not args.quality:
                if not args.file:
                    print("give the decompressed .db file (or --quality)")
                    return 2
                over = dict(x.split("=", 1) for x in args.map if "=" in x)
                res = OD.import_file(st, args.file, args.table, over, args.underlying, print)
                if res.get("error"):
                    print(res["error"])
                    return 1
                print(f"{res['table']}: {res['rows_read']} rows, {res['chains']} chains, {res['stored']} stored; "
                      f"unknown underlyings: {', '.join(res['skipped_unknown']) or 'none'}; bad rows {res['bad_rows']}")
            q = OD.quality(st)
            for a, r in q["names"].items():
                print(f"  {a}: coverage {r['coverage']:.0%}, corr {r.get('corr') or 0:.3f}, mean |diff| {r.get('mad') or 0:.2f} vol pts -> "
                      f"{'ok' if r['passed'] else 'FAILED'}")
            print("quality gate:", "PASSED — the dump can enter research (lab --iv)" if q["passed"] else "FAILED — the dump is not used")
            return 0
        from .data import imports
        res = imports.import_file(st, args.file, "estimates" if args.cmd == "import-estimates" else "options", args.source)
        print(f"{res['dataset']}: {res['rows']} values imported; skipped {res['skipped']}")
        return 0
    finally:
        st.close()


def _system_cmd(args) -> int:
    from finsim import app
    from .data.store import Store
    from .engine import system as SY
    from .engine.research import Research
    db = app.db_path()
    if args.build or args.all:
        print(SY.build(db, workers=args.workers, progress=print))
    if args.study or args.all:
        res = SY.run_study(db, progress=print, horizons=args.horizons or None, workers=args.workers)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_SYSTEM_RESULTS.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(SY.markdown(res))
        print("wrote", out)
    st = Store(db)
    try:
        spec = SY.load_spec(st)
        if args.freeze:
            if not st.kv_get(SY.LIVE_KEY):
                print("no fitted spec in this database: run `system --all` first")
                return 1
            SY.write_frozen(st.kv_get(SY.LIVE_KEY))
            print("wrote", SY.FROZEN_FILE)
        if not spec:
            print("no Shaffer System spec: run `python -m finsim2 system --all` (about 1–3 hours) or pull the frozen one")
            return 1
        src = "this database's refit" if st.kv_get(SY.LIVE_KEY) else "the committed frozen spec"
        print(f"Shaffer System {spec['version']}@{spec['hash'][:10]} — fitted {spec.get('fitted')} on data to {spec.get('data_through')} ({src})")
        if args.status or not args.assets:
            for lab, hz in spec["horizons"].items():
                print(f"  {SY.DISPLAY.get(lab, lab):>4}: " + ", ".join(f"{c} {v['adopted'] == 'learned' and v['family'] or 'E0'}/{v['reliability']}"
                                                                   for c, v in sorted(hz["classes"].items())))
        r = Research(st)
        for a in args.assets:
            b = r.bundle(a.upper())
            sy = b.get("system") or {}
            print(f"{a.upper()}:" + ("" if sy else f" no forecast ({b.get('system_error') or 'no inputs'})"))
            for lab in [x for x, _, _ in SY.HORIZONS if x in sy]:
                x = sy[lab]
                pb = "—" if x["p_beat"] is None else f"{x['p_beat']:.0%}"
                extra = f"P(up) {x['p_pos']:.0%} · expected move ±{x['abs_move']:.1%}" if lab in SY.DIRECTIONAL else \
                    f"P(>0) {x['p_pos']:.0%} · P(beat {x['benchmark']}) {pb}"
                print(f"  Shaffer {x['system']:<11} {x['label']:>4}: {100 * x['expected']:+.1f}% (median {100 * x['median']:+.1f}%) · {extra} · "
                      f"90% {100 * x['range90'][0]:+.1f}% to {100 * x['range90'][1]:+.1f}% · {x['reliability']} · {x['family'] if x['adopted'] == 'learned' else 'E0'}")
        return 0
    finally:
        st.close()


def _movesize_cmd(args) -> int:
    from finsim import app
    from .data.store import Store
    from .engine import directional, movesize
    from .engine.research import Research
    st = Store(app.db_path())
    try:
        r = Research(st)
        if args.fit or movesize.due(st, r.panel().calendar()[-1]):
            live = movesize.fit_live(st, r, progress=print)
            if not live["horizons"]:
                print("not enough history for a move-size fit: run `python -m finsim2 refresh` and `python -m finsim2 data --force` first")
                return 1
        if not (st.kv_get(directional.LIVE_KEY) or {}).get("horizons"):   # the calibrated prior-only P(up) shown next to the move size
            try:
                if not directional.fit_live(st, r, progress=print).get("horizons"):
                    print("P(up) will show the uncalibrated point-in-time prior until `python -m finsim2 lab --build` has run")
            except Exception as e:  # noqa: BLE001
                print(f"P(up) will show the uncalibrated point-in-time prior ({e})")
        if args.assets:
            for a in args.assets:
                meta = st.asset(a.upper())
                v = movesize.asset_view(st, r, meta) if meta else None
                if not v or not v.get("available"):
                    print(f"{a}: {(v or {}).get('reason', 'unknown asset')}")
                    continue
                for lab, x in v["horizons"].items():
                    pu = "—" if x["p_up"] is None else f"{x['p_up']:.0%}" + ("" if x["p_up_calibrated"] else " (uncalibrated)")
                    print(f"{meta['id']} {lab}: P(up) {pu} / expected move ±{x['move_pct']:.1%} / 90% range {x['lo_pct']:+.1%} to {x['hi_pct']:+.1%}")
            return 0
        view = movesize.today_live(st, r, progress=print)
        print(movesize.live_markdown(view, st.kv_get(movesize.LIVE_KEY) or {}, args.top))
        return 0
    finally:
        st.close()


def _news_cmd(args) -> int:
    """`news` (refresh the feeds now, aggregate, per-feed status), `--status`, `add`, `show TICKER`, `feeds`. Research only."""
    import json
    from finsim import app
    from .data import news
    from .data.store import Store
    st = Store(app.db_path())
    try:
        if args.news_cmd == "feeds":
            try:
                if args.add:
                    news.add_feed(st, args.add)
                if args.remove:
                    news.remove_feed(st, args.remove)
            except ValueError as e:
                print(f"refused: {e}")
                return 2
            for f in news.feeds(st):
                print(f"{f['source']:<12} {f['name']:<22} {'verified' if f['verified'] else ('yours' if f['user'] else 'unverified'):<10} {f['url']}")
            return 0
        if args.news_cmd == "add":
            try:
                with open(args.file, encoding="utf-8", errors="replace") as fh:
                    raw = fh.read(news.MAX_TEXT * 2)
            except OSError as e:
                print(f"cannot read {args.file}: {e}")
                return 2
            title, text, published = args.title, raw, args.published
            if raw.lstrip().startswith("{"):                        # the bookmarklet's JSON
                try:
                    j = json.loads(raw)
                    if isinstance(j, dict):
                        title = title or j.get("title")
                        text = j.get("text") if isinstance(j.get("text"), str) else ""
                        published = published or j.get("published")
                except ValueError:
                    pass
            if not title:
                title = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            try:
                res = news.import_article(st, args.url, title, text, published)
            except ValueError as e:
                print(f"refused: {e}")
                return 2
            a = res["article"]
            print(("already stored" if res["duplicate"] else "imported") + f": {a['title']} ({a['source']}, session {a['session']})")
            print("  tickers:", ", ".join(f"{t['asset']} {t['confidence']:.1f}" for t in a["tickers"]) or "none",
                  f"· sentiment {a['sent']:+.2f} · events {', '.join(a['events']) or 'none'} · text not stored ({a['text_length']} characters)")
            return 0
        if args.news_cmd == "show":
            tid = args.ticker.upper().replace(".", "-")
            if not st.asset(tid):
                print(f"unknown asset {args.ticker}")
                return 2
            rows = news.recent(st, args.limit, tid)
            print(f"{tid}: {len(rows)} linked articles ({news.RESEARCH_ONLY})")
            for a in rows:
                conf = next((t["confidence"] for t in a["tickers"] if t["asset"] == tid), None)
                print(f"  {a['known_at'][:16]}  {a['source']:<11} {a['sent']:+.2f}  [{conf:.1f}]  {a['title'][:100]}")
                print(f"      {a['url']}" + (f"  events: {', '.join(a['events'])}" if a["events"] else ""))
            return 0
        if not args.status:
            res = news.refresh(st, print)
            from .engine.research import Research
            try:
                agg = news.aggregate(st, Research(st))
                print(f"aggregated {agg['asset_sessions']} asset-sessions ({agg['rows']} rows) into news_dj")
            except LookupError as e:                                # no SPY history yet
                print(f"not aggregated: {e}")
            print(f"{res['new']} new articles, {res['ok']}/{res['feeds']} feeds ok")
        s = news.status(st)
        for f in s["feeds"]:
            print(f"{f['state']:<11} {f['source']:<12} {f['name']:<22} items {f.get('items', 0):>3}  new {f.get('new', 0):>3}  newest {f.get('newest') or '—'}"
                  + (f"  ({f['error']})" if f.get("error") else "") + ("" if f["verified"] or f["user"] else "  [unverified name]"))
        c = s["counts"]
        print(f"stored: {c['articles']} articles ({c['manual']} manual) over {c['sessions']} sessions, {c['links']} links to {c['linked_assets']} assets")
        from .engine import newslive
        ev = newslive.summary(st)
        pr = ev["progress"]
        print(f"forward evaluation: {ev['status']} — sessions {pr['have']['sessions']}/{pr['need']['sessions']}, asset-sessions with news "
              f"{pr['have']['asset_sessions']}/{pr['need']['asset_sessions']}, sessions with ≥20 assets {pr['have']['broad_sessions']}/{pr['need']['broad_sessions']}")
        for k, t in (ev.get("tests") or {}).items():
            stat = t.get("mean_ic", t.get("gain", t.get("hit_rate")))
            print(f"  {k}: {stat if stat is None else round(stat, 4)}  p {t.get('p') if t.get('p') is None else round(t['p'], 4)}  {'PASS' if t.get('passed') else 'no'}")
        print(news.RESEARCH_ONLY)
        return 0
    finally:
        st.close()


def main(argv=None) -> int:
    configure()
    from finsim import app
    ap = argparse.ArgumentParser(prog="finsim2", description="FinSim2: the portfolio-manager edition")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API + the FinSim2 interface in this terminal")
    s.add_argument("--db", default=app.db_path())
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=app.port())
    s.add_argument("--key", default=None)
    sub.add_parser("open", help="start the server if needed and open FinSim2 in its own window")
    ins = sub.add_parser("install", help="install FinSim2 as a local app: server starts at login + a desktop / Start Menu launcher (this user only)")
    ins.add_argument("--no-open", action="store_true", help="do not open the window after installing")
    un = sub.add_parser("uninstall", help="remove the FinSim2 launcher and login service (your data stays)")
    un.add_argument("--purge", action="store_true", help="also delete the research database")
    sub.add_parser("status", help="is FinSim2 running, where are its saves")
    sub.add_parser("stop", help="stop the FinSim2 server")
    ph = sub.add_parser("phone", help="reach FinSim2 from your phone (behind an access key)")
    ph.add_argument("state", nargs="?", choices=["on", "off", "show"], default="on")
    rf = sub.add_parser("refresh", help="download / update the market history in this terminal")
    rf.add_argument("--full", action="store_true", help="re-download every asset's full history")
    rs = sub.add_parser("research", help="compute the evidence for assets (and optionally train their ML models)")
    rs.add_argument("assets", nargs="*", help="asset ids (default: SPY QQQ TLT GOLD)")
    rs.add_argument("--ml", action="store_true", help="also train the walk-forward ML models")
    au = sub.add_parser("audit", help="replay the Shaffer Score and ML over the whole universe and write SHAFFER_AUDIT.md")
    au.add_argument("assets", nargs="*", help="asset ids (default: every asset with six years of prices)")
    au.add_argument("--workers", type=int, default=3)
    au.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_AUDIT.md"))
    ic = sub.add_parser("import-chain", help="load an option chain (CSV: asof, underlying, expiry, strike, right, bid, ask, last, iv, delta, gamma, vega, theta, rho, open_interest, volume)")
    ic.add_argument("file")
    ic.add_argument("--source", default="import")
    dt_ = sub.add_parser("data", help="new data sources: SEC insider + 8-K, event calendar, CFTC, EIA, crypto derivatives, news, Cboe vol indices + option snapshots, futures curves, analyst data (NEW_DATA_SOURCES.md)")
    dt_.add_argument("sources", nargs="*", help="sec calendar cftc eia crypto news cboe options futures analyst (default: every source that is due)")
    dt_.add_argument("--force", action="store_true", help="refresh the named (or all) sources now, due or not")
    dt_.add_argument("--status", action="store_true", help="rows, coverage and last publication date per dataset")
    uv = sub.add_parser("universe", help="widen the research universe to the N most liquid US common stocks")
    uv.add_argument("--expand", type=int, metavar="N", help="target number of equities (e.g. 500, 1000, 1500); "
                    "without it, only move expanded 'equities' that are really funds (IAU, GLDM) to the ETF class")
    uv.add_argument("--dry-run", action="store_true", help="rank and list, add nothing")
    uv.add_argument("--allow-partial", action="store_true", help="add from an incomplete liquidity ranking")
    for nm, what in (("import-estimates", "analyst estimates (FactSet / I/B/E/S / Zacks CSV)"), ("import-options", "historical options summaries (ORATS / Cboe / OptionMetrics CSV)")):
        ip = sub.add_parser(nm, help=f"import licensed {what} into the point-in-time store")
        ip.add_argument("file")
        ip.add_argument("--source", default="import")
    od = sub.add_parser("import-options-dump", help="import a historical options dump (SQLite; e.g. the free 2008–2025 chains) as "
                        "per-session IV / skew / OI features, then run its quality gate (data/optionsdump.py)")
    od.add_argument("file", nargs="?", help="the decompressed .db / .sqlite file")
    od.add_argument("--probe", action="store_true", help="list the tables, columns and the column mapping; import nothing")
    od.add_argument("--table", help="the table to import (default: the largest with a date and an IV column)")
    od.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN", help="override a column (e.g. iv=mid_iv)")
    od.add_argument("--underlying", help="the underlying when the table has no symbol column (one file per underlying)")
    od.add_argument("--quality", action="store_true", help="only (re)run the quality gate on what is imported")
    pr = sub.add_parser("probe", help="test a data provider's claims before building on it")
    pr.add_argument("provider", choices=["eulerpool"])
    sy = sub.add_parser("system", help="the Shaffer System: expected % return per asset and horizon (SHAFFER_SYSTEM.md)")
    sy.add_argument("assets", nargs="*", help="print these assets' forecasts")
    sy.add_argument("--build", action="store_true", help="build the point-in-time records (all asset classes, 1D–5Y)")
    sy.add_argument("--study", action="store_true", help="walk-forward study + final fit → SHAFFER_SYSTEM_RESULTS.md and the live spec")
    sy.add_argument("--all", action="store_true", help="--build then --study (the monthly refit; 1–3 hours)")
    sy.add_argument("--horizons", nargs="*", help="study only these horizons (1D 3D 1W 1M 3M 6M 12M 24M 36M 60M)")
    sy.add_argument("--workers", type=int, default=2)
    sy.add_argument("--status", action="store_true", help="the live spec: adopted equation and reliability per horizon and class")
    sy.add_argument("--freeze", action="store_true", help="write this database's fitted spec to finsim2/frozen/shaffer_system.json")
    ms = sub.add_parser("movesize", help="expected 1D / 1W move and 90% range (the validated move-size model, SHAFFER_MOVE_SIZE.md) with P(up)")
    ms.add_argument("assets", nargs="*", help="show these assets (default: the largest expected moves across the universe)")
    ms.add_argument("--fit", action="store_true", help="refit the frozen model now (otherwise weekly, or when missing)")
    ms.add_argument("--top", type=int, default=20)
    nw = sub.add_parser("news", help="research-only news: WSJ / MarketWatch public RSS now + aggregate (NEW_DATA_SOURCES.md, NEWS_SIGNALS_PROTOCOL.md)")
    nw.add_argument("--status", action="store_true", help="per-feed status, stored counts and the forward evaluation (no fetch)")
    nws = nw.add_subparsers(dest="news_cmd")
    na = nws.add_parser("add", help="import an article you are reading (wsj.com / barrons.com / marketwatch.com); its text is not stored")
    na.add_argument("--url", required=True)
    na.add_argument("--file", required=True, help="the article text, or the News page bookmarklet's JSON")
    na.add_argument("--title")
    na.add_argument("--published", help="ISO time the publisher gives (stored; timing always uses the import time)")
    nsh = nws.add_parser("show", help="recent articles linked to an asset")
    nsh.add_argument("ticker")
    nsh.add_argument("--limit", type=int, default=20)
    nf = nws.add_parser("feeds", help="list the feeds; add / remove your own (https on feeds.content.dowjones.io only)")
    nfg = nf.add_mutually_exclusive_group()
    nfg.add_argument("--add", metavar="URL")
    nfg.add_argument("--remove", metavar="URL")
    lb = sub.add_parser("lab", help="ML Lab: research Shaffer weights (hierarchical, walk-forward) and register challengers")
    lb.add_argument("--build", action="store_true", help="first rerun the point-in-time sweeps that produce the research records")
    lb.add_argument("--workers", type=int, default=3)
    lb.add_argument("--weights", action="store_true", help="only the signal-level Shaffer weight research (engine/weights.py)")
    lb.add_argument("--freeze-benchmark", metavar="ID", nargs="?", const="", default=None,
                    help="freeze the current production system as the benchmark for new-information research (once per id)")
    lb.add_argument("--newinfo", action="store_true", help="new-information research against the frozen benchmark (engine/newinfo.py)")
    lb.add_argument("--newinfo-report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "NEW_INFORMATION_RESEARCH.md"))
    lb.add_argument("--insider-500", action="store_true", help="re-test insider activity on the stocks added by the universe expansion "
                    "only (fresh sample; NEW_DATA_INSIDER_500_PROTOCOL.md)")
    lb.add_argument("--new-data", action="store_true", help="batch 2: the new data sources' signal families (NEW_DATA_SIGNALS_PROTOCOL.md)")
    lb.add_argument("--alpha-horizons", choices=["build", "study", "all"], help="Shaffer Alpha 1M–5Y with horizon / sector / stock weights (SHAFFER_ALPHA_HORIZONS_PROTOCOL.md)")
    lb.add_argument("--vnext", choices=["alpha", "directional", "hedge", "all"], help="Shaffer vNext research programs (Alpha / Directional / Hedge)")
    lb.add_argument("--learned", action="store_true", help="find the historically supported Shaffer weights (and hedge parameters): engine/learned.py")
    lb.add_argument("--residual", action="store_true", help="residual / meta-learning program on the frozen E (capability worlds first; research only): engine/residual.py")
    lb.add_argument("--finetune", action="store_true", help="fine-tune the learned 1W Shaffer Alpha (nested, G1–G5), 1D after costs, 1M–12M, hedge λ surface: engine/finetune.py")
    lb.add_argument("--extended", action="store_true", help="research-only extended-history records (pre-2001, training only) and the 1M–12M Alpha / Directional studies on them → SHAFFER_LONG_HORIZON_DATA.md")
    lb.add_argument("--hedge-panel", action="store_true", help="put the λ-conditional hedge sizing cells that passed every gate into live shadow and record / grade the λ-aware hedge panel now")
    lb.add_argument("--live-panel", action="store_true", help="score the whole research universe for production and every Shaffer challenger now (the weekly live panel)")
    lb.add_argument("--fetch-sec-extra", action="store_true", help="download the extra SEC concepts the Alpha vNext program uses (research only)")
    lb.add_argument("--breadth-hedge", action="store_true", help="does the breadth volatility forecast improve Shaffer Hedge outcomes? (hedge/volhedge.py)")
    lb.add_argument("--breadth-hedge-report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "BREADTH_HEDGE_RESEARCH.md"))
    lb.add_argument("--alpha-shadow", choices=["status", "record"], help="the 1M Alpha challenger in live shadow (research only, "
                    "SHAFFER_ALPHA_1M_SHADOW_PROTOCOL.md): its live evidence, or record this week's panel now")
    lb.add_argument("--eulerpool", action="store_true", help="analyst expectations from Eulerpool: consensus surprises and rating "
                    "actions against the frozen benchmark (SHAFFER_EP_PROTOCOL.md)")
    lb.add_argument("--iv", action="store_true", help="implied volatility and futures curves: batch-iv families + move-size IV study "
                    "(SHAFFER_IV_PROTOCOL.md)")
    lb.add_argument("--move-size", action="store_true", help="stage 3: how big the next 1D / 1W move will be (SHAFFER_MOVE_SIZE_PROTOCOL.md)")
    lb.add_argument("--event-hedge", action="store_true", help="stage 4: does the event-calendar volatility forecast improve Shaffer Hedge outcomes? (SHAFFER_EVENT_HEDGE_PROTOCOL.md)")
    lb.add_argument("--live-models", action="store_true", help="fit the daily-ledger models: the benchmark's prior-only and current Directional models, and the new-information families that passed every gate")
    lb.add_argument("--fetch-finra", action="store_true", help="download FINRA Reg SHO short-sale volume (2019 on) for US equities and ETFs")
    lb.add_argument("--directional", action="store_true", help="only the Shaffer Alpha vs Shaffer Directional research (engine/directional.py)")
    lb.add_argument("--directional-report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_DIRECTIONAL_RESEARCH.md"))
    lb.add_argument("--report", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_WEIGHT_RESEARCH.md"))
    ha = sub.add_parser("hedge-audit", help="walk-forward Shaffer Hedge evaluation and ML-adjustment training → HEDGE_AUDIT.md")
    ha.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "HEDGE_AUDIT.md"))
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .server import serve
        host = args.host or app.resolve_host()
        serve(args.db, host, args.port, args.key or (None if host in app.LOCAL_HOSTS else app.access_key()))
        return 0
    if args.cmd == "refresh":
        from .data.refresh import refresh
        from .data.store import Store
        st = Store(app.db_path())
        res = refresh(st, full=args.full, progress=lambda d, n, m: print(f"[{d}/{n}] {m}", flush=True))
        print(f"done in {res.get('seconds')}s; errors: {res.get('errors') or 'none'}")
        return 0
    if args.cmd == "research":
        from .data.store import Store
        from .engine.research import Research
        from .engine import ml
        r = Research(Store(app.db_path()))
        for a in args.assets or ["SPY", "QQQ", "TLT", "GOLD"]:
            b = r.bundle(a)
            print(a, b["regime"]["description"], {k: (round(v["score"]) if v.get("score") is not None else None) for k, v in b["horizons"].items()})
            if args.ml:
                ml.train_asset(r, a, progress=lambda d, n, m: print("  ", m, flush=True))
        return 0
    if args.cmd == "audit":
        from .engine import audit
        u = audit.run_universe(app.db_path(), assets=args.assets or None, workers=args.workers, progress=print)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(audit.markdown(u, audit.aggregates(u)))
        print("wrote", args.out)
        return 0
    if args.cmd == "import-chain":
        from .data.store import Store
        from .hedge.surface import read_csv
        rows = read_csv(args.file, args.source)
        n = Store(app.db_path()).upsert_option_quotes(rows)
        print(f"stored {n} option quotes for {', '.join(sorted({r['underlying'] for r in rows}))}; hedges now price these contracts from their quotes")
        return 0
    if args.cmd == "lab" and args.freeze_benchmark is not None:
        from .data.store import Store
        from .engine import lab
        st = Store(app.db_path())
        try:
            meta = lab.freeze_benchmark(st, args.freeze_benchmark or None)
            print(f"froze {meta['id']} at {meta['frozen']}: sha256 {meta['hash']}")
            print("production:", ", ".join(f"{k} {v}" for k, v in meta["production"].items()), "· verify:", lab.verify_benchmark(st, meta["id"])["ok"])
        finally:
            st.close()
        return 0
    if args.cmd == "lab" and args.learned:
        from .data.store import Store
        from .engine import learned
        here = os.path.dirname(os.path.abspath(__file__))
        res = learned.run_all(app.db_path(), workers=args.workers, progress=print)
        H = res["horizons"]
        st = Store(app.db_path())
        try:
            learned.register(st, res)
        finally:
            st.close()
        live = learned.build_live(app.db_path(), res, progress=print)
        md = learned.markdown(res, {k: v.get("assets_today") for k, v in H.items()}, {k: v.get("nodes") for k, v in H.items()}, live)
        open(os.path.join(here, "SHAFFER_LEARNED_WEIGHTS.md"), "w", encoding="utf-8").write(md)
        print(f"learned weights: {res['seconds']}s; wrote SHAFFER_LEARNED_WEIGHTS.md")
        return 0
    if args.cmd == "lab" and args.extended:
        from .data.store import Store
        from .engine import extrecords, finetune, finetune_report, learned
        from .engine.research import Research
        here = os.path.dirname(os.path.abspath(__file__))
        st = Store(app.db_path())
        try:
            r = Research(st)
            info = extrecords.build(r, progress=print)
            gaps = extrecords.data_gaps(r)
            ext, dext = {}, {}
            for lab_ in ("1M", "3M", "6M", "12M"):
                ext[lab_] = finetune.study_long(st, r, lab_, progress=print, extended=True)
            finetune.finalise(ext)
            for lab_ in ("1M", "3M", "6M"):
                s_ = learned.Study(st, r, lab_, progress=print)
                s_.extended = True
                dext[lab_] = {"dir": s_.run().get("dir")}
            std = st.kv_get(finetune.RESEARCH_KEY) or {}
            lw = learned.load(st) or {}
            dstd = {k: {"dir": v.get("dir")} for k, v in (lw.get("horizons") or {}).items()}
            st.kv_set("lab:extended", {"info": info, "gaps": gaps, "alpha": {k: {kk: vv for kk, vv in v.items()} for k, v in ext.items()}})
            md = finetune_report.long_data_markdown(std, ext, gaps, info, dstd, dext)
            open(os.path.join(here, "SHAFFER_LONG_HORIZON_DATA.md"), "w", encoding="utf-8").write(md)
            print("wrote SHAFFER_LONG_HORIZON_DATA.md")
        finally:
            st.close()
        return 0
    if args.cmd == "lab" and args.hedge_panel:
        from .data.store import Store
        from .engine.research import Research
        from .hedge import hedgelive
        st = Store(app.db_path())
        try:
            r = Research(st)
            v = hedgelive.activate(st)
            print("live shadow:", (v or {}).get("id"), list(((v or {}).get("live_cells") or {}).keys()))
            print("graded:", hedgelive.grade(r, progress=print))
            print("recorded:", hedgelive.record(r, progress=print, force=True))
        finally:
            st.close()
        return 0
    if args.cmd == "lab" and args.live_panel:
        from .data.store import Store
        from .engine import livexs
        from .engine.research import Research
        st = Store(app.db_path())
        try:
            print(livexs.record_panel(Research(st), progress=print, force=True))
        finally:
            st.close()
        return 0
    if args.cmd == "lab" and args.residual:
        from .data.store import Store
        from .engine import residual, residual_report
        here = os.path.dirname(os.path.abspath(__file__))
        res = residual.run_all(app.db_path(), workers=args.workers, progress=print)
        if res.get("aborted"):
            print(res["aborted"])
            return 1
        st = Store(app.db_path())
        try:
            residual.save(st, res)
            print("registered (research only):", ", ".join(residual.register(st, res)))
        finally:
            st.close()
        open(os.path.join(here, "SHAFFER_RESIDUAL_ML.md"), "w", encoding="utf-8").write(residual_report.markdown(res))
        open(os.path.join(here, "SHAFFER_META_CURRENT.md"), "w", encoding="utf-8").write(residual_report.current_markdown(res))
        print(f"residual program: {res['seconds']}s; wrote SHAFFER_RESIDUAL_ML.md and SHAFFER_META_CURRENT.md")
        return 0
    if args.cmd == "lab" and args.finetune:
        from .data.store import Store
        from .engine import finetune, finetune_report, learned
        here = os.path.dirname(os.path.abspath(__file__))
        res = finetune.run_all(app.db_path(), workers=args.workers, progress=print)
        st = Store(app.db_path())
        try:
            lw = learned.load(st)
            finetune.save(st, res)
            st.kv_set("lab:hedgetune", res.get("hedge"))
            print("registered:", ", ".join(finetune.register(st, res)))
            from .hedge import hedgelive
            hv = hedgelive.activate(st)                  # passing λ-cells go into live shadow (graded by hedgelive)
            if hv:
                print("hedge live shadow:", hv["id"], ", ".join(hv["live_cells"]))
        finally:
            st.close()
        print("live models:", finetune.build_live(app.db_path(), res, progress=print))
        open(os.path.join(here, "SHAFFER_FINETUNE.md"), "w", encoding="utf-8").write(finetune_report.markdown(res, lw))
        open(os.path.join(here, "SHAFFER_HEDGE_FINETUNE.md"), "w", encoding="utf-8").write(finetune_report.hedge_markdown(res.get("hedge") or {}))
        print(f"fine-tune: {res['seconds']}s; wrote SHAFFER_FINETUNE.md and SHAFFER_HEDGE_FINETUNE.md")
        return 0
    if args.cmd == "lab" and (args.vnext or args.fetch_sec_extra):
        from .data.store import Store
        here = os.path.dirname(os.path.abspath(__file__))
        if args.fetch_sec_extra:
            from .data import sec_extra
            st = Store(app.db_path())
            try:
                print(sec_extra.refresh(st, [a["id"] for a in st.assets() if a.get("asset_class") == "EQUITY"], progress=print))
            finally:
                st.close()
        if args.vnext:
            from .engine import alphanext, dirnext, vnext
            from .hedge import hedgenext
            which = ["alpha", "directional", "hedge"] if args.vnext == "all" else [args.vnext]
            out = {}
            if "alpha" in which:
                out["alpha"] = alphanext.run_all(app.db_path(), workers=args.workers, progress=print)
                open(os.path.join(here, "SHAFFER_ALPHA_VNEXT.md"), "w", encoding="utf-8").write(alphanext.markdown(out["alpha"]))
            if "directional" in which:
                out["directional"] = dirnext.run_all(app.db_path(), workers=min(2, args.workers), progress=print)
                open(os.path.join(here, "SHAFFER_DIRECTIONAL_VNEXT.md"), "w", encoding="utf-8").write(dirnext.markdown(out["directional"]))
            if "hedge" in which:
                out["hedge"] = hedgenext.run_all(app.db_path(), workers=args.workers, progress=print)
                open(os.path.join(here, "SHAFFER_HEDGE_VNEXT.md"), "w", encoding="utf-8").write(hedgenext.markdown(out["hedge"]))
            st = Store(app.db_path())
            try:
                ra, rd, rh = (out.get("alpha") or st.kv_get(alphanext.RESEARCH_KEY), out.get("directional") or st.kv_get(dirnext.RESEARCH_KEY),
                              out.get("hedge") or st.kv_get(hedgenext.RESEARCH_KEY))
                vnext.register(st, ra, rd, rh)
            finally:
                st.close()
            open(os.path.join(here, "SHAFFER_VNEXT_SUMMARY.md"), "w", encoding="utf-8").write(vnext.summary(ra, rd, rh))
            print("vNext reports written")
        return 0
    if args.cmd == "lab" and args.alpha_shadow:
        from .data.store import Store
        from .engine import ahzlive
        from .engine.research import Research
        st = Store(app.db_path())
        try:
            if args.alpha_shadow == "record":
                print(ahzlive.record(Research(st), progress=print))
            print(ahzlive.markdown(ahzlive.summary(st)))
        finally:
            st.close()
        return 0
    if args.cmd == "lab" and (args.iv or args.eulerpool):
        from .data.store import Store
        from .engine.lab import benchmark
        from .engine.lab import SIG_VERSION
        st = Store(app.db_path())
        try:
            missing = []
            if not st._q("SELECT 1 FROM lab_records WHERE version = ? LIMIT 1", (SIG_VERSION,)):
                missing.append("the point-in-time research records: `python -m finsim2 lab --build` (long: hours for 500 stocks)")
            if not benchmark(st):
                missing.append("a frozen benchmark: `python -m finsim2 lab` (the weight and Directional research), then "
                               "`python -m finsim2 lab --freeze-benchmark`")
            if args.eulerpool and not st._q("SELECT 1 FROM alt_data WHERE dataset = 'ep_surprises' LIMIT 1"):
                missing.append("the Eulerpool data: `python -m finsim2 data eulerpool --force`")
        finally:
            st.close()
        if missing:
            print("This research needs, in this order:\n  - " + "\n  - ".join(missing))
            return 2
    if args.cmd == "lab" and args.eulerpool:
        import re
        from .engine import newinfo
        res = newinfo.run_all(app.db_path(), workers=args.workers, progress=print, families=newinfo.BATCH_EP, key=newinfo.EP_KEY)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_EP_RESEARCH.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write("# Analyst expectations from Eulerpool — research\n\nProtocol `SHAFFER_EP_PROTOCOL.md` (fixed before any result). "
                    "Research only; Data by Eulerpool (non-commercial).\n\n" + re.sub(r"(?m)^(#+) ", r"#\1 ", newinfo.markdown(res)))
        print(f"analyst-expectations research: {res['seconds']}s; wrote", out)
        return 0
    if args.cmd == "lab" and args.iv:
        from .engine import ivstudy, newinfo
        fam = newinfo.run_all(app.db_path(), workers=args.workers, progress=print, families=newinfo.BATCH_IV, key=newinfo.IV_KEY)
        ms = ivstudy.run(app.db_path(), progress=print)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_IV_RESEARCH.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(ivstudy.report(ms, fam))
        print(f"implied-volatility research: {fam['seconds']}s families; wrote", out)
        return 0
    if args.cmd == "lab" and args.move_size:
        from .engine import movesize
        res, view = movesize.run(app.db_path(), progress=print)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_MOVE_SIZE.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(movesize.markdown(res, view))
        print("move-size research done; wrote", out)
        return 0
    if args.cmd == "lab" and args.event_hedge:
        from .hedge import volhedge
        res = volhedge.run_all(app.db_path(), workers=args.workers, progress=print, family="event_calendar")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_EVENT_HEDGE.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(volhedge.markdown(res))
        print(f"event-volatility hedge research: {res['seconds']}s; wrote", out)
        return 0
    if args.cmd == "lab" and args.breadth_hedge:
        from .hedge import volhedge
        res = volhedge.run_all(app.db_path(), workers=args.workers, progress=print)
        with open(args.breadth_hedge_report, "w", encoding="utf-8") as f:
            f.write(volhedge.markdown(res))
        print(f"breadth-volatility hedge research: {res['seconds']}s; wrote", args.breadth_hedge_report)
        return 0
    if args.cmd == "lab" and args.alpha_horizons:
        from .data.store import Store
        from .engine import alphahz
        from .engine import newinfo
        if args.alpha_horizons in ("build", "all"):
            st = Store(app.db_path())
            try:
                extra = alphahz.passed_extra(st.kv_get(newinfo.BATCH2_KEY) or {})
            finally:
                st.close()
            print("new-data features included:", [f for _, f in extra] or "none")
            print(alphahz.build(app.db_path(), workers=args.workers, extra=extra, progress=print))
        if args.alpha_horizons in ("study", "all"):
            res = alphahz.run(app.db_path(), progress=print)
            st = Store(app.db_path())
            try:
                from .engine.research import Research
                view = alphahz.today(st, Research(st), res)
                st.kv_set(alphahz.RESEARCH_KEY + ":today", view)
            finally:
                st.close()
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SHAFFER_ALPHA_HORIZONS.md")
            with open(out, "w", encoding="utf-8") as f:
                f.write(alphahz.markdown(res, view))
            print(f"alpha horizons: {res['seconds']}s; wrote", out)
        return 0
    if args.cmd == "lab" and args.insider_500:
        from .data.store import Store
        from .engine import newinfo
        st = Store(app.db_path())
        try:
            smp = newinfo.insider500_sample(st)
        finally:
            st.close()
        fresh, every = smp["fresh"], smp["every"]
        print(f"fresh sample: {len(fresh)} of {smp['candidates']} expanded stocks have Form 4 history before "
              f"{newinfo.INSIDER_HISTORY_BEFORE} ({smp['coverage']:.0%})")
        if smp["coverage"] < newinfo.INSIDER_MIN_COVERAGE or len(fresh) < 60:
            print("the insider history is incomplete: run `python -m finsim2 data sec --force` (and, without research records, "
                  "`python -m finsim2 lab --build`) first")
            return 1
        base = {"protocol": "NEW_DATA_INSIDER_500_PROTOCOL.md", "command": "--insider-500"}
        res = newinfo.run_all(app.db_path(), workers=args.workers, progress=print, families=["insider"], key=newinfo.INSIDER500_KEY,
                              assets=fresh, sample={**base, "title": "Insider activity on the expanded universe (fresh sample)",
                                                    "description": "only the stocks added by the universe expansion; the 45 stage-1 stocks are excluded"})
        info = newinfo.run_all(app.db_path(), workers=args.workers, progress=print, families=["insider"], key=newinfo.INSIDER500_KEY + ":all",
                               assets=every, sample={**base, "title": "Insider activity, stage-1 and expanded stocks combined (information only)",
                                                     "description": "the 45 stage-1 stocks plus the expanded stocks — re-uses the stage-1 sample, not part of the decision"})
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NEW_DATA_INSIDER_500.md")
        with open(out, "w", encoding="utf-8") as f:
            import re
            f.write(newinfo.markdown(res) + "\n---\n\n" + re.sub(r"(?m)^(#+) ", r"#\1 ", newinfo.markdown(info)))   # one level down
        print(f"insider re-test: {res['seconds']}s + {info['seconds']}s; wrote", out)
        return 0
    if args.cmd == "lab" and (args.newinfo or args.live_models or args.fetch_finra or args.new_data):
        from .data.store import Store
        if args.fetch_finra:
            from .data import finra
            from .engine.research import Research
            st = Store(app.db_path())
            try:
                ids = [a["id"] for a in st.assets() if a.get("asset_class") in ("EQUITY", "ETF")]
                cal = Research(st).panel().calendar()
                print(finra.refresh(st, ids, [d for d in cal if d >= finra.FIRST_AVAILABLE], progress=print))
            finally:
                st.close()
        if args.live_models:
            from .engine import directional
            from .engine.research import Research
            st = Store(app.db_path())
            try:
                directional.fit_live(st, Research(st), progress=print)
            finally:
                st.close()
        if args.new_data:
            from .engine import newinfo
            res = newinfo.run_all(app.db_path(), workers=args.workers, progress=print, families=newinfo.BATCH2, key=newinfo.BATCH2_KEY)
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NEW_DATA_SIGNALS.md")
            with open(out, "w", encoding="utf-8") as f:
                f.write(newinfo.markdown(res))
            print(f"new-data signal research: {res['seconds']}s; wrote", out)
        if args.newinfo:
            from .engine import newinfo
            res = newinfo.run_all(app.db_path(), workers=args.workers, progress=print)
            with open(args.newinfo_report, "w", encoding="utf-8") as f:
                f.write(newinfo.markdown(res))
            print(f"new-information research: {res['seconds']}s; wrote", args.newinfo_report)
        if args.live_models:
            from .engine import newinfo
            from .engine.research import Research
            st = Store(app.db_path())
            try:
                if st.kv_get(newinfo.RESEARCH_KEY):
                    newinfo.fit_live(st, Research(st), progress=print)
                else:
                    print("no new-information research yet: its live-shadow models are fitted after `lab --newinfo`")
            finally:
                st.close()
        return 0
    if args.cmd == "lab":
        from .engine import lab
        if args.build:
            from .engine.audit import run_universe
            run_universe(app.db_path(), workers=args.workers, progress=print, skip_ml=True)
        if args.directional:
            from .engine import directional
            dr = directional.run_all(app.db_path(), workers=args.workers, progress=print)
            with open(args.directional_report, "w", encoding="utf-8") as f:
                f.write(directional.markdown(dr))
            print(f"directional research: {dr['seconds']}s; wrote", args.directional_report)
            return 0
        if not args.weights:
            res = lab.run_parallel(app.db_path(), workers=args.workers, progress=print)
            print("family-weight challengers:", ", ".join(res.get("challengers") or []) or "none", f"({res['seconds']}s)")
        from .engine import weights
        wr = weights.run_all(app.db_path(), workers=args.workers, progress=print)
        for lab_, hz in sorted(wr["horizons"].items(), key=lambda kv: dict(lab.LAB_HORIZONS).get(kv[0], 0)):
            b = (hz.get("challengers") or {}).get(hz.get("best") or "", {})
            print(f"{lab_}: best {hz.get('best')} -> {(b.get('gates') or {}).get('status', hz.get('reason', '-'))}")
        print(f"signal-weight research: {wr['seconds']}s")
        from .data.store import Store
        st = Store(app.db_path())
        try:
            live = {v["id"]: lab.live_gate(st, v["id"]) for v in lab.registry(st)["versions"] if v.get("family") == "signal-weights"}
        finally:
            st.close()
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(weights.markdown(wr, live))
        print("wrote", args.report)
        return 0
    if args.cmd == "hedge-audit":
        from .data.store import Store
        from .engine.research import Research
        from .hedge import audit as haudit
        res = haudit.run(Research(Store(app.db_path())), progress=print)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(haudit.markdown(res))
        print("wrote", args.out)
        return 0
    if args.cmd == "probe":
        from finsim import app
        from .data import eulerpool
        os.makedirs(app.home(), exist_ok=True)
        print(eulerpool.probe(app.home(), print))
        return 0
    if args.cmd in ("data", "universe", "import-estimates", "import-options", "import-options-dump"):
        return _data_cmd(args)
    if args.cmd == "movesize":
        return _movesize_cmd(args)
    if args.cmd == "system":
        return _system_cmd(args)
    if args.cmd == "news":
        return _news_cmd(args)
    if args.cmd == "open":
        return app.cmd_open()
    if args.cmd == "install":
        return app.cmd_install(open_after=not args.no_open)
    if args.cmd == "uninstall":
        return app.cmd_uninstall(keep_data=not args.purge)
    if args.cmd == "status":
        return app.cmd_status()
    if args.cmd == "stop":
        if not app.health():
            print("FinSim2 is not running")
            return 0
        app._stop_server()
        print("FinSim2 stopped")
        return 0
    if args.cmd == "phone":
        return app.cmd_phone(args.state)
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Before/after check of the weekly live panel on a copy of a research DB: same rows, same cached results.

    git -C <repo> worktree add /tmp/finsim-baseline <baseline commit>     # or: git archive <commit> finsim | tar -x -C DIR
    python3 tools/panel_identity.py --db ~/.finsim2/research.db --baseline /tmp/finsim-baseline/finsim [--workers N]

Runs `livexs.record_panel` twice, each on its own copy of the DB and in its own process: with the baseline code tree
(its default, one asset at a time) and with this tree (`--workers`, default every CPU). Both use the same hash seed:
the cached result's dict orders depend on it (summarize's by-regime table iterates a set of strings). Then compares
what the two runs wrote:
  * every prediction row, all columns but the time it was written (ids included: the rows were written in the same
    order);
  * every cached shaffer_full result (kv shaffer2:*) and every stored checkpoint (kv shaffer_cp:*), byte for byte;
  * the panel record (without its timing).
By default the cached shaffer_full results are dropped from the copies first, so every asset is swept (what the first
panel after a data refresh does); --keep-cache keeps them. The source DB is never written to.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.dirname(HERE)


def _child(root: str, db: str, workers: str) -> None:
    sys.path.insert(0, root)
    from finsim2.data.store import Store
    from finsim2.engine import livexs
    from finsim2.engine.research import Research
    st = Store(db)
    kw = {} if workers == "baseline" else {"workers": int(workers) or None}
    t0 = time.time()
    rec = livexs.record_panel(Research(st), progress=lambda m: None if m.startswith("live panel ") and "/" in m else print(m, flush=True),
                              force=True, **kw)
    st.close()
    print(json.dumps({"seconds": time.time() - t0, "record": rec}), flush=True)


def _written(db: str) -> dict:
    c = sqlite3.connect(db)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(predictions)")]
        rows = [tuple(v for k, v in zip(cols, r) if k != "created_at") for r in c.execute("SELECT * FROM predictions ORDER BY id")]
        kv = dict(c.execute("SELECT key, value FROM kv WHERE key LIKE 'shaffer2:%' OR key LIKE 'shaffer_cp:%'").fetchall())
        return {"cols": [k for k in cols if k != "created_at"], "rows": rows, "kv": kv}
    finally:
        c.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="the research DB (only copied)")
    ap.add_argument("--baseline", help="the baseline finsim tree (the directory holding finsim2/)")
    ap.add_argument("--workers", type=int, default=0, help="worker processes for this tree's panel (default: every CPU)")
    ap.add_argument("--keep-cache", action="store_true", help="keep the cached shaffer_full results in the copies")
    ap.add_argument("--dir", default=None, help="where to put the copies (default: a temporary directory, removed)")
    ap.add_argument("--child", nargs=3, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.child:
        _child(*args.child)
        return 0
    if not args.db or not args.baseline:
        ap.error("--db and --baseline are required")
    work = args.dir or tempfile.mkdtemp(prefix="panel-identity-")
    os.makedirs(work, exist_ok=True)
    runs = {}
    try:
        for name, root, workers in (("before", os.path.abspath(args.baseline), "baseline"), ("after", TREE, str(args.workers))):
            db = os.path.join(work, f"{name}.db")
            src = sqlite3.connect(args.db)
            dst = sqlite3.connect(db)
            src.backup(dst)                                  # a consistent copy, even of a DB in WAL mode
            src.close()
            if not args.keep_cache:
                dst.execute("DELETE FROM kv WHERE key LIKE 'shaffer2:%'")
                dst.commit()
            dst.close()
            env = {**os.environ, "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0")}
            print(f"[{name}] {root} ({'one asset at a time' if workers == 'baseline' else (workers if workers != '0' else 'every CPU') + ' workers'})", flush=True)
            out = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", root, db, workers], env=env, capture_output=True, text=True)
            if out.returncode:
                print(out.stdout, out.stderr, sep="\n")
                return 2
            lines = out.stdout.strip().splitlines()
            for line in lines[:-1]:
                print(f"[{name}]   {line}")
            res = json.loads(lines[-1])
            runs[name] = {**res, "written": _written(db)}
            rec = res["record"]
            print(f"[{name}] {res['seconds']:.1f}s — {rec.get('assets')} assets, {rec.get('rows')} challenger rows, {len(rec.get('errors') or [])} errors", flush=True)
        a, b = runs["before"]["written"], runs["after"]["written"]
        ok = True
        if a["rows"] != b["rows"]:
            ok = False
            diff = next((i for i, (x, y) in enumerate(zip(a["rows"], b["rows"])) if x != y), min(len(a["rows"]), len(b["rows"])))
            print(f"prediction rows DIFFER ({len(a['rows'])} vs {len(b['rows'])}; first difference at row {diff})")
        else:
            print(f"prediction rows identical: {len(a['rows'])} (columns {', '.join(a['cols'])})")
        for prefix in ("shaffer2:", "shaffer_cp:"):
            ka = {k: v for k, v in a["kv"].items() if k.startswith(prefix)}
            kb = {k: v for k, v in b["kv"].items() if k.startswith(prefix)}
            bad = sorted(k for k in set(ka) | set(kb) if ka.get(k) != kb.get(k))
            ok &= not bad
            print(f"{prefix}* entries {'identical' if not bad else 'DIFFER'}: {len(ka)}" + (f" ({len(bad)} differ: {', '.join(bad[:5])})" if bad else ""))
        ra, rb = ({k: v for k, v in runs[n]["record"].items() if k != "seconds"} for n in ("before", "after"))
        ok &= ra == rb
        print(f"panel record {'identical' if ra == rb else 'DIFFERS'}")
        print(f"runtime: before {runs['before']['seconds']:.1f}s, after {runs['after']['seconds']:.1f}s "
              f"({runs['before']['seconds'] / max(runs['after']['seconds'], 1e-9):.1f}x)")
        print("IDENTICAL" if ok else "NOT IDENTICAL")
        return 0 if ok else 1
    finally:
        if not args.dir:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

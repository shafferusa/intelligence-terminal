"""Extended-history research records (research only) — more data for the long-horizon Alpha and Directional studies.

The standard signal-level research records (lab_records version lab.SIG_VERSION) begin where the production sweep's
reconstructed record begins: at most shaffer_score.HISTORY_YEARS (25) years back, i.e. 2001 for the oldest assets.
That window is part of production (the sweep's validation and calibration run on it), so it is not changed here.

But the learned models do not need production's score — only each record's signals x (clip(z / S_SCALE, ±1), the
point-in-time z-scores of the 74 production signals) and its outcome. Those exist before 2001 for every asset whose
data do: the panel calendar starts 1993-01-29 and most signals from 1994. These records are written under their own
version (EXT_VERSION) for the weeks BEFORE an asset's first standard record, with:
    raw = None                 (no production score: they can only ever be TRAINING records — every test era
                                starts in 2009 — and never enter a comparison with production)
    x   = clip(z / S_SCALE, −1, 1) per production signal present on that date
    y   = the vol-scaled forward log return, clipped ±4 (exactly ShafferRun's target), yr = the raw log return
    a, g = {}                  (production's active-signal pieces do not exist for these dates)
on the same weekly grid (every 5 sessions, phase-aligned to the asset's first standard record).

What does not exist before 2001 and cannot be manufactured: SEC fundamentals (XBRL, 2009 on) — those signals are
absent (x = 0, like any missing signal) in extended records — and some macro series (see data_gaps()).
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from .. import shaffer_score as cfg

EXT_START = "1994-01-01"
WARMUP = 252                 # sessions of an asset's own history before its first extended record
MIN_PRESENT = 0.5            # share of production signals present on the date


def ext_version() -> str:
    from .lab import SIG_VERSION
    return f"{SIG_VERSION}x"


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def build(research, labs=("1M", "3M", "6M", "12M", "1W"), progress=None, start: str = EXT_START) -> dict:
    from . import weights as W
    from .lab import LAB_HORIZONS, SIG_VERSION
    say = progress or (lambda m: None)
    st = research.store
    panel = research.panel()
    cal = panel.calendar()
    idx = {d: i for i, d in enumerate(cal)}
    sigs = W.signals()
    hmap = dict(LAB_HORIZONS)
    lo_cal = next((i for i, d in enumerate(cal) if d >= start), 0)
    out = {"version": ext_version(), "labs": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    dv = research.version()
    std = {lab: {b["asset_id"]: b["rows"][0][0] for b in st.lab_records(SIG_VERSION, lab) if b["rows"]} for lab in labs}
    assets = sorted({a for d in std.values() for a in d})
    counts = {lab: [0, 0] for lab in labs}
    for k, a in enumerate(assets):
        px = panel.series(a)
        first = next((i for i, v in enumerate(px) if v), None)
        if first is None:
            continue
        lo = max(lo_cal, first + WARMUP)
        need = {lab: idx.get(std[lab].get(a)) for lab in labs if std[lab].get(a)}
        need = {lab: f for lab, f in need.items() if f is not None and f - 5 >= lo}
        if not need:
            continue
        say(f"extended records {k + 1}/{len(assets)}: {a}")
        z = research.zscores(a)
        vol = research.features(a).get("vol_60") or [None] * len(cal)
        for lab, f in need.items():
            h = hmap[lab]
            rows = []
            for t in range(f - 5, lo - 1, -5):
                if t + h >= f:                               # only outcomes that matured before the standard record starts
                    continue
                x = {s: round(_clip(z[s][t] / cfg.S_SCALE, -1.0, 1.0), 4) for s in sigs if (z.get(s) or [None] * len(cal))[t] is not None}
                if len(x) < MIN_PRESENT * len(sigs):
                    continue
                a0, b0, v = px[t], px[t + h], vol[t]
                if not a0 or not b0 or a0 <= 0 or b0 <= 0 or not v:
                    continue
                yr = math.log(b0 / a0)
                rows.append([cal[t], None, round(_clip(yr / (v * math.sqrt(h / 252.0)), -4.0, 4.0), 6), round(yr, 6), {"x": x, "a": {}, "g": {}}])
            if rows:
                rows.sort(key=lambda r: r[0])
                st.put_lab_records(a, lab, ext_version(), dv, rows)
                counts[lab][0] += 1
                counts[lab][1] += len(rows)
    for lab in labs:
        out["labs"][lab] = {"assets": counts[lab][0], "records": counts[lab][1]}
    st.kv_set("lab:extrecords", out)
    return out


def data_gaps(research) -> dict:
    """What the store holds before the standard records begin: calendar, prices by asset class, signal coverage by
    family, fundamentals and macro series."""
    from . import weights as W
    from .lab import SIG_VERSION
    st = research.store
    panel = research.panel()
    cal = panel.calendar()
    out = {"calendar_start": cal[0], "sessions": len(cal)}
    firsts = {}
    for a in st.assets():
        px = panel.series(a["id"])
        i = next((k for k, v in enumerate(px) if v), None)
        if i is not None:
            firsts[a["id"]] = (cal[i], a.get("asset_class"))
    by_cls: Dict[str, Dict[str, int]] = {}
    for a, (d, c) in firsts.items():
        b = "before 1995" if d < "1995" else ("1995–2000" if d < "2001" else ("2001–2008" if d < "2009" else "2009 or later"))
        by_cls.setdefault(c or "?", {}).setdefault(b, 0)
        by_cls[c or "?"][b] += 1
    out["prices_first_by_class"] = by_cls
    std = {}
    for blob in st.lab_records(SIG_VERSION, "1M"):
        if blob["rows"]:
            std[blob["asset_id"]] = blob["rows"][0][0]
    out["standard_records_first"] = {k: sum(1 for d in std.values() if d[:4] == k) for k in sorted({d[:4] for d in std.values()})}
    fam_of = cfg.FAMILY_OF
    cover: Dict[str, List[str]] = {}
    probe = [a for a in list(firsts)[:200]]
    sample_years = ["1995", "1998", "2001", "2005", "2009", "2015", "2025"]
    yi = {y: next((k for k, d in enumerate(cal) if d[:4] == y), None) for y in sample_years}
    fam_cov: Dict[str, Dict[str, float]] = {}
    zs = {a: research.zscores(a) for a in probe}
    for s in W.signals():
        f = fam_of.get(s, "?")
        for y, k in yi.items():
            if k is None:
                continue
            n = sum(1 for a in probe if (zs[a].get(s) or [None] * len(cal))[k] is not None)
            fam_cov.setdefault(f, {}).setdefault(y, []).append(n / max(1, len(probe)))
    out["signal_coverage_by_family"] = {f: {y: round(sum(v) / len(v), 3) for y, v in ys.items()} for f, ys in fam_cov.items()}
    try:
        r = st._q("SELECT min(filed), count(DISTINCT asset_id) FROM fundamentals")[0]
        out["fundamentals_first_filed"], out["fundamentals_assets"] = r[0], r[1]
    except Exception:  # noqa: BLE001
        out["fundamentals_first_filed"] = None
    try:
        out["macro_first"] = {r[0]: r[1] for r in st._q("SELECT series, min(date) FROM macro GROUP BY series")}
    except Exception:  # noqa: BLE001
        out["macro_first"] = {}
    return out

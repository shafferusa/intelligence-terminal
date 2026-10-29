"""FX_CCS_PLAN rev 2, package P-A task 5 (risk R6): a read-only REAL-DATA self-check of the RFR and term-curve conventions, run by the
verifier (package V) before the gate.  Network, gentle: six requests, each source asked once (the Bank of England with the project agent
GOV_UA only; a 403 is reported "unavailable", never retried with another agent).  It writes nothing but the optional ``--json`` report.

    py -3.12 tests\\terminal_rfr_selfcheck.py [--quarter 2026Q3] [--json <report.json>]      from C:\\Users\\logan\\intelligence-terminal\\finsim

Checks
1. ESTR: over the last full calendar quarter [s, e) (s, e = its first and the next quarter's first TARGET2 day), the ECB compounded index
   ratio I(e) / I(s) equals the daily ESTR prints compounded over the TARGET2 days with n_i / 360 weights: |difference| < 1e-8.
2. SOFR: the NY Fed SOFR Index ratio over the same quarter (first US government-securities days) equals the daily SOFR prints
   compounded over the published days with n_i / 360 weights: |difference| < 1e-8 (the index is published to 8 decimals of ~1.26).
3. Bank of England: on the latest row of the yield-curve file, the 1Y and 2Y spot rates ("3. spot, short end") equal the average of the
   published instantaneous forwards ("1. fwds, short end": flat from 0 to the first maturity, trapezoids after) within 1 bp - this pins
   continuous compounding (annual compounding would differ by about 10 bp at 2Y).
4. ECB: on the latest row, SR_2Y equals the Svensson formula of the published parameters at 2 years within 1e-6.
Exit 0 when every check passes, 1 when one fails, 2 when a source is unavailable (nothing is assumed in its place).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from finsim.terminal import ccycal  # noqa: E402
from finsim.terminal.config import GOV_UA, TERMINAL_UA  # noqa: E402
from finsim.terminal.quotes import parsers as P  # noqa: E402
from finsim.terminal.quotes.http import FetchError, http_get  # noqa: E402
from finsim.terminal.quotes.providers import BOE_CURVE_URL, ECB_AAA_SPOT_KEY, ECB_URL  # noqa: E402

D = Decimal
ECB_RANGE = "https://data-api.ecb.europa.eu/service/data/EST/{key}?startPeriod={s}&endPeriod={e}&format=csvdata"
NYFED_RANGE = "https://markets.newyorkfed.org/api/rates/secured/{kind}/search.json?startDate={s}&endDate={e}"
TOL_INDEX = D("1e-8")
TOL_BOE_PCT = D("0.01")         # 1 bp in percent
TOL_SV_PCT = D("0.000001")


def get(url: str, ua: str, accept: str) -> bytes:
    return http_get(url, ua, timeout=60.0, accept=accept)


def last_full_quarter(today: date):
    q = (today.month - 1) // 3                 # 0..3: the current quarter
    y, q = (today.year - 1, 3) if q == 0 else (today.year, q - 1)
    start = date(y, 3 * q + 1, 1)
    nxt = date(y + (q == 3), 1 if q == 3 else 3 * q + 4, 1)
    return start, nxt


def parse_quarter(text: str):
    y, q = int(text[:4]), int(text[-1])
    start = date(y, 3 * (q - 1) + 1, 1)
    return start, date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)


def nyfed_rows(raw: bytes, field: str):
    data = json.loads(raw.decode("utf-8", "replace"), parse_float=Decimal)
    out = {}
    for r in data.get("refRates") or []:
        d = date.fromisoformat(str(r.get("effectiveDate"))[:10])
        v = r.get(field)
        if v is not None:
            out[d] = D(str(v))
    return out


def check_estr(qs: date, qe: date) -> dict:
    s, e = ccycal.TARGET.roll_following(qs), ccycal.TARGET.roll_following(qe)
    pr = P.parse_ecb_csv_exact(get(ECB_RANGE.format(key="B.EU000A2X2A25.WT", s=s.isoformat(), e=e.isoformat()), TERMINAL_UA, "text/csv"))
    ix = P.parse_ecb_csv_exact(get(ECB_RANGE.format(key="B.EU000A2QQF08.CI", s=s.isoformat(), e=e.isoformat()), TERMINAL_UA, "text/csv"))
    prints = dict(next(iter(pr.values()), []))
    index = dict(next(iter(ix.values()), []))
    if s not in index or e not in index:
        return {"ok": None, "unavailable": f"the ECB index of {s} or {e} is missing"}
    with localcontext() as c:
        c.prec = 34
        f, d, missing = D(1), s, []
        while d < e:
            nxt = ccycal.TARGET.next_business_day(d)
            if d not in prints:
                missing.append(d.isoformat())
            else:
                f *= 1 + prints[d] / 100 * (nxt - d).days / D(360)
            d = nxt
        ratio = index[e] / index[s]
        diff = abs(ratio - f)
    return {"ok": not missing and diff < TOL_INDEX, "start": s.isoformat(), "end": e.isoformat(), "index_ratio": str(ratio), "compounded": str(f),
            "difference": str(diff), "tolerance": str(TOL_INDEX), "missing_prints": missing, "nights": sum(1 for x in prints if s <= x < e)}


def check_sofr(qs: date, qe: date) -> dict:
    s, e = ccycal.USGS.roll_following(qs), ccycal.USGS.roll_following(qe)
    span = dict(s=s.isoformat(), e=e.isoformat())
    prints = nyfed_rows(get(NYFED_RANGE.format(kind="sofr", **span), TERMINAL_UA, "application/json"), "percentRate")
    index = nyfed_rows(get(NYFED_RANGE.format(kind="sofrai", **span), TERMINAL_UA, "application/json"), "index")
    if s not in index or e not in index:
        return {"ok": None, "unavailable": f"the SOFR Index of {s} or {e} is missing"}
    days = sorted(x for x in prints if s <= x < e)
    with localcontext() as c:
        c.prec = 34
        f, missing = D(1), []
        d = s
        while d < e:
            nxt = ccycal.USGS.next_business_day(d)
            if d not in prints:
                missing.append(d.isoformat())
            else:
                f *= 1 + prints[d] / 100 * (nxt - d).days / D(360)
            d = nxt
        ratio = index[e] / index[s]
        diff = abs(ratio - f)
    return {"ok": not missing and diff < TOL_INDEX, "start": s.isoformat(), "end": e.isoformat(), "index_ratio": str(ratio), "compounded": str(f),
            "difference": str(diff), "tolerance": str(TOL_INDEX), "missing_prints": missing, "nights": len(days)}


def check_boe() -> dict:
    raw = get(BOE_CURVE_URL, GOV_UA, "application/zip, application/octet-stream, */*")
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        inner = P._zip_member(z, P.BOE_OIS_MEMBER)
    spot = P.parse_boe_ois_sheet(P.xlsx_sheet_rows(inner, "3. spot, short end"))
    fwd = P.parse_boe_ois_sheet(P.xlsx_sheet_rows(inner, "1. fwds, short end"))
    days = sorted(set(spot) & set(fwd))
    if not days:
        return {"ok": None, "unavailable": "no day with both the spot and the forward short end"}
    d = days[-1]
    out = {"date": d.isoformat(), "rows": {}}
    ok = True
    with localcontext() as c:
        c.prec = 34
        f = fwd[d]
        months = sorted(int(k[:-1]) for k in f)
        for T in (12, 24):
            pts = [(D(m) / 12, f[f"{m}M"]) for m in months if m <= T]
            acc = pts[0][0] * pts[0][1]                                   # flat from 0 to the first maturity
            for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
                acc += (t1 - t0) * (f0 + f1) / 2
            avg = acc / (D(T) / 12)
            sp = spot[d][f"{T}M"]
            diff = abs(avg - sp)
            ok = ok and diff <= TOL_BOE_PCT
            out["rows"][f"{T}M"] = {"spot_pct": str(sp.quantize(D('0.000001'))), "average_forward_pct": str(avg.quantize(D('0.000001'))),
                                    "difference_bp": str((diff * 100).quantize(D('0.01')))}
    out.update(ok=ok, tolerance_bp="1")
    return out


def check_ecb_svensson() -> dict:
    url = ECB_URL.format(flow="YC", key=ECB_AAA_SPOT_KEY, n=1)
    got = P.parse_ecb_csv_exact(get(url, TERMINAL_UA, "text/csv"))
    from finsim.terminal.quotes.providers import ecb_spot_from
    spot, sv = ecb_spot_from(got)
    days = sorted(set(spot) & set(sv))
    if not days:
        return {"ok": None, "unavailable": "no day with both the spot rates and the Svensson parameters"}
    d = days[-1]
    p = sv[d]
    with localcontext() as c:
        c.prec = 34
        T = D(2)
        x1, x2 = T / p["TAU1"], T / p["TAU2"]
        e1, e2 = (-x1).exp(), (-x2).exp()
        y = p["BETA0"] + p["BETA1"] * (1 - e1) / x1 + p["BETA2"] * ((1 - e1) / x1 - e1) + p["BETA3"] * ((1 - e2) / x2 - e2)
        diff = abs(y - spot[d]["2Y"])
    return {"ok": diff < TOL_SV_PCT, "date": d.isoformat(), "sr_2y_pct": str(spot[d]["2Y"]), "svensson_2y_pct": str(y.quantize(D("0.0000000001"))),
            "difference_pct": str(diff), "tolerance_pct": str(TOL_SV_PCT)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quarter", help="YYYYQn (default: the last full calendar quarter)")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    qs, qe = parse_quarter(a.quarter) if a.quarter else last_full_quarter(today)
    report = {"run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "quarter": [qs.isoformat(), (qe - timedelta(days=1)).isoformat()]}
    for name, fn in (("estr_index", lambda: check_estr(qs, qe)), ("sofr_index", lambda: check_sofr(qs, qe)), ("boe_continuous_compounding", check_boe),
                     ("ecb_svensson", check_ecb_svensson)):
        try:
            report[name] = fn()
        except FetchError as e:
            report[name] = {"ok": None, "unavailable": str(e)[:200]}
        except (ValueError, KeyError) as e:
            report[name] = {"ok": None, "unavailable": f"{type(e).__name__}: {e}"[:200]}
    checks = [report[k] for k in ("estr_index", "sofr_index", "boe_continuous_compounding", "ecb_svensson")]
    code = 2 if any(c.get("ok") is None for c in checks) else (0 if all(c.get("ok") for c in checks) else 1)
    report["result"] = {0: "PASS", 1: "FAIL", 2: "UNAVAILABLE"}[code]
    text = json.dumps(report, indent=1)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())

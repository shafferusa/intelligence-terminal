"""Real economic data for career saves: the series behind CPI, jobs, GDP and the Fed's target rate, from FRED
(the St. Louis Fed's public CSV endpoint, no key needed), cached on disk and stored once in the save so replay
never touches the network.

What a career world takes from it every session:
- the macro state the screens show (growth, inflation, unemployment, policy rate, next meeting), as it was
  *known* on that date: an observation counts only once its release date has passed;
- the day's releases (CPI, jobs, GDP, retail sales, FOMC decisions) with the actual number and the previous one.
  Release dates follow the usual calendar (CPI around the 12th of the following month, jobs the first Friday,
  the GDP advance estimate near the end of the month after the quarter, FOMC decisions on the published
  meeting days); they are approximations of the agencies' exact schedules.

Nothing here moves prices: in a career world the closes are real already. Standard library only.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Dict, List, Optional

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
SERIES = {"cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "unemployment": "UNRATE", "payrolls": "PAYEMS", "gdp": "A191RL1Q225SBEA",
          "fed_upper": "DFEDTARU", "fed_lower": "DFEDTARL", "retail": "RSAFS", "core_pce": "PCEPILFE",
          # housing: the 30-year mortgage rate (weekly), starts and permits (thousands, annual rate), the Case-Shiller national index, existing-home sales
          "mortgage30": "MORTGAGE30US", "housing_starts": "HOUST", "permits": "PERMIT", "home_prices": "CSUSHPISA", "existing_sales": "EXHOSLUSM495S"}
HOUSING_KEYS = ("mortgage30", "housing_starts", "permits", "home_prices", "existing_sales")
# policy rates by currency: the ECB deposit facility rate (daily) and the OECD "immediate rates" (monthly) for the rest; a
# series FRED does not carry is skipped and that currency keeps the model's rate
POLICY_SERIES = {"EUR": "ECBDFR", "GBP": "IRSTCI01GBM156N", "JPY": "IRSTCI01JPM156N", "CHF": "IRSTCI01CHM156N", "CAD": "IRSTCI01CAM156N", "AUD": "IRSTCI01AUM156N",
                 "NZD": "IRSTCI01NZM156N", "SEK": "IRSTCI01SEM156N", "NOK": "IRSTCI01NOM156N", "MXN": "IRSTCI01MXM156N", "BRL": "IRSTCI01BRM156N", "CNH": "IRSTCI01CNM156N",
                 "KRW": "IRSTCI01KRM156N", "INR": "IRSTCI01INM156N", "IDR": "IRSTCI01IDM156N", "ZAR": "IRSTCI01ZAM156N", "PLN": "IRSTCI01PLM156N", "CZK": "IRSTCI01CZM156N",
                 "HUF": "IRSTCI01HUM156N", "TRY": "IRSTCI01TRM156N"}
PEGGED = {"HKD": ("USD", 0.0050), "SAR": ("USD", 0.0025)}      # the HKMA base rate and SAMA's repo rate sit just above the Fed's
SERIES.update({f"policy_{c}": sid for c, sid in POLICY_SERIES.items()})
UA = "FinSim/1.0 (local simulator; standard library)"
# published FOMC decision days (second day of each meeting); other years fall back to the third Wednesday of the meeting months
FOMC = {2025: ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"],
        2026: ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]}
MEETING_MONTHS = (1, 3, 4, 6, 7, 9, 10, 12)


def _home() -> str:
    return os.environ.get("FINSIM_HOME") or os.path.join(os.path.expanduser("~"), ".finsim")


def _month_add(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _first_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _third_wednesday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


def fomc_dates(year: int) -> List[date]:
    if year in FOMC:
        return [date.fromisoformat(x) for x in FOMC[year]]
    return [_third_wednesday(year, m) for m in MEETING_MONTHS]


def release_date(kind: str, period: date, roll) -> date:
    """When the observation for `period` (the first of its month/quarter) became public — the usual calendar."""
    if kind in ("cpi", "core_cpi"):
        return roll(_month_add(period, 1).replace(day=12))
    if kind in ("unemployment", "payrolls"):
        return roll(_first_friday(*(_month_add(period, 1).timetuple()[:2])))
    if kind == "retail":
        return roll(_month_add(period, 1).replace(day=16))
    if kind == "core_pce":
        return roll(_month_add(period, 1).replace(day=28))
    if kind == "gdp":                                  # advance estimate ~4 weeks after the quarter
        return roll(_month_add(period, 3).replace(day=28))
    if kind in ("housing_starts", "permits"):          # the Census release around the 17th of the next month
        return roll(_month_add(period, 1).replace(day=17))
    if kind == "existing_sales":                       # NAR, around the 22nd
        return roll(_month_add(period, 1).replace(day=22))
    if kind == "home_prices":                          # Case-Shiller runs two months behind, last Tuesday of the month
        return roll(_month_add(period, 2).replace(day=26))
    return period                                      # weekly series (the mortgage rate) are public on their date


class RealMacro:
    """FRED series by key: {key: {date: value}} with a disk cache; `fetch` is injectable for tests."""

    def __init__(self, cache_path: Optional[str] = None, fetch=None):
        self.cache_path = cache_path or os.path.join(_home(), "realmacro-cache.json")
        self.data: Dict[str, Dict[str, float]] = {}
        self.fetched_at: Dict[str, float] = {}
        self._fetch = fetch or self._fred
        self.last_failure = 0.0                 # a feed that is down is retried every 15 minutes, not on every request
        self._load()

    def _load(self) -> None:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            self.data, self.fetched_at = j.get("data", {}), j.get("fetched_at", {})
        except (OSError, ValueError):
            self.data, self.fetched_at = {}, {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"data": self.data, "fetched_at": self.fetched_at}, f)
        except OSError:
            pass

    @staticmethod
    def _fred(sid: str) -> Dict[str, float]:
        req = urllib.request.Request(FRED.format(sid=sid), headers={"User-Agent": UA})
        out: Dict[str, float] = {}
        body = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = r.read().decode("utf-8")
                break
            except Exception:
                if attempt == 1:
                    raise
                time.sleep(2.0)
        if True:
            for line in body.splitlines()[1:]:
                parts = line.strip().split(",")
                if len(parts) != 2 or parts[1] in ("", "."):
                    continue
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    continue
        return out

    def refresh(self, max_age_s: float = 6 * 3600.0, force: bool = False, budget_s: float = 90.0) -> None:
        """Fetch stale series; stop after `budget_s` or once a series fails outright (the feed is probably unreachable)."""
        now = time.time()
        if not force and now - self.last_failure < 900:
            return
        for key, sid in SERIES.items():
            if not force and key in self.data and now - self.fetched_at.get(key, 0) < max_age_s:
                continue
            if time.time() - now > budget_s:
                break
            try:
                got = self._fetch(sid)
            except urllib.error.HTTPError:
                self.fetched_at[key] = now                    # FRED does not carry it: leave it alone for a while
                continue
            except Exception:
                self.last_failure = time.time()
                break
            if got:
                self.data[key] = got
                self.fetched_at[key] = now
        self._save()

    def snapshot(self, since: Optional[date] = None) -> Dict[str, Dict[str, float]]:
        """The series a save stores (from `since`, default two years back)."""
        since = since or (date.today() - timedelta(days=760))
        return {k: {d: v for d, v in s.items() if d >= since.isoformat()} for k, s in self.data.items()}


# ---------------------------------------------------------------- views over stored series (pure functions: used live and on replay)
def _known(series: Dict[str, float], kind: str, asof: date, roll) -> List[tuple]:
    """(period, value) pairs whose release date is on or before `asof`, oldest first."""
    out = []
    for iso, v in sorted(series.items()):
        p = date.fromisoformat(iso)
        if release_date(kind, p, roll) <= asof:
            out.append((p, v))
    return out


def state_as_of(series: Dict[str, Dict[str, float]], asof: date, roll) -> Dict:
    """Growth, inflation, unemployment, the target rate and the next meeting as known on `asof`."""
    out: Dict = {}
    cpi = _known(series.get("cpi", {}), "cpi", asof, roll)
    if len(cpi) >= 13:
        out["inflation"] = round((cpi[-1][1] / cpi[-13][1] - 1) * 100, 2)
        out["inflation_period"] = cpi[-1][0].isoformat()
    core = _known(series.get("core_cpi", {}), "core_cpi", asof, roll)
    if len(core) >= 13:
        out["core_inflation"] = round((core[-1][1] / core[-13][1] - 1) * 100, 2)
    un = _known(series.get("unemployment", {}), "unemployment", asof, roll)
    if un:
        out["unemployment"] = un[-1][1]
        out["unemployment_period"] = un[-1][0].isoformat()
    pay = _known(series.get("payrolls", {}), "payrolls", asof, roll)
    if len(pay) >= 2:
        out["payrolls_change_k"] = round(pay[-1][1] - pay[-2][1], 1)
    gdp = _known(series.get("gdp", {}), "gdp", asof, roll)
    if gdp:
        out["growth"] = gdp[-1][1]
        out["growth_period"] = gdp[-1][0].isoformat()
    upper = {d: v for d, v in series.get("fed_upper", {}).items() if d <= asof.isoformat()}
    lower = {d: v for d, v in series.get("fed_lower", {}).items() if d <= asof.isoformat()}
    if upper:
        last = max(upper)
        out["policy_rate"] = round(upper[last] / 100, 5)
        out["policy_range"] = [round(lower[max(lower)] / 100, 5) if lower else None, round(upper[last] / 100, 5)]
        out["policy_rate_asof"] = last
    nxt = [m for y in (asof.year, asof.year + 1) for m in fomc_dates(y) if m > asof]
    if nxt:
        out["next_meeting"] = nxt[0].isoformat()
    return out


def releases_on(series: Dict[str, Dict[str, float]], d: date, roll) -> List[Dict]:
    """The real releases published on business day `d` (actual and previous; no consensus is fetched)."""
    out: List[Dict] = []
    for key, kind in (("cpi", "CPI"), ("unemployment", "JOBS"), ("gdp", "GDP"), ("retail", "RETAIL_SALES"), ("core_pce", "CORE_PCE")):
        s = series.get(key) or {}
        for iso in sorted(s):
            p = date.fromisoformat(iso)
            if release_date(key, p, roll) != d:
                continue
            prev_iso = max((k for k in s if k < iso), default=None)
            if key == "cpi":
                def yoy_at(period_iso: Optional[str]) -> Optional[float]:
                    if not period_iso:
                        return None
                    pp = date.fromisoformat(period_iso)
                    ya = date(pp.year - 1, pp.month, 1).isoformat()
                    return (s[period_iso] / s[ya] - 1) * 100 if ya in s else None
                yoy, pyoy = yoy_at(iso), yoy_at(prev_iso)
                mom = (s[iso] / s[prev_iso] - 1) * 100 if prev_iso else None
                out.append({"date": d.isoformat(), "kind": "CPI", "period": iso[:7], "actual": round(yoy, 2) if yoy is not None else None, "previous": round(pyoy, 2) if pyoy is not None else None,
                            "mom": round(mom, 2) if mom is not None else None, "unit": "% YoY", "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED CPIAUCSL"})
            elif key == "unemployment":
                pays = series.get("payrolls") or {}
                pprev = max((k for k in pays if k < iso), default=None)
                chg = (pays[iso] - pays[pprev]) if (iso in pays and pprev) else None
                out.append({"date": d.isoformat(), "kind": "JOBS", "period": iso[:7], "actual": s[iso], "previous": s[prev_iso] if prev_iso else None, "unit": "% unemployment",
                            "payrolls_k": round(chg, 1) if chg is not None else None, "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED UNRATE, PAYEMS"})
            elif key == "gdp":
                out.append({"date": d.isoformat(), "kind": "GDP", "period": f"{p.year} Q{(p.month - 1) // 3 + 1}", "actual": s[iso], "previous": s[prev_iso] if prev_iso else None, "unit": "% q/q annualised",
                            "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED A191RL1Q225SBEA"})
            elif key == "retail":
                mom = (s[iso] / s[prev_iso] - 1) * 100 if prev_iso else None
                out.append({"date": d.isoformat(), "kind": "RETAIL_SALES", "period": iso[:7], "actual": round(mom, 2) if mom is not None else None, "previous": None, "unit": "% m/m",
                            "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED RSAFS"})
            else:
                yago = date(p.year - 1, p.month, 1).isoformat()
                yoy = (s[iso] / s[yago] - 1) * 100 if yago in s else None
                out.append({"date": d.isoformat(), "kind": "CORE_PCE", "period": iso[:7], "actual": round(yoy, 2) if yoy is not None else None, "previous": None, "unit": "% YoY",
                            "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED PCEPILFE"})
    if d in fomc_dates(d.year):
        upper = series.get("fed_upper") or {}
        cur = {k: v for k, v in upper.items() if k <= d.isoformat()}
        if cur:
            last = max(cur)
            before = {k: v for k, v in upper.items() if k < d.isoformat()}
            prev = before[max(before)] if before else cur[last]
            step = cur[last] - prev
            what = "holds" if abs(step) < 1e-9 else ("raises" if step > 0 else "cuts")
            out.append({"date": d.isoformat(), "kind": "FOMC", "actual": round(cur[last] / 100, 4), "previous": round(prev / 100, 4), "unit": "target upper bound",
                        "consensus": None, "surprise": 0.0, "z": 0.0, "rate_shock_bp": 0.0, "equity_shock": 0.0, "source": "FRED DFEDTARU",
                        "headline": f"Fed {what} the target range{'' if abs(step) < 1e-9 else f' by {abs(step) * 100:.0f}bp'}: upper bound {cur[last]:.2f}%",
                        "note": "the decision as published; the target range is FRED's DFEDTARL–DFEDTARU"})
    return out


def policy_rates_as_of(series: Dict[str, Dict[str, float]], asof: date) -> Dict[str, Dict]:
    """Each currency's policy rate as last published on or before `asof`: {ccy: {rate, asof, source}}."""
    out: Dict[str, Dict] = {}
    iso = asof.isoformat()
    upper = {d: v for d, v in series.get("fed_upper", {}).items() if d <= iso}
    usd = round(upper[max(upper)] / 100, 5) if upper else None
    if usd is not None:
        out["USD"] = {"rate": usd, "asof": max(upper), "source": "FRED DFEDTARU"}
    for c, sid in POLICY_SERIES.items():
        s = {d: v for d, v in series.get(f"policy_{c}", {}).items() if d <= iso}
        if s:
            last = max(s)
            out[c] = {"rate": round(s[last] / 100, 5), "asof": last, "source": f"FRED {sid}"}
    for c, (anchor, add) in PEGGED.items():
        if anchor in out:
            out[c] = {"rate": round(out[anchor]["rate"] + add, 5), "asof": out[anchor]["asof"], "source": f"{anchor} + {add * 1e4:.0f}bp (peg)"}
    return out


def upcoming(series: Dict[str, Dict[str, float]], d: date, roll, days: int = 45) -> List[Dict]:
    """The real calendar ahead: the next CPI, jobs, GDP, retail sales and FOMC dates (no consensus)."""
    end = d + timedelta(days=days)
    out = []
    for key, kind in (("cpi", "CPI"), ("unemployment", "JOBS"), ("retail", "RETAIL_SALES"), ("gdp", "GDP")):
        s = series.get(key) or {}
        latest = max(s) if s else None
        if not latest:
            continue
        p = _month_add(date.fromisoformat(latest), 1 if key != "gdp" else 3)
        for _ in range(3):
            rd = release_date(key, p, roll)
            if d < rd <= end:
                out.append({"date": rd.isoformat(), "kind": kind, "consensus": None, "period": p.isoformat()[:7]})
            p = _month_add(p, 1 if key != "gdp" else 3)
    for y in (d.year, d.year + 1):
        for m in fomc_dates(y):
            if d < m <= end:
                out.append({"date": m.isoformat(), "kind": "FOMC", "consensus": None})
    out.sort(key=lambda r: r["date"])
    return out


def housing_as_of(series: Dict[str, Dict[str, float]], asof: date, roll) -> Dict:
    """The housing picture as known on `asof`: the mortgage rate, starts, permits, existing-home sales and the Case-Shiller
    index with its year-on-year change, each with the period it refers to."""
    out: Dict = {}
    for k in ("mortgage30", "housing_starts", "permits", "existing_sales", "home_prices"):
        known = _known(series.get(k, {}), k, asof, roll)
        if not known:
            continue
        p, v = known[-1]
        out[k] = v
        out[k + "_period"] = p.isoformat()
        if len(known) >= 2:
            out[k + "_prev"] = known[-2][1]
        if k == "home_prices":
            yr = [x for x in known if x[0] <= p.replace(year=p.year - 1)]
            if yr:
                out["home_prices_yoy"] = round((v / yr[-1][1] - 1) * 100, 2)
        if k == "mortgage30":
            yr = [x for x in known if x[0] <= p - timedelta(days=364)]
            if yr:
                out["mortgage30_year_ago"] = yr[-1][1]
    return out

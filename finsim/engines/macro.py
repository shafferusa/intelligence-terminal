"""Macro world: growth, inflation, unemployment, a central bank with a reaction
function and a meeting calendar, scheduled economic releases with consensus and
surprise, quarterly earnings with surprises and fundamentals updates, issuer
credit migration and defaults, dealer defaults. Everything is a pure function
of the seed and the date; the daily result is stored in the market close so
replay ingests it instead of recomputing.

Transmission to markets (applied by the market engine on the day):
  * rate shock (bp) on the curve level from release surprises and meetings,
    plus a slow pull of the short end toward the policy rate
  * equity factor shock from surprises (good growth / bad inflation),
    earnings jumps per company
  * vol index bump on surprise days
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

NEUTRAL_RATE = 0.0100          # default real neutral rate; re-calibrated at go-live so the starting configuration is an equilibrium
INFLATION_TARGET = 2.0
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "D"]
RATING_SPREAD = {"AAA": 40, "AA+": 48, "AA": 55, "AA-": 65, "A+": 75, "A": 90, "A-": 105, "BBB+": 125, "BBB": 145, "BBB-": 175, "BB+": 240, "BB": 300,
                 "BB-": 380, "B+": 460, "B": 560, "B-": 700, "CCC+": 900, "CCC": 1200, "D": 3000}
MEETING_MONTHS = (1, 3, 4, 6, 7, 9, 10, 12)
REGIME_GROWTH = {"NORMAL_GROWTH": 2.2, "RATE_HIKING": 1.6, "RECESSION": -1.8, "LIQUIDITY_STRESS": -3.5, "RATE_CUTTING": 1.2}
REGIME_INFL = {"NORMAL_GROWTH": 2.4, "RATE_HIKING": 4.2, "RECESSION": 1.2, "LIQUIDITY_STRESS": 0.6, "RATE_CUTTING": 1.6}


@dataclass
class MacroState:
    growth: float = 2.2            # real GDP growth, % annualised
    inflation: float = 2.4         # CPI YoY %
    unemployment: float = 4.1      # %
    policy_rate: float = 0.0435
    next_meeting: str = ""
    last_decision: str = ""
    cycle_hikes: int = 0


@dataclass
class IssuerState:
    rating: str
    base_rating: str
    spread_bps: float
    defaulted: bool = False
    default_date: Optional[str] = None
    watch: str = "STABLE"          # STABLE | NEGATIVE | POSITIVE


def third_wednesday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


def first_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


class MacroModel:
    def __init__(self, seed: int, securities: Dict, cal):
        self.seed = seed
        self.cal = cal
        self.state = MacroState()
        self.history: List[Dict] = []
        self.releases: List[Dict] = []          # {date, kind, consensus, actual, surprise, note}
        self.went_live = False                  # set on the first live step (or on ingest of a live close)
        self.neutral = NEUTRAL_RATE             # real neutral rate r*; calibrated at go-live (see step)
        self.earnings: List[Dict] = []          # {date, security_id, eps_consensus, eps_actual, surprise, revenue_growth, dividend_change}
        self.issuers: Dict[str, IssuerState] = {}
        self.rating_history: List[Dict] = []
        for s in securities.values():
            if s.asset_class == "CORP_BOND":
                self.issuers[s.id] = IssuerState(s.rating, s.rating, s.spread_bps_credit)
        self.earnings_dates: Dict[Tuple[str, int, int], date] = {}
        self._equity_ids = [s.id for s in securities.values() if s.asset_class in ("EQUITY", "ADR", "REIT") and s.fundamentals]
        self._securities = securities

    # ------------------------------------------------------------------ calendars (pure functions)
    def meeting_dates(self, year: int) -> List[date]:
        return [self.cal.roll(third_wednesday(year, m)) for m in MEETING_MONTHS]

    def next_meeting(self, d: date) -> date:
        for y in (d.year, d.year + 1):
            for m in self.meeting_dates(y):
                if m > d:
                    return m
        return self.meeting_dates(d.year + 1)[0]

    def release_calendar(self, year: int, month: int) -> List[Tuple[date, str]]:
        out = [(self.cal.roll(first_friday(year, month)), "PAYROLLS"), (self.cal.roll(date(year, month, 1)), "PMI"),
               (self.cal.roll(date(year, month, 12)), "CPI"), (self.cal.roll(date(year, month, 16)), "RETAIL_SALES")]
        if month in (1, 4, 7, 10):
            out.append((self.cal.roll(date(year, month, 27)), "GDP"))
        if month in MEETING_MONTHS:
            out.append((self.cal.roll(third_wednesday(year, month)), "FOMC"))
        return out

    def earnings_date(self, sid: str, year: int, quarter: int) -> date:
        key = (sid, year, quarter)
        if key not in self.earnings_dates:
            rng = random.Random(f"{self.seed}|earn|{sid}|{year}Q{quarter}")
            month = {1: 4, 2: 7, 3: 10, 4: 1}[quarter]
            y = year + 1 if quarter == 4 else year
            self.earnings_dates[key] = self.cal.roll(date(y, month, 1) + timedelta(days=rng.randint(12, 40)))
        return self.earnings_dates[key]

    def upcoming(self, d: date, days: int = 45) -> List[Dict]:
        end = d + timedelta(days=days)
        out = []
        for (y, m) in sorted({(d.year, d.month), ((d + timedelta(days=32)).year, (d + timedelta(days=32)).month), ((d + timedelta(days=62)).year, (d + timedelta(days=62)).month)}):
            for rd, kind in self.release_calendar(y, m):
                if d < rd <= end:
                    out.append({"date": rd.isoformat(), "kind": kind, "consensus": self.consensus(kind)})
        q = (d.month - 1) // 3 + 1
        for sid in self._equity_ids:
            for (yy, qq) in ((d.year, q), (d.year, q - 1) if q > 1 else (d.year - 1, 4)):
                ed = self.earnings_date(sid, yy, qq)
                if d < ed <= end:
                    out.append({"date": ed.isoformat(), "kind": "EARNINGS", "security_id": sid, "consensus": self.eps_consensus(sid)})
        out.sort(key=lambda r: r["date"])
        return out

    # ------------------------------------------------------------------ consensus
    def consensus(self, kind: str) -> float:
        s = self.state
        return {"CPI": round(s.inflation, 1), "PAYROLLS": round(150 + 60 * (s.growth - 2.0), 0), "GDP": round(s.growth, 1), "PMI": round(50 + 2.5 * s.growth, 1),
                "RETAIL_SALES": round(0.3 + 0.15 * s.growth, 1), "FOMC": round(self.policy_target() * 100, 2)}.get(kind, 0.0)

    def eps_consensus(self, sid: str) -> float:
        f = self._securities[sid].fundamentals
        return round(f.get("eps", 1.0) / 4, 2)

    def policy_target(self) -> float:
        s = self.state
        gap = s.growth - 2.0
        return max(0.0, self.neutral + s.inflation / 100 + 0.5 * (s.inflation - INFLATION_TARGET) / 100 + 0.5 * gap / 100)

    # ------------------------------------------------------------------ daily step
    def step(self, d: date, prev_bd: date, regime: str, oil_return_20d: float, hy_bps: float, mkt_hint: float, allow_defaults: bool = True,
             live: bool = True, short_rate: Optional[float] = None) -> Dict:
        """Advance the macro state one business day; return the payload with market transmission.

        During the market's prehistory (``live=False``) the activity variables evolve and releases/earnings are recorded,
        but the policy rate simply tracks the curve's short end (``short_rate``): the central bank starts acting only once
        the world is live. At go-live the activity variables snap to the initial regime's anchors and the real neutral
        rate r* is calibrated so that the reaction function's target equals the prevailing policy rate — the world starts
        in equilibrium, and subsequent decisions are driven by how growth and inflation drift away from the anchors.
        """
        s = self.state
        if not live and short_rate is not None:
            s.policy_rate = round(max(0.0, short_rate), 5)
        if live and not self.went_live:
            self.went_live = True
            s.growth, s.inflation = REGIME_GROWTH[regime], REGIME_INFL[regime]
            s.unemployment = 4.0 - 0.6 * (s.growth - 2.0)
            if short_rate is not None:
                s.policy_rate = round(max(0.0, short_rate), 5)
            self.neutral = round(s.policy_rate - s.inflation / 100 - 0.5 * (s.inflation - INFLATION_TARGET) / 100 - 0.5 * (s.growth - 2.0) / 100, 5)
        rng = random.Random(f"{self.seed}|macro|{d.isoformat()}")
        days = max(1, (d - prev_bd).days)
        dt = days / 365.0
        # slow state dynamics toward the regime's anchors
        s.growth += 0.6 * (REGIME_GROWTH[regime] - s.growth) * dt * 3 + 0.02 * rng.gauss(0, 1) * math.sqrt(days)
        s.inflation += 0.4 * (REGIME_INFL[regime] + 0.35 * s.growth * 0.3 + 4.0 * oil_return_20d - s.inflation) * dt * 3 + 0.015 * rng.gauss(0, 1) * math.sqrt(days)
        s.inflation = max(-1.0, min(12.0, s.inflation))
        s.unemployment += 0.5 * (4.0 - 0.6 * (s.growth - 2.0) - s.unemployment) * dt * 3 + 0.01 * rng.gauss(0, 1)
        s.unemployment = max(2.5, min(15.0, s.unemployment))
        payload: Dict = {"state": {}, "releases": [], "earnings": [], "ratings": [], "defaults": [], "dealer_defaults": [], "rate_shock_bp": 0.0, "equity_shock": 0.0,
                         "vol_bump": 0.0, "fx_shock": 0.0, "news": []}
        # scheduled releases
        for rd, kind in self.release_calendar(d.year, d.month):
            if rd != d:
                continue
            cons = self.consensus(kind)
            if kind == "FOMC":
                if not live:
                    continue
                rel = self._decide(d, rng)
                payload["releases"].append(rel)
                payload["rate_shock_bp"] += rel["surprise"] * 100 * 0.8
                payload["equity_shock"] += -rel["surprise"] * 0.02
                payload["vol_bump"] += 0.03 if abs(rel["surprise"]) > 0.001 else -0.02
                payload["news"].append({"code": "FOMC", "headline": rel["headline"], "category": "MACRO", "body": rel["note"]})
                continue
            sd = {"CPI": 0.15, "PAYROLLS": 70.0, "GDP": 0.6, "PMI": 1.6, "RETAIL_SALES": 0.5}[kind]
            surprise = rng.gauss(0, 1) * sd
            actual = cons + surprise
            z = surprise / sd
            if kind == "CPI":
                rate_bp, eq = 9.0 * z, -0.006 * z
                s.inflation += 0.3 * surprise
            elif kind == "PAYROLLS":
                rate_bp, eq = 5.0 * z, 0.003 * z if regime not in ("RATE_HIKING",) else -0.002 * z
                s.growth += 0.05 * z
            elif kind == "GDP":
                rate_bp, eq = 4.0 * z, 0.004 * z
                s.growth += 0.2 * surprise
            elif kind == "PMI":
                rate_bp, eq = 3.0 * z, 0.004 * z
            else:
                rate_bp, eq = 2.0 * z, 0.002 * z
            rel = {"date": d.isoformat(), "kind": kind, "consensus": round(cons, 2), "actual": round(actual, 2), "surprise": round(surprise, 2), "z": round(z, 2),
                   "rate_shock_bp": round(rate_bp, 1), "equity_shock": round(eq, 4)}
            payload["releases"].append(rel)
            payload["rate_shock_bp"] += rate_bp
            payload["equity_shock"] += eq
            payload["vol_bump"] += 0.01 * abs(z) - 0.005
            payload["fx_shock"] += 0.0015 * z if kind in ("CPI", "PAYROLLS", "GDP") else 0.0
            label = {"CPI": "CPI inflation (YoY %)", "PAYROLLS": "Nonfarm payrolls (k)", "GDP": "GDP growth (annualised %)", "PMI": "Manufacturing PMI", "RETAIL_SALES": "Retail sales (m/m %)"}[kind]
            tone = "above" if surprise > 0 else "below" if surprise < 0 else "in line with"
            payload["news"].append({"code": kind, "headline": f"{label}: {actual:.1f} vs {cons:.1f} expected", "category": "MACRO",
                                    "body": f"The release came in {tone} consensus (surprise {surprise:+.2f}, {z:+.1f} standard deviations). "
                                            f"Rates {'rose' if rate_bp > 0 else 'fell'} about {abs(rate_bp):.0f}bp on the print; equities {'firmed' if eq > 0 else 'softened'}."})
        # earnings
        q = (d.month - 1) // 3 + 1
        for sid in self._equity_ids:
            for (yy, qq) in ((d.year, q), (d.year, q - 1) if q > 1 else (d.year - 1, 4)):
                if self.earnings_date(sid, yy, qq) == d:
                    payload["earnings"].append(self._report(sid, d, yy, qq, regime, rng, payload))
        # issuer credit migration and defaults (monthly review on the first business day; daily default hazard)
        for ref, st in self.issuers.items():
            if st.defaulted:
                continue
            sec = self._securities[ref]
            idx_scale = hy_bps / 360.0 if RATING_ORDER.index(st.rating) >= RATING_ORDER.index("BB+") else (1.0 + 0.4 * (hy_bps - 360.0) / 360.0)
            target = RATING_SPREAD[st.rating] * max(0.5, idx_scale) * (1.0 + 0.6 * max(0.0, -s.growth) / 3.0)
            st.spread_bps += 0.1 * (target - st.spread_bps) + 0.01 * st.spread_bps * rng.gauss(0, 1)
            st.spread_bps = max(20.0, st.spread_bps)
            if d.month != prev_bd.month:
                r2 = random.Random(f"{self.seed}|rating|{ref}|{d.year}-{d.month:02d}")
                idx = RATING_ORDER.index(st.rating)
                stress = (st.spread_bps / RATING_SPREAD[st.rating]) - 1.0
                if stress > 0.5 and idx < len(RATING_ORDER) - 2 and r2.random() < min(0.6, 0.25 + stress * 0.3):
                    st.rating = RATING_ORDER[idx + 1]
                    st.watch = "NEGATIVE"
                    payload["ratings"].append({"reference": ref, "issuer": sec.issuer, "from": RATING_ORDER[idx], "to": st.rating, "date": d.isoformat()})
                    payload["news"].append({"code": ref, "headline": f"{sec.issuer} downgraded to {st.rating}", "category": "CREDIT",
                                            "body": f"Spreads on {sec.issuer} have traded {stress:.0%} wide of the rating's norm; the agency cut the rating to {st.rating}."})
                elif stress < -0.25 and idx > 0 and st.rating != st.base_rating and r2.random() < 0.15:
                    st.rating = RATING_ORDER[idx - 1]
                    st.watch = "POSITIVE"
                    payload["ratings"].append({"reference": ref, "issuer": sec.issuer, "from": RATING_ORDER[idx], "to": st.rating, "date": d.isoformat()})
                    payload["news"].append({"code": ref, "headline": f"{sec.issuer} upgraded to {st.rating}", "category": "CREDIT", "body": "Improved credit metrics earned an upgrade."})
                else:
                    st.watch = "STABLE"
            hazard = (st.spread_bps / 1e4) / max(0.05, 1 - sec.recovery_rate)
            p_default = hazard * days / 365.0 * (3.0 if regime == "LIQUIDITY_STRESS" else 1.5 if regime == "RECESSION" else 0.6)
            r3 = random.Random(f"{self.seed}|default|{ref}|{d.isoformat()}")
            if allow_defaults and r3.random() < p_default:
                st.defaulted, st.default_date = True, d.isoformat()
                st.rating = "D"
                payload["defaults"].append({"reference": ref, "issuer": sec.issuer, "recovery": sec.recovery_rate, "date": d.isoformat()})
                payload["news"].append({"code": ref, "headline": f"{sec.issuer} files for bankruptcy protection; bonds to trade at recovery", "category": "CREDIT",
                                        "body": f"{sec.issuer} missed a payment and entered restructuring. Bondholders expect a recovery of about {sec.recovery_rate:.0%} of face; "
                                                "credit-default swaps referencing the issuer will settle."})
        # policy pull: between meetings the short end drifts toward the policy rate (transmission handled by the market engine)
        payload["state"] = {"growth": round(s.growth, 3), "inflation": round(s.inflation, 3), "unemployment": round(s.unemployment, 3), "policy_rate": round(s.policy_rate, 5),
                            "neutral": self.neutral,
                            "policy_target": round(self.policy_target(), 5), "next_meeting": self.next_meeting(d).isoformat(), "last_decision": s.last_decision,
                            "issuers": {k: {"rating": v.rating, "spread_bps": round(v.spread_bps, 2), "defaulted": v.defaulted, "watch": v.watch} for k, v in self.issuers.items()}}
        self._apply_payload(d, payload)
        return payload

    def _decide(self, d: date, rng: random.Random) -> Dict:
        s = self.state
        target = self.policy_target()
        gap = target - s.policy_rate
        step = 0.0
        if gap > 0.00375:
            step = 0.0050 if gap > 0.0125 else 0.0025
        elif gap < -0.00375:
            step = -0.0050 if gap < -0.0125 else -0.0025
        # communication surprise: occasionally the bank does more or less than the reaction function
        u = rng.random()
        expected = step
        if u < 0.12:
            step += 0.0025 if gap > 0 else -0.0025 if gap < 0 else 0.0
        elif u < 0.22 and step != 0:
            step = 0.0
        s.policy_rate = max(0.0, round(s.policy_rate + step, 5))
        s.cycle_hikes = s.cycle_hikes + 1 if step > 0 else 0
        surprise = step - expected
        word = "raises" if step > 0 else "cuts" if step < 0 else "holds"
        s.last_decision = f"{d.isoformat()}: {word} to {s.policy_rate:.2%}"
        return {"date": d.isoformat(), "kind": "FOMC", "consensus": round((s.policy_rate - step + expected) * 100, 2), "actual": round(s.policy_rate * 100, 2), "surprise": round(surprise, 5),
                "z": round(surprise / 0.0025, 2), "rate_shock_bp": round(surprise * 1e4 * 0.8, 1), "equity_shock": round(-surprise * 0.02, 4), "step_bp": round(step * 1e4),
                "headline": f"Central bank {word} the policy rate {'by ' + str(round(abs(step) * 1e4)) + 'bp ' if step else ''}to {s.policy_rate:.2%}",
                "note": f"Reaction function target {target:.2%} vs policy {s.policy_rate:.2%} (growth {s.growth:.1f}%, inflation {s.inflation:.1f}%). "
                        + ("The decision surprised the market." if abs(surprise) > 1e-9 else "In line with expectations.")}

    def _report(self, sid: str, d: date, yy: int, qq: int, regime: str, rng: random.Random, payload: Dict) -> Dict:
        sec = self._securities[sid]
        f = sec.fundamentals
        cons = self.eps_consensus(sid)
        r2 = random.Random(f"{self.seed}|eps|{sid}|{yy}Q{qq}")
        macro = 0.15 * (self.state.growth - 2.0) / 2.0
        surprise = r2.gauss(macro, 0.09)
        actual = round(cons * (1 + surprise), 2)
        jump = max(-0.25, min(0.25, 0.55 * surprise + r2.gauss(0, 0.02)))
        rev_growth = round(0.04 + 0.5 * surprise + 0.01 * (self.state.growth - 2.0), 3)
        f["eps"] = round(max(0.01, f.get("eps", 1.0) * (1 + surprise * 0.4)), 2)
        f["revenue"] = round(f.get("revenue", 0.0) * (1 + rev_growth / 4), 0)
        f["net_income"] = round(f["eps"] * (sec.shares_outstanding or 0), 0)
        div_change = 0.0
        if sec.dividend_per_share > 0 and regime in ("RECESSION", "LIQUIDITY_STRESS") and surprise < -0.15 and r2.random() < 0.35:
            div_change = -0.5
        elif sec.dividend_per_share > 0 and surprise > 0.10 and qq == 4 and r2.random() < 0.5:
            div_change = 0.08
        rep = {"date": d.isoformat(), "security_id": sid, "quarter": f"{yy}Q{qq}", "eps_consensus": cons, "eps_actual": actual, "surprise": round(surprise, 4), "jump": round(jump, 4),
               "revenue_growth": rev_growth, "dividend_change": div_change, "fundamentals": dict(f)}
        beat = "beats" if surprise > 0.02 else "misses" if surprise < -0.02 else "meets"
        payload["news"].append({"code": sid, "headline": f"{sec.name} {beat} estimates: EPS {actual:.2f} vs {cons:.2f}", "category": "EARNINGS",
                                "body": f"{sec.name} reported {yy}Q{qq} earnings per share of {actual:.2f} against {cons:.2f} expected; revenue growth {rev_growth:+.1%}. "
                                        + (f"The board {'cut' if div_change < 0 else 'raised'} the dividend by {abs(div_change):.0%}. " if div_change else "")
                                        + f"Shares {'jumped' if jump > 0.03 else 'fell' if jump < -0.03 else 'were little changed'}."})
        return rep

    # ------------------------------------------------------------------ apply / ingest
    def _apply_payload(self, d: date, payload: Dict) -> None:
        st = payload["state"]
        self.history.append({"date": d.isoformat(), **{k: v for k, v in st.items() if k not in ("issuers",)}})
        for r in payload["releases"]:
            self.releases.append(r)
        for e in payload["earnings"]:
            self.earnings.append({k: v for k, v in e.items() if k != "fundamentals"})
            sec = self._securities[e["security_id"]]
            sec.fundamentals.update(e.get("fundamentals", {}))
            if e.get("dividend_change"):
                from ..money import money, D
                sec.dividend_per_share = money(sec.dividend_per_share * D(repr(1 + e["dividend_change"])))
        for r in payload["ratings"]:
            self.rating_history.append(r)
            sec = self._securities[r["reference"]]
            sec.rating = r["to"]
        for ref, iss in st.get("issuers", {}).items():
            sec = self._securities[ref]
            sec.spread_bps_credit = float(iss["spread_bps"])
            sec.rating = iss["rating"]
        if len(self.history) > 800:
            self.history = self.history[-800:]

    def ingest(self, d: date, payload: Dict) -> None:
        st = payload.get("state", {})
        s = self.state
        self.went_live = True
        self.neutral = float(st.get("neutral", self.neutral))
        s.growth, s.inflation, s.unemployment, s.policy_rate = float(st.get("growth", s.growth)), float(st.get("inflation", s.inflation)), float(st.get("unemployment", s.unemployment)), float(st.get("policy_rate", s.policy_rate))
        s.last_decision = st.get("last_decision", s.last_decision)
        for ref, iss in st.get("issuers", {}).items():
            cur = self.issuers.get(ref)
            if cur is None:
                cur = self.issuers[ref] = IssuerState(iss["rating"], iss["rating"], float(iss["spread_bps"]))
            cur.rating, cur.spread_bps, cur.defaulted, cur.watch = iss["rating"], float(iss["spread_bps"]), bool(iss.get("defaulted")), iss.get("watch", "STABLE")
            if cur.defaulted and not cur.default_date:
                cur.default_date = d.isoformat()
        self._apply_payload(d, payload)

    def mark_defaulted(self, reference: str, d: date) -> None:
        st = self.issuers.get(reference)
        if st and not st.defaulted:
            st.defaulted, st.default_date, st.rating = True, d.isoformat(), "D"
            self._securities[reference].rating = "D"

    def dashboard(self, d: date) -> Dict:
        s = self.state
        return {"state": {"growth": s.growth, "inflation": s.inflation, "unemployment": s.unemployment, "policy_rate": s.policy_rate, "policy_target": self.policy_target(), "neutral_real_rate": self.neutral,
                          "next_meeting": self.next_meeting(d).isoformat(), "last_decision": s.last_decision},
                "history": self.history[-260:], "releases": self.releases[-40:], "earnings": self.earnings[-60:], "upcoming": self.upcoming(d),
                "issuers": {k: {"rating": v.rating, "base_rating": v.base_rating, "spread_bps": v.spread_bps, "defaulted": v.defaulted, "default_date": v.default_date, "watch": v.watch}
                            for k, v in self.issuers.items()}, "rating_history": self.rating_history[-40:]}

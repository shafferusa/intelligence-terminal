"""
ShafferFinEval -- scheduled refresh and change detection.

    Refresh Data -> Detect Changes -> Recalculate Scores
      -> Recalculate Hedges -> Save Snapshot

Run after the relevant market closes. US equities default to 16:30 America/
New_York. Each asset class carries its own schedule so future classes (FX,
commodities, crypto, rates) can settle on their own clocks.

One bad ticker never stops the job: every asset is wrapped, failures are
counted, and the run summary reports them.
"""

from __future__ import annotations

import datetime as _dt
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import asset_models as am
import company_scoring as comp
import hedging as hedge
import macro_data
import macro_factors
import market_data as md
import prediction as pred
import routers
import sector_scoring as sect
import storage
import strategy_catalog as sc
import universe as uni

#: Per-asset-class refresh schedules. Only equities are wired today.
@dataclass
class RefreshSchedule:
    asset_class: str
    timezone: str
    hour: int
    minute: int
    implemented: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d} {self.timezone}"


SCHEDULES: dict[str, RefreshSchedule] = {
    uni.EQUITY: RefreshSchedule(
        uni.EQUITY, "America/New_York", 16, 30, True,
        "after the NYSE/Nasdaq 16:00 close",
    ),
    uni.ETF: RefreshSchedule(uni.ETF, "America/New_York", 16, 30, False,
                             "awaiting an ETF score model"),
    uni.FX: RefreshSchedule(uni.FX, "America/New_York", 17, 0, False,
                            "awaiting an FX score model; 17:00 ET is the FX day roll"),
    uni.COMMODITY: RefreshSchedule(uni.COMMODITY, "America/New_York", 14, 30, False,
                                   "awaiting a commodity model; settlement varies by contract"),
    uni.FUTURE: RefreshSchedule(uni.FUTURE, "America/New_York", 17, 0, False,
                                "awaiting a futures model"),
    uni.CRYPTO: RefreshSchedule(uni.CRYPTO, "UTC", 0, 5, False,
                                "awaiting a crypto model; crypto has no close"),
    uni.BOND: RefreshSchedule(uni.BOND, "America/New_York", 15, 30, False,
                              "awaiting a rates/credit model"),
}

#: Fundamental fields watched for change. A difference here means new
#: information (typically an earnings report), not just a price move.
FUNDAMENTAL_FIELDS = (
    "revenue", "ebitda", "net_income", "total_assets", "total_debt",
    "cash", "shares_outstanding",
)

#: Relative move before a fundamental counts as changed, so float noise and
#: tiny restatements do not spam the change log.
FUNDAMENTAL_TOLERANCE = 0.001


#: In-process cache of the universe sweep, so several manual refreshes in one
#: session do not repeat ~2,000 HTTP requests. Keyed by date and universe size.
_SWEEP_CACHE: dict = {}


def _sweep(equity_rows, day: str):
    """Fetch the whole supported universe once per day, per process."""
    key = (day, len(equity_rows))
    if key not in _SWEEP_CACHE:
        _SWEEP_CACHE.clear()
        _SWEEP_CACHE[key] = md.build_universe_records(equity_rows)
    return _SWEEP_CACHE[key]


def clear_sweep_cache() -> None:
    _SWEEP_CACHE.clear()


@dataclass
class AssetRefresh:
    symbol: str
    status: str = "skipped"
    message: str = ""
    score: Optional[float] = None
    previous_score: Optional[float] = None
    snapshot: str = ""
    predicted_return_pct: Optional[float] = None
    predicted_price: Optional[float] = None
    fundamental_changes: dict = field(default_factory=dict)


@dataclass
class RefreshSummary:
    started_at: _dt.datetime
    finished_at: Optional[_dt.datetime] = None
    scope: str = "all"
    snapshot_kind: str = storage.CLOSE
    attempted: int = 0
    scored: int = 0
    failed: int = 0
    not_implemented: int = 0
    snapshots_written: int = 0
    snapshots_existing: int = 0
    fundamental_changes: int = 0
    results: list[AssetRefresh] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scope": self.scope, "snapshot_kind": self.snapshot_kind,
            "attempted": self.attempted, "scored": self.scored,
            "failed": self.failed, "not_implemented": self.not_implemented,
            "snapshots_written": self.snapshots_written,
            "snapshots_existing": self.snapshots_existing,
            "fundamental_changes": self.fundamental_changes,
            "errors": self.errors[:50],
        }


def detect_fundamental_changes(
    stored: Optional[dict], fresh: dict, tolerance: float = FUNDAMENTAL_TOLERANCE
) -> dict:
    """Fields whose value moved materially since the last observation.

    Returns {field: {"previous": x, "new": y}}. An absent stored record is not
    a change -- it is a first observation.
    """
    if not stored:
        return {}
    changed: dict[str, dict] = {}
    for key in FUNDAMENTAL_FIELDS:
        old, new = stored.get(key), fresh.get(key)
        if old is None and new is None:
            continue
        if old is None or new is None:
            changed[key] = {"previous": old, "new": new}
            continue
        try:
            old_f, new_f = float(old), float(new)
        except (TypeError, ValueError):
            continue
        denominator = max(abs(old_f), 1e-9)
        if abs(new_f - old_f) / denominator > tolerance:
            changed[key] = {"previous": old_f, "new": new_f}
    return changed


def _fundamental_snapshot(financials: comp.CompanyFinancials) -> dict:
    return {key: getattr(financials, key, None) for key in FUNDAMENTAL_FIELDS}


def refresh_daily_scores(
    conn,
    symbols: Optional[Sequence[str]] = None,
    snapshot_kind: str = storage.CLOSE,
    snapshot_date: Optional[str] = None,
    progress: Optional[Callable[[str, int, int], None]] = None,
    catalog: Optional[Sequence] = None,
) -> RefreshSummary:
    """Refresh market data, rescore, and append today's snapshot.

    `symbols` limits the run (manual refresh); None means every supported asset.
    `snapshot_kind` CLOSE writes the official immutable daily row; INTRADAY
    writes a replaceable working row so repeated manual refreshes on the same
    day cannot disturb the close.
    """
    summary = RefreshSummary(
        started_at=_dt.datetime.now(_dt.timezone.utc),
        scope=",".join(symbols) if symbols else "all supported",
        snapshot_kind=snapshot_kind,
    )
    today = snapshot_date or _dt.date.today().isoformat()
    run_id = storage.start_refresh_run(conn, summary.scope, snapshot_kind)

    if catalog is None:
        catalog, _notes = sc.load_strategy_catalog()

    # --- universe sweep: one fetch feeding both engines -------------------
    #
    # The sweep is ALWAYS the full supported universe, even when `symbols`
    # limits what gets saved. Every score is peer-relative -- company factors
    # rank against the industry and sector percentiles rank across the market --
    # so scoring one ticker against a peer set of one would be meaningless.
    # `symbols` therefore narrows what is written, not what is fetched.
    try:
        assets, _source = uni.load_multi_asset_universe()
        storage.upsert_assets(conn, assets)
        equity_rows = uni.equity_universe_rows(assets)
        by_sector, financials, stats = _sweep(equity_rows, today)
        vti = md.get_vti_history()
        sector_scores = sect.build_all_sector_scores(by_sector, vti)
    except Exception as exc:
        summary.errors.append(f"universe sweep failed: {exc}")
        summary.finished_at = _dt.datetime.now(_dt.timezone.utc)
        storage.finish_refresh_run(conn, run_id, 0, 0, 1, summary.as_dict())
        return summary

    by_symbol = {f.ticker: f for f in financials}
    asset_rows = {row["yahoo_symbol"]: row for row in storage.list_assets(conn)
                  if row["yahoo_symbol"]}

    targets = list(by_symbol) if not symbols else [
        s for s in by_symbol if s.upper() in {x.upper() for x in symbols}
    ]
    summary.attempted = len(targets)

    for index, symbol in enumerate(sorted(targets), 1):
        if progress:
            progress(symbol, index, len(targets))
        outcome = AssetRefresh(symbol=symbol)
        try:
            row = asset_rows.get(symbol)
            if row is None:
                outcome.status = "skipped"
                outcome.message = "not in the stored universe"
                summary.results.append(outcome)
                continue

            asset_id = row["asset_id"]
            financial = by_symbol[symbol]
            sector_score = sect.get_sector_score_for_ticker(
                symbol, financial.sector, sector_scores
            )

            # --- change detection BEFORE rescoring --------------------------
            fresh = _fundamental_snapshot(financial)
            stored = storage.latest_fundamentals(conn, asset_id)
            changes = detect_fundamental_changes(stored, fresh)
            if changes or stored is None:
                storage.save_fundamentals(conn, asset_id, fresh, changes or None)
            if changes:
                summary.fundamental_changes += 1
                outcome.fundamental_changes = changes

            # --- score ------------------------------------------------------
            result = routers.score_asset(
                row["asset_class"], symbol, financial, financials, sector_score
            )
            if result.status == routers.NOT_IMPLEMENTED:
                summary.not_implemented += 1
                outcome.status = "not_implemented"
                outcome.message = result.message
                summary.results.append(outcome)
                continue

            # --- hedge (no position: hypothetical long exposure) ------------
            preferred_name = None
            if result.scored:
                position = hedge.Position(
                    ticker=symbol, direction=hedge.LONG,
                    shares=100.0, price=financial.price,
                )
                context = hedge.HedgeContext(
                    equity_score=result.shaffer_score,
                    sector=financial.sector, industry=financial.industry,
                )
                hedge_result = routers.recommend_hedge_for(
                    row["asset_class"], position, context, catalog
                )
                if hedge_result and hedge_result.preferred:
                    preferred_name = hedge_result.preferred.name
                elif hedge_result and not hedge_result.hedge_required:
                    preferred_name = "No hedge required"
                if hedge_result:
                    storage.save_hedge_recommendation(
                        conn, asset_id, today, position_id=None,
                        strategy=preferred_name,
                        strategy_key=(hedge_result.preferred.key
                                      if hedge_result.preferred else None),
                        strategy_score=(hedge_result.preferred.strategy_score
                                        if hedge_result.preferred else None),
                        hedge_ratio=hedge_result.hedge_ratio,
                        adverse_score=hedge_result.adverse_score,
                        confidence=(hedge_result.preferred.confidence
                                    if hedge_result.preferred else None),
                        ticket={"summary": hedge_result.preferred.summary}
                        if hedge_result.preferred else None,
                    )

            previous = storage.get_previous_snapshot(conn, asset_id, today)
            outcome.previous_score = previous["shaffer_score"] if previous else None

            # Shaffer Predicted Return: V1 score-to-return calibration.
            forecast = pred.build_prediction(result.shaffer_score, result.price)
            outcome.predicted_return_pct = forecast.predicted_return_pct
            outcome.predicted_price = forecast.predicted_price

            storage.save_current_score(
                conn, asset_id, price=result.price,
                shaffer_score=result.shaffer_score,
                classification=result.classification,
                preferred_hedge=preferred_name,
                sector_score=result.sector_score,
                sector_overlay=result.sector_overlay,
                company_score=result.company_score,
                score_confidence=result.confidence,
                model_status=result.status,
                factor_scores=result.factor_scores,
                raw_inputs=result.raw_inputs,
                model_version=result.model_version,
                predicted_12m_return_pct=forecast.predicted_return_pct,
                predicted_12m_price=forecast.predicted_price,
                prediction_model=forecast.model,
                prediction_model_version=forecast.model_version,
            )

            written = storage.save_score_snapshot(
                conn, asset_id, today, kind=snapshot_kind,
                model_version=result.model_version or storage.EQUITY_MODEL_VERSION,
                price=result.price, shaffer_score=result.shaffer_score,
                classification=result.classification,
                sector_overlay=result.sector_overlay,
                company_score=result.company_score,
                factor_scores=result.factor_scores,
                raw_inputs=result.raw_inputs,
                preferred_hedge=preferred_name,
            )
            # A close snapshot keeps the prediction made that day, even if the
            # calibration later changes.
            storage.save_prediction_fields(
                conn, asset_id, today, kind=snapshot_kind,
                predicted_return_pct=forecast.predicted_return_pct,
                predicted_price=forecast.predicted_price,
                model=forecast.model, model_version=forecast.model_version,
            )

            outcome.snapshot = written
            if written == "written":
                summary.snapshots_written += 1
            else:
                summary.snapshots_existing += 1

            outcome.status = "scored" if result.scored else result.status
            outcome.score = result.shaffer_score
            if result.scored:
                summary.scored += 1
            else:
                outcome.message = result.message

        except Exception as exc:                      # one bad ticker never stops the job
            summary.failed += 1
            outcome.status = "failed"
            outcome.message = f"{type(exc).__name__}: {exc}"
            summary.errors.append(f"{symbol}: {outcome.message}")
            if len(summary.errors) < 5:
                summary.errors.append(traceback.format_exc(limit=2))
        summary.results.append(outcome)

    summary.finished_at = _dt.datetime.now(_dt.timezone.utc)
    storage.finish_refresh_run(
        conn, run_id, summary.attempted, summary.scored, summary.failed,
        summary.as_dict(),
    )
    return summary


#: Which live macro engine scores each workbook subclass. Only subclasses with
#: a genuinely wired data source appear here; everything else stays unscored.
#: ONLY US instruments. The wired macro series are Fed policy, US CPI, US
#: unemployment and the US curve. A German or Japanese government bond is
#: driven by the ECB and the BoJ, so giving it the US rates score would be a
#: fabricated reading dressed as a real one. Non-US sovereigns stay unscored
#: until their own macro is wired.
MACRO_ROUTES = {
    "US Treasury": ("rates_shaffer_v1", am.RATES),
    "US Treasury bill": ("rates_shaffer_v1", am.RATES),
    "Corporate / sovereign bond": ("corp_credit_shaffer_v1", am.CORP_CREDIT),
}

#: Gold only. The v1 gold equation is built on real yields, the dollar and
#: safe-haven demand; silver, platinum and palladium are far more
#: industrial-demand driven and need their own subtype model, so they are NOT
#: silently scored with gold's factors.
PRECIOUS = {"GC", "XAU"}

#: Named so the gap is visible rather than implied.
NEEDS_SUBTYPE_MODEL = {
    "SI": "silver -- industrial demand dominates; needs its own subtype model",
    "PL": "platinum -- autocatalyst demand; needs its own subtype model",
    "PA": "palladium -- autocatalyst demand; needs its own subtype model",
}


#: Stated on every macro-scored row, so the caveat travels with the number
#: instead of living only in a run log.
MACRO_CAVEATS = {
    "rates_shaffer_v1":
        "Curve-wide conviction by design; duration changes expected return, "
        "not the view.",
    "corp_credit_shaffer_v1":
        "MARKET-LEVEL ONLY: issuer credit, cash-flow quality and technicals "
        "are not wired, and the universe carries no rating, so this is the "
        "investment-grade spread view applied to the whole class -- not a "
        "judgement about this issuer.",
    "gold_shaffer_v1":
        "No central-bank/ETF flow data and no geopolitical input; the score "
        "is the real-yield, dollar, inflation and stress view only.",
}


def _macro_caveat(engine) -> str:
    return MACRO_CAVEATS.get(getattr(engine, "version", ""), "")


def refresh_macro_scores(conn, snapshot_kind: str = storage.CLOSE,
                         snapshot_date: Optional[str] = None,
                         macro: Optional[object] = None) -> dict:
    """Score the non-equity classes that have a wired data source.

    Today that is US Treasuries and bills, corporate/sovereign USD bonds, and
    gold. Non-US sovereigns, the other precious metals and every class in
    `macro_factors.BLOCKED_ENGINES` keep their arithmetic but have no feed,
    and are deliberately left unscored rather than filled with estimates.
    """
    summary = {"scored": 0, "skipped": 0, "engines": {}, "notes": []}
    today = snapshot_date or _dt.date.today().isoformat()

    if macro is None:
        macro = macro_data.fetch_macro_snapshot(
            yahoo_keys=("vix", "vix_3m", "gold", "wti", "natgas", "copper"))
    summary["macro_coverage"] = macro.coverage
    if macro.coverage <= 0:
        summary["notes"].append(
            "No macro series available, so no non-equity asset could be scored.")
        return summary

    rates_values, rates_missing = macro_factors.rates_factors(macro)
    gold_values, gold_missing = macro_factors.gold_factors(macro)
    # The universe carries no credit rating, so IG and HY cannot be told
    # apart per issuer. Every corporate row therefore gets the INVESTMENT
    # GRADE market leg and says so, rather than a high-yield spread being
    # assigned to bonds we have not established are high yield.
    credit_values, credit_missing = macro_factors.corporate_credit_factors(
        macro, high_yield=False)

    for row in storage.list_assets(conn):
        engine = None
        values = None
        if row["asset_class"] == uni.BOND and row["subclass"] in MACRO_ROUTES:
            version, model = MACRO_ROUTES[row["subclass"]]
            engine, values = model, (
                rates_values if version == "rates_shaffer_v1" else credit_values)
        elif row["asset_class"] == uni.COMMODITY and row["symbol"] in PRECIOUS:
            engine, values = am.GOLD, gold_values
        elif row["asset_class"] == uni.COMMODITY and row["symbol"] in NEEDS_SUBTYPE_MODEL:
            summary["skipped"] += 1
            summary.setdefault("needs_subtype", []).append(
                NEEDS_SUBTYPE_MODEL[row["symbol"]])
            continue

        if engine is None or not values:
            summary["skipped"] += 1
            continue

        try:
            result = routers.score_asset(
                row["asset_class"], row["symbol"], subclass=row["subclass"],
                factor_values=values)
            if not result.scored:
                summary["skipped"] += 1
                continue

            storage.save_current_score(
                conn, row["asset_id"], price=None,
                shaffer_score=result.shaffer_score,
                classification=result.classification,
                preferred_hedge=None, score_confidence=result.confidence,
                model_status=result.status, factor_scores=result.factor_scores,
                raw_inputs={"direction": result.message,
                            "macro_fetched_at": macro.fetched_at,
                            "caveat": _macro_caveat(engine)},
                model_version=result.model_version)
            storage.save_score_snapshot(
                conn, row["asset_id"], today, kind=snapshot_kind,
                model_version=result.model_version,
                shaffer_score=result.shaffer_score,
                classification=result.classification,
                factor_scores=result.factor_scores,
                raw_inputs={"direction": result.message,
                            "caveat": _macro_caveat(engine)})
            summary["scored"] += 1
            summary["engines"][engine.version] = summary["engines"].get(
                engine.version, 0) + 1
        except Exception as exc:
            summary["skipped"] += 1
            summary["notes"].append(f"{row['symbol']}: {exc}")

    summary["notes"].append(
        "Every US Treasury shares one macro conviction score by design: the "
        "rates view is curve-wide. Duration differentiates expected RETURN "
        "magnitude, not conviction -- see asset_models.bond_price_change.")
    summary["notes"].append(
        "Every corporate bond shares one score for a different and weaker "
        "reason: only the MARKET-level spread and rates legs are wired, so "
        "two issuers of very different quality currently score identically. "
        "That is a data gap, not a view that they are equivalent credits.")
    if rates_missing:
        summary["notes"].append("Rates factors unavailable: " + ", ".join(rates_missing))
    if gold_missing:
        summary["notes"].append("Gold factors unavailable: " + ", ".join(gold_missing))
    if credit_missing:
        summary["notes"].append(
            "Corporate credit factors unavailable: " + ", ".join(credit_missing))
    summary["blocked_engines"] = dict(macro_factors.BLOCKED_ENGINES)
    return summary


def score_delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Change in Shaffer Score since the prior saved snapshot."""
    if current is None or previous is None:
        return None
    return float(current) - float(previous)


def explain_score_change(
    current_factors: Optional[dict], previous_factors: Optional[dict]
) -> list[tuple[str, float, float, float]]:
    """Per-factor drivers of a score move, largest absolute change first.

    Deterministic: built purely from the difference between two saved snapshots.
    """
    if not current_factors or not previous_factors:
        return []
    drivers = []
    for key, now in current_factors.items():
        before = previous_factors.get(key)
        if now is None or before is None:
            continue
        try:
            now_f, before_f = float(now), float(before)
        except (TypeError, ValueError):
            continue
        if abs(now_f - before_f) < 1e-9:
            continue
        drivers.append((key, before_f, now_f, now_f - before_f))
    drivers.sort(key=lambda d: abs(d[3]), reverse=True)
    return drivers


def next_scheduled_run(asset_class: str = uni.EQUITY,
                       now: Optional[_dt.datetime] = None) -> Optional[str]:
    """Human description of the next scheduled refresh for a class."""
    schedule = SCHEDULES.get(asset_class)
    if schedule is None:
        return None
    state = "active" if schedule.implemented else "not yet active"
    return f"{schedule.label} daily ({state}) -- {schedule.note}"

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

import company_scoring as comp
import hedging as hedge
import market_data as md
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

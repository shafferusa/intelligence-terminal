"""Page 3 -- ML LAB.

Research and challenger analysis. Nothing on this page changes production:
the Shaffer Score, its weights, the hedge formula and the V1 score-to-return
calibration are untouched by every model shown here.
"""

from __future__ import annotations

import streamlit as st

import counterfactual as CF
import mllib
import ml_lab
import political
import prediction as pred
import storage
import synthetic as SYN
from views.common import fmt_int, fmt_price, fmt_score, fmt_signed_pct

STATUS_COLOR = {
    ml_lab.INSUFFICIENT: "#6b7681",
    ml_lab.EXPERIMENTAL: "#c47a2c",
    ml_lab.VALIDATED: "#2e9e5b",
}


def _metric(value, digits: int = 3) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def _pct(value, digits: int = 2) -> str:
    return "--" if value is None else f"{value * 100:+.{digits}f}%"


def render(conn) -> None:
    st.markdown("## ML LAB")
    st.caption(
        "Research and challenger models. The production Shaffer model is never "
        "changed from this page."
    )

    horizons = list(pred.HORIZONS)
    horizon = st.selectbox(
        "Prediction horizon", horizons,
        index=horizons.index(pred.PRIMARY_HORIZON),
        help="12M is the production horizon. The short ones exist so the "
             "score can be checked in weeks instead of a year.",
    )
    if horizon in pred.EARLY_HORIZONS:
        st.warning(
            f"**{horizon} is an EARLY horizon.** It exists to show whether the "
            f"score has any cross-sectional signal months before "
            f"{pred.PRIMARY_HORIZON} can say anything. It is not evidence "
            f"about the {pred.PRIMARY_HORIZON} calibration, and a good "
            f"{horizon} model is not a reason to change the "
            f"{pred.V1_SLOPE:.2f} slope."
        )
    target_label = st.radio(
        "Target", ["Absolute price return", "VTI-relative excess return"],
        horizontal=True,
    )
    target = (ml_lab.TARGET_ABSOLUTE if target_label.startswith("Absolute")
              else ml_lab.TARGET_EXCESS)
    sampling = st.select_slider(
        "Observation sampling", [ml_lab.DAILY, ml_lab.WEEKLY, ml_lab.MONTHLY],
        value=ml_lab.DEFAULT_SAMPLING.get(horizon, ml_lab.MONTHLY),
        help="Daily snapshots produce heavily overlapping long-horizon labels.",
    )

    tabs = st.tabs([
        "DATA STATUS", "SYNTHETIC VALIDATION", "PRODUCTION VS CHALLENGERS",
        "SHAFFER CALIBRATION", "FACTOR IMPORTANCE", "DECILES", "REGIME",
        "POLITICAL", "HEDGE RESEARCH", "TRADE RESEARCH", "PROPOSALS",
        "MODEL REGISTRY", "CURRENT PREDICTIONS",
    ])

    with tabs[0]:
        _render_data_status(conn, horizon, target, sampling)
    with tabs[1]:
        _render_synthetic()
    with tabs[2]:
        _render_models(conn, horizon, target, sampling)
    with tabs[3]:
        _render_calibration(conn, horizon, target, sampling)
    with tabs[4]:
        _render_importance(conn, horizon, target, sampling)
    with tabs[5]:
        _render_deciles(conn, horizon, target, sampling)
    with tabs[6]:
        _render_regime(conn, horizon, target, sampling)
    with tabs[7]:
        _render_political(conn)
    with tabs[8]:
        _render_hedge_research(conn)
    with tabs[9]:
        _render_trade_research(conn)
    with tabs[10]:
        _render_proposals(conn, horizon, target, sampling)
    with tabs[11]:
        _render_registry(conn, horizon)
    with tabs[12]:
        _render_current_predictions(conn)


def _dataset(conn, horizon, target, sampling):
    key = f"ml_dataset_{horizon}_{target}_{sampling}"
    if key not in st.session_state:
        st.session_state[key] = ml_lab.build_dataset(conn, horizon, target, sampling)
    return st.session_state[key]


def _render_data_status(conn, horizon, target, sampling) -> None:
    status = ml_lab.data_status(conn)
    cells = st.columns(4)
    cells[0].metric("Shaffer snapshots", fmt_int(status.get("snapshots")))
    cells[1].metric("Equities represented", fmt_int(status.get("assets")))
    cells[2].metric("Snapshot dates", fmt_int(status.get("dates")))
    cells[3].metric("Models registered", fmt_int(status.get("models_registered")))
    st.caption(
        f"Earliest snapshot: {status.get('earliest') or '--'}  |  "
        f"Latest: {status.get('latest') or '--'}"
    )

    labels = status.get("labels") or {}
    st.markdown("### LABELLED OBSERVATIONS")
    st.table({
        "Horizon": list(pred.HORIZONS),
        "Labelled observations": [fmt_int(labels.get(h, 0)) for h in pred.HORIZONS],
    })

    dataset = _dataset(conn, horizon, target, sampling)
    cells = st.columns(3)
    cells[0].metric("Rows available", fmt_int(dataset.total_rows))
    cells[1].metric(f"Rows after {sampling} sampling", fmt_int(dataset.n))
    cells[2].metric("Effective (non-overlapping)",
                    fmt_int(dataset.effective_observations))
    if dataset.dropped_incomplete:
        st.caption(f"{dataset.dropped_incomplete} rows dropped for incomplete factors.")
    for note in dataset.notes:
        st.caption(f"- {note}")

    if dataset.effective_observations < ml_lab.MIN_OBS_EXPERIMENTAL:
        st.error(
            f"**INSUFFICIENT DATA FOR RELIABLE {horizon} ML TRAINING.** "
            f"{dataset.effective_observations} effective observations against a "
            f"{ml_lab.MIN_OBS_EXPERIMENTAL} minimum. Snapshots must age "
            f"{horizon} before an outcome exists, so the database needs to "
            f"accumulate naturally. No backtest is fabricated in the meantime."
        )
    st.markdown("### REFRESH OUTCOME LABELS")
    st.caption(
        "Labels are realised forward prices for snapshots that have aged past "
        "each horizon. Run this after the daily refresh, or from the CLI: "
        "`python3 ml_job.py --labels`."
    )
    if st.button("REFRESH LABELS NOW"):
        _refresh_labels(conn)

    with st.expander("How the ML Lab works"):
        st.markdown(ml_lab.ML_LAB_DETAILS)


def _refresh_labels(conn) -> None:
    import market_data as md

    def price_history(symbol):
        prices = md.fetch_price_history(symbol)
        return []      # dates are not returned by the adapter; see ml_job.py

    with st.spinner("Refreshing outcome labels..."):
        try:
            from ml_job import refresh_labels_with_dates
            summary = refresh_labels_with_dates(conn)
            st.success(
                f"{summary['labels_written']} labels written, "
                f"{summary['pending']} still pending (not yet aged), "
                f"{summary['errors']} errors."
            )
        except Exception as exc:
            st.error(f"Label refresh failed: {exc}")


def _render_models(conn, horizon, target, sampling) -> None:
    dataset = _dataset(conn, horizon, target, sampling)
    if dataset.n == 0:
        st.info(
            f"No labelled {horizon} observations yet, so no model can be "
            "trained or compared. The Shaffer V1 calibration remains in "
            "production regardless."
        )
        return

    key = f"ml_reports_{horizon}_{target}_{sampling}"
    if st.button("TRAIN / EVALUATE CHALLENGERS", key="train_btn"):
        with st.spinner("Walk-forward training..."):
            _dataset_obj, reports, calibration = ml_lab.train_all(
                conn, horizon, target, sampling)
            st.session_state[key] = reports
            st.session_state[f"ml_calib_{horizon}_{target}_{sampling}"] = calibration

    reports = st.session_state.get(key)
    if not reports:
        st.info("Press **Train / Evaluate Challengers** to run walk-forward validation.")
        return

    st.dataframe({
        "Model": [r.name for r in reports],
        "Target": [r.target for r in reports],
        "Training period": [f"{r.training_start} → {r.training_end}" for r in reports],
        "Folds": [len(r.folds) for r in reports],
        "Train obs": [fmt_int(r.n_train) for r in reports],
        "Test obs": [fmt_int(r.n_test) for r in reports],
        "MAE": [_metric(r.metrics.get("mae")) for r in reports],
        "RMSE": [_metric(r.metrics.get("rmse")) for r in reports],
        "R²": [_metric(r.metrics.get("r2")) for r in reports],
        "Pearson": [_metric(r.metrics.get("pearson")) for r in reports],
        "Spearman": [_metric(r.metrics.get("spearman")) for r in reports],
        "Direction": [_metric(r.metrics.get("direction_accuracy"), 3) for r in reports],
        "Status": [r.status for r in reports],
    }, use_container_width=True, hide_index=True)

    st.caption(
        "The Shaffer V1 baseline is the benchmark every challenger must beat. "
        "Spearman matters most: it asks whether higher predictions really did "
        "correspond to higher realised returns."
    )
    st.info(
        "**No model on this page is in production.** Promotion requires an "
        "explicit status change, which no training path performs."
    )

    st.markdown("### WALK-FORWARD FOLDS")
    chosen = st.selectbox("Model", [r.name for r in reports])
    report = next(r for r in reports if r.name == chosen)
    if report.folds:
        st.table({
            "Fold": list(range(1, len(report.folds) + 1)),
            "Train": [f"{f.train_start} → {f.train_end}" for f in report.folds],
            "Test": [f"{f.test_start} → {f.test_end}" for f in report.folds],
            "N train": [f.n_train for f in report.folds],
            "N test": [f.n_test for f in report.folds],
            "MAE": [_metric(f.metrics.get("mae")) for f in report.folds],
            "Spearman": [_metric(f.metrics.get("spearman")) for f in report.folds],
        })
    else:
        st.caption(report.note or "No folds could be formed.")


def _render_calibration(conn, horizon, target, sampling) -> None:
    st.markdown("### CURRENT PRODUCTION CALIBRATION")
    st.code(f"PredictedReturnPct = {pred.V1_SLOPE:.2f} x ShafferScore\n"
            f"version: {pred.PREDICTION_MODEL_VERSION}", language=None)

    calibration = st.session_state.get(f"ml_calib_{horizon}_{target}_{sampling}")
    if calibration is None:
        dataset = _dataset(conn, horizon, target, sampling)
        calibration = ml_lab.estimate_calibration(dataset)

    st.markdown("### ML-ESTIMATED CALIBRATION")
    if calibration.beta is None:
        st.warning(calibration.note or "Not enough realised outcomes to estimate.")
        st.caption("V1 stays in production. Nothing is inferred from no data.")
        return

    st.code(f"ActualReturnPct = {calibration.alpha:+.3f} "
            f"{'+' if calibration.beta >= 0 else '-'} "
            f"{abs(calibration.beta):.4f} x ShafferScore", language=None)
    cells = st.columns(5)
    cells[0].metric("Sample size", fmt_int(calibration.n))
    cells[1].metric("R²", _metric(calibration.r2))
    cells[2].metric("Spearman", _metric(calibration.spearman))
    cells[3].metric("MAE (pp)", _metric(calibration.mae, 2))
    cells[4].metric("Status", calibration.status)
    st.caption(f"Training period: {calibration.training_start} → {calibration.training_end}")

    rows = calibration.implied_at
    if rows:
        st.markdown("### PRODUCTION VS LEARNED")
        st.table({
            "Shaffer Score": [f"{s:+d}" for s, _, _ in rows],
            "V1 predicted": [f"{p:+.1f}%" for _, p, _ in rows],
            "ML estimated": [f"{l:+.1f}%" for _, _, l in rows],
            "Difference": [f"{l - p:+.1f}pp" for _, p, l in rows],
        })
    st.info(
        "This is a **finding**, not a change. The production calibration stays "
        "at 0.20 until it is explicitly replaced."
    )


def _render_importance(conn, horizon, target, sampling) -> None:
    reports = st.session_state.get(f"ml_reports_{horizon}_{target}_{sampling}")
    if not reports:
        st.info("Train the challengers first (Production vs Challengers tab).")
        return
    trained = [r for r in reports if r.importance]
    if not trained:
        st.info("No model produced importances — usually too little data to fit.")
        return

    chosen = st.selectbox("Model", [r.name for r in trained], key="imp_model")
    report = next(r for r in trained if r.name == chosen)
    st.markdown("### PREDICTIVE IMPORTANCE")
    st.caption(
        "This is predictive importance — how much a feature helped the model "
        "predict. It is **not** a cause of returns."
    )
    st.dataframe({
        "Feature": [name for name, _ in report.importance],
        "Share": [f"{share:.1%}" for _, share in report.importance],
    }, use_container_width=True, hide_index=True)

    if report.coefficients:
        st.markdown("### COEFFICIENTS")
        st.table({
            "Feature": list(report.coefficients),
            "Coefficient": [f"{v:+.5f}" for v in report.coefficients.values()],
        })

    st.markdown("### CURRENT WEIGHTS VS LEARNED SIGNAL")
    import company_scoring as comp
    production = {k: v for k, v in comp.MAJOR_WEIGHTS.items()}
    learned = {k: s for k, s in report.importance if k in production}
    total = sum(learned.values())
    st.table({
        "Factor": [k.title() for k in production],
        "Shaffer (production)": [f"{v:.0%}" for v in production.values()],
        "Challenger signal share": [
            f"{learned[k] / total:.0%}" if total and k in learned else "--"
            for k in production
        ],
    })
    st.caption(
        "Shown only for the four comparable major factors. Tree feature "
        "importance is not convertible into formula weights, so it is never "
        "presented as a proposed weighting."
    )


def _render_deciles(conn, horizon, target, sampling) -> None:
    reports = st.session_state.get(f"ml_reports_{horizon}_{target}_{sampling}")
    if not reports:
        st.info("Train the challengers first.")
        return
    chosen = st.selectbox("Model", [r.name for r in reports], key="dec_model")
    report = next(r for r in reports if r.name == chosen)
    buckets = report.metrics.get("quantiles") or []
    if not buckets:
        st.info("Not enough out-of-sample predictions to rank.")
        return

    st.markdown("### DECILE ANALYSIS (out-of-sample)")
    st.dataframe({
        "Rank": [b["bucket"] for b in buckets],
        "N": [b["n"] for b in buckets],
        "Mean predicted": [_pct(b["mean_predicted"]) for b in buckets],
        "Mean ACTUAL": [_pct(b["mean_actual"]) for b in buckets],
        "Positive rate": [f"{b['hit_rate']:.0%}" for b in buckets],
    }, use_container_width=True, hide_index=True)
    st.caption(
        "Bucket 1 is the highest-predicted decile. A useful ranking model shows "
        "realised returns declining down the table."
    )

    calibration = report.metrics.get("calibration") or []
    if calibration:
        st.markdown("### CALIBRATION BUCKETS")
        st.table({
            "Predicted band": [c["range"] for c in calibration],
            "N": [c["n"] for c in calibration],
            "Mean predicted": [_pct(c["mean_predicted"]) for c in calibration],
            "Mean actual": [_pct(c["mean_actual"]) for c in calibration],
        })


def _render_registry(conn, horizon) -> None:
    models = storage.list_models(conn, horizon)
    if not models:
        st.info("No models registered yet.")
        return
    st.dataframe({
        "ID": [m["model_id"] for m in models],
        "Name": [m["model_name"] for m in models],
        "Family": [m["model_family"] for m in models],
        "Target": [m["target"] for m in models],
        "Horizon": [m["horizon"] for m in models],
        "Sampling": [m["sampling"] for m in models],
        "Validation": [m["validation_method"] for m in models],
        "Train obs": [fmt_int(m["training_observations"]) for m in models],
        "Test obs": [fmt_int(m["test_observations"]) for m in models],
        "Version": [m["model_version"] for m in models],
        "Status": [m["status"] for m in models],
        "Created": [(m["created_at"] or "")[:16] for m in models],
    }, use_container_width=True, hide_index=True)

    chosen = st.selectbox("Inspect model",
                          [f"#{m['model_id']} {m['model_name']}" for m in models])
    model_id = int(chosen.split()[0].lstrip("#"))
    row = storage.get_model(conn, model_id)
    st.table({
        "Field": ["Features", "Hyperparameters", "Training range", "Metrics", "Status"],
        "Value": [
            ", ".join(storage.loads(row["feature_list_json"]) or []),
            str(storage.loads(row["hyperparameters_json"]) or {}),
            f"{row['training_start']} → {row['training_end']}",
            str(storage.loads(row["metrics_json"]) or {}),
            row["status"],
        ],
    })
    st.warning(
        "Promotion is deliberately manual. To promote a model, call "
        "`storage.set_model_status(conn, model_id, storage.PRODUCTION)` "
        "explicitly — no training path does this for you."
    )


def _render_current_predictions(conn) -> None:
    st.markdown("### CURRENT PREDICTIONS")
    rows = [r for r in storage.market_rows(conn) if r["shaffer_score"] is not None]
    if not rows:
        st.info("No scored assets yet.")
        return
    rows.sort(key=lambda r: r["shaffer_score"], reverse=True)
    st.dataframe({
        "Ticker": [r["symbol"] for r in rows],
        "Price": [fmt_price(r["price"]) for r in rows],
        "Shaffer Score": [fmt_score(r["shaffer_score"]) for r in rows],
        "Shaffer V1 12M return": [
            "--" if r["predicted_12m_return_pct"] is None
            else f"{r['predicted_12m_return_pct']:+.1f}%" for r in rows],
        "Shaffer 12M price": [fmt_price(r["predicted_12m_price"]) for r in rows],
        "ML challenger return": ["--" for _ in rows],
        "ML challenger price": ["--" for _ in rows],
        "Difference": ["--" for _ in rows],
    }, use_container_width=True, hide_index=True, height=420)
    st.info(
        "The ML challenger columns stay empty until a challenger has enough "
        "realised outcomes to train on. The official terminal value is always "
        "the Shaffer V1 prediction; a challenger prediction would be shown "
        "here, clearly labelled, and would not replace it."
    )


# --------------------------------------------------------------------------
# Synthetic validation
# --------------------------------------------------------------------------

def _render_synthetic() -> None:
    st.markdown("### SYNTHETIC ML VALIDATION")
    st.warning(
        "**These tests validate the SOFTWARE, not the investment model.** "
        "They confirm the ML machinery works on data with a known built-in "
        "relationship. They say nothing about whether the Shaffer Score "
        "predicts real markets."
    )
    if st.button("RUN SYNTHETIC VALIDATION", key="syn_run"):
        with st.spinner("Running 11 synthetic tests..."):
            st.session_state["synthetic_results"] = SYN.run_all()

    results = st.session_state.get("synthetic_results")
    if not results:
        st.info("Press **Run Synthetic Validation** to exercise the ML pipeline.")
        st.markdown(SYN.SYNTHETIC_DISCLAIMER)
        return

    passed = sum(1 for r in results if r.passed)
    (st.success if passed == len(results) else st.error)(
        f"{passed}/{len(results)} synthetic validation tests passing "
        f"— {SYN.SYNTHETIC_LABEL}"
    )
    st.dataframe({
        "Test": [r.name for r in results],
        "Status": ["PASS" if r.passed else "FAIL" for r in results],
        "Detail": [r.detail for r in results],
        "Label": [r.note for r in results],
    }, use_container_width=True, hide_index=True)
    st.markdown(SYN.SYNTHETIC_DISCLAIMER)


# --------------------------------------------------------------------------
# Regime analysis
# --------------------------------------------------------------------------

def _render_regime(conn, horizon, target, sampling) -> None:
    st.markdown("### REGIME ANALYSIS")
    st.caption(
        "Does a factor's relationship with returns hold up across periods, or "
        "does it flip? Walk-forward folds are the evidence."
    )
    reports = st.session_state.get(f"ml_reports_{horizon}_{target}_{sampling}")
    if not reports:
        st.info("Train the challengers first (Production vs Challengers tab).")
        return
    chosen = st.selectbox("Model", [r.name for r in reports], key="regime_model")
    report = next(r for r in reports if r.name == chosen)
    if not report.folds:
        st.info(report.note or "No folds available.")
        return
    spearmans = [f.metrics.get("spearman") for f in report.folds]
    usable = [s for s in spearmans if s is not None]
    st.dataframe({
        "Period": [f"{f.test_start} → {f.test_end}" for f in report.folds],
        "N": [f.n_test for f in report.folds],
        "MAE": [_metric(f.metrics.get("mae")) for f in report.folds],
        "Spearman": [_metric(s) for s in spearmans],
        "Direction": [_metric(f.metrics.get("direction_accuracy")) for f in report.folds],
    }, use_container_width=True, hide_index=True)
    if len(usable) >= 2:
        spread = max(usable) - min(usable)
        sign_flip = min(usable) < 0 < max(usable)
        if sign_flip:
            st.error(
                f"**Unstable across periods.** Rank correlation ranges "
                f"{min(usable):+.3f} to {max(usable):+.3f} and changes sign — "
                f"the relationship does not hold in every regime."
            )
        elif spread > 0.25:
            st.warning(
                f"Rank correlation varies by {spread:.2f} across periods. "
                f"Treat a single-period result with caution."
            )
        else:
            st.success(f"Stable: rank correlation varies by only {spread:.2f}.")


# --------------------------------------------------------------------------
# Political analysis
# --------------------------------------------------------------------------

def _render_political(conn) -> None:
    st.markdown("### POLITICAL / GEOPOLITICAL ANALYSIS")
    events = storage.list_political_events(conn)
    if not events:
        st.info(
            "No political events recorded. The GPI engine is implemented and "
            "tested, but there is no automated event feed wired in — events are "
            "entered explicitly, and nothing is inferred from news."
        )
        st.markdown(political.GPI_DETAILS)
        return

    st.dataframe({
        "ID": [e["event_id"] for e in events],
        "Type": [e["event_type"] for e in events],
        "Description": [(e["description"] or "")[:50] for e in events],
        "Severity": [_metric(e["severity"], 1) for e in events],
        "Confidence": [_metric(e["confidence"], 2) for e in events],
        "Started": [(e["start_time"] or "")[:10] for e in events],
        "Status": [e["status"] for e in events],
    }, use_container_width=True, hide_index=True)
    st.caption(
        "Severity is a property of the EVENT. Impact is a property of the "
        "event/asset pairing, and the same event can be bullish one asset and "
        "bearish another."
    )
    st.markdown("### POLITICAL FEATURES AVAILABLE TO ML")
    st.caption(
        "Stored separately rather than only as the final overlay, so the Lab "
        "can test WHICH political input mattered, for which asset class, and "
        "at which horizon: "
        + ", ".join(political.POLITICAL_FEATURES)
    )
    st.markdown(political.GPI_DETAILS)


# --------------------------------------------------------------------------
# Hedge research
# --------------------------------------------------------------------------

def _render_hedge_research(conn) -> None:
    st.markdown("### HEDGE RESEARCH")
    rows = storage.list_counterfactuals(conn)
    if not rows:
        st.info(
            "No hedge outcomes recorded yet. Every eligible strategy is stored "
            "at decision time, so once positions close the Lab can replay what "
            "each one WOULD have done — without having traded them all."
        )
        st.caption(
            "Research metrics: protection benefit, net protection benefit, "
            "hedge efficiency, upside sacrificed, downside avoided, drawdown "
            "reduction."
        )
        return

    actual = [r for r in rows if r["label"] == CF.ACTUAL]
    simulated = [r for r in rows if r["label"] == CF.SIMULATED]
    cells = st.columns(3)
    cells[0].metric("Hedge decisions recorded", fmt_int(len(rows)))
    cells[1].metric("Actually traded", fmt_int(len(actual)))
    cells[2].metric("Simulated counterfactuals", fmt_int(len(simulated)))
    st.warning(
        "**ACTUAL** rows were really traded. **SIMULATED COUNTERFACTUAL** rows "
        "are intrinsic-value replays of strategies that were scored but not "
        "traded — they assume no slippage or liquidity constraint."
    )
    st.dataframe({
        "Group": [r["group_key"] for r in rows],
        "Date": [r["snapshot_date"] for r in rows],
        "Strategy": [r["strategy_name"] or r["strategy_key"] for r in rows],
        "Label": [r["label"] for r in rows],
        "Net P&L": [fmt_money(r["net_pnl"]) for r in rows],
        "Hedge efficiency": [_metric(r["hedge_efficiency"], 2) for r in rows],
    }, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------
# Trade research
# --------------------------------------------------------------------------

def _render_trade_research(conn) -> None:
    st.markdown("### FINSIM TRADE RESEARCH")
    st.error(
        "**My trades are not a random sample of the market.** This dataset is "
        "used for execution, hedging and behaviour analysis only — never to "
        "train a general asset-return model. That is the MARKET dataset's job."
    )
    trades = storage.list_trades(conn)
    if not trades:
        st.info(
            "No blotter imported. Use the importer below, or call "
            "`blotter.import_finsim_blotter(conn, path)` with a CSV, TSV, "
            "Excel or SQLite export."
        )
    else:
        groups = storage.list_trade_groups(conn)
        links = storage.list_hedge_links(conn)
        cells = st.columns(4)
        cells[0].metric("Trades", fmt_int(len(trades)))
        cells[1].metric("Economic positions", fmt_int(len(groups)))
        cells[2].metric("Hedge links", fmt_int(len(links)))
        cells[3].metric("Mapped to universe",
                        fmt_int(sum(1 for t in trades if t["asset_id"])))
        st.dataframe({
            "Trade": [t["trade_id"] for t in trades],
            "Date": [(t["trade_date"] or "")[:10] for t in trades],
            "Symbol": [t["source_symbol"] for t in trades],
            "Universe": [t["universe_symbol"] or "UNMAPPED" for t in trades],
            "Side": [t["side"] for t in trades],
            "Qty": [fmt_int(t["quantity"]) for t in trades],
            "Price": [fmt_price(t["price"]) for t in trades],
            "Right": [_cell(t, "option_right") or "--" for t in trades],
            "Strike": [fmt_price(_cell(t, "strike")) if _cell(t, "strike")
                       else "--" for t in trades],
            "Group": [t["trade_group_id"] or "--" for t in trades],
            "Relationship": [t["hedge_relationship"] or "--" for t in trades],
        }, use_container_width=True, hide_index=True)

        incomplete = [t for t in trades if _cell(t, "contract_note")]
        if incomplete:
            st.warning(
                "**Cannot be replayed** (an option's payoff is not inferred): "
                + "; ".join(f"{t['trade_id']} — {_cell(t, 'contract_note')}"
                            for t in incomplete[:8])
            )

    st.markdown("### IMPORT A BLOTTER")
    st.caption(
        "Import freezes each trade's entry state — score, factors, prediction, "
        "hedge recommendation, GPI and model versions — write-once, so a later "
        "rescore can never change what the decision was made on."
    )
    path = st.text_input("Path to a FinSim export (.csv / .tsv / .xlsx / .db)",
                         key="blotter_path")
    if st.button("IMPORT BLOTTER") and path.strip():
        _import_blotter(conn, path.strip())

    _render_closed_positions(conn)


def _cell(row, name):
    """Read a column that may be absent on an older stored row."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _render_closed_positions(conn) -> None:
    """Grade a closed position and replay what else could have been traded."""
    import trade_research as TR

    st.markdown("### GRADE A CLOSED POSITION")
    st.caption(
        "The fast evidence path: a position held 18 days is graded on day 18. "
        "No 12-month wait, and no fabricated backtest in the meantime."
    )
    groups = storage.list_trade_groups(conn)
    if not groups:
        st.info("No economic positions stored yet. Import a blotter first.")
        return

    keys = [g["group_key"] for g in groups]
    chosen = st.selectbox("Position", keys, key="grade_group")
    cells = st.columns(3)
    exit_price = cells[0].number_input("Exit price", min_value=0.0, value=0.0,
                                       step=0.01, key="grade_exit_price")
    exit_date = cells[1].text_input("Exit date (YYYY-MM-DD)", key="grade_exit_date")
    raw_path = cells[2].text_input(
        "Price path (comma-separated, optional)", key="grade_path",
        help="Needed for drawdown metrics. Without it they stay unavailable "
             "rather than being guessed from the endpoints.")

    if st.button("GRADE POSITION") and exit_price > 0:
        path = []
        for piece in raw_path.split(","):
            piece = piece.strip()
            if piece:
                try:
                    path.append(float(piece))
                except ValueError:
                    st.error(f"'{piece}' is not a price.")
                    return
        outcome = TR.evaluate_closed_position(
            conn, chosen, exit_price=exit_price,
            exit_date=exit_date.strip() or None, price_path=path or None)
        st.session_state[f"graded_outcome_{chosen}"] = outcome

    # Keyed by position: switching positions must not leave another
    # position's grade on screen under the new name.
    outcome = st.session_state.get(f"graded_outcome_{chosen}")
    if outcome is None:
        return
    if outcome.net_pnl is None:
        st.error(" ".join(outcome.notes) or "Position could not be graded.")
        return

    cells = st.columns(4)
    cells[0].metric("Symbol", outcome.symbol or "--")
    cells[1].metric("Held (days)", fmt_int(outcome.holding_days))
    cells[2].metric("Net P&L", fmt_price(outcome.net_pnl))
    cells[3].metric("Unhedged return", _pct(outcome.unhedged_return))

    cells = st.columns(4)
    cells[0].metric("Underlying P&L", fmt_price(outcome.underlying_pnl))
    cells[1].metric("Hedge P&L", fmt_price(outcome.hedge_pnl))
    cells[2].metric("Hedge cost",
                    fmt_price(outcome.hedge_cost)
                    if outcome.hedge_cost is not None else "UNAVAILABLE")
    cells[3].metric("Hedge efficiency", _metric(outcome.hedge_efficiency, 2))

    cells = st.columns(3)
    cells[0].metric("Drawdown unhedged",
                    fmt_price(outcome.max_drawdown_unhedged)
                    if outcome.max_drawdown_unhedged is not None else "--")
    cells[1].metric("Drawdown hedged",
                    fmt_price(outcome.max_drawdown_hedged)
                    if outcome.max_drawdown_hedged is not None else "UNAVAILABLE")
    cells[2].metric("Prediction error (pp)", _metric(outcome.prediction_error, 2))

    if outcome.score_agreed_with_direction is False:
        st.warning(
            f"The position was held {outcome.direction} against a Shaffer "
            f"score of {fmt_score(outcome.entry_score)}. That disagreement is "
            "recorded as behaviour data, not corrected after the fact."
        )
    for gap in outcome.missing:
        st.caption(f"- unavailable: {gap}")
    for note in outcome.notes:
        st.caption(f"- {note}")

    if outcome.alternatives:
        st.markdown("#### WHAT ELSE COULD HAVE BEEN TRADED")
        st.caption(
            "Replayed from the strategies that were ELIGIBLE AT ENTRY, not "
            "chosen with hindsight. ACTUAL was really traded; SIMULATED "
            "COUNTERFACTUAL was scored but not traded."
        )
        ranked = CF.rank_outcomes(outcome.alternatives)
        st.dataframe({
            "Strategy": [a.strategy_name for a in ranked],
            "Label": [a.label for a in ranked],
            "Net P&L": [fmt_price(a.net_pnl) for a in ranked],
            "Hedge P&L": [fmt_price(a.hedge_pnl) for a in ranked],
            "Cost": [fmt_price(a.hedge_cost) if a.hedge_cost is not None
                     else "--" for a in ranked],
            "Efficiency": [_metric(a.hedge_efficiency, 2) for a in ranked],
            "Drawdown cut": [fmt_price(a.drawdown_reduction)
                             if a.drawdown_reduction is not None else "--"
                             for a in ranked],
        }, use_container_width=True, hide_index=True)

    summary = TR.trade_performance_summary([outcome])
    rows = TR.hedge_effectiveness_dataset(conn)
    st.markdown("#### HEDGE-EFFECTIVENESS DATASET")
    cells = st.columns(4)
    cells[0].metric("Rows", fmt_int(len(rows)))
    cells[1].metric("ACTUAL", fmt_int(sum(1 for r in rows
                                          if r.get("label") == CF.ACTUAL)))
    cells[2].metric("Counterfactual",
                    fmt_int(sum(1 for r in rows if r.get("label") == CF.SIMULATED)))
    cells[3].metric("Graded positions", fmt_int(summary.get("positions", 0)))
    st.caption(
        "This is the hedging/behaviour dataset. It is never merged with the "
        "market dataset to train a general return model."
    )


def _import_blotter(conn, path: str) -> None:
    """Import, group, link AND freeze entry states in one step.

    Freezing is part of importing rather than a separate button: a trade whose
    entry state was never captured can never be graded honestly afterwards.
    """
    import trade_research as TR

    with st.spinner(f"Importing {path}..."):
        summary = TR.import_and_freeze(conn, path)
    if not summary["rows_read"]:
        st.error(" ".join(summary["notes"]) or "Nothing imported.")
        return
    st.success(
        f"{summary['rows_read']} rows read, {summary['mapped']} mapped, "
        f"{summary['stored']} new trades stored, {summary['groups']} economic "
        f"positions, {summary['frozen']} entry states frozen."
    )
    if summary["already_frozen"]:
        st.caption(
            f"{summary['already_frozen']} trades were already frozen and were "
            "left exactly as they were — an entry state is written once."
        )
    if summary["no_score"]:
        st.caption(
            f"{summary['no_score']} trades had no Shaffer score stored at "
            "entry. The gap is recorded rather than filled in later."
        )
    for note in summary["notes"]:
        st.caption(note)
    if summary["unmapped"]:
        st.warning("Not in the Shaffer universe (kept, flagged): "
                   + ", ".join(summary["unmapped"][:20]))


# --------------------------------------------------------------------------
# Improvement proposals
# --------------------------------------------------------------------------

def _render_proposals(conn, horizon, target, sampling) -> None:
    st.markdown("### SHAFFER IMPROVEMENT PROPOSALS")
    st.info(
        "Proposals are **evidence, not changes**. Nothing here is applied "
        "automatically; promoting anything is an explicit action."
    )
    reports = st.session_state.get(f"ml_reports_{horizon}_{target}_{sampling}")
    calibration = st.session_state.get(f"ml_calib_{horizon}_{target}_{sampling}")
    dataset = _dataset(conn, horizon, target, sampling)

    proposals = build_proposals(dataset, reports, calibration)
    if not proposals:
        st.warning(
            f"**No proposals.** With {dataset.effective_observations} effective "
            f"observations there is not enough evidence to propose a change to "
            f"any Shaffer v1 formula. That is the correct answer, not a gap."
        )
        return
    for proposal in proposals:
        st.markdown(f"- {proposal}")


def build_proposals(dataset, reports, calibration) -> list:
    """Deterministic, evidence-gated proposals. Empty when evidence is thin."""
    if dataset is None or dataset.effective_observations < ml_lab.MIN_OBS_EXPERIMENTAL:
        return []

    proposals = []
    if calibration is not None and calibration.beta is not None:
        if abs(calibration.beta - pred.V1_SLOPE) > 0.05:
            direction = "too high" if pred.V1_SLOPE > calibration.beta else "too low"
            proposals.append(
                f"**Score-to-return slope may be {direction}.** Production uses "
                f"{pred.V1_SLOPE:.2f}; the realised fit is {calibration.beta:.3f} "
                f"over {calibration.n} observations "
                f"({calibration.training_start} → {calibration.training_end})."
            )
    if not reports:
        return proposals

    baseline = next((r for r in reports if r.family == "baseline"), None)
    for report in reports:
        if report.family == "baseline" or report.status == ml_lab.INSUFFICIENT:
            continue
        spearman = report.metrics.get("spearman")
        base_spearman = baseline.metrics.get("spearman") if baseline else None
        if (spearman is not None and base_spearman is not None
                and spearman > base_spearman + ml_lab.MIN_SPEARMAN_EDGE):
            proposals.append(
                f"**{report.name} out-ranks the Shaffer baseline** "
                f"({spearman:.3f} vs {base_spearman:.3f} Spearman, "
                f"{len(report.folds)} walk-forward folds). Status: "
                f"{report.status}."
            )
        if report.importance:
            weakest = report.importance[-1]
            if weakest[1] < 0.03:
                proposals.append(
                    f"**{weakest[0]} adds little incremental value** in "
                    f"{report.name} ({weakest[1]:.1%} of predictive importance)."
                )
    return proposals

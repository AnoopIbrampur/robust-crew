"""Connection-break risk model.

Label. A crew connection i -> j (consecutive legs of some legal pairing)
*breaks* if the actual arrival of i plus the minimum connection time is later
than the scheduled departure of j. Since the scheduled gap is
``min_connect + slack``, this is simply ``ArrDelay(i) > slack``. Connections
where i was cancelled or diverted, or j was cancelled, are excluded from
training and evaluation (the replay drops cancelled flights from the plan).

Two feature sets, kept strictly separate:

- PLANNING (known when the schedule is built): stations, scheduled hours, day of
  week, month, distance, block time, scheduled slack, same-tail flag, the
  tail's scheduled turn before i and leg number of the day, CLT bank
  congestion (all carriers' scheduled movements), and historical route delay
  statistics computed ONLY from the TRAIN window (out-of-fold by month for
  training rows, so a row never sees its own month's outcomes).
- OPERATIONS (known the morning of the day, at ``OPS_CUTOFF_LOCAL`` Eastern):
  planning features plus the latest weather observation at the hub and at i's
  origin, and delays already observed that day at CLT and across AA.
  Used only by repair.py.

Splits are by date only (config). The model is fit on TRAIN (last two TRAIN
months used for early stopping), calibrated with isotonic regression on
CALIB, and evaluated on each test window.

Usage::

    uv run python -m robust_crew.delay_model connections   # build labelled connections
    uv run python -m robust_crew.delay_model train         # fit, calibrate, evaluate
    uv run python -m robust_crew.delay_model predict       # score every connection -> conn_risk
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import time
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, roc_auc_score

from robust_crew import config
from robust_crew.ingest import connect
from robust_crew.network import build_connection_graph, load_day_legs
from robust_crew.pairings import enumerate_pairings
from robust_crew.rules import DEFAULT_RULES

log = logging.getLogger(__name__)

PROCESSED_DIR = config.DATA_DIR / "processed"
MODEL_DIR = config.DATA_DIR / "models"
OPS_CUTOFF_LOCAL = "07:00"  # operations features use only information before this (Eastern)
EARLY_STOP_START = date(2023, 3, 1)  # last TRAIN months, held out for early stopping
SEED = 0
EXCEED_THRESHOLDS = [0, 15, 30, 60, 120]  # minutes; route exceedance rates

CAT_FEATURES = ["i_origin", "i_dest"]
PLANNING_FEATURES = CAT_FEATURES + [
    "station_is_hub", "i_dep_hour", "i_arr_hour", "j_dep_hour", "dow", "month",
    "i_distance", "i_block", "gap_min", "slack_min", "same_tail",
    "tail_prior_turn_min", "tail_leg_seq",
    "hub_deps_at_j", "hub_arrs_at_i",
    "route_n", "route_mean_delay", "route_p_exceed_slack",
] + [f"route_p_gt{t}" for t in EXCEED_THRESHOLDS]
OPS_EXTRA = [
    "hub_vsby", "hub_ceiling", "hub_sknt", "hub_gust", "hub_p01i", "hub_ts", "hub_sn", "hub_fog",
    "org_vsby", "org_ceiling", "org_sknt", "org_gust", "org_p01i", "org_ts", "org_sn", "org_fog",
    "hub_dep_delay_sofar", "hub_late_share_sofar", "hub_n_sofar", "aa_dep_delay_sofar",
]
OPS_FEATURES = PLANNING_FEATURES + OPS_EXTRA


# --- Connections -------------------------------------------------------------------

def build_connections(con: duckdb.DuckDBPyConnection) -> int:
    """Table ``connections``: every connection used by some legal pairing, every day."""
    rows = []
    d = config.DATA_START
    while d <= config.DATA_END:
        legs = load_day_legs(con, d)
        edges = {(e.i, e.j): e for e in build_connection_graph(legs)}
        res = enumerate_pairings(legs, list(edges.values()))
        used = {(p.legs[k], p.legs[k + 1]) for p in res.pairings for k in range(len(p.legs) - 1)}
        for i, j in used:
            e = edges[(i, j)]
            need = DEFAULT_RULES.min_connect_min(e.same_tail)
            rows.append((d, legs[i].flight_id, legs[j].flight_id, legs[i].dest,
                         e.gap_min, need, e.gap_min - need, e.same_tail))
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["FlightDate", "i_id", "j_id", "station", "gap_min",
                                     "min_conn", "slack_min", "same_tail"])
    con.execute("DROP TABLE IF EXISTS connections")
    con.register("conn_df", df)
    con.execute("CREATE TABLE connections AS SELECT * FROM conn_df")
    con.unregister("conn_df")
    return len(df)


# --- Training-only historical route statistics ---------------------------------------

def build_route_stats(con: duckdb.DuckDBPyConnection) -> None:
    """Table ``route_month_stats``: per route and month delay sums, TRAIN window only."""
    gt = ", ".join(f"sum((ArrDelay > {t})::INT) AS n_gt{t}" for t in EXCEED_THRESHOLDS)
    con.execute("DROP TABLE IF EXISTS route_month_stats")
    con.execute(
        f"""CREATE TABLE route_month_stats AS
            SELECT Origin, Dest, date_trunc('month', FlightDate)::DATE AS month_start,
                   max(FlightDate) AS max_date, count(*) AS n, sum(ArrDelay) AS sum_delay, {gt}
            FROM flights
            WHERE Reporting_Airline = ? AND FlightDate BETWEEN ? AND ?
              AND NOT Cancelled AND NOT Diverted AND ArrDelay IS NOT NULL
            GROUP BY ALL""",
        [config.CARRIER, config.TRAIN_START, config.TRAIN_END],
    )


# --- Features ------------------------------------------------------------------------

def load_features(con: duckdb.DuckDBPyConnection, table: str = "connections") -> pd.DataFrame:
    """One row per labelled connection in ``table`` with planning + operations features and split."""
    gt_tot = ", ".join(f"sum(n_gt{t}) AS t_gt{t}" for t in EXCEED_THRESHOLDS)
    gt_oof = ", ".join(
        f"""CASE WHEN c.split = 'train'
                 THEN (rt.t_gt{t} - coalesce(rm.n_gt{t}, 0)) / nullif(rt.t_n - coalesce(rm.n, 0), 0)
                 ELSE rt.t_gt{t} / rt.t_n END AS route_p_gt{t}"""
        for t in EXCEED_THRESHOLDS
    )
    wx = lambda p: f"""  {p}.vsby AS {p[0:3]}_vsby, coalesce({p}.ceiling_ft, 99999) AS {p[0:3]}_ceiling,
        {p}.sknt AS {p[0:3]}_sknt, coalesce({p}.gust, 0) AS {p[0:3]}_gust, {p}.p01i AS {p[0:3]}_p01i,
        coalesce(contains({p}.wxcodes, 'TS'), false)::INT AS {p[0:3]}_ts,
        coalesce(contains({p}.wxcodes, 'SN'), false)::INT AS {p[0:3]}_sn,
        coalesce(regexp_matches({p}.wxcodes, 'FG|BR'), false)::INT AS {p[0:3]}_fog"""  # noqa: E731
    sql = f"""
    WITH c AS (
      SELECT c.*, fi.split, fi.Origin AS i_origin, fi.Dest AS i_dest,
             fi.ArrDelay AS i_arr_delay, fi.Distance AS i_distance, fi.CRSElapsedTime AS i_block,
             fi.sched_dep_utc AS i_dep_utc, fi.sched_arr_utc AS i_arr_utc,
             fi.origin_tz, fi.dest_tz, fj.sched_dep_utc AS j_dep_utc,
             (fi.Cancelled OR fi.Diverted OR fj.Cancelled) AS excluded,
             timezone('America/New_York', c.FlightDate + TIME '{OPS_CUTOFF_LOCAL}') AS cutoff_utc
      FROM {table} c
      JOIN flights fi ON fi.flight_id = c.i_id
      JOIN flights fj ON fj.flight_id = c.j_id
    ),
    rt AS (SELECT Origin, Dest, sum(n) AS t_n, sum(sum_delay) AS t_sum, {gt_tot}
           FROM route_month_stats GROUP BY ALL),
    hubdep AS (SELECT time_bucket(INTERVAL 30 MINUTE, sched_dep_utc) AS b, count(*) AS n
               FROM flights WHERE Origin = '{config.HUB}' GROUP BY 1),
    hubarr AS (SELECT time_bucket(INTERVAL 30 MINUTE, sched_arr_utc) AS b, count(*) AS n
               FROM flights WHERE Dest = '{config.HUB}' GROUP BY 1),
    sofar AS (
      SELECT d.FlightDate,
             avg(f.DepDelay) FILTER (WHERE f.Origin = '{config.HUB}') AS hub_dep_delay_sofar,
             avg((f.DepDelay > 15)::INT) FILTER (WHERE f.Origin = '{config.HUB}') AS hub_late_share_sofar,
             count(*) FILTER (WHERE f.Origin = '{config.HUB}') AS hub_n_sofar,
             avg(f.DepDelay) FILTER (WHERE f.Reporting_Airline = '{config.CARRIER}') AS aa_dep_delay_sofar
      FROM (SELECT DISTINCT FlightDate,
                   timezone('America/New_York', FlightDate + TIME '{OPS_CUTOFF_LOCAL}') AS cutoff_utc
            FROM {table}) d
      JOIN flights f ON f.FlightDate = d.FlightDate AND f.actual_dep_utc < d.cutoff_utc
      GROUP BY d.FlightDate
    ),
    seq AS (  -- from the full schedule, cancelled flights included (planning-time knowledge)
      SELECT flight_id,
             row_number() OVER (PARTITION BY Tail_Number, FlightDate ORDER BY sched_dep_utc) AS tail_leg_seq,
             date_diff('minute', lag(sched_arr_utc) OVER (PARTITION BY Tail_Number ORDER BY sched_dep_utc),
                       sched_dep_utc) AS tail_prior_turn_min
      FROM flights WHERE Reporting_Airline = '{config.CARRIER}' AND Tail_Number IS NOT NULL
    )
    SELECT c.FlightDate, c.split, c.i_id, c.j_id, c.i_origin, c.i_dest,
           (c.station = '{config.HUB}')::INT AS station_is_hub,
           hour(timezone(c.origin_tz, c.i_dep_utc)) AS i_dep_hour,
           hour(timezone(c.dest_tz, c.i_arr_utc)) AS i_arr_hour,
           hour(timezone(c.dest_tz, c.j_dep_utc)) AS j_dep_hour,
           dayofweek(c.FlightDate) AS dow, month(c.FlightDate) AS month,
           c.i_distance, c.i_block, c.gap_min, c.slack_min, c.same_tail::INT AS same_tail,
           seq.tail_prior_turn_min, seq.tail_leg_seq,
           coalesce(hd.n, 0) AS hub_deps_at_j, coalesce(ha.n, 0) AS hub_arrs_at_i,
           CASE WHEN c.split = 'train' THEN rt.t_n - coalesce(rm.n, 0) ELSE rt.t_n END AS route_n,
           CASE WHEN c.split = 'train'
                THEN (rt.t_sum - coalesce(rm.sum_delay, 0)) / nullif(rt.t_n - coalesce(rm.n, 0), 0)
                ELSE rt.t_sum / rt.t_n END AS route_mean_delay,
           {gt_oof},
           {wx('hubw')}, {wx('orgw')},
           s.hub_dep_delay_sofar, s.hub_late_share_sofar, coalesce(s.hub_n_sofar, 0) AS hub_n_sofar,
           s.aa_dep_delay_sofar,
           c.i_arr_delay, (c.i_arr_delay > c.slack_min)::INT AS broke, c.excluded
    FROM c
    LEFT JOIN rt ON rt.Origin = c.i_origin AND rt.Dest = c.i_dest
    LEFT JOIN route_month_stats rm ON rm.Origin = c.i_origin AND rm.Dest = c.i_dest
         AND rm.month_start = date_trunc('month', c.FlightDate)::DATE
    LEFT JOIN hubdep hd ON hd.b = time_bucket(INTERVAL 30 MINUTE, c.j_dep_utc)
    LEFT JOIN hubarr ha ON ha.b = time_bucket(INTERVAL 30 MINUTE, c.i_arr_utc)
    LEFT JOIN seq ON seq.flight_id = c.i_id
    LEFT JOIN sofar s ON s.FlightDate = c.FlightDate
    ASOF LEFT JOIN weather_obs hubw ON hubw.station = '{config.HUB}' AND c.cutoff_utc >= hubw.valid_utc
    ASOF LEFT JOIN weather_obs orgw ON orgw.station = c.i_origin AND c.cutoff_utc >= orgw.valid_utc
    """
    df = con.sql(sql).df()
    df["route_p_exceed_slack"] = route_exceedance(df)
    for col in CAT_FEATURES:
        df[col] = df[col].astype("category")
    return df


def route_exceedance(df: pd.DataFrame) -> np.ndarray:
    """P(route arrival delay > slack), interpolated from the route's threshold rates."""
    xs = np.array(EXCEED_THRESHOLDS, dtype=float)
    ys = df[[f"route_p_gt{t}" for t in EXCEED_THRESHOLDS]].to_numpy(dtype=float)
    s = df["slack_min"].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    for k in range(len(df)):
        if not np.isnan(ys[k]).any():
            out[k] = np.interp(s[k], xs, ys[k])
    return out


# --- Model ---------------------------------------------------------------------------

@dataclass
class FittedModel:
    """A LightGBM booster plus its isotonic calibrator and feature list."""

    name: str
    features: list[str]
    booster: lgb.Booster
    calibrator: IsotonicRegression
    categories: dict

    def predict_raw(self, df: pd.DataFrame) -> np.ndarray:
        X = df[self.features].copy()
        for col, cats in self.categories.items():
            X[col] = pd.Categorical(X[col].astype(str), categories=cats)
        return self.booster.predict(X, num_iteration=self.booster.best_iteration)

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Calibrated break probability."""
        return self.calibrator.predict(self.predict_raw(df))


LGB_PARAMS = {
    "objective": "binary", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 200,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "seed": SEED, "deterministic": True, "force_row_wise": True, "verbose": -1,
}


def fit_model(name: str, features: list[str], train: pd.DataFrame, calib: pd.DataFrame) -> FittedModel:
    """Fit LightGBM on TRAIN (early stopping on its last months), calibrate on CALIB."""
    categories = {c: sorted(train[c].astype(str).unique()) for c in features if c in CAT_FEATURES}

    def X(df):
        x = df[features].copy()
        for col, cats in categories.items():
            x[col] = pd.Categorical(x[col].astype(str), categories=cats)
        return x

    fit = train[train.FlightDate < pd.Timestamp(EARLY_STOP_START)]
    stop = train[train.FlightDate >= pd.Timestamp(EARLY_STOP_START)]
    booster = lgb.train(
        LGB_PARAMS, lgb.Dataset(X(fit), fit.broke),
        num_boost_round=3000, valid_sets=[lgb.Dataset(X(stop), stop.broke)],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    raw = booster.predict(X(calib), num_iteration=booster.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, calib.broke)
    return FittedModel(name, features, booster, iso, categories)


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    """ROC AUC, Brier score, log loss, base rate and mean prediction."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "n": int(len(y)), "base_rate": round(float(y.mean()), 4), "mean_pred": round(float(p.mean()), 4),
        "auc": round(float(roc_auc_score(y, p)), 4), "brier": round(float(brier_score_loss(y, p)), 5),
        "log_loss": round(float(log_loss(y, p)), 5),
    }


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    """Observed vs predicted break rate in equal-count bins of predicted probability."""
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, bins):
        out.append({"pred": round(float(p[chunk].mean()), 4), "obs": round(float(y[chunk].mean()), 4),
                    "n": int(len(chunk))})
    return out


def train_and_evaluate() -> dict:
    """Fit planning and operations models plus baselines; write reports and figures."""
    con = connect()
    build_route_stats(con)
    t0 = time.perf_counter()
    df = load_features(con)
    con.close()
    log.info("features: %d rows in %.1fs", len(df), time.perf_counter() - t0)
    df = df[~df.excluded].copy()
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    train = df[df.split == "train"]
    calib = df[df.split == "calib"]
    tests = {s: df[df.split == s] for s in config.TEST_WINDOWS}

    models = {
        "planning": fit_model("planning", PLANNING_FEATURES, train, calib),
        "operations": fit_model("operations", OPS_FEATURES, train, calib),
    }

    # Baseline 1: logistic regression on scheduled slack alone.
    slack_lr = LogisticRegression().fit(train[["slack_min"]], train.broke)
    # Baseline 2: the route's historical P(delay > slack) (train-only), isotonic-calibrated on CALIB.
    def route_base(d):
        return d.route_p_exceed_slack.fillna(train.broke.mean()).to_numpy()
    route_iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(route_base(calib), calib.broke)

    predictors = {
        "baseline_slack_logistic": lambda d: slack_lr.predict_proba(d[["slack_min"]])[:, 1],
        "baseline_route_history": lambda d: route_iso.predict(route_base(d)),
        "planning_model": models["planning"].predict_proba,
        "operations_model": models["operations"].predict_proba,
        "planning_model_uncalibrated": models["planning"].predict_raw,
    }
    report: dict = {
        "rows": {"train": len(train), "calib": len(calib), **{k: len(v) for k, v in tests.items()}},
        "break_rate": {"train": round(float(train.broke.mean()), 4), "calib": round(float(calib.broke.mean()), 4),
                       **{k: round(float(v.broke.mean()), 4) for k, v in tests.items()}},
        "best_iteration": {k: m.booster.best_iteration for k, m in models.items()},
        "ops_cutoff_local": OPS_CUTOFF_LOCAL,
        "metrics": {}, "calibration": {},
    }
    for split, d in {"calib": calib, **tests}.items():
        y = d.broke.to_numpy()
        report["metrics"][split] = {name: metrics(y, f(d)) for name, f in predictors.items()}
        report["calibration"][split] = {
            name: calibration_table(y, predictors[name](d))
            for name in ["planning_model", "operations_model", "planning_model_uncalibrated"]
        }
    imp = models["planning"].booster.feature_importance("gain")
    report["planning_feature_importance_gain_share"] = {
        f: round(float(v / imp.sum()), 4)
        for f, v in sorted(zip(PLANNING_FEATURES, imp), key=lambda t: -t[1])
    }
    report["arrival_delay_regressor"] = fit_delay_regressor(train, calib, tests)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_DIR / "break_models.pkl", "wb") as fh:
        pickle.dump(models, fh)
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "phase4_delay_model.json").write_text(json.dumps(report, indent=2))
    plot_calibration(report)
    return report


def fit_delay_regressor(train: pd.DataFrame, calib: pd.DataFrame, tests: dict) -> dict:
    """LightGBM regressor of i's arrival delay (one row per distinct leg i), planning features.

    Uses a Huber loss because delays are heavy-tailed. Compared with predicting
    the route's historical mean and with predicting zero.
    """
    leg_feats = [f for f in PLANNING_FEATURES if f not in
                 ("gap_min", "slack_min", "same_tail", "hub_deps_at_j", "j_dep_hour", "route_p_exceed_slack")]

    def legs(d):
        return d.drop_duplicates("i_id")

    tr, ca = legs(train), legs(calib)
    cats = {c: sorted(tr[c].astype(str).unique()) for c in CAT_FEATURES}

    def X(d):
        x = d[leg_feats].copy()
        for col, cs in cats.items():
            x[col] = pd.Categorical(x[col].astype(str), categories=cs)
        return x

    fit = tr[tr.FlightDate < pd.Timestamp(EARLY_STOP_START)]
    stop = tr[tr.FlightDate >= pd.Timestamp(EARLY_STOP_START)]
    params = {**LGB_PARAMS, "objective": "huber", "alpha": 30.0}
    booster = lgb.train(params, lgb.Dataset(X(fit), fit.i_arr_delay), num_boost_round=3000,
                        valid_sets=[lgb.Dataset(X(stop), stop.i_arr_delay)],
                        callbacks=[lgb.early_stopping(100, verbose=False)])
    out = {"objective": "huber(alpha=30)", "best_iteration": booster.best_iteration}
    for split, d in {"calib": ca, **{k: legs(v) for k, v in tests.items()}}.items():
        y = d.i_arr_delay.to_numpy()
        pred = booster.predict(X(d), num_iteration=booster.best_iteration)
        out[split] = {
            "n_legs": int(len(d)),
            "mae_model": round(float(mean_absolute_error(y, pred)), 2),
            "mae_route_mean": round(float(mean_absolute_error(y, d.route_mean_delay.fillna(0))), 2),
            "mae_zero": round(float(mean_absolute_error(y, np.zeros_like(y))), 2),
            "median_abs_err_model": round(float(np.median(np.abs(y - pred))), 2),
        }
    return out


def plot_calibration(report: dict) -> None:
    """Reliability diagrams per evaluation window for planning and operations models."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    splits = list(report["calibration"])
    fig, axes = plt.subplots(1, len(splits), figsize=(4.2 * len(splits), 4.2), sharey=True)
    for ax, split in zip(axes, splits):
        for name, style in [("planning_model_uncalibrated", ":"), ("planning_model", "-o"), ("operations_model", "-s")]:
            tab = report["calibration"][split][name]
            m = report["metrics"][split].get(name, {})
            label = f"{name.replace('_model', '')} (AUC {m.get('auc', float('nan')):.3f})"
            ax.plot([r["pred"] for r in tab], [r["obs"] for r in tab], style, ms=4, label=label)
        ax.plot([0, 1], [0, 1], color="grey", lw=0.8)
        top = max(max(r["pred"], r["obs"]) for n in report["calibration"][split].values() for r in n)
        ax.set_xlim(0, min(1, top * 1.1)); ax.set_ylim(0, min(1, top * 1.1))
        ax.set_title(f"{split} (break rate {report['break_rate'][split]:.3f})")
        ax.set_xlabel("predicted break probability")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="upper left")
    axes[0].set_ylabel("observed break rate")
    fig.suptitle("Connection-break model calibration (10 equal-count bins)")
    fig.tight_layout()
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.FIGURES_DIR / "break_model_calibration.png", dpi=150)
    plt.close(fig)


def predict_risk() -> int:
    """Table ``conn_risk``: calibrated planning and operations break probabilities per connection.

    Scored for every connection, including those whose flights were later
    cancelled (a planner does not know cancellations in advance).
    """
    with open(MODEL_DIR / "break_models.pkl", "rb") as fh:
        models = pickle.load(fh)
    con = connect()
    df = load_features(con)
    out = pd.DataFrame({
        "FlightDate": df.FlightDate, "split": df.split, "i_id": df.i_id, "j_id": df.j_id,
        "p_plan": models["planning"].predict_proba(df),
        "p_ops": models["operations"].predict_proba(df),
    })
    con.execute("DROP TABLE IF EXISTS conn_risk")
    con.register("risk_df", out)
    con.execute("CREATE TABLE conn_risk AS SELECT * FROM risk_df")
    con.unregister("risk_df")
    con.close()
    return len(out)


def main() -> None:
    """Command-line entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["connections", "train", "predict", "all"])
    args = ap.parse_args()
    if args.step in ("connections", "all"):
        con = connect()
        log.info("connections: %d rows", build_connections(con))
        con.close()
    if args.step in ("train", "all"):
        rep = train_and_evaluate()
        print(json.dumps({k: rep[k] for k in ("rows", "break_rate", "metrics", "arrival_delay_regressor")}, indent=2))
    if args.step in ("predict", "all"):
        log.info("conn_risk: %d rows", predict_risk())


if __name__ == "__main__":
    main()

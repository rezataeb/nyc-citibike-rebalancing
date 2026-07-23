"""Full-year demand model: real per-date features, three honestly-compared
tiers, walk-forward validation, and an explicit extrapolation guard.

Session 5's original model trained on weather joined at (month, day_type)
grain -- one averaged temperature per month, not per real day -- and lost
to the seasonal-naive baseline on a held-out month whose actual
temperature fell outside the training range: HistGradientBoostingRegressor
clamps flat past the edge of what it saw in training. This module fixes
both problems at once. It trains directly on data/daily_net_flow.parquet
(real per-date, per-hour net flow, built by pipeline/build_full_year.py),
so every row carries its own real day's weather instead of a monthly
average, and it compares three tiers -- seasonal-naive, a spline-based
GAM, and a tuned GBM -- with a named extrapolation guard that stops
trusting the GBM once a prediction's temperature leaves the range it was
trained on, falling back to the GAM (which degrades more gracefully) or,
past a wider margin, to the naive baseline.

Validation is a 12-fold walk-forward (leave-one-month-out) over the full
Jul 2025-Jun 2026 window, not a single train/test split -- see
PROGRESS.md's Session 5 entry for why a single split couldn't tell
"extrapolation failure" apart from "the model is generally worse."
Station typology is refit independently within each fold's training data
only (not once on the full year) so a fold's held-out month never
influences the very cluster label used to predict it.

Deliberately still predicting net flow only, not arrivals/departures
separately -- that split is real, separate work, tracked in PROGRESS.md's
Deferred list.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, SplineTransformer

from pipeline.build_full_year import DAILY_NET_FLOW_PATH, TARGET_MONTHS
from pipeline.flows import SEASON_OF
from pipeline.station_typology import LOW_SIGNAL_NAME, build_typology
from pipeline.weather import fetch_daily_weather

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODEL_PERFORMANCE_PATH = DATA_DIR / "model_performance.json"

WEATHER_START, WEATHER_END = "2025-07-01", "2026-06-30"

# How far past a fold's observed training temperature range (in Celsius)
# the GAM tier is still trusted before falling back all the way to the
# naive baseline. Named and tunable, same style as the dashboard's
# existing 12%/88% dock-risk thresholds -- not a silent magic number.
GAM_EXTRAPOLATION_MARGIN_C = 5.0

# Validation slice size (fraction of a fold's training dates, most recent
# first) carved out for GBM hyperparameter selection -- never the fold's
# true held-out month, which would tune against the test set itself.
VALIDATION_FRACTION = 0.15

GBM_FEATURE_COLUMNS = [
    "lat", "lng", "hour_sin", "hour_cos", "day_of_week",
    "temp_mean_c", "precip_mm", "doy_sin", "doy_cos", "is_holiday", "typology_cluster",
]
GBM_CATEGORICAL_COLUMNS = ["day_of_week", "typology_cluster"]

GBM_PARAM_GRID = [
    {"max_iter": 100, "learning_rate": 0.1, "max_depth": None},
    {"max_iter": 200, "learning_rate": 0.05, "max_depth": None},
    {"max_iter": 200, "learning_rate": 0.1, "max_depth": 6},
]

UNKNOWN_TYPOLOGY_CLUSTER = -2  # a station absent from this fold's training typology fit entirely


# ---------------------------------------------------------------------------
# Feature preparation
# ---------------------------------------------------------------------------

def load_daily_table(path: Path = DAILY_NET_FLOW_PATH) -> pd.DataFrame:
    """Load the persisted (station, date, hour) net-flow table."""
    daily = pd.read_parquet(path)
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def _day_type(dates: pd.Series) -> pd.Series:
    return dates.dt.dayofweek.map(lambda d: "weekend" if d >= 5 else "weekday")


def get_holidays(start: str = WEATHER_START, end: str = WEATHER_END) -> set:
    """Real US federal holidays in [start, end], via pandas' own calendar --
    not hand-derived, so a moving weekday-based holiday (e.g. the third
    Monday of a month) can't be quietly miscalculated.
    """
    from pandas.tseries.holiday import USFederalHolidayCalendar

    holidays = USFederalHolidayCalendar().holidays(start=start, end=end)
    return set(holidays.date)


def add_calendar_features(daily: pd.DataFrame, holidays: set) -> pd.DataFrame:
    """Add month/day_type/day_of_week/cyclical time features, real per-date --
    the direct fix for Session 5's (month, day_type)-averaged weather join.
    """
    daily = daily.copy()
    daily["month"] = daily["date"].dt.strftime("%Y-%m")
    daily["day_type"] = _day_type(daily["date"])
    daily["day_of_week"] = daily["date"].dt.day_name()
    daily["is_holiday"] = daily["date"].dt.date.isin(holidays).astype(int)

    day_of_year = daily["date"].dt.dayofyear
    daily["doy_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    daily["doy_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    daily["hour_sin"] = np.sin(2 * np.pi * daily["hour"] / 24)
    daily["hour_cos"] = np.cos(2 * np.pi * daily["hour"] / 24)
    return daily


def join_weather(daily: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Real per-date temp/precip -- an inner join, so a date outside the
    fetched weather range is dropped rather than fabricated.
    """
    return daily.merge(weather[["date", "temp_mean_c", "precip_mm"]], on="date", how="inner")


def build_feature_frame(daily: pd.DataFrame, holidays: set, weather: pd.DataFrame) -> pd.DataFrame:
    """Full real-per-date feature frame, before per-fold typology is attached."""
    return join_weather(add_calendar_features(daily, holidays), weather)


# ---------------------------------------------------------------------------
# Per-fold station typology (refit on training data only)
# ---------------------------------------------------------------------------

def weekday_curve_dict(daily_train: pd.DataFrame) -> dict:
    """Station -> {"weekday": [24 floats]} mean weekday net flow, built only
    from this fold's training rows -- the input station_typology.py's
    build_typology() expects, reused rather than reimplemented.
    """
    # .unstack introduces real NaN for any (station, hour) combination with
    # zero training rows -- e.g. a low-activity station with no recorded
    # weekday trips at 3am -- not just for hours missing from the columns
    # entirely. reindex's own fill_value only covers the latter, so a
    # separate .fillna(0.0) is required for the former; without it, a
    # single quiet station-hour anywhere in the fold silently poisons
    # every downstream KMeans fit with a NaN input row.
    weekday = daily_train[daily_train["day_type"] == "weekday"]
    curves = (
        weekday.groupby(["station_id", "hour"])["net"]
        .mean()
        .unstack("hour")
        .reindex(columns=range(24))
        .fillna(0.0)
    )
    return {
        str(station_id): {"weekday": row.tolist()}
        for station_id, row in curves.iterrows()
    }


def refit_typology(daily_train: pd.DataFrame) -> dict[str, tuple[int, str]]:
    """Refit station typology from this fold's training months only --
    avoids the held-out month's own data informing the cluster label used
    to predict it (see module docstring).
    """
    stations = weekday_curve_dict(daily_train)
    assignments, _ = build_typology(stations)
    return assignments


def add_typology(frame: pd.DataFrame, assignments: dict[str, tuple[int, str]]) -> pd.DataFrame:
    """Attach typology_cluster to every row. A station absent from this
    fold's training assignments entirely (e.g. it only appears in the
    held-out test month) gets UNKNOWN_TYPOLOGY_CLUSTER, a code distinct
    from both real clusters and station_typology's own -1 low-signal
    code -- so "never seen in training" stays visibly different from
    "seen but low-signal."
    """
    frame = frame.copy()
    cluster = frame["station_id"].map(lambda sid: assignments.get(str(sid), (UNKNOWN_TYPOLOGY_CLUSTER, "Unseen in training"))[0])
    frame["typology_cluster"] = cluster.astype(str)
    return frame


# ---------------------------------------------------------------------------
# Tier 1: seasonal-naive / flat baseline
#
# build_naive_forecast/build_flat_baseline originated in pipeline/backtest.py
# (Session 4). Moved here (Session 33) when backtest.py and gbm.py were
# retired -- demand_model.py's walk-forward superseded the single-split
# workflow those two modules were built for, but these two functions
# themselves weren't superseded, just relocated to their one remaining real
# caller. compute_mae, backtest.py's other export, had no remaining
# consumer once gbm.py was gone (this module scores its own predictions via
# score()/score_by_group() instead) and was retired along with it, not
# moved -- see PROGRESS.md's Session 33 entry.
# ---------------------------------------------------------------------------

def _weighted_average(group: pd.DataFrame, value_col: str, weight_col: str = "n_days") -> float:
    """Average of value_col weighted by weight_col (e.g. a 31-day month counts more than 28)."""
    return (group[value_col] * group[weight_col]).sum() / group[weight_col].sum()


def build_naive_forecast(train_flows: pd.DataFrame) -> pd.DataFrame:
    """Seasonal-naive forecast: predicted net_per_day per (station_id, day_type, hour).

    If train_flows spans multiple months, each month's contribution is
    weighted by its own distinct-date count, same as flows.py's curves.
    """
    predicted = (
        train_flows.groupby(["station_id", "day_type", "hour"])
        .apply(_weighted_average, value_col="net_per_day", include_groups=False)
        .rename("predicted_net_per_day")
        .reset_index()
    )
    return predicted


def build_flat_baseline(train_flows: pd.DataFrame) -> pd.DataFrame:
    """Dumb baseline: predicted net_per_day per station_id only, ignoring hour-of-day.

    The same single number is used to predict every hour of every day for
    that station -- no seasonal-naive structure at all.
    """
    predicted = (
        train_flows.groupby("station_id")
        .apply(_weighted_average, value_col="net_per_day", include_groups=False)
        .rename("predicted_net_per_day")
        .reset_index()
    )
    return predicted


def daily_to_flow_rows(daily_train: pd.DataFrame) -> pd.DataFrame:
    """Reshape daily training rows into the (station_id, month, day_type,
    hour, net_per_day, n_days) shape build_naive_forecast/build_flat_baseline
    above expect. n_days=1 per row (each row already IS one real day, no
    monthly distinct-date normalization needed) makes their n_days-weighted
    average a plain mean here -- correct, not a special case.
    """
    rows = daily_train[["station_id", "month", "day_type", "hour", "net"]].copy()
    rows = rows.rename(columns={"net": "net_per_day"})
    rows["n_days"] = 1
    return rows


def train_baselines(daily_train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit seasonal-naive and flat baselines on a fold's training data."""
    flow_rows = daily_to_flow_rows(daily_train)
    return build_naive_forecast(flow_rows), build_flat_baseline(flow_rows)


def predict_baseline(naive: pd.DataFrame, flat: pd.DataFrame, test_frame: pd.DataFrame) -> pd.Series:
    """Broadcast the seasonal-naive prediction onto every real test row,
    falling back to the flat baseline for a (station, day_type, hour) slot
    the naive table never saw in training (e.g. an hour with no training
    activity at that station), and to 0.0 only if the station itself is
    entirely unseen in training.
    """
    merged = test_frame.merge(naive, on=["station_id", "day_type", "hour"], how="left")
    merged = merged.merge(flat, on="station_id", how="left", suffixes=("", "_flat"))
    predicted = merged["predicted_net_per_day"].fillna(merged["predicted_net_per_day_flat"]).fillna(0.0)
    return predicted


# ---------------------------------------------------------------------------
# Tier 2: GAM (penalized splines + Ridge)
# ---------------------------------------------------------------------------

def build_gam_pipeline() -> Pipeline:
    """Ridge regression over a penalized-spline/one-hot feature expansion --
    a structured, interpretable middle tier between the naive lookup and
    the GBM. Ridge, not Poisson: the target is net flow, which can be
    negative (arrivals - departures), and Poisson regression requires a
    non-negative target -- that only becomes relevant if the deferred
    arrivals/departures split (see module docstring) is built later.
    """
    preprocess = ColumnTransformer(
        transformers=[
            ("temp_spline", SplineTransformer(n_knots=5, degree=3), ["temp_mean_c"]),
            ("hour_spline", SplineTransformer(n_knots=6, degree=3, extrapolation="periodic", knots="uniform"), ["hour"]),
            ("day_of_week", OneHotEncoder(handle_unknown="ignore"), ["day_of_week"]),
            ("typology", OneHotEncoder(handle_unknown="ignore"), ["typology_cluster"]),
            ("precip_bin", OneHotEncoder(handle_unknown="ignore"), ["precip_bin"]),
            ("passthrough", "passthrough", ["is_holiday", "doy_sin", "doy_cos"]),
        ]
    )
    return Pipeline([("features", preprocess), ("ridge", Ridge(alpha=1.0))])


def _add_precip_bin(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["precip_bin"] = pd.cut(
        frame["precip_mm"], bins=[-0.01, 0.1, 5, 20, np.inf], labels=["dry", "light", "moderate", "heavy"]
    ).astype(str)
    return frame


def train_gam(train_frame: pd.DataFrame) -> Pipeline:
    train_frame = _add_precip_bin(train_frame)
    pipeline = build_gam_pipeline()
    pipeline.fit(train_frame, train_frame["net"])
    return pipeline


def predict_gam(pipeline: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    return pipeline.predict(_add_precip_bin(frame))


# ---------------------------------------------------------------------------
# Tier 3: GBM (tuned, with an extrapolation guard)
# ---------------------------------------------------------------------------

def _apply_categories(frame: pd.DataFrame, categories: dict[str, list]) -> pd.DataFrame:
    frame = frame.copy()
    for col, cats in categories.items():
        frame[col] = pd.Categorical(frame[col], categories=cats)
    return frame


def _shared_categories(train_frame: pd.DataFrame) -> dict[str, list]:
    return {col: sorted(train_frame[col].astype(str).unique()) for col in GBM_CATEGORICAL_COLUMNS}


def _split_validation(train_frame: pd.DataFrame, validation_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carve a validation slice from the most recent training dates -- never
    the fold's true held-out month -- for GBM hyperparameter selection.
    """
    dates = np.sort(train_frame["date"].unique())
    cutoff = dates[int(len(dates) * (1 - validation_fraction))]
    return train_frame[train_frame["date"] < cutoff], train_frame[train_frame["date"] >= cutoff]


def train_gbm(train_frame: pd.DataFrame, param_grid: list[dict] = GBM_PARAM_GRID) -> tuple[HistGradientBoostingRegressor, dict, dict]:
    """Fit HistGradientBoostingRegressor, selecting hyperparameters by MAE on
    a validation slice of the training months -- the original Session 5
    model ran with defaults only (random_state=0, nothing tuned).
    """
    categories = _shared_categories(train_frame)
    fit_part, val_part = _split_validation(train_frame, VALIDATION_FRACTION)
    fit_part = _apply_categories(fit_part, categories)
    val_part = _apply_categories(val_part, categories)

    best_model, best_params, best_mae = None, None, np.inf
    for params in param_grid:
        model = HistGradientBoostingRegressor(categorical_features="from_dtype", random_state=0, **params)
        model.fit(fit_part[GBM_FEATURE_COLUMNS], fit_part["net"])
        val_pred = model.predict(val_part[GBM_FEATURE_COLUMNS])
        mae = float(np.abs(val_pred - val_part["net"]).mean())
        if mae < best_mae:
            best_model, best_params, best_mae = model, params, mae

    # Refit the winning configuration on the FULL training set (fit_part +
    # val_part), not just the validation-carved subset, so the deployed
    # model still sees every real training day.
    full_train = _apply_categories(train_frame, categories)
    final_model = HistGradientBoostingRegressor(categorical_features="from_dtype", random_state=0, **best_params)
    final_model.fit(full_train[GBM_FEATURE_COLUMNS], full_train["net"])

    temp_range = (float(train_frame["temp_mean_c"].min()), float(train_frame["temp_mean_c"].max()))
    return final_model, categories, {"params": best_params, "validation_mae": best_mae, "temp_range": temp_range}


def predict_gbm(model: HistGradientBoostingRegressor, categories: dict[str, list], frame: pd.DataFrame) -> np.ndarray:
    frame = _apply_categories(frame, categories)
    return model.predict(frame[GBM_FEATURE_COLUMNS])


def extrapolation_tier(temp_c: pd.Series, temp_range: tuple[float, float], margin_c: float = GAM_EXTRAPOLATION_MARGIN_C) -> pd.Series:
    """Which tier a row's own temperature should be predicted with:
    'gbm' inside the fold's training range, 'gam' within margin_c of it
    (splines degrade more gracefully than a clamped tree), 'naive' beyond
    that. Named, tunable, and returned per-row so it can be reported
    alongside every prediction -- not a silent override.
    """
    low, high = temp_range
    in_range = (temp_c >= low) & (temp_c <= high)
    near_range = (temp_c >= low - margin_c) & (temp_c <= high + margin_c)
    return pd.Series(np.where(in_range, "gbm", np.where(near_range, "gam", "naive")), index=temp_c.index)


def predict_with_guard(
    gbm_model, gbm_categories, gam_pipeline, naive, flat, test_frame: pd.DataFrame, temp_range: tuple[float, float]
) -> pd.DataFrame:
    """Predict every test row with the tier the extrapolation guard selects
    for its own temperature, rather than trusting GBM everywhere.
    """
    result = test_frame.copy()
    result["tier_used"] = extrapolation_tier(result["temp_mean_c"], temp_range)

    result["predicted_net"] = np.nan
    gbm_rows = result["tier_used"] == "gbm"
    gam_rows = result["tier_used"] == "gam"
    naive_rows = result["tier_used"] == "naive"

    if gbm_rows.any():
        result.loc[gbm_rows, "predicted_net"] = predict_gbm(gbm_model, gbm_categories, result[gbm_rows])
    if gam_rows.any():
        result.loc[gam_rows, "predicted_net"] = predict_gam(gam_pipeline, result[gam_rows])
    if naive_rows.any():
        result.loc[naive_rows, "predicted_net"] = predict_baseline(naive, flat, result[naive_rows]).values

    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(predicted, actual) -> dict:
    """MAE between predicted and actual, compared positionally (not by pandas
    index alignment): predict_baseline's merge-based broadcast resets its
    result's index, so comparing it against a still-originally-indexed
    actual column via pandas' automatic index alignment silently produces
    NaN for every misaligned row instead of raising -- converting both to
    plain arrays first removes that hazard regardless of which caller's
    Series index happens to differ.
    """
    predicted = np.asarray(predicted, dtype=float)
    actual = np.asarray(actual, dtype=float)
    abs_error = np.abs(predicted - actual)
    return {"mae": float(abs_error.mean()), "n": int(len(actual))}


def score_by_group(frame: pd.DataFrame, predicted_col: str, actual_col: str, group_col: str) -> dict[str, dict]:
    return {
        str(key): score(group[predicted_col], group[actual_col])
        for key, group in frame.groupby(group_col, observed=True)
    }


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------

def run_fold(all_features: pd.DataFrame, test_month: str, train_months: list[str]) -> dict:
    train_frame = all_features[all_features["month"].isin(train_months)]
    test_frame = all_features[all_features["month"] == test_month]

    assignments = refit_typology(train_frame)
    train_frame = add_typology(train_frame, assignments)
    test_frame = add_typology(test_frame, assignments)

    naive, flat = train_baselines(train_frame)
    gam_pipeline = train_gam(train_frame)
    gbm_model, gbm_categories, gbm_info = train_gbm(train_frame)

    predictions = predict_with_guard(
        gbm_model, gbm_categories, gam_pipeline, naive, flat, test_frame, gbm_info["temp_range"]
    )
    predictions["season"] = predictions["month"].map(lambda m: SEASON_OF[int(m.split("-")[1])])

    naive_only = predict_baseline(naive, flat, test_frame)
    gam_only = predict_gam(gam_pipeline, _add_precip_bin(test_frame))
    gbm_only = predict_gbm(gbm_model, gbm_categories, test_frame)

    return {
        "test_month": test_month,
        "gbm_temp_range": gbm_info["temp_range"],
        "gbm_params": gbm_info["params"],
        "n_typology_clusters": len({c for c, _ in assignments.values() if c not in (-1,)}),
        "tiers": {
            "naive": {
                "overall": score(naive_only, test_frame["net"]),
                "by_season": score_by_group(predictions.assign(pred=naive_only.values), "pred", "net", "season"),
            },
            "gam": {
                "overall": score(pd.Series(gam_only, index=test_frame.index), test_frame["net"]),
                "by_season": score_by_group(predictions.assign(pred=gam_only), "pred", "net", "season"),
            },
            "gbm": {
                "overall": score(pd.Series(gbm_only, index=test_frame.index), test_frame["net"]),
                "by_season": score_by_group(predictions.assign(pred=gbm_only), "pred", "net", "season"),
            },
            "guarded": {
                "overall": score(predictions["predicted_net"], predictions["net"]),
                "by_season": score_by_group(predictions, "predicted_net", "net", "season"),
                "by_typology": score_by_group(predictions, "predicted_net", "net", "typology_cluster"),
                "tier_usage": predictions["tier_used"].value_counts().to_dict(),
            },
        },
    }


def paired_significance(fold_maes_a: list[float], fold_maes_b: list[float]) -> dict:
    """Wilcoxon signed-rank test across paired fold-level MAEs -- turns
    "tier A's aggregate MAE was lower" into a quantified claim instead of
    an eyeballed single-number comparison.
    """
    if len(fold_maes_a) < 2 or all(a == b for a, b in zip(fold_maes_a, fold_maes_b)):
        return {"statistic": None, "p_value": None, "note": "not enough variation across folds to test"}
    result = stats.wilcoxon(fold_maes_a, fold_maes_b)
    return {"statistic": float(result.statistic), "p_value": float(result.pvalue)}


def run_walk_forward(months: list[str] = TARGET_MONTHS) -> dict:
    """12-fold leave-one-month-out walk-forward across the full year."""
    daily = load_daily_table()
    holidays = get_holidays()
    weather = fetch_daily_weather(WEATHER_START, WEATHER_END)
    all_features = build_feature_frame(daily, holidays, weather)

    folds = []
    for test_month in months:
        train_months = [m for m in months if m != test_month]
        print(f"[fold {test_month}] training on {len(train_months)} months...")
        folds.append(run_fold(all_features, test_month, train_months))
        print(f"[fold {test_month}] naive={folds[-1]['tiers']['naive']['overall']['mae']:.3f} "
              f"gam={folds[-1]['tiers']['gam']['overall']['mae']:.3f} "
              f"gbm={folds[-1]['tiers']['gbm']['overall']['mae']:.3f} "
              f"guarded={folds[-1]['tiers']['guarded']['overall']['mae']:.3f}")

    naive_maes = [f["tiers"]["naive"]["overall"]["mae"] for f in folds]
    gam_maes = [f["tiers"]["gam"]["overall"]["mae"] for f in folds]
    guarded_maes = [f["tiers"]["guarded"]["overall"]["mae"] for f in folds]

    result = {
        "months": months,
        "folds": folds,
        "aggregate": {
            "naive_mean_mae": float(np.mean(naive_maes)),
            "gam_mean_mae": float(np.mean(gam_maes)),
            "guarded_mean_mae": float(np.mean(guarded_maes)),
        },
        "significance": {
            "gam_vs_naive": paired_significance(gam_maes, naive_maes),
            "guarded_vs_naive": paired_significance(guarded_maes, naive_maes),
        },
    }

    MODEL_PERFORMANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PERFORMANCE_PATH.write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    result = run_walk_forward()
    agg = result["aggregate"]
    print(f"\nSeasonal-naive mean MAE: {agg['naive_mean_mae']:.3f}")
    print(f"GAM mean MAE:            {agg['gam_mean_mae']:.3f}")
    print(f"Guarded (GBM+GAM+naive): {agg['guarded_mean_mae']:.3f}")
    print(f"\nGAM vs naive:     {result['significance']['gam_vs_naive']}")
    print(f"Guarded vs naive: {result['significance']['guarded_vs_naive']}")
    print(f"\nwrote {MODEL_PERFORMANCE_PATH}")

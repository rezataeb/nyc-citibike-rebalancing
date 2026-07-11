"""Gradient-boosted forecast: same (station, month, day_type, hour) grain as
flows.py / backtest.py, with weather, holiday, and calendar features added.

Deliberately excludes day-to-day/week-to-week lag features: those need an
actual prior day's data, which does not exist in a leak-free way for the
first days of a never-seen test month, and letting the model use its own
test month's early days as lag inputs would hand it information the
static seasonal-naive/flat baselines structurally cannot use -- exactly
the "saw more data, not a better model" confound this backtest is trying
to avoid (see PROGRESS.md Session 5). Weather and calendar position are
used instead: both are computable for any date, train or test, with no
leakage risk.

Fixed 2026 holidays covered by this project's train/test months (Feb,
Apr, Jun 2026): Presidents Day (Feb 16) and Juneteenth (Jun 19). No
federal holiday falls in April 2026.

Station identity is represented by lat/lng, not a station_id categorical:
sklearn's HistGradientBoostingRegressor caps native categorical cardinality
at 255, well under this project's ~2,300+ stations, and an arbitrary
integer encoding of station_id would have no meaningful order for a tree
to split on anyway. lat/lng lets the model learn spatial patterns (e.g.
Midtown-office vs residential behavior) instead of memorizing IDs -- and
unlike a bare ID, it degrades gracefully for a station outside training.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

HOLIDAYS = {date(2026, 2, 16), date(2026, 6, 19)}
FEATURE_COLUMNS = [
    "lat", "lng", "hour", "day_type",
    "temp_mean_c", "precip_mm", "holiday_fraction", "doy_sin", "doy_cos",
]
CATEGORICAL_COLUMNS = ["day_type"]


def _month_calendar(year_month: str) -> pd.DataFrame:
    """Every calendar date in a 'YYYY-MM' month, with day_type and is_holiday."""
    year, month = (int(part) for part in year_month.split("-"))
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(1)
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "month": year_month,
            "day_type": ["weekend" if d.dayofweek >= 5 else "weekday" for d in dates],
            "is_holiday": [d.date() in HOLIDAYS for d in dates],
        }
    )


def _calendar_features(months: list[str], weather: pd.DataFrame) -> pd.DataFrame:
    """Per (month, day_type): mean temp/precip, holiday fraction, mean day-of-year."""
    calendar = pd.concat([_month_calendar(m) for m in months], ignore_index=True)
    merged = calendar.merge(weather, on="date", how="left")
    features = (
        merged.groupby(["month", "day_type"])
        .agg(
            temp_mean_c=("temp_mean_c", "mean"),
            precip_mm=("precip_mm", "mean"),
            holiday_fraction=("is_holiday", "mean"),
            day_of_year=("date", lambda d: d.dt.dayofyear.mean()),
        )
        .reset_index()
    )
    features["doy_sin"] = np.sin(2 * np.pi * features["day_of_year"] / 365.25)
    features["doy_cos"] = np.cos(2 * np.pi * features["day_of_year"] / 365.25)
    return features.drop(columns="day_of_year")


def add_features(flows: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Merge weather/holiday/calendar-position features onto a flows table."""
    months = sorted(flows["month"].unique())
    calendar_features = _calendar_features(months, weather)
    return flows.merge(calendar_features, on=["month", "day_type"], how="left")


def _apply_shared_categories(df: pd.DataFrame, categories: dict[str, list]) -> pd.DataFrame:
    """Set categorical dtypes using a shared category list across train and test.

    Without this, a train-fitted category set and a test set built
    independently could assign the same category different internal codes,
    corrupting predictions silently.
    """
    df = df.copy()
    for col, cats in categories.items():
        df[col] = pd.Categorical(df[col], categories=cats)
    return df


def train_gbm(train_flows: pd.DataFrame, categories: dict[str, list]) -> HistGradientBoostingRegressor:
    """Fit HistGradientBoostingRegressor on train_flows, weighted by n_days per row."""
    train_flows = _apply_shared_categories(train_flows, categories)
    model = HistGradientBoostingRegressor(categorical_features="from_dtype", random_state=0)
    model.fit(
        train_flows[FEATURE_COLUMNS],
        train_flows["net_per_day"],
        sample_weight=train_flows["n_days"],
    )
    return model


def predict_gbm(
    model: HistGradientBoostingRegressor, flows: pd.DataFrame, categories: dict[str, list]
) -> pd.DataFrame:
    """Predict net_per_day for each (station_id, day_type, hour) row in flows.

    Carries n_days through so compute_mae can correctly weight-collapse
    predictions if flows itself spans more than one month label for the
    same (station_id, day_type, hour) -- see backtest.py's _collapse.
    """
    flows = _apply_shared_categories(flows, categories)
    predicted = flows[["station_id", "day_type", "hour", "n_days"]].copy()
    predicted["predicted_net_per_day"] = model.predict(flows[FEATURE_COLUMNS])
    return predicted


if __name__ == "__main__":
    from pipeline.backtest import build_flat_baseline, build_naive_forecast, compute_mae
    from pipeline.download import download_month, load_trips
    from pipeline.flows import compute_net_flow
    from pipeline.qc import run_qc
    from pipeline.weather import fetch_daily_weather

    TRAIN_MONTHS = ["2026-02", "2026-04"]
    TEST_MONTH = "2026-06"

    def _load_flows(year_month: str) -> pd.DataFrame:
        zip_path = download_month(year_month)
        trips = load_trips(zip_path)
        clean, report = run_qc(trips)
        print(f"[{year_month}] {report.summary()}\n")
        return compute_net_flow(clean)

    train_flows = pd.concat([_load_flows(m) for m in TRAIN_MONTHS], ignore_index=True)
    test_flows = _load_flows(TEST_MONTH)

    weather = fetch_daily_weather("2026-02-01", "2026-06-30")
    train_features = add_features(train_flows, weather)
    test_features = add_features(test_flows, weather)

    categories = {"day_type": ["weekday", "weekend"]}

    model = train_gbm(train_features, categories)
    gbm_forecast = predict_gbm(model, test_features, categories)

    seasonal_forecast = build_naive_forecast(train_flows)
    flat_forecast = build_flat_baseline(train_flows)

    gbm_result = compute_mae(gbm_forecast, test_flows, ["station_id", "day_type", "hour"])
    seasonal_result = compute_mae(seasonal_forecast, test_flows, ["station_id", "day_type", "hour"])
    flat_result = compute_mae(flat_forecast, test_flows, ["station_id"])

    print(f"Train: {TRAIN_MONTHS}  Test (held out): {TEST_MONTH}\n")
    for name, result in [("GBM (weather+holiday+calendar)", gbm_result),
                          ("Seasonal-naive", seasonal_result),
                          ("Flat baseline", flat_result)]:
        print(f"{name:35s} MAE: {result['mae']:.3f} bikes/day  "
              f"(coverage {result['n_predictions']:,}/{result['n_actual_rows']:,} "
              f"= {result['coverage']:.1%})")

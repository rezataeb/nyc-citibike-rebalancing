"""Backtest the seasonal-naive forecast: train on one month, evaluate on another.

The seasonal-naive forecast IS the weekday/weekend curve pipeline/flows.py
already builds: "this (station, day-type, hour) will look like its
historical average." This module turns that curve into a lookup table,
predicts a held-out month's net flow with it, and scores the prediction
with MAE (mean absolute error, in bikes/day -- same units as net flow).

Also builds a dumber "flat" baseline -- one predicted value per station,
ignoring hour-of-day entirely -- so the seasonal-naive MAE has something
to beat. If the seasonal curve does not clearly outperform the flat
baseline, the extra granularity (24 numbers per station instead of 1)
is not earning its keep.

A fair MAE requires a genuinely held-out month: evaluating a curve
against the same month it was built from is circular (see CLAUDE.md /
PROGRESS.md Session 4 notes on why April->May was rejected as a test --
same season, does not stress the seasonal design -- in favor of
April->February, a real cross-season test).
"""

from __future__ import annotations

import pandas as pd


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


FLOWS_GRAIN = ["station_id", "day_type", "hour"]


def _collapse_to_grain(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Collapse df to one row per (station_id, day_type, hour) -- or whichever
    of those columns df actually has.

    Fixes accidental duplicate rows from a table spanning more than one
    `month` label for the same slot (see flows.py's midnight-crossing
    note: a held-out month's early/late trips can spill into the adjacent
    calendar month), without discarding real hour/day_type granularity or
    forcing columns a table never had. This matters because it must NOT
    collapse to join_cols -- the flat baseline's join_cols is ["station_id"]
    only, by design, so it can broadcast one prediction across every
    hour's actual value; collapsing actual_flows down to join_cols there
    would average away the exact variation the flat baseline is being
    scored against.

    Weighted by n_days if present (needed when duplicates come from a
    genuine month split); a plain mean otherwise, which is a no-op for
    build_naive_forecast/build_flat_baseline's output (already unique).
    """
    grain = [col for col in FLOWS_GRAIN if col in df.columns]
    if "n_days" in df.columns:
        return (
            df.groupby(grain, observed=True)
            .apply(_weighted_average, value_col=value_col, include_groups=False)
            .rename(value_col)
            .reset_index()
        )
    return df.groupby(grain, observed=True)[value_col].mean().reset_index()


def compute_mae(predicted: pd.DataFrame, actual_flows: pd.DataFrame, join_cols: list[str]) -> dict:
    """Join a forecast onto held-out actuals and score it with MAE.

    join_cols controls forecast granularity: ["station_id","day_type","hour"]
    for the seasonal-naive forecast (one prediction per slot), ["station_id"]
    for the flat baseline (one prediction broadcast across every slot).

    Both sides are collapsed to the natural flows grain first (see
    _collapse_to_grain) so evaluation is never accidentally split by a
    month label, while still leaving the flat baseline's coarser join
    free to broadcast across every hour.

    Uses an inner join, so stations/slots present in the test month but
    absent from training (e.g. a station that did not exist in April)
    are dropped, not silently predicted as zero -- coverage reports how
    much of the actual data that affected.
    """
    actual = _collapse_to_grain(actual_flows, "net_per_day")
    pred = _collapse_to_grain(predicted, "predicted_net_per_day")
    merged = actual.merge(pred, on=join_cols, how="inner")
    merged["abs_error"] = (merged["net_per_day"] - merged["predicted_net_per_day"]).abs()
    return {
        "mae": float(merged["abs_error"].mean()),
        "n_predictions": len(merged),
        "n_actual_rows": len(actual),
        "coverage": len(merged) / len(actual),
    }


if __name__ == "__main__":
    from pipeline.download import download_month, load_trips
    from pipeline.flows import compute_net_flow
    from pipeline.qc import run_qc

    TRAIN_MONTH = "2026-04"
    TEST_MONTH = "2026-02"

    def _load_flows(year_month: str) -> pd.DataFrame:
        zip_path = download_month(year_month)
        trips = load_trips(zip_path)
        clean, report = run_qc(trips)
        print(f"[{year_month}] {report.summary()}\n")
        return compute_net_flow(clean)

    train_flows = _load_flows(TRAIN_MONTH)
    test_flows = _load_flows(TEST_MONTH)

    seasonal_forecast = build_naive_forecast(train_flows)
    flat_forecast = build_flat_baseline(train_flows)

    seasonal_result = compute_mae(seasonal_forecast, test_flows, ["station_id", "day_type", "hour"])
    flat_result = compute_mae(flat_forecast, test_flows, ["station_id"])

    print(f"Train: {TRAIN_MONTH}  Test (held out): {TEST_MONTH}\n")
    print(f"Seasonal-naive MAE: {seasonal_result['mae']:.3f} bikes/day")
    print(
        f"  coverage: {seasonal_result['n_predictions']:,} / "
        f"{seasonal_result['n_actual_rows']:,} ({seasonal_result['coverage']:.1%})"
    )
    print(f"\nFlat-baseline MAE:  {flat_result['mae']:.3f} bikes/day")
    print(
        f"  coverage: {flat_result['n_predictions']:,} / "
        f"{flat_result['n_actual_rows']:,} ({flat_result['coverage']:.1%})"
    )

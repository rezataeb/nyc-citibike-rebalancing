"""Tests for pipeline/backtest.py."""

import pandas as pd

from pipeline.backtest import build_flat_baseline, build_naive_forecast, compute_mae


def make_flows_row(station_id, day_type, hour, net_per_day, n_days=20) -> dict:
    return {
        "station_id": station_id,
        "day_type": day_type,
        "hour": hour,
        "net_per_day": net_per_day,
        "n_days": n_days,
    }


def test_build_naive_forecast_weights_months_by_n_days():
    # station "1", weekday, hour 8: two contributing months with different weights.
    train_flows = pd.DataFrame(
        [
            make_flows_row("1", "weekday", 8, net_per_day=10.0, n_days=10),
            make_flows_row("1", "weekday", 8, net_per_day=20.0, n_days=30),
        ]
    )
    forecast = build_naive_forecast(train_flows)

    row = forecast[(forecast["station_id"] == "1") & (forecast["hour"] == 8)]
    # weighted average: (10*10 + 20*30) / (10+30) = 17.5
    assert row["predicted_net_per_day"].iloc[0] == 17.5


def test_build_flat_baseline_ignores_hour():
    train_flows = pd.DataFrame(
        [
            make_flows_row("1", "weekday", 8, net_per_day=10.0, n_days=20),
            make_flows_row("1", "weekday", 18, net_per_day=-10.0, n_days=20),
        ]
    )
    baseline = build_flat_baseline(train_flows)

    row = baseline[baseline["station_id"] == "1"]
    assert len(row) == 1  # one prediction per station, no hour dimension
    assert row["predicted_net_per_day"].iloc[0] == 0.0  # average of +10 and -10


def test_compute_mae_matches_hand_calculation():
    predicted = pd.DataFrame(
        [
            {"station_id": "1", "day_type": "weekday", "hour": 8, "predicted_net_per_day": 10.0},
            {"station_id": "1", "day_type": "weekday", "hour": 18, "predicted_net_per_day": -10.0},
        ]
    )
    actual = pd.DataFrame(
        [
            make_flows_row("1", "weekday", 8, net_per_day=12.0),  # error = 2
            make_flows_row("1", "weekday", 18, net_per_day=-16.0),  # error = 6
        ]
    )

    result = compute_mae(predicted, actual, ["station_id", "day_type", "hour"])

    assert result["mae"] == 4.0  # (2 + 6) / 2
    assert result["n_predictions"] == 2
    assert result["n_actual_rows"] == 2
    assert result["coverage"] == 1.0


def test_compute_mae_reports_coverage_when_test_has_unseen_station():
    predicted = pd.DataFrame(
        [{"station_id": "1", "day_type": "weekday", "hour": 8, "predicted_net_per_day": 10.0}]
    )
    actual = pd.DataFrame(
        [
            make_flows_row("1", "weekday", 8, net_per_day=10.0),  # error = 0, matches
            make_flows_row("2", "weekday", 8, net_per_day=99.0),  # station "2" unseen in training
        ]
    )

    result = compute_mae(predicted, actual, ["station_id", "day_type", "hour"])

    assert result["n_predictions"] == 1  # unseen station dropped by inner join, not predicted as 0
    assert result["n_actual_rows"] == 2
    assert result["coverage"] == 0.5
    assert result["mae"] == 0.0

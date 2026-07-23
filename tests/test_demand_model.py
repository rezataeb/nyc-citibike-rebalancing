"""Tests for pipeline/demand_model.py.

Focused on the non-obvious behaviors: real per-date calendar features, the
extrapolation guard's tier routing, per-fold typology never leaking a
held-out month's data back into its own feature, the baseline-forecast
functions (moved here from pipeline/backtest.py in Session 33, see
demand_model.py's own comment on why), and a small end-to-end walk-forward
smoke test wiring everything together on a tiny synthetic dataset (not the
real 11.7M-row table -- that's exercised for real via
pipeline/demand_model.py's own __main__, not in the unit suite).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.demand_model import (
    UNKNOWN_TYPOLOGY_CLUSTER,
    add_calendar_features,
    add_typology,
    build_feature_frame,
    build_flat_baseline,
    build_naive_forecast,
    daily_to_flow_rows,
    extrapolation_tier,
    join_weather,
    paired_significance,
    predict_baseline,
    predict_gam,
    predict_gbm,
    refit_typology,
    run_walk_forward,
    score,
    station_coords_from_daily,
    train_baselines,
    train_gam,
    train_gbm,
    weekday_curve_dict,
)


def _daily_row(station_id, date, hour, net, lat=40.0, lng=-74.0):
    return {"station_id": station_id, "station_name": f"St {station_id}", "date": pd.Timestamp(date),
            "hour": hour, "lat": lat, "lng": lng, "arrivals_count": max(net, 0),
            "departures_count": max(-net, 0), "net": net}


def test_add_calendar_features_marks_real_holiday():
    daily = pd.DataFrame([_daily_row("1", "2025-12-25", 8, 2), _daily_row("1", "2025-12-26", 8, 2)])
    holidays = {pd.Timestamp("2025-12-25").date()}
    result = add_calendar_features(daily, holidays)

    assert result.loc[result["date"] == pd.Timestamp("2025-12-25"), "is_holiday"].iloc[0] == 1
    assert result.loc[result["date"] == pd.Timestamp("2025-12-26"), "is_holiday"].iloc[0] == 0


def test_add_calendar_features_day_of_week_is_real_not_binary():
    # 2025-12-25 is a Thursday, 2025-12-27 is a Saturday.
    daily = pd.DataFrame([_daily_row("1", "2025-12-25", 8, 1), _daily_row("1", "2025-12-27", 8, 1)])
    result = add_calendar_features(daily, holidays=set())

    assert set(result["day_of_week"]) == {"Thursday", "Saturday"}
    assert set(result["day_type"]) == {"weekday", "weekend"}


def test_weekday_curve_dict_excludes_weekend_rows():
    # Thursday (weekday) and Saturday (weekend) rows for the same station/hour;
    # only the weekday value should feed the curve.
    daily = pd.DataFrame([_daily_row("1", "2025-12-25", 8, 4), _daily_row("1", "2025-12-27", 8, -100)])
    daily = add_calendar_features(daily, holidays=set())
    curves = weekday_curve_dict(daily)

    assert curves["1"]["weekday"][8] == 4.0
    assert len(curves["1"]["weekday"]) == 24


def test_weekday_curve_dict_fills_missing_hours_with_zero_not_nan():
    # Station 1 only has activity at hour 8 -- every other hour has zero
    # training rows, not just a value of zero. Real bug (found against the
    # actual full-year data, not caught by earlier all-hours-active
    # fixtures): unstack() leaves those NaN, and reindex's fill_value only
    # covers hour columns missing entirely, not NaN cells within columns
    # that do exist for other stations.
    daily = pd.DataFrame([_daily_row("1", "2025-12-01", 8, 4), _daily_row("2", "2025-12-01", 3, -1)])
    daily = add_calendar_features(daily, holidays=set())
    curves = weekday_curve_dict(daily)

    assert not any(np.isnan(v) for v in curves["1"]["weekday"])
    assert curves["1"]["weekday"][8] == 4.0
    assert curves["1"]["weekday"][3] == 0.0  # station 1 had no rows at hour 3 -- must be 0.0, not NaN


def test_refit_typology_only_uses_provided_rows():
    # Three stations with clearly distinct AM/PM shapes -- enough for k=2
    # to be a valid candidate (K_RANGE requires len(vectors) > k).
    rows = []
    for day in ("2025-12-01", "2025-12-08", "2025-12-15"):  # three Mondays
        rows += [
            _daily_row("commuter_a", day, 8, -5), _daily_row("commuter_a", day, 18, 5),
            _daily_row("commuter_b", day, 8, -4), _daily_row("commuter_b", day, 18, 4),
            _daily_row("residential_a", day, 8, 5), _daily_row("residential_a", day, 18, -5),
        ]
    daily = add_calendar_features(pd.DataFrame(rows), holidays=set())
    assignments = refit_typology(daily)

    assert set(assignments.keys()) == {"commuter_a", "commuter_b", "residential_a"}
    # The two AM-draining stations must land in the same cluster, distinct from the AM-filling one.
    assert assignments["commuter_a"][0] == assignments["commuter_b"][0]
    assert assignments["commuter_a"][0] != assignments["residential_a"][0]


def test_add_typology_flags_station_unseen_in_training():
    frame = pd.DataFrame({"station_id": ["1", "2"]})
    assignments = {"1": (0, "Commuter core (fills AM, drains PM)")}
    result = add_typology(frame, assignments)

    assert result.loc[result["station_id"] == "1", "typology_cluster"].iloc[0] == "0"
    assert result.loc[result["station_id"] == "2", "typology_cluster"].iloc[0] == str(UNKNOWN_TYPOLOGY_CLUSTER)


def test_station_coords_from_daily_reads_lat_lng_already_in_the_table():
    daily = pd.DataFrame(
        [_daily_row("1", "2025-12-25", 8, 3, lat=40.70, lng=-74.00), _daily_row("1", "2025-12-26", 9, 1, lat=40.70, lng=-74.00)]
    )
    coords = station_coords_from_daily(daily)
    assert coords["1"]["lat"] == 40.70
    assert coords["1"]["lng"] == -74.00


def test_join_weather_uses_each_stations_own_zone_not_a_uniform_value():
    daily = pd.DataFrame(
        [
            _daily_row("north", "2025-07-01", 8, 5, lat=40.85, lng=-73.90),
            _daily_row("south", "2025-07-01", 8, 3, lat=40.65, lng=-74.00),
        ]
    )
    daily = add_calendar_features(daily, holidays=set())
    zone_by_station = {"north": 0, "south": 1}
    weather_by_zone = [
        pd.DataFrame({"date": [pd.Timestamp("2025-07-01")], "temp_mean_c": [30.0], "precip_mm": [0.0]}),
        pd.DataFrame({"date": [pd.Timestamp("2025-07-01")], "temp_mean_c": [20.0], "precip_mm": [5.0]}),
    ]

    result = join_weather(daily, weather_by_zone, zone_by_station).set_index("station_id")

    assert result.loc["north", "temp_mean_c"] == 30.0
    assert result.loc["south", "temp_mean_c"] == 20.0


def test_build_feature_frame_drops_stations_with_no_zone_assignment():
    # A station absent from zone_by_station (e.g. never seen when zones
    # were computed) must be dropped, not fabricated a zone.
    daily = pd.DataFrame([_daily_row("known", "2025-07-01", 8, 5), _daily_row("unknown", "2025-07-01", 8, 3)])
    zone_by_station = {"known": 0}
    weather_by_zone = [pd.DataFrame({"date": [pd.Timestamp("2025-07-01")], "temp_mean_c": [25.0], "precip_mm": [0.0]})]

    result = build_feature_frame(daily, holidays=set(), weather_by_zone=weather_by_zone, zone_by_station=zone_by_station)

    assert set(result["station_id"]) == {"known"}


def test_daily_to_flow_rows_sets_n_days_to_one():
    daily = add_calendar_features(pd.DataFrame([_daily_row("1", "2025-12-25", 8, 3)]), holidays=set())
    flow_rows = daily_to_flow_rows(daily)

    assert flow_rows["n_days"].iloc[0] == 1
    assert flow_rows["net_per_day"].iloc[0] == 3


# Moved from tests/test_backtest.py (Session 33) alongside
# build_naive_forecast/build_flat_baseline themselves -- see
# demand_model.py's comment on why those two functions moved here.

def _flow_row(station_id, day_type, hour, net_per_day, n_days=20) -> dict:
    return {
        "station_id": station_id, "day_type": day_type, "hour": hour,
        "net_per_day": net_per_day, "n_days": n_days,
    }


def test_build_naive_forecast_weights_months_by_n_days():
    # station "1", weekday, hour 8: two contributing months with different weights.
    train_flows = pd.DataFrame(
        [
            _flow_row("1", "weekday", 8, net_per_day=10.0, n_days=10),
            _flow_row("1", "weekday", 8, net_per_day=20.0, n_days=30),
        ]
    )
    forecast = build_naive_forecast(train_flows)

    row = forecast[(forecast["station_id"] == "1") & (forecast["hour"] == 8)]
    # weighted average: (10*10 + 20*30) / (10+30) = 17.5
    assert row["predicted_net_per_day"].iloc[0] == 17.5


def test_build_flat_baseline_ignores_hour():
    train_flows = pd.DataFrame(
        [
            _flow_row("1", "weekday", 8, net_per_day=10.0, n_days=20),
            _flow_row("1", "weekday", 18, net_per_day=-10.0, n_days=20),
        ]
    )
    baseline = build_flat_baseline(train_flows)

    row = baseline[baseline["station_id"] == "1"]
    assert len(row) == 1  # one prediction per station, no hour dimension
    assert row["predicted_net_per_day"].iloc[0] == 0.0  # average of +10 and -10


def test_train_baselines_and_predict_baseline_broadcasts_naive_over_real_dates():
    rows = []
    for day in ("2025-12-01", "2025-12-08"):  # two Mondays, same (station, day_type, hour) slot
        rows.append(_daily_row("1", day, 8, 4))
    train = add_calendar_features(pd.DataFrame(rows), holidays=set())
    naive, flat = train_baselines(train)

    test_rows = add_calendar_features(pd.DataFrame([_daily_row("1", "2025-12-15", 8, 999)]), holidays=set())
    predicted = predict_baseline(naive, flat, test_rows)

    assert predicted.iloc[0] == 4.0  # broadcast from training average, ignoring the test row's own (irrelevant) net


def test_predict_baseline_falls_back_to_flat_when_naive_slot_unseen():
    train = add_calendar_features(pd.DataFrame([_daily_row("1", "2025-12-01", 8, 4)]), holidays=set())
    naive, flat = train_baselines(train)

    # Test row asks for hour 9, never seen in training at station 1 -- naive has no slot for it.
    test_rows = add_calendar_features(pd.DataFrame([_daily_row("1", "2025-12-15", 9, 999)]), holidays=set())
    predicted = predict_baseline(naive, flat, test_rows)

    assert predicted.iloc[0] == 4.0  # falls back to the flat (station-only) baseline, not 0 or NaN


def test_extrapolation_tier_routes_by_temperature():
    temps = pd.Series([10.0, 15.0, 18.0, 40.0], index=[0, 1, 2, 3])
    tiers = extrapolation_tier(temps, temp_range=(10.0, 15.0), margin_c=5.0)

    assert tiers[0] == "gbm"    # at the low edge, in range
    assert tiers[1] == "gbm"    # at the high edge, in range
    assert tiers[2] == "gam"    # 18 is within margin_c=5 of the range's high edge (15+5=20)
    assert tiers[3] == "naive"  # 40 is far beyond even the margin


def test_extrapolation_tier_near_margin_boundary():
    temps = pd.Series([20.0, 21.0], index=[0, 1])
    tiers = extrapolation_tier(temps, temp_range=(10.0, 15.0), margin_c=5.0)

    assert tiers[0] == "gam"    # exactly at the margin (15 + 5 = 20) -- inclusive
    assert tiers[1] == "naive"  # just past it


def test_paired_significance_identical_folds_reports_no_variation():
    result = paired_significance([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert result["p_value"] is None


def test_paired_significance_returns_a_p_value_for_real_differences():
    fold_a = [0.5, 0.6, 0.4, 0.55, 0.45, 0.52]
    fold_b = [1.0, 1.1, 0.9, 1.05, 0.95, 1.02]
    result = paired_significance(fold_a, fold_b)

    assert result["p_value"] is not None
    assert 0.0 <= result["p_value"] <= 1.0


def _synthetic_month(year_month: str, stations: list[str]) -> pd.DataFrame:
    """A few real weekdays of one month, with a genuine AM/PM commute split
    across two station groups, and mild temperature variation baked into
    which days are used (December colder than June in this fixture).
    """
    rows = []
    year, month = year_month.split("-")
    for day in (2, 9, 16):  # three Tuesdays-ish, close enough for a smoke test
        for i, station in enumerate(stations):
            date = f"{year}-{month}-{day:02d}"
            if i % 2 == 0:
                rows += [_daily_row(station, date, 8, -3), _daily_row(station, date, 18, 3)]
            else:
                rows += [_daily_row(station, date, 8, 3), _daily_row(station, date, 18, -3)]
    return pd.DataFrame(rows)


def test_run_walk_forward_end_to_end_smoke(monkeypatch, tmp_path):
    months = ["2025-12", "2026-01", "2026-02"]
    stations = ["1", "2", "3", "4"]
    daily = pd.concat([_synthetic_month(m, stations) for m in months], ignore_index=True)

    weather_rows = []
    for m, base_temp in zip(months, [2.0, -1.0, 4.0]):
        for day in (2, 9, 16):
            weather_rows.append({"date": pd.Timestamp(f"{m}-{day:02d}"), "temp_mean_c": base_temp, "precip_mm": 0.0})
    weather = pd.DataFrame(weather_rows)

    # All synthetic stations here share the same (lat, lng) (see _daily_row's
    # defaults) -- compute_weather_zones/fetch_weather_at_points are mocked
    # to a single trivial zone rather than exercising real k-means on
    # degenerate identical-coordinate data, which has its own dedicated
    # tests in test_weather.py.
    monkeypatch.setattr("pipeline.demand_model.load_daily_table", lambda path=None: daily)
    monkeypatch.setattr(
        "pipeline.demand_model.compute_weather_zones",
        lambda stations, cell_km=6.0: ({sid: 0 for sid in stations}, [(40.0, -74.0)]),
    )
    monkeypatch.setattr("pipeline.demand_model.fetch_weather_at_points", lambda points, start, end: [weather])
    monkeypatch.setattr("pipeline.demand_model.MODEL_PERFORMANCE_PATH", tmp_path / "model_performance.json")

    result = run_walk_forward(months=months)

    assert result["months"] == months
    assert len(result["folds"]) == 3
    for fold in result["folds"]:
        for tier in ("naive", "gam", "gbm", "guarded"):
            assert fold["tiers"][tier]["overall"]["mae"] >= 0
    assert "naive_mean_mae" in result["aggregate"]
    assert "gam_vs_naive" in result["significance"]
    assert (tmp_path / "model_performance.json").exists()

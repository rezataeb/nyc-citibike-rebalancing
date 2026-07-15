"""Tests for pipeline/elasticities.py."""

import numpy as np
import pandas as pd
import pytest

from pipeline.gbm import DEFAULT_CATEGORIES, train_gbm
from pipeline.elasticities import (
    build_elasticities,
    capacity_local_slope,
    ceiling_effect_note,
    clean_feature_rows,
    fit_capacity_curve,
    pdp_slope,
    station_magnitude,
)


def test_station_magnitude_is_mean_absolute_value():
    assert station_magnitude([1, -2, 3, -4] + [0] * 20) == pytest.approx((1 + 2 + 3 + 4) / 24)


def test_clean_feature_rows_drops_nan_numeric_columns_only():
    df = pd.DataFrame(
        {
            "lat": [40.7, 40.7, 40.7],
            "lng": [-74.0, -74.0, -74.0],
            "hour": [8, 9, 10],
            "day_type": ["weekday", "weekday", "weekday"],
            "temp_mean_c": [10.0, float("nan"), 12.0],
            "precip_mm": [1.0, 2.0, float("nan")],
            "holiday_fraction": [0.0, 0.0, 0.0],
            "doy_sin": [0.1, 0.1, 0.1],
            "doy_cos": [0.9, 0.9, 0.9],
        }
    )
    cleaned = clean_feature_rows(df)
    assert len(cleaned) == 1
    assert cleaned["hour"].iloc[0] == 8


def _make_model_and_features():
    """A small real HistGradientBoostingRegressor fit on synthetic data with
    gbm.py's exact feature schema, so pdp_slope's real sklearn call path
    (categorical dtype handling, method='brute') is genuinely exercised,
    not mocked.
    """
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame(
        {
            "station_id": rng.choice(["A", "B", "C"], n),
            "lat": rng.uniform(40.7, 40.8, n),
            "lng": rng.uniform(-74.0, -73.9, n),
            "hour": rng.integers(0, 24, n),
            "day_type": rng.choice(["weekday", "weekend"], n),
            "temp_mean_c": rng.uniform(-5, 25, n),
            "precip_mm": rng.uniform(0, 10, n),
            "holiday_fraction": rng.uniform(0, 1, n),
            "doy_sin": rng.uniform(-1, 1, n),
            "doy_cos": rng.uniform(-1, 1, n),
            "n_days": rng.integers(1, 10, n),
        }
    )
    df["net_per_day"] = 0.5 * df["temp_mean_c"] - 0.3 * df["precip_mm"] + rng.normal(0, 1, n)
    model = train_gbm(df, DEFAULT_CATEGORIES)
    return model, df


def test_pdp_slope_recovers_known_direction_on_synthetic_data():
    model, df = _make_model_and_features()
    temp_slope = pdp_slope(model, df, "temp_mean_c", DEFAULT_CATEGORIES)
    precip_slope = pdp_slope(model, df, "precip_mm", DEFAULT_CATEGORIES)
    assert temp_slope > 0  # true coefficient +0.5
    assert precip_slope < 0  # true coefficient -0.3


def test_pdp_slope_returns_none_with_fewer_than_two_rows():
    model, df = _make_model_and_features()
    assert pdp_slope(model, df.iloc[:1], "temp_mean_c", DEFAULT_CATEGORIES) is None


def test_pdp_slope_drops_nan_rows_instead_of_crashing():
    # Real bug found against real data (PROGRESS.md Session 21): a handful
    # of midnight-crossing-spillover rows carry NaN temp_mean_c/precip_mm,
    # which used to crash np.polyfit with a LinAlgError.
    model, df = _make_model_and_features()
    df = df.copy()
    df.loc[df.index[0], "temp_mean_c"] = float("nan")
    df.loc[df.index[1], "precip_mm"] = float("nan")
    slope = pdp_slope(model, df, "temp_mean_c", DEFAULT_CATEGORIES)
    assert slope is not None


def test_fit_capacity_curve_recovers_known_quadratic_and_throughput_term():
    rng = np.random.default_rng(1)
    n = 200
    throughput = rng.uniform(50, 500, n)
    capacity = np.clip(0.1 * throughput + rng.normal(0, 5, n), 5, 150)
    magnitude = 0.5 * throughput / 100 + 0.3 * capacity - 0.004 * capacity**2 + rng.normal(0, 0.3, n)

    curve = fit_capacity_curve(capacity, magnitude, throughput)
    assert curve is not None
    b0, b1, b2, b3 = curve
    assert b1 == pytest.approx(0.3, abs=0.05)
    assert b2 == pytest.approx(-0.004, abs=0.002)


def test_fit_capacity_curve_shows_diminishing_effect():
    rng = np.random.default_rng(1)
    n = 200
    throughput = rng.uniform(50, 500, n)
    capacity = np.clip(0.1 * throughput + rng.normal(0, 5, n), 5, 150)
    magnitude = 0.3 * capacity - 0.004 * capacity**2 + rng.normal(0, 0.3, n)

    curve = fit_capacity_curve(capacity, magnitude, throughput)
    b0, b1, b2, b3 = curve
    low_slope = capacity_local_slope(b1, b2, 20)
    high_slope = capacity_local_slope(b1, b2, 130)
    assert low_slope > high_slope  # the effect must shrink (or reverse) at high capacity, not stay flat


def test_fit_capacity_curve_returns_none_with_too_few_distinct_capacities():
    capacities = np.array([10.0] * 3 + [20.0] * 3)  # only 2 distinct values
    magnitudes = np.array([1.0, 1.1, 0.9, 2.0, 2.1, 1.9])
    throughput = np.array([100.0] * 6)
    assert fit_capacity_curve(capacities, magnitudes, throughput) is None


def test_ceiling_effect_note_calibrates_confidence_to_evidence():
    strong = ceiling_effect_note("commuter_core", -0.64, 730)
    weak = ceiling_effect_note("residential_feeder", 0.0, 1235)
    assert "clean, consistent" in strong
    assert "weaker, less consistent" in weak
    assert "commuter_core" in strong
    assert "residential_feeder" in weak


def test_ceiling_effect_note_handles_too_few_points():
    note = ceiling_effect_note("commuter_core", None, 1)
    assert "too few" in note


# ---- build_elasticities end-to-end -------------------------------------------


def _flows_payload():
    def _station(name, lat, lng, cluster, cluster_name, weekday):
        return {
            "name": name, "lat": lat, "lng": lng,
            "weekday": weekday, "weekend": weekday,
            "seasons": {}, "months": {},
            "cluster": cluster, "cluster_name": cluster_name,
            "context": {
                "near_nycha": 0, "near_school": 0, "nycha_dist_m": 900, "nycha_nearest": "N",
                "school_dist_m": 900, "school_nearest": "S", "subway_dist_m": 300,
                "subway_nearest": "Sub", "transit_gap": 0,
            },
        }

    commuter_curve = [2.0, -2.0] + [0.0] * 22  # nonzero magnitude
    residential_curve = [-1.5, 1.5] + [0.0] * 22
    flat_curve = [0.05, -0.05] + [0.0] * 22  # low-signal: near-flat

    return {
        "granularity": {"seasons": [], "months": []},
        "stations": {
            "A": _station("Station A", 40.75, -73.98, 0, "Commuter core (fills AM, drains PM)", commuter_curve),
            "B": _station("Station B", 40.76, -73.97, 0, "Commuter core (fills AM, drains PM)", commuter_curve),
            "C": _station("Station C", 40.70, -73.95, 1, "Residential feeder (drains AM, fills PM)", residential_curve),
            "D": _station("Station D", 40.71, -73.94, 1, "Residential feeder (drains AM, fills PM)", residential_curve),
            "E": _station("Station E", 40.72, -73.93, -1, "Low signal (excluded from clustering)", flat_curve),
        },
    }


def _live_payload():
    return {
        "last_updated": "2026-07-15T00:00:00+00:00",
        "n_dropped": 0,
        "stations": {
            "A": {"capacity": 20, "bikes_available": 5, "docks_available": 15, "is_renting": True, "is_returning": True},
            "B": {"capacity": 30, "bikes_available": 5, "docks_available": 25, "is_renting": True, "is_returning": True},
            "C": {"capacity": 15, "bikes_available": 5, "docks_available": 10, "is_renting": True, "is_returning": True},
            "D": {"capacity": 25, "bikes_available": 5, "docks_available": 20, "is_renting": True, "is_returning": True},
            "E": {"capacity": 0, "bikes_available": 0, "docks_available": 0, "is_renting": False, "is_returning": False},
        },
    }


def _train_features_for(station_ids, rng):
    rows = []
    for sid in station_ids:
        for _ in range(20):
            rows.append(
                {
                    "station_id": sid,
                    "lat": 40.7 + rng.uniform(0, 0.1),
                    "lng": -74.0 + rng.uniform(0, 0.1),
                    "hour": int(rng.integers(0, 24)),
                    "day_type": rng.choice(["weekday", "weekend"]),
                    "temp_mean_c": rng.uniform(-5, 25),
                    "precip_mm": rng.uniform(0, 10),
                    "holiday_fraction": rng.uniform(0, 1),
                    "doy_sin": rng.uniform(-1, 1),
                    "doy_cos": rng.uniform(-1, 1),
                    "n_days": int(rng.integers(1, 10)),
                }
            )
    df = pd.DataFrame(rows)
    df["net_per_day"] = 0.4 * df["temp_mean_c"] - 0.2 * df["precip_mm"] + rng.normal(0, 1, len(df))
    return df


def test_build_elasticities_end_to_end_with_tiny_fixture():
    rng = np.random.default_rng(2)
    flows_payload = _flows_payload()
    live_payload = _live_payload()
    train_features = _train_features_for(["A", "B", "C", "D"], rng)  # E deliberately has zero rows
    model = train_gbm(train_features, DEFAULT_CATEGORIES)
    throughput_by_id = pd.Series({"A": 200.0, "B": 350.0, "C": 150.0, "D": 300.0})

    payload = build_elasticities(flows_payload, live_payload, model, train_features, throughput_by_id)

    assert set(payload["by_typology"].keys()) == {"commuter_core", "residential_feeder"}
    assert set(payload["by_station"].keys()) == {"A", "B", "C", "D"}  # E excluded: low-signal AND zero rows

    for slug, entry in payload["by_typology"].items():
        assert "capacity_elasticity_rank_correlation" in entry
        assert entry["n_stations"] == 2

    for sid in ["A", "B", "C", "D"]:
        entry = payload["by_station"][sid]
        assert "temp_elasticity" in entry
        assert "precip_elasticity" in entry
        assert entry["n_obs"] == 20
        # This fixture only has 2 distinct capacity values per group (fewer
        # than fit_capacity_curve's 8-distinct-value degrees-of-freedom
        # floor for 4 coefficients), so capacity_elasticity is correctly
        # ABSENT here, not a bug -- see the dedicated capacity test below
        # for a fixture with enough spread to actually exercise that path.
        assert "capacity_elasticity" not in entry

    assert "Low signal (excluded from clustering)" in payload["notes"]
    assert "commuter_core" in payload["notes"]
    assert "residential_feeder" in payload["notes"]


def test_build_elasticities_computes_capacity_elasticity_with_enough_spread():
    # A separate, bigger fixture (10 stations/group, real capacity spread)
    # specifically to exercise fit_capacity_curve's real path -- the tiny
    # fixture above deliberately doesn't have enough distinct capacity
    # values to clear its degrees-of-freedom floor.
    rng = np.random.default_rng(4)
    commuter_curve = [2.0, -2.0] + [0.0] * 22
    residential_curve = [-1.5, 1.5] + [0.0] * 22

    def _station(cluster, cluster_name, curve):
        return {
            "name": "S", "lat": 40.7, "lng": -74.0, "weekday": curve, "weekend": curve,
            "seasons": {}, "months": {}, "cluster": cluster, "cluster_name": cluster_name,
            "context": {
                "near_nycha": 0, "near_school": 0, "nycha_dist_m": 900, "nycha_nearest": "N",
                "school_dist_m": 900, "school_nearest": "S", "subway_dist_m": 300,
                "subway_nearest": "Sub", "transit_gap": 0,
            },
        }

    station_ids = []
    stations = {}
    live_stations = {}
    for i in range(10):
        sid_c, sid_r = f"C{i}", f"R{i}"
        # Scaled slightly per station, not an identical curve for all 10 --
        # otherwise magnitude has zero real variance to regress against
        # capacity and the rank-correlation check below degenerates to a
        # constant-input warning.
        scale = 0.85 + 0.03 * i
        stations[sid_c] = _station(0, "Commuter core (fills AM, drains PM)", [v * scale for v in commuter_curve])
        stations[sid_r] = _station(1, "Residential feeder (drains AM, fills PM)", [v * scale for v in residential_curve])
        live_stations[sid_c] = {"capacity": 10 + i * 5, "bikes_available": 5, "docks_available": 5, "is_renting": True, "is_returning": True}
        live_stations[sid_r] = {"capacity": 12 + i * 6, "bikes_available": 5, "docks_available": 5, "is_renting": True, "is_returning": True}
        station_ids += [sid_c, sid_r]

    flows_payload = {"granularity": {"seasons": [], "months": []}, "stations": stations}
    live_payload = {"last_updated": "2026-07-15T00:00:00+00:00", "n_dropped": 0, "stations": live_stations}
    train_features = _train_features_for(station_ids, rng)
    model = train_gbm(train_features, DEFAULT_CATEGORIES)
    throughput_by_id = pd.Series({sid: float(rng.uniform(100, 400)) for sid in station_ids})

    payload = build_elasticities(flows_payload, live_payload, model, train_features, throughput_by_id)

    for slug, entry in payload["by_typology"].items():
        assert entry["n_stations_with_capacity"] == 10
        assert entry["capacity_elasticity"] is not None

    for sid in station_ids:
        assert "capacity_elasticity" in payload["by_station"][sid]


def test_build_elasticities_skips_station_with_zero_capacity():
    # Station E is low-signal already, so add a real-cluster station with
    # capacity 0 (live_status.json really does report this for ~52 real
    # stations, see PROGRESS.md Session 7) to confirm it gets no
    # capacity_elasticity rather than a divide-by-zero or fabricated value.
    rng = np.random.default_rng(3)
    flows_payload = _flows_payload()
    flows_payload["stations"]["F"] = {
        **flows_payload["stations"]["A"], "cluster": 0, "cluster_name": "Commuter core (fills AM, drains PM)",
    }
    live_payload = _live_payload()
    live_payload["stations"]["F"] = {"capacity": 0, "bikes_available": 0, "docks_available": 0, "is_renting": True, "is_returning": True}
    train_features = _train_features_for(["A", "B", "C", "D", "F"], rng)
    model = train_gbm(train_features, DEFAULT_CATEGORIES)
    throughput_by_id = pd.Series({"A": 200.0, "B": 350.0, "C": 150.0, "D": 300.0, "F": 250.0})

    payload = build_elasticities(flows_payload, live_payload, model, train_features, throughput_by_id)

    assert "F" in payload["by_station"]
    assert "capacity_elasticity" not in payload["by_station"]["F"]

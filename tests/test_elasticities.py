"""Tests for pipeline/elasticities.py."""

import numpy as np
import pandas as pd
import pytest

from pipeline.elasticities import (
    build_elasticities,
    capacity_local_slope,
    capacity_local_slope_se,
    ceiling_effect_note,
    elasticity_ci95,
    fit_capacity_curve,
    fit_daily_weather_regression,
    load_daily_weather_panel,
    load_station_throughput,
    station_magnitude,
)


def _write_daily_net_flow_parquet(path, rows):
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_load_daily_weather_panel_reads_persisted_table_not_raw_trips(tmp_path, monkeypatch):
    # Session 31 regression guard: this must never touch pipeline.download
    # again -- the raw trip archives it used to re-download are deleted by
    # design after pipeline/build_full_year.py aggregates them.
    parquet_path = tmp_path / "daily_net_flow.parquet"
    _write_daily_net_flow_parquet(
        parquet_path,
        [
            {"station_id": "1", "station_name": "A", "date": "2025-07-01", "hour": 8, "lat": 40.0, "lng": -74.0,
             "arrivals_count": 1, "departures_count": 5, "net": -4},
            {"station_id": "1", "station_name": "A", "date": "2025-07-01", "hour": 18, "lat": 40.0, "lng": -74.0,
             "arrivals_count": 6, "departures_count": 0, "net": 6},
        ],
    )
    weather = pd.DataFrame({"date": [pd.Timestamp("2025-07-01")], "temp_mean_c": [22.0], "precip_mm": [0.0]})
    monkeypatch.setattr("pipeline.weather.fetch_daily_weather", lambda start, end: weather)

    panel = load_daily_weather_panel(daily_net_flow_path=parquet_path)

    assert len(panel) == 1  # one (station, date) row
    assert panel["station_id"].iloc[0] == "1"
    assert panel["magnitude"].iloc[0] == pytest.approx((4 + 6) / 2)  # mean(|-4|, |6|)
    assert panel["temp_mean_c"].iloc[0] == 22.0


def test_load_station_throughput_sums_both_sides_per_real_day(tmp_path):
    parquet_path = tmp_path / "daily_net_flow.parquet"
    _write_daily_net_flow_parquet(
        parquet_path,
        [
            {"station_id": "1", "station_name": "A", "date": "2025-07-01", "hour": 8, "lat": 40.0, "lng": -74.0,
             "arrivals_count": 1, "departures_count": 5, "net": -4},
            {"station_id": "1", "station_name": "A", "date": "2025-07-02", "hour": 8, "lat": 40.0, "lng": -74.0,
             "arrivals_count": 3, "departures_count": 3, "net": 0},
        ],
    )
    throughput = load_station_throughput(daily_net_flow_path=parquet_path)

    assert throughput["1"] == pytest.approx((6 + 6) / 2)  # (1+5) on day 1, (3+3) on day 2, averaged


def test_station_magnitude_is_mean_absolute_value():
    assert station_magnitude([1, -2, 3, -4] + [0] * 20) == pytest.approx((1 + 2 + 3 + 4) / 24)


def test_fit_daily_weather_regression_recovers_known_direction_on_synthetic_data():
    rng = np.random.default_rng(0)
    n = 300
    temps = rng.uniform(-5, 25, n)
    precips = rng.uniform(0, 10, n)
    magnitudes = 0.5 * temps - 0.3 * precips + rng.normal(0, 1, n)

    curve = fit_daily_weather_regression(magnitudes, temps, precips, n_distinct_days=90)
    assert curve is not None
    assert curve.b1 == pytest.approx(0.5, abs=0.1)  # true temp coefficient
    assert curve.b2 == pytest.approx(-0.3, abs=0.1)  # true precip coefficient
    assert curve.se_b1 > 0
    assert curve.se_b2 > 0


def test_fit_daily_weather_regression_controls_for_correlated_features():
    # temp and precip are deliberately correlated (rainy days run cooler)
    # -- a joint fit must still recover each TRUE coefficient separately,
    # not let one leak into the other the way two independent univariate
    # fits would.
    rng = np.random.default_rng(1)
    n = 300
    temps = rng.uniform(-5, 25, n)
    precips = np.clip(10 - 0.3 * temps + rng.normal(0, 1, n), 0, None)  # correlated with temp
    magnitudes = 0.4 * temps - 0.5 * precips + rng.normal(0, 0.5, n)

    curve = fit_daily_weather_regression(magnitudes, temps, precips, n_distinct_days=90)
    assert curve.b1 == pytest.approx(0.4, abs=0.15)
    assert curve.b2 == pytest.approx(-0.5, abs=0.15)


def test_fit_daily_weather_regression_returns_none_with_too_few_distinct_days():
    temps = np.array([10.0, 12.0, 15.0])
    precips = np.array([1.0, 2.0, 0.5])
    magnitudes = np.array([1.0, 1.2, 0.9])
    assert fit_daily_weather_regression(magnitudes, temps, precips, n_distinct_days=3) is None


def test_fit_capacity_curve_recovers_known_quadratic_and_throughput_term():
    rng = np.random.default_rng(1)
    n = 200
    throughput = rng.uniform(50, 500, n)
    capacity = np.clip(0.1 * throughput + rng.normal(0, 5, n), 5, 150)
    magnitude = 0.5 * throughput / 100 + 0.3 * capacity - 0.004 * capacity**2 + rng.normal(0, 0.3, n)

    curve = fit_capacity_curve(capacity, magnitude, throughput)
    assert curve is not None
    assert curve.b1 == pytest.approx(0.3, abs=0.05)
    assert curve.b2 == pytest.approx(-0.004, abs=0.002)
    assert curve.var_b1 > 0
    assert curve.var_b2 > 0


def test_fit_capacity_curve_shows_diminishing_effect():
    rng = np.random.default_rng(1)
    n = 200
    throughput = rng.uniform(50, 500, n)
    capacity = np.clip(0.1 * throughput + rng.normal(0, 5, n), 5, 150)
    magnitude = 0.3 * capacity - 0.004 * capacity**2 + rng.normal(0, 0.3, n)

    curve = fit_capacity_curve(capacity, magnitude, throughput)
    low_slope = capacity_local_slope(curve.b1, curve.b2, 20)
    high_slope = capacity_local_slope(curve.b1, curve.b2, 130)
    assert low_slope > high_slope  # the effect must shrink (or reverse) at high capacity, not stay flat


def test_fit_capacity_curve_returns_none_with_too_few_distinct_capacities():
    capacities = np.array([10.0] * 3 + [20.0] * 3)  # only 2 distinct values
    magnitudes = np.array([1.0, 1.1, 0.9, 2.0, 2.1, 1.9])
    throughput = np.array([100.0] * 6)
    assert fit_capacity_curve(capacities, magnitudes, throughput) is None


def test_fit_daily_weather_regression_ci_narrows_with_more_data():
    # More real days at the same true relationship must produce a tighter
    # (not wider, not equal) confidence interval -- the whole point of
    # Session 31 pooling a full year instead of 90 days.
    rng = np.random.default_rng(7)

    def _fit(n):
        temps = rng.uniform(-5, 25, n)
        precips = rng.uniform(0, 10, n)
        magnitudes = 0.5 * temps - 0.3 * precips + rng.normal(0, 1, n)
        return fit_daily_weather_regression(magnitudes, temps, precips, n_distinct_days=n)

    small = _fit(60)
    large = _fit(600)
    assert large.se_b1 < small.se_b1


def test_elasticity_ci95_brackets_the_point_estimate():
    ci = elasticity_ci95(slope=0.5, se=0.1, denominator=10.0)
    point_estimate = 0.5 / 10.0
    assert ci[0] < point_estimate < ci[1]
    assert ci[0] == pytest.approx((0.5 - 1.96 * 0.1) / 10.0, abs=1e-4)
    assert ci[1] == pytest.approx((0.5 + 1.96 * 0.1) / 10.0, abs=1e-4)


def test_capacity_local_slope_se_accounts_for_covariance_not_just_variances():
    # A real (valid, Cauchy-Schwarz-respecting: |cov| <= sqrt(var_b1*var_b2))
    # negative covariance between b1 and b2 should meaningfully change the
    # derivative's SE relative to ignoring it entirely -- confirms the
    # delta-method cross term isn't a no-op in the formula.
    with_covariance = capacity_local_slope_se(var_b1=1.0, var_b2=0.01, cov_b1_b2=-0.09, capacity=20)
    ignoring_covariance = capacity_local_slope_se(var_b1=1.0, var_b2=0.01, cov_b1_b2=0.0, capacity=20)
    assert not np.isnan(with_covariance)
    assert with_covariance != pytest.approx(ignoring_covariance)


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


def _daily_panel_for(station_ids, rng, n_days=20):
    """Synthetic replacement for the old _train_features_for helper -- real
    per-(station, date) rows, matching load_daily_weather_panel's own
    output schema (station_id, date, magnitude, temp_mean_c, precip_mm),
    not the retired hourly feature-panel shape.
    """
    dates = pd.date_range("2026-02-01", periods=n_days, freq="D")
    rows = []
    for sid in station_ids:
        for date in dates:
            temp = rng.uniform(-5, 25)
            precip = rng.uniform(0, 10)
            rows.append(
                {
                    "station_id": sid,
                    "date": date,
                    "temp_mean_c": temp,
                    "precip_mm": precip,
                    "magnitude": 5.0 + 0.4 * temp - 0.2 * precip + rng.normal(0, 1),
                }
            )
    return pd.DataFrame(rows)


def test_build_elasticities_end_to_end_with_tiny_fixture():
    rng = np.random.default_rng(2)
    flows_payload = _flows_payload()
    live_payload = _live_payload()
    daily_panel = _daily_panel_for(["A", "B", "C", "D"], rng)  # E deliberately has zero rows
    throughput_by_id = pd.Series({"A": 200.0, "B": 350.0, "C": 150.0, "D": 300.0})

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    assert set(payload["by_typology"].keys()) == {"commuter_core", "residential_feeder"}
    assert set(payload["by_station"].keys()) == {"A", "B", "C", "D"}  # E excluded: low-signal (no group to attach to)

    for slug, entry in payload["by_typology"].items():
        assert "capacity_elasticity_rank_correlation" in entry
        assert entry["n_stations"] == 2
        assert entry["n_daily_observations"] == 40  # 2 stations x 20 days
        assert entry["temp_elasticity"] is not None
        assert entry["precip_elasticity"] is not None
        assert entry["temp_elasticity_ci95"][0] < entry["temp_elasticity"] < entry["temp_elasticity_ci95"][1]
        assert entry["precip_elasticity_ci95"][0] < entry["precip_elasticity"] < entry["precip_elasticity_ci95"][1]

    for sid in ["A", "B", "C", "D"]:
        entry = payload["by_station"][sid]
        assert entry["n_obs"] == 20
        assert entry["temp_elasticity"] is not None
        assert entry["precip_elasticity"] is not None
        assert entry["temp_elasticity_ci95"][0] < entry["temp_elasticity"] < entry["temp_elasticity_ci95"][1]
        # This fixture only has 2 distinct capacity values per group (fewer
        # than fit_capacity_curve's 8-distinct-value degrees-of-freedom
        # floor for 4 coefficients), so capacity_elasticity is correctly
        # ABSENT here, not a bug -- see the dedicated capacity test below
        # for a fixture with enough spread to actually exercise that path.
        assert "capacity_elasticity" not in entry

    assert "Low signal (excluded from clustering)" in payload["notes"]
    assert "commuter_core" in payload["notes"]
    assert "residential_feeder" in payload["notes"]


def test_build_elasticities_omits_temp_precip_below_min_daily_observations():
    # A station with fewer real days than MIN_DAILY_OBSERVATIONS (10) must
    # get a by_station entry (it still has a real cluster and real
    # capacity), just without temp_elasticity/precip_elasticity -- the
    # dashboard's documented fallback-to-typology contract, extended one
    # level further than "sparse stations get no entry at all."
    rng = np.random.default_rng(5)
    flows_payload = _flows_payload()
    live_payload = _live_payload()
    daily_panel = _daily_panel_for(["A", "B", "C", "D"], rng, n_days=20)
    # Station A gets only 5 real days -- below MIN_DAILY_OBSERVATIONS (10).
    rows_to_drop = daily_panel[daily_panel["station_id"] == "A"].iloc[5:].index
    daily_panel = daily_panel.drop(rows_to_drop)
    throughput_by_id = pd.Series({"A": 200.0, "B": 350.0, "C": 150.0, "D": 300.0})

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    assert "A" in payload["by_station"]
    assert payload["by_station"]["A"]["n_obs"] < 10
    assert payload["by_station"]["A"]["temp_elasticity"] is None
    assert payload["by_station"]["A"]["precip_elasticity"] is None
    # B still has its full 20 days -- must still get a real fit.
    assert payload["by_station"]["B"]["temp_elasticity"] is not None


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
    daily_panel = _daily_panel_for(station_ids, rng)
    throughput_by_id = pd.Series({sid: float(rng.uniform(100, 400)) for sid in station_ids})

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    for slug, entry in payload["by_typology"].items():
        assert entry["n_stations_with_capacity"] == 10
        assert entry["capacity_elasticity"] is not None
        ci = entry["capacity_elasticity_ci95"]
        assert ci[0] <= entry["capacity_elasticity"] <= ci[1]

    for sid in station_ids:
        assert "capacity_elasticity" in payload["by_station"][sid]
        assert "capacity_elasticity_ci95" in payload["by_station"][sid]


def test_build_elasticities_skips_capacity_for_station_with_zero_capacity():
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
    daily_panel = _daily_panel_for(["A", "B", "C", "D", "F"], rng)
    throughput_by_id = pd.Series({"A": 200.0, "B": 350.0, "C": 150.0, "D": 300.0, "F": 250.0})

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    assert "F" in payload["by_station"]
    assert "capacity_elasticity" not in payload["by_station"]["F"]
    # F's temp/precip must still be computed independently of its missing capacity.
    assert payload["by_station"]["F"]["temp_elasticity"] is not None

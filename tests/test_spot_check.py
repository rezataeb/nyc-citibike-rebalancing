"""Tests for pipeline/spot_check.py."""

import pandas as pd

from pipeline.spot_check import (
    am_pm_net,
    check_holiday_dip,
    check_seasonal_amplitude,
    check_station_direction,
    run_all_checks,
)


def make_curve(am_value: float, pm_value: float) -> list[float]:
    curve = [0.0] * 24
    for h in (7, 8, 9):
        curve[h] = am_value
    for h in (17, 18, 19):
        curve[h] = pm_value
    return curve


def test_am_pm_net_sums_the_two_windows():
    curve = make_curve(am_value=1.0, pm_value=-2.0)
    am_net, pm_net = am_pm_net(curve)
    assert am_net == 3.0
    assert pm_net == -6.0


def test_check_station_direction_passes_when_real_data_matches_expectation():
    stations = {"s1": {"weekday": make_curve(am_value=1.0, pm_value=-1.0)}}
    result = check_station_direction(stations, "s1", "test office district", "fills_am_drains_pm")
    assert result.passed


def test_check_station_direction_fails_when_real_data_contradicts_expectation():
    # A station claimed to be an office core but whose real data drains AM
    # and fills PM (the opposite direction) must fail, not be waved through.
    stations = {"s1": {"weekday": make_curve(am_value=-1.0, pm_value=1.0)}}
    result = check_station_direction(stations, "s1", "test office district", "fills_am_drains_pm")
    assert not result.passed


def test_check_station_direction_fails_cleanly_when_station_missing():
    result = check_station_direction({}, "missing_id", "test", "fills_am_drains_pm")
    assert not result.passed
    assert "not found" in result.detail


def test_check_holiday_dip_passes_when_real_dip_exceeds_threshold():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-11-20", "2025-11-27", "2025-12-04"]),
            "departures_count": [1000, 400, 1000],
        }
    )
    result = check_holiday_dip(daily, "2025-11-27", ["2025-11-20", "2025-12-04"], min_dip_fraction=0.3)
    assert result.passed


def test_check_holiday_dip_fails_when_real_volume_is_not_actually_lower():
    # If the "holiday" date shows normal or higher volume than its
    # comparison dates, that's a real finding -- not something to pass.
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-11-20", "2025-11-27", "2025-12-04"]),
            "departures_count": [1000, 950, 1000],
        }
    )
    result = check_holiday_dip(daily, "2025-11-27", ["2025-11-20", "2025-12-04"], min_dip_fraction=0.3)
    assert not result.passed


def test_check_holiday_dip_fails_cleanly_when_date_missing_from_data():
    daily = pd.DataFrame({"date": pd.to_datetime(["2025-11-20"]), "departures_count": [1000]})
    result = check_holiday_dip(daily, "2025-11-27", ["2025-11-20"], min_dip_fraction=0.3)
    assert not result.passed
    assert "not present" in result.detail


def test_check_seasonal_amplitude_passes_when_summer_is_real_higher():
    stations = {
        "s1": {
            "seasons": {
                "summer": {"weekday": [2.0] * 24},
                "winter": {"weekday": [0.5] * 24},
            }
        }
    }
    result = check_seasonal_amplitude(stations, "s1", min_ratio=1.5)
    assert result.passed


def test_check_seasonal_amplitude_fails_when_winter_is_not_actually_lower():
    stations = {
        "s1": {
            "seasons": {
                "summer": {"weekday": [1.0] * 24},
                "winter": {"weekday": [1.0] * 24},
            }
        }
    }
    result = check_seasonal_amplitude(stations, "s1", min_ratio=1.5)
    assert not result.passed


def test_run_all_checks_returns_one_result_per_configured_check():
    stations = {
        "4920.13": {"weekday": make_curve(1.0, -1.0), "seasons": {"summer": {"weekday": [1.0] * 24}, "winter": {"weekday": [1.0] * 24}}},
        "4962.08": {"weekday": make_curve(1.0, -1.0), "seasons": {"summer": {"weekday": [1.0] * 24}, "winter": {"weekday": [1.0] * 24}}},
        "5854.10": {"weekday": make_curve(-1.0, 1.0), "seasons": {"summer": {"weekday": [1.0] * 24}, "winter": {"weekday": [1.0] * 24}}},
    }
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-11-20", "2025-11-27", "2025-12-04"]),
            "departures_count": [1000, 1000, 1000],
        }
    )
    results = run_all_checks(stations, daily)
    assert len(results) == 5

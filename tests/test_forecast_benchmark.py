"""Tests for pipeline/forecast_benchmark.py on small synthetic cubes.

What they guard: no method sees the week it predicts (forward-only), the
zero-filled cube really fills hours without trips, Holt-Winters recovers a
pure weekly pattern, and the scoring levels add up the right hours.
"""
import numpy as np
import pandas as pd

from pipeline.forecast_benchmark import (
    AM_HOURS, dense_cube, holt_winters_states, hw_forecast, level_errors, predict_lag, predict_profile, summarize,
    weekly_origins,
)

DAYS = pd.date_range("2026-01-05", periods=56, freq="D")  # starts on a Monday, 8 weeks
WEEKEND = np.asarray(DAYS.dayofweek >= 5)


def cube_with(values_by_day):
    """1 station; every hour of day d holds values_by_day[d]."""
    return np.repeat(np.asarray(values_by_day, dtype=np.float32)[None, :, None], 24, axis=2)


def test_dense_cube_zero_fills_and_keeps_only_full_coverage_stations():
    daily = pd.DataFrame({
        "station_id": ["A", "A", "B"],
        "date": pd.to_datetime(["2026-01-01", "2026-02-10", "2026-01-03"]),
        "hour": [8, 17, 9], "net": [3, -2, 5],
    })
    cube, stations, days = dense_cube(daily, ["2026-01", "2026-02"])
    assert stations == ["A"], "B has no February trips, so its empty February would look like real zeros"
    assert cube.shape == (1, 59, 24)
    assert cube[0, 0, 8] == 3 and cube[0, 40, 17] == -2
    assert cube.sum() == 1, "every other hour is a zero, not missing"


def test_methods_never_see_the_week_they_predict():
    w0 = 28
    cube = cube_with([1.0] * w0 + [100.0] * (len(DAYS) - w0))  # the test weeks are wildly different
    assert np.allclose(predict_profile(cube, WEEKEND, w0), 1.0)
    assert np.allclose(predict_lag(cube, w0, (7,)), 1.0)
    assert np.allclose(predict_lag(cube, w0, (7, 14, 21, 28)), 1.0)
    level, seasonal = holt_winters_states(cube.transpose(0, 2, 1).reshape(24, -1)[:, :w0], 0.2, 0.2)
    assert np.allclose(hw_forecast(level, seasonal, w0), 1.0)


def test_profile_keeps_weekdays_and_weekends_apart():
    cube = cube_with(np.where(WEEKEND, 10.0, 2.0))
    pred = predict_profile(cube, WEEKEND, 28)
    assert np.allclose(pred[0, :5], 2.0) and np.allclose(pred[0, 5:], 10.0)  # Mon-Fri, then Sat-Sun


def test_holt_winters_recovers_a_pure_weekly_pattern():
    pattern = np.array([5, 4, 3, 2, 1, -3, -4], dtype=np.float64)
    series = np.tile(pattern, 8)[None, :]
    level, seasonal = holt_winters_states(series[:, :49], 0.1, 0.1)
    assert np.allclose(hw_forecast(level, seasonal, 49)[0], pattern, atol=1e-9)


def test_weekly_origins_start_on_mondays_after_the_history_months():
    days = pd.date_range("2025-10-01", "2026-09-30", freq="D")
    months = [f"2025-{m}" for m in ("10", "11", "12")] + [f"2026-{m:02d}" for m in range(1, 10)]
    weeks = weekly_origins(days, months)
    assert all(days[w].dayofweek == 0 for w in weeks)
    assert days[weeks[0]] >= pd.Timestamp("2026-01-01")
    assert weeks[-1] + 7 <= len(days)


def test_levels_sum_the_right_hours():
    actual = np.zeros((1, 7, 24), dtype=np.float32)
    pred = actual.copy()
    pred[0, 0, 7] = 2.0   # Monday 7 AM: inside the morning peak
    pred[0, 0, 20] = 1.0  # Monday 8 PM: outside it
    weekday = ~WEEKEND[:7]
    errors = level_errors(pred, actual, weekday)
    assert np.isclose(errors["hourly"], (2.0 + 1.0) / (7 * 24))
    assert np.isclose(errors["am_peak"], 2.0 / 5), "one weekday off by 2 bikes, averaged over the 5 weekdays"
    assert np.isclose(errors["daily"], 3.0 / 7)
    assert 7 in AM_HOURS and 20 not in AM_HOURS


def test_summary_skill_and_wins_are_relative_to_the_profile():
    weeks = [{"hourly": 1.0, "am_peak": 2.0, "daily": 3.0}] * 6
    better = [{"hourly": 0.5, "am_peak": 1.0, "daily": 1.5}] * 6
    out = summarize({"profile": weeks, "last_week": better})
    assert out["hourly"]["profile"]["skill_vs_profile"] == 0
    assert out["hourly"]["last_week"]["skill_vs_profile"] == 0.5
    assert out["daily"]["last_week"]["weeks_better_than_profile"] == 6

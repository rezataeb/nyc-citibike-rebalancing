"""Tests for pipeline/window.py and update_window.plan_window: the window is set in one place."""

from pipeline.update_window import plan_window
from pipeline.window import N_MONTHS, TARGET_MONTHS, WEATHER_END, WEATHER_START, target_months, window_dates


def test_twelve_months_ending_at_the_end_month_across_a_year_boundary():
    assert target_months("2026-09") == ["2025-10", "2025-11", "2025-12"] + [f"2026-{m:02d}" for m in range(1, 10)]
    assert target_months("2027-01", 3) == ["2026-11", "2026-12", "2027-01"]


def test_window_covers_every_calendar_month_once():
    """Why 12 months: every month appears once, so any date maps to a typical month."""
    assert len(TARGET_MONTHS) == N_MONTHS == 12
    assert sorted(m[5:] for m in TARGET_MONTHS) == [f"{m:02d}" for m in range(1, 13)]


def test_window_dates_are_first_and_last_day():
    assert window_dates(["2025-10", "2026-09"]) == ("2025-10-01", "2026-09-30")
    assert window_dates(["2026-12"]) == ("2026-12-01", "2026-12-31")
    assert window_dates(["2028-02"]) == ("2028-02-01", "2028-02-29")
    assert (WEATHER_START, WEATHER_END) == window_dates(TARGET_MONTHS)


def test_plan_window_adds_and_drops_only_the_difference():
    current = target_months("2026-08")
    assert plan_window(current, target_months("2026-09")) == (["2026-09"], ["2025-09"])
    assert plan_window(current, current) == ([], [])

"""Tests for pipeline/scenario_presets.py."""

from pipeline.scenario_presets import OBSERVED_TEMP_RANGE_C, build_scenario_presets


def test_hot_day_preset_exists_and_is_documented():
    result = build_scenario_presets()
    ids = {p["id"] for p in result["presets"]}
    assert "hot_day" in ids
    assert "hot_day" in result["notes"], "adding hot_day must be documented in the file's own notes, not just silent"


def test_hot_day_is_the_warmest_preset():
    result = build_scenario_presets()
    by_id = {p["id"]: p for p in result["presets"]}
    assert by_id["hot_day"]["temp_c"] > by_id["ideal"]["temp_c"]
    assert by_id["hot_day"]["temp_c"] > by_id["rain_day"]["temp_c"]


def test_ideal_is_the_reference_preset_with_zero_precip():
    result = build_scenario_presets()
    assert result["reference_preset_id"] == "ideal"
    ideal = next(p for p in result["presets"] if p["id"] == "ideal")
    assert ideal["precip_mm"] == 0.0


def test_presets_use_celsius_and_mm_not_fahrenheit_and_inches():
    result = build_scenario_presets()
    for preset in result["presets"]:
        assert "temp_c" in preset
        assert "precip_mm" in preset
        assert "temp_f" not in preset
        assert "precip_in" not in preset


def test_snow_day_is_colder_than_rain_day():
    result = build_scenario_presets()
    by_id = {p["id"]: p for p in result["presets"]}
    assert by_id["snow_day"]["temp_c"] < by_id["rain_day"]["temp_c"]


def test_all_preset_temps_are_within_the_real_observed_range():
    # OBSERVED_TEMP_RANGE_C is a dated fact (-14.3C to 32.3C, verified
    # 2026-07-24 against the real full-year daily weather panel), not a
    # permanent guarantee -- this must fail loudly if a future data refresh
    # ever narrows the real range back below hot_day's configured value,
    # not silently keep shipping a preset that's become an extrapolation.
    low, high = OBSERVED_TEMP_RANGE_C
    result = build_scenario_presets()
    for preset in result["presets"]:
        assert low <= preset["temp_c"] <= high, (
            f"{preset['id']} ({preset['temp_c']}C) is outside the documented real "
            f"observed range {OBSERVED_TEMP_RANGE_C} -- re-verify against real data "
            "before trusting this preset is still within range, not just historically"
        )

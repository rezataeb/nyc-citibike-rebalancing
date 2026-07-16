"""Tests for pipeline/scenario_presets.py."""

from pipeline.scenario_presets import build_scenario_presets


def test_heat_wave_is_deliberately_excluded():
    result = build_scenario_presets()
    ids = {p["id"] for p in result["presets"]}
    assert "heat_wave" not in ids
    assert "heat_wave" in result["notes"], "the exclusion must be documented in the file's own notes, not just silent"


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


def test_all_preset_temps_are_within_or_near_the_observed_training_range():
    # The real Feb+April training panel's observed temp_mean_c range is
    # -4.4C to 18.7C (pipeline/elasticities.py's own SPARSE_GRID_CAVEAT) --
    # every REMAINING preset (heat_wave already excluded) should be at or
    # only modestly beyond that range, not a wild extrapolation.
    result = build_scenario_presets()
    for preset in result["presets"]:
        assert -10 <= preset["temp_c"] <= 25, f"{preset['id']} is a much bigger extrapolation than intended: {preset['temp_c']}C"

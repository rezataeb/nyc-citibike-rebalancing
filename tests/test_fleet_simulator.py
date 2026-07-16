"""Tests for pipeline/fleet_simulator.py."""

from pipeline.fleet_simulator import build_fleet_scenarios
from pipeline.plan_routes import DEFAULT_CAPACITY, DEFAULT_MAX_STOPS

BASE_LAT, BASE_LNG = 40.700, -74.000


def _record(lat, lng, am_total, name="S"):
    """A minimal flows.json station record with a given AM-window (hours 6-9) total."""
    weekday = [0.0] * 24
    weekday[6] = am_total  # all on hour 6 -- sum over AM_HOURS is just am_total
    return {
        "name": name, "lat": lat, "lng": lng,
        "weekday": weekday, "weekend": [0.0] * 24,
        "seasons": {}, "months": {},
    }


def _payload(stations):
    return {"granularity": {"seasons": [], "months": []}, "stations": stations}


def test_build_fleet_scenarios_covers_requested_fleet_sizes():
    payload = _payload({"s": _record(BASE_LAT, BASE_LNG, 5.0)})
    result = build_fleet_scenarios(payload, fleet_sizes=[1, 2, 3], capacity=10)

    assert result["fleet_sizes"] == [1, 2, 3]
    assert set(result["scenarios"].keys()) == {"1", "2", "3"}
    for n in [1, 2, 3]:
        assert result["scenarios"][str(n)]["n_trucks_requested"] == n


def test_build_fleet_scenarios_uses_fixed_baseline_params_by_default():
    # Confirms the decoupling-from-route.json decision: default params come
    # from plan_routes.py's own constants, not something route.json happens
    # to be set to.
    payload = _payload({"s": _record(BASE_LAT, BASE_LNG, 5.0)})
    result = build_fleet_scenarios(payload)

    assert result["period"] == "all"
    assert result["capacity"] == DEFAULT_CAPACITY
    assert result["max_stops"] == DEFAULT_MAX_STOPS


def test_build_fleet_scenarios_reflects_custom_params():
    payload = _payload({"s": _record(BASE_LAT, BASE_LNG, 5.0)})
    result = build_fleet_scenarios(payload, fleet_sizes=[1], capacity=15, max_stops=20)

    assert result["capacity"] == 15
    assert result["max_stops"] == 20
    assert result["scenarios"]["1"]["capacity"] == 15
    assert result["scenarios"]["1"]["max_stops"] == 20


def test_marginal_benefit_calculation_is_correct_when_candidates_get_exhausted():
    # NOT a claim that the real system shows diminishing returns -- it
    # doesn't, within any deployable fleet size (see PROGRESS.md Session
    # 24: every real truck at 1-40 trucks hits its own max_stops cap,
    # never runs out of candidates). This is a fixture ENGINEERED to
    # exhaust candidates on purpose (10 separated surplus/deficit pairs,
    # demand 8 each matching capacity=10 so each pair clears in exactly 2
    # stops, max_stops=6 so a single truck can only reach 3 pairs before
    # its tour caps), purely to verify build_fleet_scenarios' marginal-
    # benefit arithmetic is correct WHEN that mechanism actually triggers,
    # not to assert it's what real data does. Verified empirically, not
    # just hand-derived: truck 1 clears pairs 0-2, truck 2 clears 3-5,
    # truck 3 clears 6-8, truck 4
    # clears the last pair (9) and stops on its own (ran out of work, not
    # capped), truck 5 is a genuine no-op. Cumulative serviced:
    # [3, 6, 9, 10, 10] -- marginal benefit per truck: [3, 3, 3, 1, 0].
    stations = {}
    for i in range(10):
        lat = BASE_LAT + i * 0.05  # spaced out so one truck can't cheaply reach every pair
        stations[f"surp{i}"] = _record(lat, BASE_LNG, 8.0, f"Surplus {i}")
        stations[f"def{i}"] = _record(lat, BASE_LNG + 0.001, -8.0, f"Deficit {i}")
    payload = _payload(stations)

    result = build_fleet_scenarios(payload, fleet_sizes=list(range(1, 6)), capacity=10, max_stops=6)

    serviced = [result["scenarios"][str(n)]["n_deficit_serviced"] for n in range(1, 6)]
    assert serviced == [3, 6, 9, 10, 10]

    marginal = [serviced[0]] + [serviced[i] - serviced[i - 1] for i in range(1, len(serviced))]
    assert marginal == [3, 3, 3, 1, 0]
    assert marginal[4] < marginal[0], "the 5th truck must help strictly less than the 1st"
    for earlier, later in zip(marginal, marginal[1:]):
        assert later <= earlier, f"marginal benefit must never increase as fleet size grows: {marginal}"


def test_build_fleet_scenarios_notes_mention_marginal_benefit_and_decoupling():
    payload = _payload({"s": _record(BASE_LAT, BASE_LNG, 5.0)})
    result = build_fleet_scenarios(payload, fleet_sizes=[1, 2])
    assert "MARGINAL" in result["notes"]
    assert "decoupled" in result["notes"]

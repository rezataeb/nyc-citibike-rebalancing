"""Tests for pipeline/plan_routes.py."""

import pytest

from pipeline.plan_routes import (
    DEPOT_ASSUMPTION_NOTE,
    StationCandidate,
    build_candidates,
    build_route_payload,
    max_stops_note,
    plan_single_tour,
    select_curve,
    validate_period,
)

BASE_LAT, BASE_LNG = 40.700, -74.000


def _record(lat, lng, am_total, name="S", seasons=None, months=None):
    """A minimal flows.json station record with a given AM-window (hours 6-9) total."""
    weekday = [0.0] * 24
    weekday[6] = am_total  # all on hour 6 -- sum over AM_HOURS is just am_total
    return {
        "name": name,
        "lat": lat,
        "lng": lng,
        "weekday": weekday,
        "weekend": [0.0] * 24,
        "seasons": seasons or {},
        "months": months or {},
    }


def _payload(stations, seasons=("winter",), months=("2026-01",)):
    return {"granularity": {"seasons": list(seasons), "months": list(months)}, "stations": stations}


# ---- validate_period / select_curve ----------------------------------------


def test_validate_period_accepts_none_and_known_period():
    payload = _payload({})
    validate_period(payload, None)  # no raise
    validate_period(payload, "winter")  # no raise
    validate_period(payload, "2026-01")  # no raise


def test_validate_period_raises_for_unknown_season():
    payload = _payload({})  # only "winter" is a known season here
    with pytest.raises(ValueError):
        validate_period(payload, "summer")


def test_validate_period_raises_for_unknown_month():
    payload = _payload({})
    with pytest.raises(ValueError):
        validate_period(payload, "2026-06")


def test_select_curve_returns_top_level_when_period_none():
    record = _record(BASE_LAT, BASE_LNG, 5.0)
    assert select_curve(record, None) is record


def test_select_curve_returns_season_curve():
    winter_curve = {"weekday": [1.0] * 24, "weekend": [0.0] * 24}
    record = _record(BASE_LAT, BASE_LNG, 5.0, seasons={"winter": winter_curve})
    assert select_curve(record, "winter") == winter_curve


def test_select_curve_returns_none_when_station_missing_period_data():
    record = _record(BASE_LAT, BASE_LNG, 5.0)  # no seasons/months data
    assert select_curve(record, "winter") is None


# ---- build_candidates --------------------------------------------------------


def test_build_candidates_flags_at_threshold_boundary():
    stations = {
        "deficit_at_threshold": _record(BASE_LAT, BASE_LNG, -1.0, "Deficit"),
        "surplus_at_threshold": _record(BASE_LAT, BASE_LNG, 1.0, "Surplus"),
        "just_under_deficit": _record(BASE_LAT, BASE_LNG, -0.9, "NotFlagged1"),
        "just_under_surplus": _record(BASE_LAT, BASE_LNG, 0.9, "NotFlagged2"),
    }
    candidates = build_candidates(stations, None)
    by_id = {c.station_id: c for c in candidates}

    assert by_id["deficit_at_threshold"].kind == "deficit"
    assert by_id["deficit_at_threshold"].magnitude == 1
    assert by_id["surplus_at_threshold"].kind == "surplus"
    assert "just_under_deficit" not in by_id
    assert "just_under_surplus" not in by_id


def test_build_candidates_skips_station_missing_period_curve():
    stations = {
        "has_winter": _record(BASE_LAT, BASE_LNG, -5.0, seasons={"winter": {"weekday": [0.0] * 24, "weekend": [0.0] * 24}}),
        "no_winter": _record(BASE_LAT, BASE_LNG, -5.0),  # low-volume that season, no seasons key
    }
    # give has_winter's winter curve a real deficit; no_winter has no winter data at all
    stations["has_winter"]["seasons"]["winter"]["weekday"][6] = -3.0

    candidates = build_candidates(stations, "winter")
    ids = {c.station_id for c in candidates}
    assert ids == {"has_winter"}


# ---- plan_single_tour ---------------------------------------------------------


def test_plan_single_tour_pickup_then_dropoff_with_capacity_cap():
    # surplus (15) fits entirely under capacity (20) with no leftover, so there's
    # nothing left at that station to draw the truck back to -- isolates the
    # capacity cap to the dropoff side (deficit needs 25, truck only has 15).
    surplus = StationCandidate("SURP", "Surplus Station", BASE_LAT, BASE_LNG, "surplus", 15.0, 15, 15)
    deficit = StationCandidate("DEF", "Deficit Station", BASE_LAT + 0.001, BASE_LNG, "deficit", -25.0, 25, 25)

    result = plan_single_tour([surplus, deficit], capacity=20)
    stops = result.stops

    assert result.capped is False  # ran out of reachable work, not the stop cap
    assert [s.action for s in stops] == ["pickup", "dropoff"]
    assert stops[0].station_id == "SURP"
    assert stops[0].amount == 15
    assert stops[0].running_load == 15
    assert stops[1].station_id == "DEF"
    assert stops[1].amount == 15
    assert stops[1].running_load == 0
    assert surplus.remaining == 0  # fully picked up
    assert deficit.remaining == 10  # 25 needed, only 15 delivered -- capacity-capped


def test_plan_single_tour_revisits_a_station_with_leftover_capacity_since_distance_is_zero():
    # A pure nearest-neighbor rule has no notion of "already visited" -- if a
    # station still has remaining > 0 it's reachable again, and distance 0 (the
    # same coordinates) is always the nearest option. Here surplus (25) exceeds
    # capacity (20), so after delivering to a nearby deficit the truck still has
    # headroom and the leftover 5 at the original surplus station is, correctly
    # under this rule, the "nearest" remaining pickup -- a real, documented
    # limitation of greedy nearest-neighbor (see module docstring), not a bug.
    surplus = StationCandidate("SURP", "Surplus Station", BASE_LAT, BASE_LNG, "surplus", 25.0, 25, 25)
    deficit = StationCandidate("DEF", "Deficit Station", BASE_LAT + 0.001, BASE_LNG, "deficit", -10.0, 10, 10)

    result = plan_single_tour([surplus, deficit], capacity=20)
    stops = result.stops

    assert result.capped is False
    assert [s.action for s in stops] == ["pickup", "dropoff", "pickup"]
    assert [s.station_id for s in stops] == ["SURP", "DEF", "SURP"]
    assert surplus.remaining == 0
    assert deficit.remaining == 0


def test_plan_single_tour_visits_nearest_reachable_station_first():
    surplus = StationCandidate("SURP", "Surplus", BASE_LAT, BASE_LNG, "surplus", 50.0, 50, 50)
    near_deficit = StationCandidate("NEAR", "Near", BASE_LAT + 0.001, BASE_LNG, "deficit", -5.0, 5, 5)
    far_deficit = StationCandidate("FAR", "Far", BASE_LAT + 0.05, BASE_LNG, "deficit", -5.0, 5, 5)

    result = plan_single_tour([surplus, near_deficit, far_deficit], capacity=20)

    visited_order = [s.station_id for s in result.stops]
    assert visited_order.index("NEAR") < visited_order.index("FAR")


def test_plan_single_tour_returns_empty_when_no_surplus():
    deficit = StationCandidate("DEF", "Deficit", BASE_LAT, BASE_LNG, "deficit", -5.0, 5, 5)
    result = plan_single_tour([deficit], capacity=20)
    assert result.stops == []
    assert result.capped is False


# ---- max_stops cap -------------------------------------------------------------


def test_plan_single_tour_stops_at_max_stops_and_reports_capped():
    # Plenty of reachable work available (large surplus/deficit magnitudes,
    # same location so distance never blocks a move) -- the tour would keep
    # going indefinitely without the cap.
    surplus = StationCandidate("SURP", "Surplus", BASE_LAT, BASE_LNG, "surplus", 1000.0, 1000, 1000)
    deficit = StationCandidate("DEF", "Deficit", BASE_LAT, BASE_LNG, "deficit", -1000.0, 1000, 1000)

    result = plan_single_tour([surplus, deficit], capacity=5, max_stops=10)

    assert len(result.stops) == 10
    assert result.capped is True
    # capacity 5, alternating pickup/dropoff of 5 each -- neither fully resolved
    assert surplus.remaining > 0
    assert deficit.remaining > 0


def test_plan_single_tour_not_capped_when_it_finishes_before_the_limit():
    surplus = StationCandidate("SURP", "Surplus", BASE_LAT, BASE_LNG, "surplus", 5.0, 5, 5)
    deficit = StationCandidate("DEF", "Deficit", BASE_LAT, BASE_LNG, "deficit", -5.0, 5, 5)

    result = plan_single_tour([surplus, deficit], capacity=20, max_stops=45)

    assert result.capped is False
    assert len(result.stops) == 2


def test_max_stops_note_mentions_the_configured_value():
    note = max_stops_note(45)
    assert "45" in note
    assert "capped" in note.lower()


# ---- build_route_payload -------------------------------------------------------


def test_build_route_payload_second_truck_picks_up_what_first_left_stranded():
    # No deficit exists at all, so each truck fills to capacity at one surplus
    # station and then immediately has nowhere to deliver (reachable becomes
    # empty once full) -- truck 1 never even reaches the second surplus station.
    # This isolates the mechanic build_route_payload relies on: candidates are
    # shared, mutable state, so truck 2 sees exactly what truck 1 left behind.
    stations = {
        "surp1": _record(BASE_LAT, BASE_LNG, 15.0, "Surplus 1"),
        "surp2": _record(BASE_LAT + 0.01, BASE_LNG, 15.0, "Surplus 2"),
    }
    payload = _payload(stations)

    result = build_route_payload(payload, period=None, capacity=10, n_trucks=2)

    assert result["n_trucks_used"] == 2
    assert result["n_surplus_flagged"] == 2
    assert result["trucks"][0]["stops"][0]["station_id"] == "surp1"
    assert result["trucks"][1]["stops"][0]["station_id"] == "surp2"


def test_build_route_payload_extra_trucks_are_a_noop_once_demand_is_cleared():
    # One truck with a large enough capacity clears this scenario entirely in a
    # single tour (pick up all 30, drop 10, nothing left reachable) -- a second
    # requested truck finds no surplus left and contributes nothing.
    stations = {
        "surp": _record(BASE_LAT, BASE_LNG, 30.0, "Surplus"),
        "def": _record(BASE_LAT + 0.001, BASE_LNG, -10.0, "Deficit"),
    }
    payload = _payload(stations)

    result = build_route_payload(payload, period=None, capacity=30, n_trucks=2)

    assert result["n_trucks_requested"] == 2
    assert result["n_trucks_used"] == 1
    assert result["n_deficit_serviced"] == 1
    assert result["n_surplus_serviced"] == 1


def test_build_route_payload_includes_depot_assumption_note():
    payload = _payload({"s": _record(BASE_LAT, BASE_LNG, 5.0)})
    result = build_route_payload(payload, period=None, capacity=20, n_trucks=1)
    assert result["depot_assumption"] == DEPOT_ASSUMPTION_NOTE
    assert "stopgap" in result["depot_assumption"]
    assert "No real depot" in result["depot_assumption"]


def test_build_route_payload_surfaces_capped_flag_when_truck_hits_max_stops():
    # Effectively unlimited work available at zero distance -- without a cap
    # this would run forever. any_truck_capped=True is the run-level signal
    # that some unserviced stations may just be untried, not unreachable.
    stations = {
        "surp": _record(BASE_LAT, BASE_LNG, 1000.0, "Surplus"),
        "def": _record(BASE_LAT, BASE_LNG, -1000.0, "Deficit"),
    }
    payload = _payload(stations)

    result = build_route_payload(payload, period=None, capacity=5, n_trucks=1, max_stops=10)

    assert result["max_stops"] == 10
    assert "10" in result["max_stops_note"]
    assert result["any_truck_capped"] is True
    assert result["trucks"][0]["capped"] is True
    assert len(result["trucks"][0]["stops"]) == 10

"""Tests for pipeline/briefings.py (step 1: facts records only, no model calls)."""

import pytest

from pipeline.briefings import (
    build_all_facts,
    curve_strength,
    day_type_flow,
    facts_hash,
    flow_peaks,
    hour_gap,
    hour_label,
    rank_unusable,
    reliability_facts,
    reliability_window_label,
    station_facts,
    trip_window_label,
    typology_matches_peak,
    weekend_differs,
)
from pipeline.reliability import MIN_OBSERVATIONS


def _curve(**hours):
    """A flat 24-hour curve with the given hours set, e.g. _curve(h8=1.2, h17=-0.9)."""
    curve = [0.0] * 24
    for key, value in hours.items():
        curve[int(key[1:])] = value
    return curve


CONTEXT = {
    "nycha_dist_m": 748, "school_dist_m": 120, "subway_dist_m": 307,
    "nycha_nearest": "BORINQUEN PLAZA II", "school_nearest": "P.S. 196 Ten Eyck",
    "subway_nearest": "Morgan Av", "near_nycha": 0, "near_school": 1, "transit_gap": 0,
}

RELIABILITY = {
    "window": {"start": "2026-07-13T18:22:42+00:00", "end": "2026-10-27T23:32:26+00:00"},
    "system": {"unusable_rate": 0.13020994670948274},
    "stations": {
        "A": {"unusable_rate": 0.40, "n_observations": 500, "n_unusable": 200},
        "B": {"unusable_rate": 0.10, "n_observations": 500, "n_unusable": 50},
        "C": {"unusable_rate": 0.10, "n_observations": 500, "n_unusable": 50},
        "D": {"unusable_rate": 0.05, "n_observations": 500, "n_unusable": 25},
        "THIN": {"unusable_rate": 0.90, "n_observations": MIN_OBSERVATIONS - 1, "n_unusable": 17},
    },
}

WINDOWS = {"trip": "Sep 2025 – Aug 2026", "reliability": "Jul 13 – Oct 27, 2026"}


def _station(cluster=1, weekday=None, weekend=None):
    return {
        "name": "Morgan Ave & Test St",
        "cluster": cluster,
        "cluster_name": "Commuter core (fills AM, drains PM)",
        "weekday": weekday or _curve(h8=1.24, h17=-0.86),
        "weekend": weekend or _curve(h12=1.5, h19=-1.1),
        "context": CONTEXT,
    }


# --- small helpers ---------------------------------------------------------

@pytest.mark.parametrize("hour,label", [(0, "12 AM"), (8, "8 AM"), (11, "11 AM"), (12, "12 PM"), (17, "5 PM"), (23, "11 PM")])
def test_hour_label(hour, label):
    assert hour_label(hour) == label


def test_curve_strength_is_l2_norm():
    assert curve_strength(_curve(h0=3.0, h1=4.0)) == pytest.approx(5.0)


# --- flow peaks and the sign convention ------------------------------------

def test_flow_peaks_positive_is_fill_negative_is_drain():
    """flows.py convention: net = arrivals - departures, so positive means filling."""
    peaks = flow_peaks(_curve(h8=1.24, h17=-0.86))
    assert peaks == {
        "fill_peak_hour": "8 AM", "fill_bikes_per_day": 1.2,
        "drain_peak_hour": "5 PM", "drain_bikes_per_day": 0.9,
    }


def test_flow_peaks_side_that_never_happens_is_none():
    """A curve that only fills has no drain peak -- not a 'drain of 0.0'."""
    peaks = flow_peaks(_curve(h8=1.0, h9=0.5))
    assert peaks["drain_peak_hour"] is None and peaks["drain_bikes_per_day"] is None
    assert peaks["fill_peak_hour"] == "8 AM"


def test_flow_peaks_that_round_to_zero_are_none():
    peaks = flow_peaks(_curve(h8=1.0, h17=-0.04))
    assert peaks["drain_peak_hour"] is None


def test_low_signal_day_type_has_no_peaks_at_all():
    """Nothing for the model to describe -- no keys it could fill in from noise."""
    assert day_type_flow(_curve(h8=5.0), low_signal=True) == {"low_signal": True}


# --- weekend comparison ----------------------------------------------------

@pytest.mark.parametrize("a,b,gap", [(8, 8, 0), (8, 10, 2), (17, 8, 9), (23, 1, 2), (0, 12, 12)])
def test_hour_gap_wraps_around_the_clock(a, b, gap):
    assert hour_gap(a, b) == gap


def test_weekend_differs_when_fill_peak_moves_two_hours():
    weekday = _curve(h8=1.0, h17=-1.0)
    weekend = _curve(h10=1.0, h17=-1.0)
    assert weekend_differs(weekday, weekend, False, False) is True


def test_weekend_differs_when_only_drain_peak_moves():
    weekday = _curve(h8=1.0, h17=-1.0)
    weekend = _curve(h8=1.0, h21=-1.0)
    assert weekend_differs(weekday, weekend, False, False) is True


def test_one_hour_move_is_jitter_not_a_difference():
    weekday = _curve(h8=1.0, h17=-1.0)
    weekend = _curve(h9=1.0, h18=-1.0)
    assert weekend_differs(weekday, weekend, False, False) is False


def test_weekend_same_when_peak_hours_match_even_if_sizes_differ():
    weekday = _curve(h8=1.0, h17=-1.0)
    weekend = _curve(h8=3.0, h17=-0.2)
    assert weekend_differs(weekday, weekend, False, False) is False


def test_missing_peak_on_one_side_is_not_compared():
    """Weekend never drains: only the fill peaks are compared, and they match."""
    weekday = _curve(h8=1.0, h17=-1.0)
    weekend = _curve(h8=1.0)
    assert weekend_differs(weekday, weekend, False, False) is False


def test_weekend_comparison_is_none_when_either_side_is_low_signal():
    curve = _curve(h8=1.0)
    assert weekend_differs(curve, curve, False, True) is None
    assert weekend_differs(curve, curve, True, False) is None


# --- typology vs. the station's own peaks ----------------------------------

COMMUTER = "Commuter core (fills AM, drains PM)"
FEEDER = "Residential feeder (drains AM, fills PM)"


def test_commuter_core_matches_when_fill_is_morning_and_drain_after_noon():
    assert typology_matches_peak(COMMUTER, _curve(h8=1.0, h17=-1.0), False) is True


def test_commuter_core_contradicted_by_afternoon_fill_peak():
    """The 'fills most at 2 PM' case that must never be called a commuter core outright."""
    assert typology_matches_peak(COMMUTER, _curve(h14=1.1, h15=-0.4), False) is False


def test_commuter_core_contradicted_by_night_fill_peak():
    """1 AM is 'AM' on a clock, not the morning a reader assumes."""
    assert typology_matches_peak(COMMUTER, _curve(h1=1.0, h17=-1.0), False) is False


def test_residential_feeder_sides_are_reversed():
    assert typology_matches_peak(FEEDER, _curve(h8=-1.0, h18=1.0), False) is True
    assert typology_matches_peak(FEEDER, _curve(h8=1.0, h18=-1.0), False) is False


def test_missing_peak_cannot_match_a_directional_name():
    """A commuter core that never drains does not support 'drains PM'."""
    assert typology_matches_peak(COMMUTER, _curve(h8=1.0), False) is False


def test_typology_match_is_none_for_low_signal_and_undirected_names():
    assert typology_matches_peak(COMMUTER, _curve(h8=1.0, h17=-1.0), True) is None
    assert typology_matches_peak("Low signal (excluded from clustering)", _curve(h8=1.0, h17=-1.0), False) is None


def test_weekend_low_signal_uses_typology_cutoff():
    """Weekend curve with L2 norm below 1.0 bikes/day gets no weekend peaks."""
    facts = station_facts("A", _station(weekend=_curve(h12=0.6, h19=-0.6)), RELIABILITY, rank_unusable(RELIABILITY["stations"]), WINDOWS)
    assert facts["weekend_flow"] == {"low_signal": True}
    assert facts["weekend_differs"] is None


def test_weekday_low_signal_follows_the_typology_cluster():
    """The typology's own cluster -1 decides weekday low-signal, not a recomputation."""
    facts = station_facts("A", _station(cluster=-1), RELIABILITY, rank_unusable(RELIABILITY["stations"]), WINDOWS)
    assert facts["weekday_flow"] == {"low_signal": True}


# --- reliability ranks and statuses ----------------------------------------

def test_rank_unusable_worst_is_one_and_ties_share_a_rank():
    ranks = rank_unusable(RELIABILITY["stations"])
    assert ranks == {"A": 1, "B": 2, "C": 2, "D": 4}


def test_rank_unusable_excludes_thin_stations():
    assert "THIN" not in rank_unusable(RELIABILITY["stations"])


def test_reliability_facts_ok():
    ranks = rank_unusable(RELIABILITY["stations"])
    assert reliability_facts("B", RELIABILITY, ranks) == {
        "status": "ok", "unusable_pct": 10.0, "rank": 2, "n_ranked": 4, "system_unusable_pct": 13.0,
    }


def test_reliability_facts_not_rankable():
    ranks = rank_unusable(RELIABILITY["stations"])
    assert reliability_facts("THIN", RELIABILITY, ranks) == {"status": "not_rankable", "min_observations": MIN_OBSERVATIONS}


def test_reliability_facts_no_data_carries_no_numbers():
    """A station missing from reliability.json gets a status, nothing to guess from."""
    assert reliability_facts("MISSING", RELIABILITY, rank_unusable(RELIABILITY["stations"])) == {"status": "no_data"}


# --- context and windows ---------------------------------------------------

def test_context_names_only_appear_when_near():
    facts = station_facts("A", _station(), RELIABILITY, rank_unusable(RELIABILITY["stations"]), WINDOWS)
    context = facts["context"]
    assert context["near_school"] is True and context["school_name"] == "P.S. 196 Ten Eyck"
    assert context["near_nycha"] is False and context["nycha_name"] is None
    assert context["nycha_threshold_m"] == 300 and context["transit_gap_threshold_m"] == 800


def test_trip_window_label():
    months = ["2026-08", "2025-09", "2026-01"]  # order must not matter
    assert trip_window_label(months) == "Sep 2025 – Aug 2026"


def test_reliability_window_label_same_year():
    assert reliability_window_label(RELIABILITY["window"]) == "Jul 13 – Oct 27, 2026"


def test_reliability_window_label_across_years():
    window = {"start": "2026-12-20T00:00:00+00:00", "end": "2027-01-05T00:00:00+00:00"}
    assert reliability_window_label(window) == "Dec 20, 2026 – Jan 5, 2027"


# --- full record and hash --------------------------------------------------

def test_facts_hash_ignores_key_order_but_not_values():
    facts = {"b": 1, "a": {"y": 2, "x": 3}}
    reordered = {"a": {"x": 3, "y": 2}, "b": 1}
    changed = {"a": {"x": 3, "y": 2}, "b": 2}
    assert facts_hash(facts) == facts_hash(reordered)
    assert facts_hash(facts) != facts_hash(changed)


def test_build_all_facts_covers_every_flows_station_and_reads_windows_from_data():
    flows = {"stations": {"A": _station(), "MISSING": _station()}}
    manifest = {"months": ["2025-09", "2026-08"]}
    all_facts = build_all_facts(flows, RELIABILITY, manifest)
    assert set(all_facts) == {"A", "MISSING"}
    assert all_facts["A"]["trip_window"] == "Sep 2025 – Aug 2026"
    assert all_facts["A"]["reliability_window"] == "Jul 13 – Oct 27, 2026"
    assert all_facts["MISSING"]["reliability"] == {"status": "no_data"}


def test_build_all_facts_skips_excluded_ids_exactly():
    """Exclusion is by exact id: a similar-looking id is still briefed."""
    flows = {"stations": {"A": _station(), "DEPOT": _station(), "DEPOT2": _station()}}
    all_facts = build_all_facts(flows, RELIABILITY, {"months": ["2025-09"]}, excluded={"DEPOT": "operator facility"})
    assert set(all_facts) == {"A", "DEPOT2"}


def test_full_record_carries_the_typology_flag():
    facts = station_facts("A", _station(weekday=_curve(h14=1.1, h15=-0.4)), RELIABILITY, rank_unusable(RELIABILITY["stations"]), WINDOWS)
    assert facts["typology"] == COMMUTER
    assert facts["typology_matches_peak"] is False

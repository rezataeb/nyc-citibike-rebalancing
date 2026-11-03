"""Tests for pipeline/guardrails.py -- including negative controls with injected fake numbers."""

import pytest

from pipeline.guardrails import (
    check_briefing,
    check_day_type_scope,
    check_numbers,
    check_style,
    check_typology_hedge,
    extract_numbers,
    number_is_allowed,
)

FACTS = {
    "station_id": "5980.10",
    "name": "E 17 St & Broadway",
    "typology": "Commuter core (fills AM, drains PM)",
    "typology_matches_peak": True,
    "weekday_flow": {"low_signal": False, "fill_peak_hour": "8 AM", "fill_bikes_per_day": 6.3,
                     "drain_peak_hour": "5 PM", "drain_bikes_per_day": 4.1},
    "weekend_flow": {"low_signal": False, "fill_peak_hour": "1 PM", "fill_bikes_per_day": 2.2,
                     "drain_peak_hour": "6 PM", "drain_bikes_per_day": 1.9},
    "weekend_differs": True,
    "reliability": {"status": "ok", "unusable_pct": 13.0, "rank": 412, "n_ranked": 2507, "system_unusable_pct": 13.0},
    "context": {"nearest_subway": "14 St-Union Sq", "subway_distance_m": 230, "near_nycha": False, "nycha_name": None,
                "near_school": True, "school_name": "P.S. 40", "transit_gap": False,
                "nycha_threshold_m": 300, "school_threshold_m": 300, "transit_gap_threshold_m": 800},
    "trip_window": "Sep 2025 – Aug 2026",
    "reliability_window": "Jul 13 – Oct 27, 2026",
}

GOOD = (
    "On weekdays, E 17 St & Broadway fills most at 8 AM (about 6.3 bikes a day) and drains most at 5 PM; "
    "on weekends its peaks move to 1 PM and 6 PM. "
    "It was unusable in 13.0% of observations, ranking 412th of 2,507 stations, the same as the system rate of 13.0%. "
    "The nearest subway, 14 St-Union Sq, is 230 m away, and P.S. 40 is within 300 m."
)


# --- extraction and normalization ------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("13.0%", [13.0]),
    ("2,507 stations", [2507.0]),
    ("at 8 AM", ["8 AM"]),
    ("at 8 a.m.", ["8 AM"]),
    ("at 8am", ["8 AM"]),
    ("at 8:00 AM", ["8 AM"]),
    ("at 5 PM", ["5 PM"]),
    ("around noon", ["12 PM"]),
    ("ranked 12th", [12.0]),
    ("ranked #3", [3.0]),
    ("two stations", [2.0]),
    ("the third busiest", [3.0]),
])
def test_extract_numbers_normalizes_each_form(text, expected):
    assert extract_numbers(text) == expected


def test_one_and_first_are_words_not_numbers():
    assert extract_numbers("this one is the first stop") == []


def test_time_is_one_token_not_a_time_plus_a_stray_number():
    assert extract_numbers("8 AM") == ["8 AM"]


def test_facts_strings_contribute_numbers_and_times():
    """Station names, window labels and hour labels are allowed sources too."""
    assert check_numbers("E 17 St, Sep 2025 to Aug 2026, Oct 27", FACTS) == []


# --- the number check: positives -------------------------------------------

def test_good_briefing_passes_the_number_check():
    assert check_numbers(GOOD, FACTS) == []


def test_percent_and_decimal_forms_match():
    assert check_numbers("13% of observations", FACTS) == []


def test_text_may_round_a_fact_to_fewer_places():
    """'about 6 bikes' is derived from 6.3; that's allowed."""
    assert number_is_allowed(6.0, {6.3})


# --- negative controls: injected fake numbers must be caught ----------------

@pytest.mark.parametrize("fake", [
    "It was unusable in 14.0% of observations.",   # wrong rate
    "It ranks 413th of 2,507 stations.",           # off-by-one rank
    "It ranks 412th of 2,600 stations.",           # wrong denominator
    "On weekdays it fills most at 9 AM.",          # wrong hour
    "The subway is 250 m away.",                   # wrong distance
    "About 6.4 bikes arrive a day.",               # more precision than the fact
    "Three new docks were added.",                 # spelled-out invented number
])
def test_injected_fake_number_is_rejected(fake):
    assert check_numbers(fake, FACTS) != []


def test_rounding_only_goes_one_way():
    """6.3 in facts allows '6'; 6 in facts does not allow '6.3'."""
    assert not number_is_allowed(6.3, {6.0})


# --- day-type scope ----------------------------------------------------------

def test_time_claim_without_day_type_fails():
    assert check_day_type_scope("It fills most at 8 AM.") != []


def test_time_of_day_word_without_day_type_fails():
    assert check_day_type_scope("It drains in the evening.") != []


def test_time_claim_with_day_type_passes():
    assert check_day_type_scope("On weekday mornings it fills most at 8 AM. On weekends it peaks at 1 PM.") == []


def test_sentence_without_any_time_needs_no_day_type():
    assert check_day_type_scope("It was unusable in 13.0% of observations.") == []


# --- typology hedge ----------------------------------------------------------

def test_contradicting_station_may_not_be_called_its_type():
    facts = {**FACTS, "typology_matches_peak": False}
    assert check_typology_hedge("It is a commuter core station.", facts) != []


def test_contradicting_station_may_be_grouped_with_its_type():
    facts = {**FACTS, "typology_matches_peak": False}
    assert check_typology_hedge("It is grouped with commuter-core stations.", facts) == []


def test_matching_station_may_be_called_its_type():
    assert check_typology_hedge("It is a commuter core station.", FACTS) == []


# --- style -------------------------------------------------------------------

def test_causal_wording_fails():
    assert check_style("It empties because of the subway.") != []


def test_too_many_sentences_fails():
    assert check_style("One. Two. Three. Four.") != []


def test_a_m_does_not_split_a_sentence():
    assert check_style("On weekdays it fills at 8 a.m. and drains at 5 p.m. On weekends it is quiet.") == []


# --- all together ------------------------------------------------------------

def test_good_briefing_passes_every_check():
    assert check_briefing(GOOD, FACTS) == []


def test_bad_briefing_reports_every_problem():
    bad = "It fills at 9 AM because offices open. It is unusable 20% of the time."
    problems = check_briefing(bad, FACTS)
    assert any("9 AM" in p for p in problems)
    assert any("20" in p for p in problems)
    assert any("weekday/weekend" in p for p in problems)
    assert any("causal" in p for p in problems)


# --- kinds must match ----------------------------------------------------------

def test_percentage_cannot_borrow_a_number_from_a_name():
    """'14' is in the subway name '14 St-Union Sq'; '14.0%' must still fail."""
    assert check_numbers("It was unusable 14.0% of the time.", FACTS) != []


def test_distance_cannot_borrow_a_non_distance_number():
    """13.0 is a percentage fact, not a distance."""
    assert check_numbers("The subway is 13 m away.", FACTS) != []


def test_threshold_distance_is_a_distance_fact():
    assert check_numbers("P.S. 40 is within 300 meters.", FACTS) == []


def test_percent_word_is_read_as_a_percentage():
    assert extract_numbers("13.0 percent") == [13.0]
    assert check_numbers("13.0 percent of observations", FACTS) == []


# --- edge cases found by running the template over every real station ----------

def test_comma_ordinal_is_one_number():
    assert extract_numbers("ranked 1,174th") == [1174.0]


def test_initials_and_street_abbreviations_do_not_split_sentences():
    from pipeline.guardrails import split_sentences

    assert len(split_sentences("It is near St. Nicholas Ave and Ralph A. Fabrizio School. It is busy.")) == 2


def test_time_words_inside_other_words_are_not_time_claims():
    assert check_day_type_scope("It is near P.S. 235 Janice Marie Knight School.") == []


def test_time_words_inside_a_fact_name_are_not_time_claims():
    facts = {**FACTS, "context": {**FACTS["context"], "school_name": "Manhattan Comprehensive Night and Day High School"}}
    assert check_day_type_scope("It is near Manhattan Comprehensive Night and Day High School.", facts) == []


def test_number_glued_to_m_inside_a_name_is_allowed_when_the_name_is_a_fact():
    facts = {**FACTS, "context": {**FACTS["context"], "school_name": "P.S. 184m Shuang Wen"}}
    assert check_numbers("It is near P.S. 184m Shuang Wen.", facts) == []


# --- found in the first pilot -----------------------------------------------------

def test_mangled_station_name_is_rejected_even_when_its_numbers_are_real():
    facts = {**FACTS, "name": "59 St & 4 Ave"}
    from pipeline.guardrails import check_name

    assert check_name("59 4 Ave is grouped with commuter core stations.", facts) != []
    assert check_name("59 St & 4 Ave is grouped with commuter core stations.", facts) == []


def test_weekend_hours_only_when_weekend_differs():
    from pipeline.guardrails import check_weekend_scope

    same = {**FACTS, "weekend_differs": False}
    assert check_weekend_scope("On weekends it fills most at 3 PM.", same) != []
    assert check_weekend_scope("On weekends it fills most at 3 PM.", FACTS) == []  # FACTS: weekend_differs True


def test_saying_weekend_trips_are_few_has_no_hours_so_it_is_not_a_weekend_claim():
    from pipeline.guardrails import check_weekend_scope

    assert check_weekend_scope("It has too few weekday or weekend trips to show a pattern.", {**FACTS, "weekend_differs": None}) == []


def test_above_or_below_the_system_rate_must_be_the_right_way_round():
    from pipeline.guardrails import check_directions

    low = {**FACTS, "reliability": {**FACTS["reliability"], "unusable_pct": 4.7}}
    assert check_directions("It was unusable in 4.7% of observations, above the system rate of 13.0%.", low) != []
    assert check_directions("It was unusable in 4.7% of observations, below the system rate of 13.0%.", low) == []


def test_swapped_fill_and_drain_hours_are_rejected():
    """Both hours are real facts, so only a direction check can catch the swap."""
    from pipeline.guardrails import check_directions

    swapped = "On weekdays it fills most at 5 PM and drains most at 8 AM."
    assert check_directions(swapped, FACTS) != []
    assert check_directions("On weekdays it fills most at 8 AM and drains most at 5 PM.", FACTS) == []

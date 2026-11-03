"""Tests for pipeline/station_ids.py."""

import pandas as pd

from pipeline.station_ids import SUFFIX_ID_MERGES, canonical_id_map, canonicalize_columns, repair_daily


def test_dropped_trailing_zero_merges_into_its_twin():
    assert canonical_id_map(["5980.10", "5980.1"]) == {"5980.1": "5980.10"}


def test_one_decimal_id_without_a_twin_is_left_alone():
    """3593.1 really is '3593.1' in the live feed -- padding it would break the join."""
    assert canonical_id_map(["3593.1", "4000.20"]) == {}


def test_two_decimal_ids_are_never_touched():
    assert canonical_id_map(["5980.10", "5980.11"]) == {}


def test_suffix_ids_merge_only_from_the_approved_list_and_only_with_their_base():
    assert canonical_id_map(["5308.04_", "5308.04"]) == {"5308.04_": "5308.04"}
    assert canonical_id_map(["5308.04_"]) == {}  # base absent: nothing to merge into
    assert canonical_id_map(["5303.06_", "5303.06"]) == {}  # live in its own right
    assert canonical_id_map(["5348.06_old", "5348.06"]) == {}  # old location 290 m away
    assert "5303.06_" not in SUFFIX_ID_MERGES and "5348.06_old" not in SUFFIX_ID_MERGES


def test_canonicalize_columns_uses_ids_from_both_columns():
    """The twin may only appear as an end station -- still a twin."""
    trips = pd.DataFrame({"start_station_id": ["5980.1", None], "end_station_id": ["A", "5980.10"]})
    out = canonicalize_columns(trips, ["start_station_id", "end_station_id"])
    assert out["start_station_id"].tolist()[0] == "5980.10"
    assert pd.isna(out["start_station_id"].tolist()[1])


def _daily(rows):
    frame = pd.DataFrame(rows, columns=["station_id", "station_name", "date", "hour", "lat", "lng", "arrivals_count", "departures_count", "net"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def test_repair_sums_merged_rows_and_keeps_the_canonical_metadata():
    daily = _daily([
        ("5980.10", "E 17 St & Broadway", "2026-01-05", 8, 40.7, -73.9, 10, 3, 7),
        ("5980.1", "E 17 St & Broadway", "2026-01-05", 8, 40.8, -73.8, 2, 1, 1),
    ])
    out = repair_daily(daily, ["2026-01"])
    assert len(out) == 1
    row = out.iloc[0]
    assert (row.station_id, row.arrivals_count, row.departures_count, row.net) == ("5980.10", 12, 4, 8)
    assert (row.lat, row.lng) == (40.7, -73.9)


def test_repair_drops_rows_outside_the_window():
    """The Jun 30, 2025 leak: a row dated outside TARGET_MONTHS is removed."""
    daily = _daily([
        ("A", "A St", "2025-06-30", 8, 40.7, -73.9, 1, 0, 1),
        ("A", "A St", "2025-09-01", 8, 40.7, -73.9, 1, 0, 1),
    ])
    out = repair_daily(daily, ["2025-09"])
    assert out["date"].dt.strftime("%Y-%m-%d").tolist() == ["2025-09-01"]


def test_repair_loses_no_trips():
    daily = _daily([
        ("5980.10", "X", "2026-01-05", 8, 40.7, -73.9, 10, 3, 7),
        ("5980.1", "X", "2026-01-05", 9, 40.7, -73.9, 2, 1, 1),
        ("B", "B", "2026-01-05", 8, 40.6, -73.9, 5, 5, 0),
    ])
    out = repair_daily(daily, ["2026-01"])
    assert out["arrivals_count"].sum() == 17 and out["departures_count"].sum() == 9

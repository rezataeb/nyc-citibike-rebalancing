"""Tests for pipeline/qc.py."""

import pandas as pd
import pytest

from pipeline.download import RAW_DATA_DIR, load_trips
from pipeline.qc import run_qc

BASE_ROW = {
    "ride_id": "r0",
    "rideable_type": "classic_bike",
    "start_station_name": "A St",
    "start_station_id": "1",
    "end_station_name": "B St",
    "end_station_id": "2",
    "start_lat": 40.0,
    "start_lng": -74.0,
    "end_lat": 40.1,
    "end_lng": -74.1,
    "member_casual": "member",
}


def make_trip(started_at: str, ended_at: str, **overrides) -> dict:
    row = {**BASE_ROW, "started_at": started_at, "ended_at": ended_at}
    row.update(overrides)
    return row


def test_drops_short_and_long_trips_separately():
    trips = pd.DataFrame(
        [
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:00:30", ride_id="short"),  # 30s
            make_trip("2026-04-01 00:00:00", "2026-04-01 05:00:00", ride_id="long"),  # 5h
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="ok"),  # 10m
        ]
    )

    clean, report = run_qc(trips)

    assert report.rows_in == 3
    assert report.dropped_short == 1
    assert report.dropped_long == 1
    assert report.rows_out == 1
    assert list(clean["ride_id"]) == ["ok"]


def test_missing_end_station_is_kept_and_flagged_not_dropped():
    trips = pd.DataFrame(
        [
            make_trip(
                "2026-04-01 00:00:00",
                "2026-04-01 00:10:00",
                ride_id="no_end",
                end_station_id=float("nan"),
                end_station_name=float("nan"),
            ),
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="has_end"),
        ]
    )

    clean, report = run_qc(trips)

    assert report.rows_out == 2  # neither row dropped
    assert report.missing_end_station == 1
    flags = clean.set_index("ride_id")["has_valid_arrival_station"]
    assert not flags["no_end"]
    assert flags["has_end"]


def test_missing_start_station_is_kept_and_flagged_not_dropped():
    trips = pd.DataFrame(
        [
            make_trip(
                "2026-04-01 00:00:00",
                "2026-04-01 00:10:00",
                ride_id="no_start",
                start_station_id=float("nan"),
                start_station_name=float("nan"),
            ),
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="has_start"),
        ]
    )

    clean, report = run_qc(trips)

    assert report.rows_out == 2  # neither row dropped
    assert report.missing_start_station == 1
    flags = clean.set_index("ride_id")["has_valid_departure_station"]
    assert not flags["no_start"]
    assert flags["has_start"]


def test_drops_trips_with_implausible_location():
    # Real contamination found in the wild (Session 35): "LA Metro Demo"
    # stations at real Los Angeles coordinates, and a warehouse/logistics
    # record at (0, 0) -- both far outside any reasonable NYC-area box.
    trips = pd.DataFrame(
        [
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="la_demo",
                      start_lat=34.02621, start_lng=-118.25158, end_lat=34.02618, end_lng=-118.2515),
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="null_island",
                      start_lat=0.0, start_lng=0.0, end_lat=0.0, end_lng=0.0),
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="ok"),
        ]
    )

    clean, report = run_qc(trips)

    assert report.dropped_bad_location == 2
    assert report.rows_out == 1
    assert list(clean["ride_id"]) == ["ok"]


def test_implausible_location_check_applies_to_either_end():
    # A trip with a real NYC start but a bogus end (or vice versa) must
    # still be dropped -- checking only one side would let a
    # partially-contaminated trip through.
    trips = pd.DataFrame(
        [
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="bad_end_only",
                      end_lat=34.02618, end_lng=-118.2515),
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="bad_start_only",
                      start_lat=34.02621, start_lng=-118.25158),
        ]
    )

    clean, report = run_qc(trips)

    assert report.dropped_bad_location == 2
    assert report.rows_out == 0


def test_missing_coordinates_are_not_treated_as_bad_location():
    # A NaN coordinate is a separate, already-handled gap (has_valid_*_station
    # below) -- this rule's job is rejecting an implausible real value, not
    # standing in for missing-data handling. Dropping these here too would
    # double-count the same gap under two different QC reasons.
    trips = pd.DataFrame(
        [
            make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00", ride_id="no_end_coords",
                      end_station_id=float("nan"), end_station_name=float("nan"),
                      end_lat=float("nan"), end_lng=float("nan")),
        ]
    )

    clean, report = run_qc(trips)

    assert report.dropped_bad_location == 0
    assert report.rows_out == 1


def test_report_summary_documents_departure_arrival_imbalance():
    trips = pd.DataFrame([make_trip("2026-04-01 00:00:00", "2026-04-01 00:10:00")])
    _, report = run_qc(trips)
    assert "will NOT balance by construction" in report.summary()


@pytest.mark.skipif(
    not (RAW_DATA_DIR / "202604-citibike-tripdata.zip").exists(),
    reason="requires 202604 trip data already downloaded to data/raw/",
)
def test_qc_report_matches_known_202604_counts():
    """Regression check against the real file's known-good counts (see PROGRESS.md).

    missing_end_station is 8,750, not the raw 10,498 -- 1,748 of the
    missing-end-station rows are ALSO over-4h trips (a bike ridden for
    hours that also never got redocked) and get removed by the duration
    rule first, so they don't reach the flagging step.

    missing_start_station is 1,930, not the raw 1,939 -- 9 of those rows
    are also dropped by the duration rule first, same overlap logic as
    the end-station case above.
    """
    trips = load_trips(RAW_DATA_DIR / "202604-citibike-tripdata.zip")
    clean, report = run_qc(trips)

    assert report.rows_in == 3_860_371
    assert report.dropped_short == 0
    assert report.dropped_long == 3_611
    assert report.rows_out == report.rows_in - report.dropped_long - report.dropped_bad_location
    # dropped_bad_location's real count for April specifically hasn't been
    # verified (the known contamination was found via flows.json's full-year
    # roster, not confirmed against this one cached month) -- not asserted
    # to an unverified number, just checked that it's a real, non-negative
    # count and that it's consistent with the row-count equation above.
    assert report.dropped_bad_location >= 0
    assert report.missing_start_station == 1_930
    assert report.missing_end_station == 8_750

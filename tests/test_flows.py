"""Tests for pipeline/flows.py."""

import pandas as pd
import pytest

from pipeline.download import RAW_DATA_DIR, load_trips
from pipeline.flows import add_season, compute_daily_net_flow, compute_net_flow, compute_throughput, export_flows
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


def qc_clean(rows: list[dict]) -> pd.DataFrame:
    clean, _ = run_qc(pd.DataFrame(rows))
    return clean


def test_net_flow_counts_arrivals_and_departures_correctly():
    # station 1: one departure (Wed 2026-04-01, hour 8). station 2: one arrival (same trip).
    clean = qc_clean(
        [make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1")]
    )
    flows = compute_net_flow(clean)

    dep_row = flows[(flows["station_id"] == "1") & (flows["hour"] == 8)]
    arr_row = flows[(flows["station_id"] == "2") & (flows["hour"] == 8)]

    assert dep_row["net"].iloc[0] == -1
    assert arr_row["net"].iloc[0] == 1


def test_missing_end_station_counts_as_departure_only():
    clean = qc_clean(
        [
            make_trip(
                "2026-04-01 08:00:00",
                "2026-04-01 08:15:00",
                ride_id="no_end",
                end_station_id=float("nan"),
                end_station_name=float("nan"),
            )
        ]
    )
    flows = compute_net_flow(clean)

    assert set(flows["station_id"]) == {"1"}  # only the departure station appears
    assert flows["net"].iloc[0] == -1


def test_net_per_day_normalizes_by_distinct_dates():
    # two Wednesdays in April 2026 (weekday), one departure from station 1 on each.
    clean = qc_clean(
        [
            make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1"),
            make_trip("2026-04-08 08:00:00", "2026-04-08 08:15:00", ride_id="t2"),
        ]
    )
    flows = compute_net_flow(clean)
    dep_row = flows[(flows["station_id"] == "1") & (flows["hour"] == 8)]

    assert dep_row["n_days"].iloc[0] == 2
    assert dep_row["net"].iloc[0] == -2
    assert dep_row["net_per_day"].iloc[0] == -1.0


def test_throughput_sums_arrivals_and_departures_unsigned():
    # Same one-trip fixture as test_net_flow_counts_arrivals_and_departures_correctly,
    # but throughput must NOT net them against each other -- both the
    # departure station and the arrival station get a positive count of 1,
    # not -1/+1.
    clean = qc_clean(
        [make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1")]
    )
    throughput = compute_throughput(clean)

    dep_row = throughput[(throughput["station_id"] == "1") & (throughput["hour"] == 8)]
    arr_row = throughput[(throughput["station_id"] == "2") & (throughput["hour"] == 8)]

    assert dep_row["count"].iloc[0] == 1
    assert arr_row["count"].iloc[0] == 1


def test_throughput_missing_end_station_counts_as_departure_only():
    # Mirrors test_missing_end_station_counts_as_departure_only: a trip
    # missing its end station still contributes to departure throughput,
    # same asymmetric-validity handling compute_net_flow relies on.
    clean = qc_clean(
        [
            make_trip(
                "2026-04-01 08:00:00",
                "2026-04-01 08:15:00",
                ride_id="no_end",
                end_station_id=float("nan"),
                end_station_name=float("nan"),
            )
        ]
    )
    throughput = compute_throughput(clean)

    assert set(throughput["station_id"]) == {"1"}
    assert throughput["count"].iloc[0] == 1


def test_throughput_per_day_normalizes_by_distinct_dates():
    # Mirrors test_net_per_day_normalizes_by_distinct_dates: two departures
    # from station 1 on two distinct Wednesdays -> throughput_per_day == 1,
    # not the raw count of 2.
    clean = qc_clean(
        [
            make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1"),
            make_trip("2026-04-08 08:00:00", "2026-04-08 08:15:00", ride_id="t2"),
        ]
    )
    throughput = compute_throughput(clean)
    dep_row = throughput[(throughput["station_id"] == "1") & (throughput["hour"] == 8)]

    assert dep_row["n_days"].iloc[0] == 2
    assert dep_row["count"].iloc[0] == 2
    assert dep_row["throughput_per_day"].iloc[0] == 1.0


def test_daily_net_flow_counts_arrivals_and_departures_correctly():
    # Mirrors test_net_flow_counts_arrivals_and_departures_correctly, but
    # keyed by real calendar date, not a month label -- and no per-day
    # normalization is needed since each row already IS one specific date.
    clean = qc_clean(
        [make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1")]
    )
    daily = compute_daily_net_flow(clean)

    dep_row = daily[(daily["station_id"] == "1") & (daily["hour"] == 8)]
    arr_row = daily[(daily["station_id"] == "2") & (daily["hour"] == 8)]

    assert dep_row["date"].iloc[0] == pd.Timestamp("2026-04-01")
    assert dep_row["net"].iloc[0] == -1
    assert arr_row["net"].iloc[0] == 1


def test_daily_net_flow_missing_end_station_counts_as_departure_only():
    # Mirrors test_missing_end_station_counts_as_departure_only: a trip
    # missing its end station still contributes to the departure side,
    # same asymmetric-validity handling compute_net_flow relies on.
    clean = qc_clean(
        [
            make_trip(
                "2026-04-01 08:00:00",
                "2026-04-01 08:15:00",
                ride_id="no_end",
                end_station_id=float("nan"),
                end_station_name=float("nan"),
            )
        ]
    )
    daily = compute_daily_net_flow(clean)

    assert set(daily["station_id"]) == {"1"}
    assert daily["net"].iloc[0] == -1


def test_daily_net_flow_keeps_distinct_dates_separate():
    # The key behavioral difference from compute_net_flow: two departures
    # from the same station on two different Wednesdays must stay as TWO
    # separate rows here (one per real date), not get merged/averaged into
    # one normalized net_per_day value the way compute_net_flow's monthly
    # grain does. This is exactly what elasticity fitting needs -- real
    # per-day variance, not a single monthly average.
    clean = qc_clean(
        [
            make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1"),
            make_trip("2026-04-08 08:00:00", "2026-04-08 08:15:00", ride_id="t2"),
        ]
    )
    daily = compute_daily_net_flow(clean)
    dep_rows = daily[(daily["station_id"] == "1") & (daily["hour"] == 8)]

    assert len(dep_rows) == 2, "two distinct dates must produce two separate rows, not one merged/averaged row"
    assert set(dep_rows["date"]) == {pd.Timestamp("2026-04-01"), pd.Timestamp("2026-04-08")}
    assert (dep_rows["net"] == -1).all()


def test_add_season_maps_month_to_fixed_season():
    flows = pd.DataFrame({"month": ["2026-01", "2026-04", "2026-07", "2026-10"]})
    result = add_season(flows)
    assert list(result["season"]) == ["winter", "spring", "summer", "fall"]


def test_export_flows_with_single_month_only_populates_that_months_season():
    """With only April loaded, seasons{} must have exactly 'spring' -- no winter/
    summer/fall entries, no error, nothing assumed about months we don't have."""
    clean = qc_clean(
        [make_trip("2026-04-01 08:00:00", "2026-04-01 08:15:00", ride_id="t1")]
    )
    flows = compute_net_flow(clean)
    payload = export_flows(flows)

    assert payload["granularity"]["months"] == ["2026-04"]
    assert payload["granularity"]["seasons"] == ["spring"]

    station = payload["stations"]["1"]
    assert set(station["seasons"].keys()) == {"spring"}
    assert set(station["months"].keys()) == {"2026-04"}
    assert len(station["weekday"]) == 24
    assert len(station["weekend"]) == 24


@pytest.mark.skipif(
    not (RAW_DATA_DIR / "202604-citibike-tripdata.zip").exists(),
    reason="requires 202604 trip data already downloaded to data/raw/",
)
def test_export_flows_on_real_202604_data():
    """A few late-April trips end after midnight on May 1, so '2026-05' legitimately
    appears too -- that's real spillover, not a bug (see flows.py docstring)."""
    trips = load_trips(RAW_DATA_DIR / "202604-citibike-tripdata.zip")
    clean, _ = run_qc(trips)
    flows = compute_net_flow(clean)
    payload = export_flows(flows)

    assert payload["granularity"]["months"] == ["2026-04", "2026-05"]
    assert payload["granularity"]["seasons"] == ["spring"]  # April and May are both spring
    assert len(payload["stations"]) > 0
    assert flows["net_per_day"].isna().sum() == 0  # no unnormalized (NaN) buckets

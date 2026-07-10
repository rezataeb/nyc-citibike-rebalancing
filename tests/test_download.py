"""Tests for pipeline/download.py."""

import pytest

from pipeline.download import RAW_DATA_DIR, build_filename, download_month, load_trips


def test_build_filename_accepts_dashed_or_plain_year_month():
    assert build_filename("2026-04") == "202604-citibike-tripdata.zip"
    assert build_filename("202604") == "202604-citibike-tripdata.zip"


def test_download_month_uses_cache_without_hitting_network(tmp_path, monkeypatch):
    """If the zip is already on disk, download_month must not call requests.get."""
    cached = tmp_path / "202601-citibike-tripdata.zip"
    cached.write_bytes(b"fake zip contents")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("download_month hit the network despite a cached file")

    monkeypatch.setattr("pipeline.download.requests.get", fail_if_called)

    result = download_month("2026-01", dest_dir=tmp_path)
    assert result == cached


@pytest.mark.skipif(
    not (RAW_DATA_DIR / "202604-citibike-tripdata.zip").exists(),
    reason="requires 202604 trip data already downloaded to data/raw/",
)
def test_load_trips_returns_expected_columns():
    zip_path = RAW_DATA_DIR / "202604-citibike-tripdata.zip"
    trips = load_trips(zip_path)

    expected_columns = {
        "ride_id",
        "rideable_type",
        "started_at",
        "ended_at",
        "start_station_name",
        "start_station_id",
        "end_station_name",
        "end_station_id",
        "start_lat",
        "start_lng",
        "end_lat",
        "end_lng",
        "member_casual",
    }
    assert expected_columns.issubset(trips.columns)
    assert len(trips) > 0

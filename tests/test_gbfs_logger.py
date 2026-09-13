"""Tests for pipeline/gbfs_logger.py."""

import json
from datetime import date

import pandas as pd

from pipeline.gbfs_logger import (
    append_snapshot,
    build_live_status_payload,
    current_log_path,
    export_live_status,
    fetch_station_id_crosswalk,
    log_snapshot,
    parse_snapshot,
)


def test_current_log_path_is_one_file_per_utc_day(tmp_path):
    """Session 71: replaces the single ever-growing snapshots.csv, which hit
    GitHub's 100MB per-file push limit. today is injected, not read from the
    real clock, so this is deterministic."""
    path = current_log_path(log_dir=tmp_path, today=date(2026, 9, 14))
    assert path == tmp_path / "snapshots_2026-09-14.csv"


def test_current_log_path_changes_with_the_day(tmp_path):
    """Two different days must resolve to two different files -- that's the
    entire point of rotating."""
    day1 = current_log_path(log_dir=tmp_path, today=date(2026, 9, 14))
    day2 = current_log_path(log_dir=tmp_path, today=date(2026, 9, 15))
    assert day1 != day2


def test_log_snapshot_defaults_to_todays_rotated_file(tmp_path, monkeypatch):
    """Without an explicit log_path, log_snapshot() must write into
    current_log_path() -- not the old frozen LOG_PATH -- so a real
    (un-parametrized) run rotates correctly."""
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {"stations": [{"station_id": "abc-123", "num_bikes_available": 1, "num_docks_available": 1, "is_renting": 1, "is_returning": 1}]},
    }
    info_payload = {"data": {"stations": [{"station_id": "abc-123", "short_name": "6433.01"}]}}

    def fake_get(url, timeout):
        return _fake_response(status_payload if "station_status" in url else info_payload)

    monkeypatch.setattr("pipeline.gbfs_logger.requests.get", fake_get)
    monkeypatch.setattr("pipeline.gbfs_logger.LOG_DIR", tmp_path)
    monkeypatch.setattr("pipeline.gbfs_logger.current_log_path", lambda: current_log_path(tmp_path, date(2026, 9, 14)))

    log_snapshot()

    assert (tmp_path / "snapshots_2026-09-14.csv").exists()
    assert not (tmp_path / "snapshots.csv").exists()


def _fake_response(payload):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return FakeResponse()


def test_fetch_station_id_crosswalk_maps_gbfs_id_to_short_name(monkeypatch):
    payload = {
        "data": {
            "stations": [
                {"station_id": "abc-123", "short_name": "6433.01", "name": "92 St & 37 Ave"},
                {"station_id": "def-456", "short_name": "4781.03", "name": "Concord St & Bridge St"},
            ]
        }
    }
    monkeypatch.setattr(
        "pipeline.gbfs_logger.requests.get", lambda url, timeout: _fake_response(payload)
    )

    crosswalk = fetch_station_id_crosswalk()
    assert crosswalk == {"abc-123": "6433.01", "def-456": "4781.03"}


def test_parse_snapshot_resolves_short_name_and_counts_dropped_unmatched_stations():
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {
            "stations": [
                {
                    "station_id": "abc-123",
                    "num_bikes_available": 5,
                    "num_docks_available": 10,
                    "is_renting": 1,
                    "is_returning": 1,
                },
                {
                    # not in the crosswalk -- e.g. added between the two fetches
                    "station_id": "unknown-999",
                    "num_bikes_available": 0,
                    "num_docks_available": 0,
                    "is_renting": 0,
                    "is_returning": 0,
                },
            ]
        },
    }
    crosswalk = {"abc-123": "6433.01"}

    result = parse_snapshot(status_payload, crosswalk)
    snapshot = result.rows

    assert result.n_dropped == 1
    assert list(snapshot["station_id"]) == ["6433.01"]
    assert snapshot["bikes_available"].iloc[0] == 5
    assert snapshot["docks_available"].iloc[0] == 10
    assert snapshot["is_renting"].iloc[0] == 1
    assert snapshot["is_returning"].iloc[0] == 1
    assert snapshot["timestamp"].iloc[0] == pd.Timestamp(1_700_000_000, unit="s", tz="UTC")


def test_parse_snapshot_reports_zero_dropped_when_everything_matches():
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {"stations": [{"station_id": "abc-123", "num_bikes_available": 5, "num_docks_available": 10, "is_renting": 1, "is_returning": 1}]},
    }
    result = parse_snapshot(status_payload, {"abc-123": "6433.01"})
    assert result.n_dropped == 0
    assert "dropped 0" in result.summary()


def test_append_snapshot_writes_header_once_then_appends(tmp_path):
    log_path = tmp_path / "snapshots.csv"
    first = pd.DataFrame([{"station_id": "6433.01", "timestamp": "2026-07-13T00:00:00Z", "bikes_available": 5, "docks_available": 10, "is_renting": 1, "is_returning": 1}])
    second = pd.DataFrame([{"station_id": "6433.01", "timestamp": "2026-07-13T00:10:00Z", "bikes_available": 4, "docks_available": 11, "is_renting": 1, "is_returning": 1}])

    append_snapshot(first, log_path=log_path)
    append_snapshot(second, log_path=log_path)

    lines = log_path.read_text().splitlines()
    assert lines[0] == "station_id,timestamp,bikes_available,docks_available,is_renting,is_returning"
    assert len(lines) == 3  # header + 2 data rows, no repeated header


def test_log_snapshot_fetches_parses_and_appends(tmp_path, monkeypatch):
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {
            "stations": [
                {
                    "station_id": "abc-123",
                    "num_bikes_available": 3,
                    "num_docks_available": 7,
                    "is_renting": 1,
                    "is_returning": 1,
                }
            ]
        },
    }
    info_payload = {"data": {"stations": [{"station_id": "abc-123", "short_name": "6433.01"}]}}

    def fake_get(url, timeout):
        if "station_status" in url:
            return _fake_response(status_payload)
        return _fake_response(info_payload)

    monkeypatch.setattr("pipeline.gbfs_logger.requests.get", fake_get)

    log_path = tmp_path / "snapshots.csv"
    result = log_snapshot(log_path=log_path)

    assert list(result.rows["station_id"]) == ["6433.01"]
    assert result.n_dropped == 0
    assert log_path.exists()
    assert len(log_path.read_text().splitlines()) == 2  # header + 1 row


def test_build_live_status_payload_joins_status_and_information_by_short_name():
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {
            "stations": [
                {
                    "station_id": "abc-123",
                    "num_bikes_available": 5,
                    "num_docks_available": 10,
                    "is_renting": 1,
                    "is_returning": 1,
                },
                {
                    # not in station_information -- e.g. added between the two fetches
                    "station_id": "unknown-999",
                    "num_bikes_available": 0,
                    "num_docks_available": 0,
                    "is_renting": 0,
                    "is_returning": 0,
                },
            ]
        },
    }
    info_stations = [{"station_id": "abc-123", "short_name": "6433.01", "capacity": 33}]

    payload = build_live_status_payload(status_payload, info_stations)

    assert payload["n_dropped"] == 1
    assert payload["last_updated"] == pd.Timestamp(1_700_000_000, unit="s", tz="UTC").isoformat()
    assert payload["stations"] == {
        "6433.01": {
            "capacity": 33,
            "bikes_available": 5,
            "docks_available": 10,
            "is_renting": True,
            "is_returning": True,
        }
    }


def test_build_live_status_payload_defaults_missing_capacity_to_zero():
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {"stations": [{"station_id": "abc-123", "num_bikes_available": 0, "num_docks_available": 0, "is_renting": 0, "is_returning": 0}]},
    }
    # capacity deliberately absent -- station_information doesn't always carry it
    info_stations = [{"station_id": "abc-123", "short_name": "6433.01"}]

    payload = build_live_status_payload(status_payload, info_stations)

    assert payload["stations"]["6433.01"]["capacity"] == 0
    assert payload["stations"]["6433.01"]["is_renting"] is False


def test_export_live_status_fetches_and_writes_json(tmp_path, monkeypatch):
    status_payload = {
        "last_updated": 1_700_000_000,
        "data": {"stations": [{"station_id": "abc-123", "num_bikes_available": 5, "num_docks_available": 10, "is_renting": 1, "is_returning": 1}]},
    }
    info_payload = {"data": {"stations": [{"station_id": "abc-123", "short_name": "6433.01", "capacity": 33}]}}

    def fake_get(url, timeout):
        if "station_status" in url:
            return _fake_response(status_payload)
        return _fake_response(info_payload)

    monkeypatch.setattr("pipeline.gbfs_logger.requests.get", fake_get)

    live_status_path = tmp_path / "live_status.json"
    payload = export_live_status(path=live_status_path)

    assert payload["stations"]["6433.01"]["capacity"] == 33
    assert live_status_path.exists()
    on_disk = json.loads(live_status_path.read_text())
    assert on_disk == payload

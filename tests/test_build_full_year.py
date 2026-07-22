"""Tests for pipeline/build_full_year.py.

Focused on this module's own job -- the month loop, zip deletion, schema-
drift detection, and coverage/manifest output -- not on typology or equity
join logic, which already have their own test suites. apply_typology and
run_equity_join are monkeypatched to lightweight passthroughs here so
these tests never hit the network or run real clustering on tiny fixtures.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from pipeline.build_full_year import SchemaDriftError, build_full_year, process_month

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


def month_trips(year_month: str, day: int = 1, ride_id: str = "t1", **overrides) -> pd.DataFrame:
    ts = f"{year_month}-{day:02d} 08:00:00"
    ts_end = f"{year_month}-{day:02d} 08:15:00"
    return pd.DataFrame([make_trip(ts, ts_end, ride_id=ride_id, **overrides)])


def _passthrough_typology(payload: dict) -> dict:
    payload["typology"] = {"stub": True}
    return payload


def _fake_zip_path(tmp_path):
    """A real, deletable file standing in for a downloaded zip -- lets the
    test assert it's actually gone afterward, not just that unlink() was
    called.
    """
    def _make(year_month: str):
        path = tmp_path / f"{year_month.replace('-', '')}-citibike-tripdata.zip"
        path.write_bytes(b"not a real zip, just needs to exist and be deletable")
        return path
    return _make


def test_process_month_deletes_zip_after_aggregating(tmp_path, monkeypatch):
    make_zip = _fake_zip_path(tmp_path)
    zip_path = make_zip("2025-07")
    monkeypatch.setattr("pipeline.build_full_year.download_month", lambda ym: zip_path)
    monkeypatch.setattr("pipeline.build_full_year.load_trips", lambda path: month_trips("2025-07"))

    assert zip_path.exists()

    monthly, daily, schema = process_month("2025-07")

    assert not zip_path.exists(), "raw zip must be deleted after aggregation"
    assert set(monthly["station_id"]) == {"1", "2"}
    assert set(daily["station_id"]) == {"1", "2"}
    assert "start_station_id" in schema


def test_build_full_year_detects_schema_drift(tmp_path, monkeypatch):
    make_zip = _fake_zip_path(tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.download_month", lambda ym: make_zip(ym))

    def fake_load_trips(path):
        ym = path.stem[:6]
        trips = month_trips(f"{ym[:4]}-{ym[4:]}")
        if path.stem.startswith("202508"):
            trips = trips.rename(columns={"rideable_type": "bike_type"})  # simulate schema drift
        return trips

    monkeypatch.setattr("pipeline.build_full_year.load_trips", fake_load_trips)
    monkeypatch.setattr("pipeline.build_full_year.apply_typology", _passthrough_typology)
    monkeypatch.setattr("pipeline.build_full_year.run_equity_join", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr("pipeline.build_full_year.DATA_DIR", tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.FLOWS_PATH", tmp_path / "flows.json")
    monkeypatch.setattr("pipeline.build_full_year.DAILY_NET_FLOW_PATH", tmp_path / "daily_net_flow.parquet")
    monkeypatch.setattr("pipeline.build_full_year.MANIFEST_PATH", tmp_path / "data_manifest.json")

    with pytest.raises(SchemaDriftError):
        build_full_year(months=["2025-07", "2025-08"])


def test_build_full_year_writes_flows_daily_table_and_manifest(tmp_path, monkeypatch):
    make_zip = _fake_zip_path(tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.download_month", lambda ym: make_zip(ym))
    monkeypatch.setattr("pipeline.build_full_year.load_trips", lambda path: month_trips(path.stem[:4] + "-" + path.stem[4:6]))
    monkeypatch.setattr("pipeline.build_full_year.apply_typology", _passthrough_typology)
    monkeypatch.setattr("pipeline.build_full_year.run_equity_join", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr("pipeline.build_full_year.DATA_DIR", tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.FLOWS_PATH", tmp_path / "flows.json")
    monkeypatch.setattr("pipeline.build_full_year.DAILY_NET_FLOW_PATH", tmp_path / "daily_net_flow.parquet")
    monkeypatch.setattr("pipeline.build_full_year.MANIFEST_PATH", tmp_path / "data_manifest.json")

    result = build_full_year(months=["2025-07", "2025-08"])

    assert (tmp_path / "flows.json").exists()
    assert (tmp_path / "daily_net_flow.parquet").exists()
    assert (tmp_path / "data_manifest.json").exists()

    manifest = json.loads((tmp_path / "data_manifest.json").read_text())
    assert manifest["months"] == ["2025-07", "2025-08"]
    assert manifest["n_stations_any_coverage"] == 2  # stations "1" and "2"

    daily = pd.read_parquet(tmp_path / "daily_net_flow.parquet")
    assert set(daily["station_id"]) == {"1", "2"}

    assert set(result["coverage"]["station_id"]) == {"1", "2"}


def test_build_full_year_flags_partial_coverage_without_excluding(tmp_path, monkeypatch):
    """A station present in only one of two target months must still appear
    in the output, with its coverage visible -- not silently dropped or
    silently treated as equal to a full-coverage station.
    """
    make_zip = _fake_zip_path(tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.download_month", lambda ym: make_zip(ym))

    def fake_load_trips(path):
        ym = path.stem[:4] + "-" + path.stem[4:6]
        if ym == "2025-07":
            return pd.concat([month_trips(ym, ride_id="t1"), month_trips(ym, ride_id="t2", start_station_id="3", end_station_id="3")], ignore_index=True)
        return month_trips(ym, ride_id="t3")

    monkeypatch.setattr("pipeline.build_full_year.load_trips", fake_load_trips)
    monkeypatch.setattr("pipeline.build_full_year.apply_typology", _passthrough_typology)
    monkeypatch.setattr("pipeline.build_full_year.run_equity_join", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr("pipeline.build_full_year.DATA_DIR", tmp_path)
    monkeypatch.setattr("pipeline.build_full_year.FLOWS_PATH", tmp_path / "flows.json")
    monkeypatch.setattr("pipeline.build_full_year.DAILY_NET_FLOW_PATH", tmp_path / "daily_net_flow.parquet")
    monkeypatch.setattr("pipeline.build_full_year.MANIFEST_PATH", tmp_path / "data_manifest.json")

    result = build_full_year(months=["2025-07", "2025-08"])
    coverage = result["coverage"].set_index("station_id")["months_present"]

    assert coverage.loc["3"] == ["2025-07"]  # station 3 only appears in July -- partial coverage, visible
    assert coverage.loc["1"] == ["2025-07", "2025-08"]  # full coverage
    assert result["manifest"]["n_stations_any_coverage"] == 3
    assert result["manifest"]["n_stations_full_coverage"] == 2  # stations 1 and 2, not 3

"""Tests for pipeline/equity_join.py."""

import json

import pytest

from pipeline.equity_join import (
    build_equity_context,
    fetch_socrata_rows,
    haversine_distance_m,
    nearest_facility,
    nycha_developments_frame,
    school_locations_frame,
    subway_entrances_frame,
)


def _fake_response(payload):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return FakeResponse()


def test_fetch_socrata_rows_uses_cache_without_hitting_network(tmp_path, monkeypatch):
    cached = tmp_path / "abcd-1234.json"
    cached.write_text(json.dumps([{"name": "cached row"}]))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("fetch_socrata_rows hit the network despite a cached file")

    monkeypatch.setattr("pipeline.equity_join.requests.get", fail_if_called)

    rows = fetch_socrata_rows("data.example.gov", "abcd-1234", cache_dir=tmp_path)
    assert rows == [{"name": "cached row"}]


def test_fetch_socrata_rows_paginates_past_the_default_page_size(tmp_path, monkeypatch):
    """Total rows exceed one page -- the fetch must keep requesting until exhausted."""
    all_rows = [{"id": i} for i in range(1500)]

    def fake_get(url, params, timeout):
        if params.get("$select") == "count(*)":
            return _fake_response([{"count": str(len(all_rows))}])
        offset = params["$offset"]
        limit = params["$limit"]
        return _fake_response(all_rows[offset : offset + limit])

    monkeypatch.setattr("pipeline.equity_join.requests.get", fake_get)

    rows = fetch_socrata_rows("data.example.gov", "big-set", cache_dir=tmp_path)
    assert len(rows) == 1500
    assert rows == all_rows


def test_fetch_socrata_rows_raises_on_truncated_pagination(tmp_path, monkeypatch):
    """If count(*) disagrees with what pagination actually returned, fail loudly."""

    def fake_get(url, params, timeout):
        if params.get("$select") == "count(*)":
            return _fake_response([{"count": "500"}])
        offset = params["$offset"]
        if offset > 0:
            return _fake_response([])
        return _fake_response([{"id": i} for i in range(100)])  # short first page

    monkeypatch.setattr("pipeline.equity_join.requests.get", fake_get)

    with pytest.raises(ValueError, match="pagination likely truncated"):
        fetch_socrata_rows("data.example.gov", "truncated-set", cache_dir=tmp_path)


def test_nycha_developments_frame_uses_polygon_centroid():
    square = {
        "type": "MultiPolygon",
        "coordinates": [[[[-74.0, 40.0], [-74.0, 40.002], [-73.998, 40.002], [-73.998, 40.0], [-74.0, 40.0]]]],
    }
    rows = [{"the_geom": square, "developmen": "TEST HOUSES"}]

    frame = nycha_developments_frame(rows)
    assert list(frame["name"]) == ["TEST HOUSES"]
    assert frame["lat"].iloc[0] == pytest.approx(40.001, abs=1e-3)
    assert frame["lng"].iloc[0] == pytest.approx(-73.999, abs=1e-3)


def test_school_locations_frame_drops_closed_and_missing_coordinates():
    rows = [
        {"location_name": "Open School", "status_descriptions": "Open", "latitude": "40.7", "longitude": "-74.0"},
        {"location_name": "Closed School", "status_descriptions": "Closed", "latitude": "40.7", "longitude": "-74.0"},
        {"location_name": "No Coords", "status_descriptions": "Open", "latitude": "", "longitude": ""},
        # Socrata serializes some missing numeric fields as the literal string "NULL",
        # not an empty string -- e.g. an out-of-city District 75 site in the real data.
        {"location_name": "Null Coords", "status_descriptions": "Open", "latitude": "NULL", "longitude": "NULL"},
    ]
    frame = school_locations_frame(rows)
    assert list(frame["name"]) == ["Open School"]


def test_subway_entrances_frame_extracts_name_and_coordinates():
    rows = [{"stop_name": "Atlantic Av-Barclays Ctr", "entrance_latitude": "40.683905", "entrance_longitude": "-73.978879"}]
    frame = subway_entrances_frame(rows)
    assert frame["name"].iloc[0] == "Atlantic Av-Barclays Ctr"
    assert frame["lat"].iloc[0] == pytest.approx(40.683905)
    assert frame["lng"].iloc[0] == pytest.approx(-73.978879)


def test_haversine_distance_m_matches_known_one_degree_longitude_at_equator():
    import numpy as np

    dist = haversine_distance_m(0.0, 0.0, np.array([0.0]), np.array([1.0]))
    assert dist[0] == pytest.approx(111_195, rel=1e-3)


def test_nearest_facility_picks_the_closer_row():
    import pandas as pd

    facilities = pd.DataFrame(
        {"name": ["near", "far"], "lat": [40.7002, 40.71], "lng": [-74.0, -74.0]}
    )
    dist, name = nearest_facility(40.7, -74.0, facilities)
    assert name == "near"
    assert dist < 1000


def test_build_equity_context_flags_respect_thresholds():
    import pandas as pd

    stations = {"1": {"lat": 40.7000, "lng": -74.0000}}
    nycha = pd.DataFrame({"name": ["close nycha"], "lat": [40.7020], "lng": [-74.0000]})  # ~222m -> near
    schools = pd.DataFrame({"name": ["far school"], "lat": [40.7100], "lng": [-74.0000]})  # ~1100m -> not near
    subway = pd.DataFrame({"name": ["far subway"], "lat": [40.7100], "lng": [-74.0000]})  # ~1100m -> transit_gap

    context = build_equity_context(stations, nycha, schools, subway)["1"]
    assert context["near_nycha"] == 1
    assert context["near_school"] == 0
    assert context["transit_gap"] == 1
    assert context["nycha_nearest"] == "close nycha"

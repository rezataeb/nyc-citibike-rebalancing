"""Tests for pipeline/weather.py."""

import pandas as pd
import pytest

from pipeline.weather import NYC_LAT, NYC_LNG, compute_weather_zones, fetch_daily_weather, fetch_weather_at_points


def test_fetch_daily_weather_uses_cache_without_hitting_network(tmp_path, monkeypatch):
    # Cache filename embeds lat/lng (Session 36, multi-point support) -- using
    # the default NYC point here, same as any existing single-point caller.
    cached = tmp_path / f"weather_{NYC_LAT:.4f}_{NYC_LNG:.4f}_2026-02-01_2026-02-02.csv"
    pd.DataFrame(
        {"date": ["2026-02-01", "2026-02-02"], "temp_mean_c": [-10.6, -6.6], "precip_mm": [0.0, 0.0]}
    ).to_csv(cached, index=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("fetch_daily_weather hit the network despite a cached file")

    monkeypatch.setattr("pipeline.weather.requests.get", fail_if_called)

    result = fetch_daily_weather("2026-02-01", "2026-02-02", cache_dir=tmp_path)
    assert len(result) == 2
    assert result["temp_mean_c"].iloc[0] == -10.6


def test_fetch_daily_weather_parses_and_caches_response(tmp_path, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "daily": {
                    "time": ["2026-06-01", "2026-06-02"],
                    "temperature_2m_mean": [22.1, 23.4],
                    "precipitation_sum": [0.0, 3.2],
                }
            }

    monkeypatch.setattr("pipeline.weather.requests.get", lambda *a, **kw: FakeResponse())

    result = fetch_daily_weather("2026-06-01", "2026-06-02", cache_dir=tmp_path)
    assert list(result["temp_mean_c"]) == [22.1, 23.4]
    assert (tmp_path / f"weather_{NYC_LAT:.4f}_{NYC_LNG:.4f}_2026-06-01_2026-06-02.csv").exists()


def test_fetch_daily_weather_caches_different_points_separately(tmp_path, monkeypatch):
    # Two different points requesting the same date range must not collide
    # on one cache file -- each point's real weather is genuinely different.
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            calls.append(1)
            return {"daily": {"time": ["2026-06-01"], "temperature_2m_mean": [20.0 + len(calls)], "precipitation_sum": [0.0]}}

    monkeypatch.setattr("pipeline.weather.requests.get", lambda *a, **kw: FakeResponse())

    result_a = fetch_daily_weather("2026-06-01", "2026-06-01", lat=40.7, lng=-73.9, cache_dir=tmp_path)
    result_b = fetch_daily_weather("2026-06-01", "2026-06-01", lat=40.6, lng=-74.0, cache_dir=tmp_path)

    assert len(calls) == 2  # both points actually hit the network, no false cache hit
    assert result_a["temp_mean_c"].iloc[0] != result_b["temp_mean_c"].iloc[0]


def test_compute_weather_zones_groups_nearby_stations_together():
    # Two tight geographic clusters, far apart -- k=2 must recover them as
    # two zones, not split a tight cluster across zones or merge the two.
    stations = {
        "a1": {"lat": 40.70, "lng": -74.00}, "a2": {"lat": 40.701, "lng": -74.001},
        "a3": {"lat": 40.699, "lng": -73.999},
        "b1": {"lat": 40.85, "lng": -73.90}, "b2": {"lat": 40.851, "lng": -73.901},
        "b3": {"lat": 40.849, "lng": -73.899},
    }
    assignments, centroids = compute_weather_zones(stations, n_zones=2)

    assert len(centroids) == 2
    assert assignments["a1"] == assignments["a2"] == assignments["a3"]
    assert assignments["b1"] == assignments["b2"] == assignments["b3"]
    assert assignments["a1"] != assignments["b1"]


def test_compute_weather_zones_is_deterministic():
    stations = {f"s{i}": {"lat": 40.6 + i * 0.01, "lng": -74.0 + i * 0.01} for i in range(20)}
    assignments_1, centroids_1 = compute_weather_zones(stations, n_zones=4)
    assignments_2, centroids_2 = compute_weather_zones(stations, n_zones=4)
    assert assignments_1 == assignments_2
    assert centroids_1 == centroids_2


def test_fetch_weather_at_points_returns_one_frame_per_point(tmp_path, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"daily": {"time": ["2026-06-01"], "temperature_2m_mean": [21.0], "precipitation_sum": [1.0]}}

    monkeypatch.setattr("pipeline.weather.requests.get", lambda *a, **kw: FakeResponse())

    points = [(40.70, -74.00), (40.85, -73.90), (40.75, -73.95)]
    results = fetch_weather_at_points(points, "2026-06-01", "2026-06-01", cache_dir=tmp_path)

    assert len(results) == 3
    assert all(len(r) == 1 for r in results)

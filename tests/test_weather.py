"""Tests for pipeline/weather.py."""

import pandas as pd

from pipeline.weather import fetch_daily_weather


def test_fetch_daily_weather_uses_cache_without_hitting_network(tmp_path, monkeypatch):
    cached = tmp_path / "weather_2026-02-01_2026-02-02.csv"
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
    assert (tmp_path / "weather_2026-06-01_2026-06-02.csv").exists()

"""Fetch and cache historical daily weather from Open-Meteo, at one or several points.

Open-Meteo's historical archive API is free, requires no API key, and is
one of the public data sources this project is scoped to (see CLAUDE.md).

Session 36: weather is no longer a single fixed NYC reference point by
default for callers that want real spatial variation -- see
compute_weather_zones() below, which derives a small number of geographic
zones directly from the real station distribution (k-means on lat/lng,
the same reasoning pipeline/station_typology.py already uses for
behavioral clustering, applied here to geography instead of shape), and
fetch_weather_at_points(), which fetches real weather independently at
each zone's centroid. fetch_daily_weather() itself is unchanged in
signature and still fetches one point -- now parameterized by lat/lng
instead of hardcoded to NYC_LAT/NYC_LNG, which remain the default so
existing single-point callers keep working unmodified.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.cluster import KMeans

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NYC_LAT, NYC_LNG = 40.7829, -73.9654
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Number of geographic weather zones -- matches the "3-4 borough-representative
# points" originally recommended (see PROGRESS.md's credibility-review entry).
N_WEATHER_ZONES = 4
ZONE_RANDOM_STATE = 0


def fetch_daily_weather(
    start_date: str, end_date: str, lat: float = NYC_LAT, lng: float = NYC_LNG, cache_dir: Path = RAW_DATA_DIR
) -> pd.DataFrame:
    """Return daily mean temperature (C) and precipitation (mm) at (lat, lng), cached to disk.

    start_date/end_date are 'YYYY-MM-DD', inclusive. Defaults to the
    original single NYC reference point for backward compatibility with
    existing single-point callers.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"weather_{lat:.4f}_{lng:.4f}_{start_date}_{end_date}.csv"

    if cache_path.exists():
        print(f"Using cached file: {cache_path}")
        return pd.read_csv(cache_path, parse_dates=["date"])

    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_mean,precipitation_sum",
        "timezone": "America/New_York",
    }
    print(f"Fetching weather {start_date}..{end_date} at ({lat:.4f}, {lng:.4f}) from {ARCHIVE_URL}")
    response = requests.get(ARCHIVE_URL, params=params, timeout=60)
    response.raise_for_status()
    daily = response.json()["daily"]

    weather = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["time"]),
            "temp_mean_c": daily["temperature_2m_mean"],
            "precip_mm": daily["precipitation_sum"],
        }
    )
    weather.to_csv(cache_path, index=False)
    return weather


def compute_weather_zones(stations: dict, n_zones: int = N_WEATHER_ZONES) -> tuple[dict[str, int], list[tuple[float, float]]]:
    """K-means cluster real station (lat, lng) into n_zones geographic weather
    zones. Returns (station_id -> zone_index, [zone_centroid (lat, lng), ...]).

    Zones are derived directly from the real station distribution, not
    hand-picked borough boundaries this pipeline doesn't otherwise model or
    verify -- the same reasoning pipeline/station_typology.py already uses
    for behavioral clustering (real data decides the groups, not an
    assumption about NYC geography), just applied to lat/lng instead of
    weekday-curve shape. Deterministic (fixed random_state): the same
    station roster always produces the same zones, so callers on either
    side of the pipeline (elasticities.py, demand_model.py) that each call
    this independently still agree on the same zone boundaries.
    """
    station_ids = list(stations.keys())
    coords = np.array([[stations[sid]["lat"], stations[sid]["lng"]] for sid in station_ids])
    km = KMeans(n_clusters=n_zones, random_state=ZONE_RANDOM_STATE, n_init=10)
    labels = km.fit_predict(coords)
    assignments = {sid: int(label) for sid, label in zip(station_ids, labels)}
    centroids = [(float(c[0]), float(c[1])) for c in km.cluster_centers_]
    return assignments, centroids


def fetch_weather_at_points(
    points: list[tuple[float, float]], start_date: str, end_date: str, cache_dir: Path = RAW_DATA_DIR
) -> list[pd.DataFrame]:
    """fetch_daily_weather() at each of `points`, real independent API calls
    (Open-Meteo is free, no key, no rate-limit concern at this scale) --
    returns one DataFrame per point, same order as `points`.
    """
    return [fetch_daily_weather(start_date, end_date, lat=lat, lng=lng, cache_dir=cache_dir) for lat, lng in points]

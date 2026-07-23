"""Fetch and cache historical daily weather from Open-Meteo, at one or several points.

Open-Meteo's historical archive API is free, requires no API key, and is
one of the public data sources this project is scoped to (see CLAUDE.md).

Session 36: weather is no longer a single fixed NYC reference point by
default for callers that want real spatial variation -- see
compute_weather_zones() below, and fetch_weather_at_points(), which
fetches real weather independently at each zone's centroid.
fetch_daily_weather() itself is unchanged in signature and still fetches
one point -- now parameterized by lat/lng instead of hardcoded to
NYC_LAT/NYC_LNG, which remain the default so existing single-point
callers keep working unmodified.

Session 38: compute_weather_zones() was originally k-means on station
lat/lng (same reasoning pipeline/station_typology.py uses for behavioral
clustering, applied to geography instead of shape). Verified against the
four equity-priority areas this dashboard cares about most and found it
skewed: South Bronx and Upper Manhattan (dense, large local populations)
each landed within ~2-3km of their assigned zone centroid, but the
sparser East Queens and Southern Brooklyn were swept into distant,
station-dense zones 6-8km away -- weather-zone quality tracked local
station density, not geography, exactly the risk raised before this was
built. Raising K (tried 8) didn't fix it (East Queens still 5km off);
switched to a fixed geographic grid instead, which decouples zone
assignment from station density by construction -- see GRID_CELL_KM.
"""

from __future__ import annotations

from math import asin, ceil, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NYC_LAT, NYC_LNG = 40.7829, -73.9654
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Grid cell size for weather zones. Chosen empirically (Session 38): 6km was
# the coarsest cell size that brought all four named equity-priority areas
# (South Bronx, Upper Manhattan, East Queens, Southern Brooklyn) within
# ~1.6-2.6km of their assigned grid point's weather -- comparable to or
# better than the well-served areas under the old k-means approach. Finer
# grids (4-5km) don't meaningfully improve on this and roughly double the
# number of real Open-Meteo API calls for no equity benefit; coarser (8km)
# leaves East Queens ~4km off.
GRID_CELL_KM = 6.0
EARTH_RADIUS_KM = 6371.0


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


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two (lat, lng) points."""
    lat1, lng1, lat2, lng2 = (radians(v) for v in (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def compute_weather_zones(
    stations: dict, cell_km: float = GRID_CELL_KM
) -> tuple[dict[str, int], list[tuple[float, float]]]:
    """Assign each station to a fixed geographic grid cell, independent of
    station density. Returns (station_id -> zone_index, [zone_point (lat, lng), ...]).

    A grid of points spaced cell_km apart covers the real station bounding
    box; each station is assigned to its nearest grid point by haversine
    distance. Grid points with no assigned station are dropped (no point
    fetching weather nobody uses) and the remaining zones are renumbered
    0..N-1. This is deliberately NOT k-means on station coordinates: k-means
    balances zone *population*, which pulls zone centroids toward dense
    station clusters and leaves sparse peripheral areas -- exactly the
    equity-priority areas this project cares about most (South Bronx, Upper
    Manhattan, East Queens, Southern Brooklyn) -- assigned to a distant,
    unrepresentative centroid. A fixed grid has no notion of station density
    at all, so it can't be pulled off course by it. Deterministic: the same
    station roster always produces the same grid and zone count, so callers
    on either side of the pipeline (elasticities.py, demand_model.py) that
    each call this independently still agree on the same zones.
    """
    station_ids = list(stations.keys())
    lats = [stations[sid]["lat"] for sid in station_ids]
    lngs = [stations[sid]["lng"] for sid in station_ids]
    lat_min, lat_max = min(lats), max(lats)
    lng_min, lng_max = min(lngs), max(lngs)

    lat_span_km = haversine_km(lat_min, lng_min, lat_max, lng_min)
    lng_span_km = haversine_km(lat_min, lng_min, lat_min, lng_max)
    rows = max(1, ceil(lat_span_km / cell_km))
    cols = max(1, ceil(lng_span_km / cell_km))

    grid_points = [
        (
            lat_min + (r + 0.5) * (lat_max - lat_min) / rows,
            lng_min + (c + 0.5) * (lng_max - lng_min) / cols,
        )
        for r in range(rows)
        for c in range(cols)
    ]

    raw_assignments = {
        sid: min(range(len(grid_points)), key=lambda i: haversine_km(stations[sid]["lat"], stations[sid]["lng"], *grid_points[i]))
        for sid in station_ids
    }

    occupied = sorted(set(raw_assignments.values()))
    reindex = {old: new for new, old in enumerate(occupied)}
    assignments = {sid: reindex[zone] for sid, zone in raw_assignments.items()}
    centroids = [grid_points[old] for old in occupied]
    return assignments, centroids


def fetch_weather_at_points(
    points: list[tuple[float, float]], start_date: str, end_date: str, cache_dir: Path = RAW_DATA_DIR
) -> list[pd.DataFrame]:
    """fetch_daily_weather() at each of `points`, real independent API calls
    (Open-Meteo is free, no key, no rate-limit concern at this scale) --
    returns one DataFrame per point, same order as `points`.
    """
    return [fetch_daily_weather(start_date, end_date, lat=lat, lng=lng, cache_dir=cache_dir) for lat, lng in points]

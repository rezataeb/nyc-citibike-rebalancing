"""Fetch and cache historical NYC daily weather from Open-Meteo.

Open-Meteo's historical archive API is free, requires no API key, and is
one of the public data sources this project is scoped to (see CLAUDE.md).
Coordinates are a single fixed NYC reference point (near Central Park) --
a borough-wide proxy, not per-station weather.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NYC_LAT, NYC_LNG = 40.7829, -73.9654
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def fetch_daily_weather(start_date: str, end_date: str, cache_dir: Path = RAW_DATA_DIR) -> pd.DataFrame:
    """Return daily mean temperature (C) and precipitation (mm) for NYC, cached to disk.

    start_date/end_date are 'YYYY-MM-DD', inclusive.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"weather_{start_date}_{end_date}.csv"

    if cache_path.exists():
        print(f"Using cached file: {cache_path}")
        return pd.read_csv(cache_path, parse_dates=["date"])

    params = {
        "latitude": NYC_LAT,
        "longitude": NYC_LNG,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_mean,precipitation_sum",
        "timezone": "America/New_York",
    }
    print(f"Fetching weather {start_date}..{end_date} from {ARCHIVE_URL}")
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

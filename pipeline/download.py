"""Download one month of Citi Bike trip data and load it into a DataFrame.

Source: the public Citi Bike S3 archive (https://s3.amazonaws.com/tripdata).
Each monthly object is a zip file that may contain several split CSVs
(e.g. 202604-citibike-tripdata-part1.csv, -part2.csv, ...).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import requests

BUCKET_URL = "https://s3.amazonaws.com/tripdata"
RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
CHUNK_SIZE = 1024 * 1024  # 1 MB, for streaming the download to disk


def build_filename(year_month: str) -> str:
    """Convert 'YYYY-MM' into the S3 object name, e.g. '202604-citibike-tripdata.zip'."""
    yyyymm = year_month.replace("-", "")
    return f"{yyyymm}-citibike-tripdata.zip"


def download_month(year_month: str, dest_dir: Path = RAW_DATA_DIR) -> Path:
    """Download one month's trip data zip into dest_dir, reusing a cached copy if present."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / build_filename(year_month)

    if dest_path.exists():
        print(f"Using cached file: {dest_path}")
        return dest_path

    url = f"{BUCKET_URL}/{dest_path.name}"
    print(f"Downloading {url} -> {dest_path}")
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

    return dest_path


def load_trips(zip_path: Path) -> pd.DataFrame:
    """Read every CSV inside the monthly zip and concatenate them into one DataFrame."""
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = sorted(
            name
            for name in zf.namelist()
            if name.endswith(".csv") and not name.startswith("__MACOSX")
        )
        frames = [pd.read_csv(zf.open(name)) for name in csv_names]
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys

    year_month = sys.argv[1] if len(sys.argv) > 1 else "2026-04"
    zip_path = download_month(year_month)
    trips = load_trips(zip_path)
    print(f"Loaded {len(trips):,} trips with columns: {list(trips.columns)}")

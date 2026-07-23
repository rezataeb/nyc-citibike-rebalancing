"""Fetch NYCHA/school/subway layers from NYC/NY Open Data and join them to flows.json stations.

Fetch step: three Socrata datasets, each paginated ($limit/$offset, default
page size 1000) and verified against the dataset's own $select=count(*)
total -- catches silent truncation before it corrupts distances downstream.

- NYCHA developments: data.cityofnewyork.us/phvi-damg. Rows are polygon
  footprints, not points -- each development's location is the centroid of
  its footprint (shapely).
- School locations: data.cityofnewyork.us/wg9x-4ke6, "2019-2020 School
  Locations", filtered to status_descriptions == "Open". This is the most
  recent Socrata dataset that still serves rows through the row API -- the
  current DOE school-location layer (3bkj-34v2) is GIS-only and rejects row
  queries outright. See SCHOOL_VINTAGE_LABEL: this layer is more likely
  than the other two to have drifted from today's school footprint, so the
  label travels with the layer's metadata (equity_join.layers.school) and
  must be surfaced anywhere school-proximity numbers are shown, not just
  noted here.

  Re-verified 2026-07-23 (Session 37), not assumed still true: no newer
  row-queryable, coordinate-bearing school-location dataset exists on NYC
  Open Data. Checked directly, not just re-read from an old note --
  (1) 3bkj-34v2 still returns HTTP 403 ("no row or column access to
  non-tabular tables"); (2) i2i8-9vjc ("NYC School Locations", a
  non-year-versioned, current-sounding name) IS row-queryable but has
  exactly one field (address, no coordinates) and is itself a 2017
  vintage -- older than wg9x-4ke6, not a real candidate; (3) jfju-ynrr
  ("School Point Locations") is file/GIS-only, the same failure mode as
  the reference implementation's broken hardcoded id (see the live
  Socrata check elsewhere in this module); (4) cmjf-yawu ("School Zones
  2024-2025") is real and current but contains attendance-ZONE polygons,
  not individual school point locations; (5) the year-versioned "20XX-20XX
  School Locations" series that produced wg9x-4ke6 (2012-2013 through
  2019-2020 all exist) has no entry after 2019-2020 -- DOE appears to
  have stopped publishing this specific series entirely, not just paused
  it. wg9x-4ke6 remains not just the best available option but the only
  one that actually works for this pipeline's needs.
- Subway entrances: data.ny.gov/i9wp-a4ja, "MTA Subway Entrances and Exits:
  2024". Not on data.cityofnewyork.us -- migrated to the state portal, but
  still MTA-authoritative Socrata data.

Join step: plain haversine nearest-neighbor, brute-force (station count x
facility count is a few million operations at most -- no spatial index
needed). Thresholds (CLAUDE.md): 300 m for NYCHA/school, 800 m for subway.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import shape

RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PAGE_SIZE = 1000
EARTH_RADIUS_M = 6_371_000

NYCHA_DOMAIN = "data.cityofnewyork.us"
NYCHA_DATASET_ID = "phvi-damg"
SCHOOL_DOMAIN = "data.cityofnewyork.us"
SCHOOL_DATASET_ID = "wg9x-4ke6"
SUBWAY_DOMAIN = "data.ny.gov"
SUBWAY_DATASET_ID = "i9wp-a4ja"

SCHOOL_VINTAGE_LABEL = (
    "2019-2020 school locations -- confirmed the only row-queryable, "
    "coordinate-bearing school-location dataset on NYC Open Data as of "
    "2026-07-23 (re-checked, not assumed; no newer edition of this "
    "dataset series has ever been published)"
)

NYCHA_THRESHOLD_M = 300
SCHOOL_THRESHOLD_M = 300
SUBWAY_THRESHOLD_M = 800


def fetch_socrata_rows(domain: str, dataset_id: str, cache_dir: Path = RAW_DATA_DIR) -> list[dict]:
    """Fetch every row of a Socrata dataset via offset pagination, verified against count(*).

    Socrata's default page size is 1000 rows; a single unpaginated request
    silently truncates anything larger than that. Pages are fetched
    ordered by the internal :id column for a stable cursor, and the total
    rows fetched is asserted against a separate $select=count(*) query --
    a truncated fetch raises instead of silently corrupting distances
    computed downstream.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{dataset_id}.json"

    if cache_path.exists():
        print(f"Using cached file: {cache_path}")
        return json.loads(cache_path.read_text())

    base_url = f"https://{domain}/resource/{dataset_id}.json"

    count_response = requests.get(base_url, params={"$select": "count(*)"}, timeout=60)
    count_response.raise_for_status()
    expected_count = int(count_response.json()[0]["count"])

    rows: list[dict] = []
    offset = 0
    while True:
        response = requests.get(
            base_url,
            params={"$limit": PAGE_SIZE, "$offset": offset, "$order": ":id"},
            timeout=60,
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        rows.extend(page)
        offset += PAGE_SIZE

    if len(rows) != expected_count:
        raise ValueError(
            f"{dataset_id}: fetched {len(rows):,} rows but count(*) reports "
            f"{expected_count:,} -- pagination likely truncated"
        )

    cache_path.write_text(json.dumps(rows))
    print(f"Fetched {len(rows):,} rows from {dataset_id} (verified against count(*))")
    return rows


def nycha_developments_frame(rows: list[dict]) -> pd.DataFrame:
    """Extract (name, lat, lng) per NYCHA development, using footprint centroids.

    Rows carry a MultiPolygon under the_geom rather than a point -- there is
    no direct lat/lng column on this dataset.
    """
    records = []
    for row in rows:
        geom = row.get("the_geom")
        if geom is None:
            continue
        centroid = shape(geom).centroid
        records.append({"name": row["developmen"], "lat": centroid.y, "lng": centroid.x})
    return pd.DataFrame.from_records(records)


def school_locations_frame(rows: list[dict]) -> pd.DataFrame:
    """Extract (name, lat, lng) for open schools only.

    status_descriptions also includes "Closed" and "Initiated, never
    opened" -- those aren't real proximity anchors and are dropped.
    """
    records = []
    for row in rows:
        if row.get("status_descriptions") != "Open":
            continue
        lat, lng = row.get("latitude"), row.get("longitude")
        if not lat or not lng or lat == "NULL" or lng == "NULL":
            continue
        records.append({"name": row["location_name"], "lat": float(lat), "lng": float(lng)})
    return pd.DataFrame.from_records(records)


def subway_entrances_frame(rows: list[dict]) -> pd.DataFrame:
    """Extract (name, lat, lng) per subway entrance, named by station complex."""
    records = []
    for row in rows:
        lat, lng = row.get("entrance_latitude"), row.get("entrance_longitude")
        if not lat or not lng:
            continue
        records.append({"name": row.get("stop_name", ""), "lat": float(lat), "lng": float(lng)})
    return pd.DataFrame.from_records(records)


def haversine_distance_m(
    lat1: float, lng1: float, lat2: np.ndarray, lng2: np.ndarray
) -> np.ndarray:
    """Great-circle distance in meters from one point to an array of points.

    Haversine, not a full ellipsoidal method (Vincenty/geodesic): at the
    scale of these thresholds (300-800 m) the two agree to within a few
    meters at NYC's latitude, and we're comparing straight-line distance,
    not walking-network distance, either way.
    """
    lat1_r, lng1_r, lat2_r, lng2_r = np.radians(lat1), np.radians(lng1), np.radians(lat2), np.radians(lng2)
    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def nearest_facility(station_lat: float, station_lng: float, facilities: pd.DataFrame) -> tuple[float, str]:
    """Return (distance_m, name) of the facility row nearest to (station_lat, station_lng)."""
    distances = haversine_distance_m(
        station_lat, station_lng, facilities["lat"].to_numpy(), facilities["lng"].to_numpy()
    )
    idx = int(np.argmin(distances))
    return float(distances[idx]), str(facilities["name"].iloc[idx])


def build_equity_context(
    stations: dict, nycha: pd.DataFrame, schools: pd.DataFrame, subway: pd.DataFrame
) -> dict:
    """Compute the per-station context{} block: nearest distance/name/flag for each layer."""
    context = {}
    for station_id, record in stations.items():
        lat, lng = record["lat"], record["lng"]
        nycha_dist, nycha_name = nearest_facility(lat, lng, nycha)
        school_dist, school_name = nearest_facility(lat, lng, schools)
        subway_dist, subway_name = nearest_facility(lat, lng, subway)
        context[station_id] = {
            "nycha_dist_m": round(nycha_dist),
            "school_dist_m": round(school_dist),
            "subway_dist_m": round(subway_dist),
            "nycha_nearest": nycha_name,
            "school_nearest": school_name,
            "subway_nearest": subway_name,
            "near_nycha": int(nycha_dist <= NYCHA_THRESHOLD_M),
            "near_school": int(school_dist <= SCHOOL_THRESHOLD_M),
            "transit_gap": int(subway_dist >= SUBWAY_THRESHOLD_M),
        }
    return context


def build_equity_join_metadata(nycha: pd.DataFrame, schools: pd.DataFrame, subway: pd.DataFrame) -> dict:
    """Top-level equity_join{} block: per-layer source, size, and threshold.

    The school layer's vintage_label lives here, not buried in a comment --
    this is the single block any downstream dashboard/README rendering of
    school-proximity numbers must read from.
    """
    return {
        "layers": {
            "nycha": {
                "domain": NYCHA_DOMAIN,
                "dataset_id": NYCHA_DATASET_ID,
                "n_records": len(nycha),
                "threshold_m": NYCHA_THRESHOLD_M,
            },
            "school": {
                "domain": SCHOOL_DOMAIN,
                "dataset_id": SCHOOL_DATASET_ID,
                "n_records": len(schools),
                "threshold_m": SCHOOL_THRESHOLD_M,
                "vintage_label": SCHOOL_VINTAGE_LABEL,
            },
            "subway": {
                "domain": SUBWAY_DOMAIN,
                "dataset_id": SUBWAY_DATASET_ID,
                "n_records": len(subway),
                "threshold_m": SUBWAY_THRESHOLD_M,
            },
        }
    }


def run_equity_join(flows_path: Path) -> dict:
    """Load flows.json, fetch+join the three equity layers, write the result back in place."""
    payload = json.loads(flows_path.read_text())

    nycha_rows = fetch_socrata_rows(NYCHA_DOMAIN, NYCHA_DATASET_ID)
    school_rows = fetch_socrata_rows(SCHOOL_DOMAIN, SCHOOL_DATASET_ID)
    subway_rows = fetch_socrata_rows(SUBWAY_DOMAIN, SUBWAY_DATASET_ID)

    nycha = nycha_developments_frame(nycha_rows)
    schools = school_locations_frame(school_rows)
    subway = subway_entrances_frame(subway_rows)

    context = build_equity_context(payload["stations"], nycha, schools, subway)
    for station_id, station_context in context.items():
        payload["stations"][station_id]["context"] = station_context

    payload["equity_join"] = build_equity_join_metadata(nycha, schools, subway)

    flows_path.write_text(json.dumps(payload))
    return payload


if __name__ == "__main__":
    import sys

    flows_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "flows.json"
    payload = run_equity_join(flows_path)
    layers = payload["equity_join"]["layers"]
    for layer_name, meta in layers.items():
        print(f"{layer_name}: {meta['n_records']:,} records from {meta['domain']}/{meta['dataset_id']}")
    print(f"wrote context{{}} for {len(payload['stations']):,} stations -> {flows_path}")

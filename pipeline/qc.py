"""QC pass over raw Citi Bike trips: drop bad-duration/bad-location rows, flag departures/arrivals.

Fixed rules (see CLAUDE.md):
- Trips under 60s or over 4h are dropped. Under 60s is usually a false
  start (undock/redock without a real ride); over 4h usually means the
  bike was never cleanly redocked (lost, stolen, or pulled from service).
- Trips with a start or end coordinate outside a generous NYC bounding box
  are dropped (Session 35). Found by accident while designing multi-point
  weather: the full-year station roster (data/flows.json) contained two
  stations literally named "LA Metro Demo 2"/"LA Metro Demo 3" at real Los
  Angeles coordinates (34.03, -118.25) and one ("SYS018", "Bronx WH
  station") at (0, 0) -- off the coast of Africa. All three had near-zero
  ridership and were already excluded from typology clustering as
  low-signal, but still counted toward the station roster and (for the
  zero-coordinate and LA records, both thousands of km from any real NYC
  facility) trivially inflated the equity_join subway-gap flag count.
  This looks like real contamination in Citi Bike's own published S3
  archive (internal warehouse/demo records leaking into the public trip
  export, not corrupted by anything in this pipeline) -- LA Metro Bike
  Share is a real, separate Lyft-operated system, and "WH" reads as
  warehouse/logistics, not a public-facing station.
- Trips missing start_station_id or end_station_id are KEPT (not dropped)
  but flagged via has_valid_departure_station / has_valid_arrival_station
  respectively, so each side of a trip is only counted where it actually
  has a station to attribute it to. A trip missing only end_station_id
  still counts as a valid departure; a trip missing only start_station_id
  still counts as a valid arrival (this is common for electric_bike trips,
  which can start or end outside the formal station network). Because of
  this, station-level departure and arrival totals will NOT balance by
  construction -- this is expected, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 4 * 60 * 60

# A generous tri-state-area bounding box, not a tight NYC-only one --
# chosen with real margin around the actual legit station roster (checked
# directly against data/flows.json: real stations span lat 40.611-40.912,
# lng -74.088 to -73.839), so it has no realistic chance of excluding a
# genuine station even if the system expands into a neighboring state, or
# of tripping over an ordinary test fixture's round-number coordinate
# (e.g. lat=40.0, used throughout this project's own test suite) --
# while still rejecting contamination that's off by hundreds or
# thousands of km (Los Angeles, null island) by a wide margin.
NYC_LAT_RANGE = (39.5, 41.5)
NYC_LNG_RANGE = (-75.0, -73.0)


@dataclass
class QCReport:
    """Row counts through each QC step, for an auditable before/after trail."""

    rows_in: int
    dropped_short: int
    dropped_long: int
    dropped_bad_location: int
    rows_out: int
    missing_start_station: int  # subset of rows_out kept as arrivals only
    missing_end_station: int  # subset of rows_out kept as departures only

    def summary(self) -> str:
        """Human-readable before/after report."""
        return "\n".join(
            [
                f"rows in:                          {self.rows_in:,}",
                f"dropped (< {MIN_DURATION_SECONDS}s):                   {self.dropped_short:,}",
                f"dropped (> {MAX_DURATION_SECONDS // 3600}h):                      {self.dropped_long:,}",
                f"dropped (outside NYC bounding box): {self.dropped_bad_location:,}",
                f"rows out:                         {self.rows_out:,}",
                f"  retained, missing start_station_id: {self.missing_start_station:,}",
                "  -> kept as valid arrivals, excluded from departure counts",
                f"  retained, missing end_station_id: {self.missing_end_station:,}",
                "  -> kept as valid departures, excluded from arrival counts",
                "  -> departure/arrival totals will NOT balance by construction",
            ]
        )


def add_duration_seconds(trips: pd.DataFrame) -> pd.DataFrame:
    """Add a duration_s column computed from started_at/ended_at."""
    trips = trips.copy()
    trips["started_at"] = pd.to_datetime(trips["started_at"])
    trips["ended_at"] = pd.to_datetime(trips["ended_at"])
    trips["duration_s"] = (trips["ended_at"] - trips["started_at"]).dt.total_seconds()
    return trips


def _within_nyc(lat: pd.Series, lng: pd.Series) -> pd.Series:
    """True where (lat, lng) falls inside NYC_LAT_RANGE/NYC_LNG_RANGE, OR
    either coordinate is missing -- a missing coordinate isn't evidence of
    being outside NYC, it's a separate, already-handled gap (see
    has_valid_departure_station/has_valid_arrival_station below); this
    check's only job is rejecting a coordinate that's actively implausible,
    not standing in for missing-data handling.
    """
    in_box = (
        lat.between(NYC_LAT_RANGE[0], NYC_LAT_RANGE[1])
        & lng.between(NYC_LNG_RANGE[0], NYC_LNG_RANGE[1])
    )
    return in_box | lat.isna() | lng.isna()


def flag_valid_arrival(trips: pd.DataFrame) -> pd.DataFrame:
    """Add a has_valid_arrival_station column: False where end_station_id is missing."""
    trips = trips.copy()
    trips["has_valid_arrival_station"] = trips["end_station_id"].notna()
    return trips


def flag_valid_departure(trips: pd.DataFrame) -> pd.DataFrame:
    """Add a has_valid_departure_station column: False where start_station_id is missing."""
    trips = trips.copy()
    trips["has_valid_departure_station"] = trips["start_station_id"].notna()
    return trips


def run_qc(trips: pd.DataFrame) -> tuple[pd.DataFrame, QCReport]:
    """Drop short/long-duration and implausible-location trips; flag (not drop)
    trips with no departure/arrival station.
    """
    rows_in = len(trips)

    trips = add_duration_seconds(trips)
    is_short = trips["duration_s"] < MIN_DURATION_SECONDS
    is_long = trips["duration_s"] > MAX_DURATION_SECONDS

    duration_clean = trips[~(is_short | is_long)]
    is_bad_location = ~(
        _within_nyc(duration_clean["start_lat"], duration_clean["start_lng"])
        & _within_nyc(duration_clean["end_lat"], duration_clean["end_lng"])
    )

    clean = duration_clean[~is_bad_location].copy()
    clean = flag_valid_arrival(clean)
    clean = flag_valid_departure(clean)

    report = QCReport(
        rows_in=rows_in,
        dropped_short=int(is_short.sum()),
        dropped_long=int(is_long.sum()),
        dropped_bad_location=int(is_bad_location.sum()),
        rows_out=len(clean),
        missing_start_station=int((~clean["has_valid_departure_station"]).sum()),
        missing_end_station=int((~clean["has_valid_arrival_station"]).sum()),
    )
    return clean, report


if __name__ == "__main__":
    import sys

    from pipeline.download import download_month, load_trips

    year_month = sys.argv[1] if len(sys.argv) > 1 else "2026-04"
    zip_path = download_month(year_month)
    trips = load_trips(zip_path)
    clean, report = run_qc(trips)
    print(report.summary())

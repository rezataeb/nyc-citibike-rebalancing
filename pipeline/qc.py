"""QC pass over raw Citi Bike trips: drop bad-duration rows, flag departures/arrivals.

Fixed rules (see CLAUDE.md):
- Trips under 60s or over 4h are dropped. Under 60s is usually a false
  start (undock/redock without a real ride); over 4h usually means the
  bike was never cleanly redocked (lost, stolen, or pulled from service).
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


@dataclass
class QCReport:
    """Row counts through each QC step, for an auditable before/after trail."""

    rows_in: int
    dropped_short: int
    dropped_long: int
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
    """Drop short/long-duration trips; flag (not drop) trips with no departure/arrival station."""
    rows_in = len(trips)

    trips = add_duration_seconds(trips)
    is_short = trips["duration_s"] < MIN_DURATION_SECONDS
    is_long = trips["duration_s"] > MAX_DURATION_SECONDS

    clean = trips[~(is_short | is_long)].copy()
    clean = flag_valid_arrival(clean)
    clean = flag_valid_departure(clean)

    report = QCReport(
        rows_in=rows_in,
        dropped_short=int(is_short.sum()),
        dropped_long=int(is_long.sum()),
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

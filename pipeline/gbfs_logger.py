"""Poll Citi Bike's live GBFS feed and append station-status snapshots to a local log.

Trip archives only record trips that succeeded -- they cannot see a rider
who found an empty station and left, or one who couldn't dock because every
slot was full. GBFS's station_status feed reports live bikes/docks counts
with no auth and no cost; snapshotting it on a schedule (a free GitHub
Actions cron, e.g. every 10 minutes) turns that invisible failure mode into
a directly measured one. This module is just the capture step -- turning
the log into empty/full minutes per station is empty_minutes.py's job,
once enough snapshots have accumulated.

station_status.json only carries GBFS's own internal station_id (a long
UUID-like string) and a legacy_id (a plain integer) -- neither matches the
"6433.01"-style station numbering used everywhere else in this project
(flows.json, trip data). That numbering only exists as short_name in the
separate station_information feed (station metadata: name/lat/lng/capacity,
changes rarely). So each snapshot also fetches station_information to
resolve GBFS's station_id to short_name, logging short_name as the
station_id column -- directly joinable to flows.json later with no
separate crosswalk step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

STATION_STATUS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"
STATION_INFORMATION_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_information.json"
LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "gbfs_log" / "snapshots.csv"


@dataclass
class SnapshotResult:
    """One poll's rows plus a dropped-station count, for an auditable per-run trail.

    n_dropped is stations present in station_status with no matching
    station_information entry -- currently always 0 against the live feed
    (verified directly), but the two are independent HTTP requests and
    could drift out of sync under an unattended cron. Reporting it on every
    run, even at 0, is the only way that drift would ever be noticed.
    """

    rows: pd.DataFrame
    n_dropped: int

    def summary(self) -> str:
        return f"logged {len(self.rows):,} rows, dropped {self.n_dropped:,} (no station_information match)"


def fetch_station_status() -> dict:
    """GET the live station_status feed."""
    response = requests.get(STATION_STATUS_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_station_id_crosswalk() -> dict[str, str]:
    """GET station_information and return {gbfs station_id: short_name}.

    short_name is the "6433.01"-style id used throughout this project;
    station_status never carries it directly (see module docstring).
    """
    response = requests.get(STATION_INFORMATION_URL, timeout=30)
    response.raise_for_status()
    stations = response.json()["data"]["stations"]
    return {station["station_id"]: station["short_name"] for station in stations}


def parse_snapshot(status_payload: dict, crosswalk: dict[str, str]) -> SnapshotResult:
    """Build one row per station from a station_status payload.

    timestamp is the feed's own last_updated (unix epoch, UTC) -- when Citi
    Bike's backend last updated the data -- rather than our local poll time,
    since that's the accurate "as-of" for the interval math empty_minutes.py
    will do later. Stations present in station_status but missing from the
    crosswalk (e.g. added between the two fetches) are dropped rather than
    logged with a null station_id; see SnapshotResult.n_dropped.
    """
    timestamp = pd.to_datetime(status_payload["last_updated"], unit="s", utc=True)
    records = []
    n_dropped = 0
    for station in status_payload["data"]["stations"]:
        short_name = crosswalk.get(station["station_id"])
        if short_name is None:
            n_dropped += 1
            continue
        records.append(
            {
                "station_id": short_name,
                "timestamp": timestamp,
                "bikes_available": station["num_bikes_available"],
                "docks_available": station["num_docks_available"],
                "is_renting": station["is_renting"],
                "is_returning": station["is_returning"],
            }
        )
    return SnapshotResult(rows=pd.DataFrame.from_records(records), n_dropped=n_dropped)


def append_snapshot(snapshot: pd.DataFrame, log_path: Path = LOG_PATH) -> None:
    """Append a snapshot to the CSV log, writing the header only on the first write."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(log_path, mode="a", header=not log_path.exists(), index=False)


def log_snapshot(log_path: Path = LOG_PATH) -> SnapshotResult:
    """Fetch one snapshot of the live GBFS feed and append it to the log."""
    status_payload = fetch_station_status()
    crosswalk = fetch_station_id_crosswalk()
    result = parse_snapshot(status_payload, crosswalk)
    append_snapshot(result.rows, log_path)
    return result


if __name__ == "__main__":
    result = log_snapshot()
    as_of = result.rows["timestamp"].iloc[0] if len(result.rows) else "n/a"
    print(f"as of {as_of} -> {LOG_PATH}")
    print(result.summary())

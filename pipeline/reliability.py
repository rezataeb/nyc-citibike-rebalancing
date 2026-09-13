"""Turn the GBFS snapshot log into an observed station-unusability RATE.

gbfs_logger.py's docstring promised an `empty_minutes.py` that would convert
the log into empty/full *minutes* per station. That module was never written,
and the log as actually collected cannot support it. The snapshots are ~hourly,
not the 10-minute cadence that docstring assumed:

    190 snapshots over 12 days -- gap median 85 min, mean 98 min, max 236 min
    (only 3 of 189 gaps are <=10 min; 48 exceed 120 min)

The contract's rebalancing standard is time-based -- a station must not sit
completely full or empty past 60 min (peak) or 120 min (off-peak). Measuring a
60-minute breach from a median 85-minute sampling gap is not possible: a station
observed empty at 14:00 and again at 15:15 may have been refilled at 14:05.
Any "outage hours" figure -- and therefore any $50/hr penalty figure -- would be
an inference at roughly the same resolution as the thing it claims to measure.

So this module reports what ~hourly sampling genuinely supports: the share of
station-observations in which the station was unusable. Each row of the log is
an independent point-in-time read of "could a rider use this station right now,"
which needs no duration resolution at all. It is also the Comptroller's own
framing -- "Bronx riders are 89% more likely to hit an unusable station" is a
ratio of exactly this quantity, not a duration -- so the per-station rates here
are the input an equity-disparity metric needs later.

Method, stated plainly:
  - unusable observation := bikes_available == 0 (cannot rent)
                         or docks_available == 0 (cannot return)
  - offline observations (is_renting or is_returning == 0) are dropped from
    BOTH numerator and denominator, per this project's fixed QC rule that
    offline stations are excluded from failure denominators.
  - rate := unusable observations / online observations, observation-weighted.

Observation-weighted, not station-weighted: a station observed 190 times
contributes 190 chances to have been caught unusable. That matches the rider's-
eye question ("how often is a station I walk up to unusable?") and avoids
letting sparsely-observed stations swing the system figure.

This is a rate over a fixed, closed, historical window -- NOT a live reading and
NOT a 24-hour figure. The window is stamped into the output so the dashboard can
label it on the tile's face.

Run:
    python3 pipeline/reliability.py
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "gbfs_log"
LOG_PATH = LOG_DIR / "snapshots.csv"  # the frozen pre-Session-71 file -- still read, via LOG_DIR's glob below
RELIABILITY_PATH = Path(__file__).resolve().parent.parent / "data" / "reliability.json"

# Per-station rates below this many online observations are too thin to rank on
# (the same reasoning as this project's low-volume forecasting exclusion). They
# are still counted in the system total -- the exclusion is about per-station
# display, not about discarding real observations.
MIN_OBSERVATIONS = 20

CAVEAT = (
    "Observed unusable RATE over a closed historical window -- the share of "
    "station-observations in which the station had 0 bikes or 0 docks. This is "
    "NOT outage hours and NOT a penalty figure: the snapshot log is ~hourly "
    "(median gap 85 min), which cannot resolve the contract's 60/120-minute "
    "breach thresholds. Offline observations are excluded from both numerator "
    "and denominator. Not a live reading."
)


def _is_online(row: dict) -> bool:
    """True when the feed reported the station both renting and returning."""
    return row["is_renting"].strip() == "1" and row["is_returning"].strip() == "1"


def _is_unusable(bikes: int, docks: int) -> bool:
    """True when a rider could neither take a bike nor return one."""
    return bikes == 0 or docks == 0


def _iter_log_paths(log_path: Path) -> list[Path]:
    """A single file is read as-is -- every existing test passes one, and
    that keeps working unchanged. A directory (LOG_DIR, the real default
    since Session 71) is every snapshots*.csv file inside it, sorted so the
    frozen pre-rotation snapshots.csv (no date suffix, sorts first) is read
    before the dated files that continue its history -- not that read
    ORDER matters for correctness here (every count below is a running
    total and timestamps go through a global sort of their own), just for
    a stable, boring iteration order.
    """
    if log_path.is_dir():
        return sorted(log_path.glob("snapshots*.csv"))
    return [log_path]


def compute_reliability(log_path: Path = LOG_DIR) -> dict:
    """Read the snapshot log (or, as of Session 71, every rotated snapshot
    log file in a directory) and return the reliability payload.

    Streams each CSV rather than loading it into a DataFrame -- the combined
    history is millions of rows and every quantity here is a running count,
    so there is nothing a DataFrame would buy beyond memory.
    """
    timestamps: set[str] = set()
    per_station: dict[str, dict[str, int]] = {}
    n_online = 0
    n_unusable = 0
    n_empty = 0
    n_full = 0
    n_offline = 0

    for path in _iter_log_paths(log_path):
        with path.open() as handle:
            for row in csv.DictReader(handle):
                timestamps.add(row["timestamp"])
                station_id = row["station_id"]

                if not _is_online(row):
                    n_offline += 1
                    continue

                bikes = int(float(row["bikes_available"]))
                docks = int(float(row["docks_available"]))

                counts = per_station.setdefault(station_id, {"n_observations": 0, "n_unusable": 0})
                counts["n_observations"] += 1
                n_online += 1

                if _is_unusable(bikes, docks):
                    counts["n_unusable"] += 1
                    n_unusable += 1
                    if bikes == 0:
                        n_empty += 1
                    if docks == 0:
                        n_full += 1

    moments = sorted(datetime.fromisoformat(value) for value in timestamps)
    gaps = [
        (moments[i + 1] - moments[i]).total_seconds() / 60
        for i in range(len(moments) - 1)
    ]

    stations = {
        station_id: {
            "unusable_rate": counts["n_unusable"] / counts["n_observations"],
            "n_observations": counts["n_observations"],
            "n_unusable": counts["n_unusable"],
        }
        for station_id, counts in per_station.items()
    }
    rankable = [
        entry["unusable_rate"]
        for entry in stations.values()
        if entry["n_observations"] >= MIN_OBSERVATIONS
    ]

    return {
        "window": {
            "start": moments[0].isoformat(),
            "end": moments[-1].isoformat(),
            "days": (moments[-1] - moments[0]).days,
            "n_snapshots": len(moments),
            "median_gap_minutes": round(statistics.median(gaps), 1) if gaps else None,
            "max_gap_minutes": round(max(gaps), 1) if gaps else None,
        },
        "system": {
            "unusable_rate": n_unusable / n_online,
            "n_observations": n_online,
            "n_unusable": n_unusable,
            "n_empty": n_empty,
            "n_full": n_full,
            "n_offline_excluded": n_offline,
        },
        "per_station_summary": {
            "n_stations": len(stations),
            "n_rankable": len(rankable),
            "min_observations": MIN_OBSERVATIONS,
            "median_rate": statistics.median(rankable) if rankable else None,
            "max_rate": max(rankable) if rankable else None,
        },
        "stations": stations,
        "caveat": CAVEAT,
    }


if __name__ == "__main__":
    payload = compute_reliability()

    RELIABILITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    RELIABILITY_PATH.write_text(json.dumps(payload, indent=2))

    window = payload["window"]
    system = payload["system"]
    summary = payload["per_station_summary"]
    print(f"  window       {window['start']} -> {window['end']} ({window['days']} days)")
    print(f"  snapshots    {window['n_snapshots']} (median gap {window['median_gap_minutes']} min)")
    print(f"  observations {system['n_observations']:,} online, {system['n_offline_excluded']:,} offline excluded")
    print(f"  unusable     {system['n_unusable']:,} = {system['unusable_rate'] * 100:.1f}%")
    print(f"  per-station  {summary['n_rankable']}/{summary['n_stations']} rankable (>={summary['min_observations']} obs)")
    print(f"wrote {RELIABILITY_PATH}")

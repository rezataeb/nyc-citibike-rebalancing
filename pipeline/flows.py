"""Compute per-station net flow (arrivals - departures) and export flows.json.

net_flow = arrivals - departures, per (station, month, day_type, hour).
Negative means the station is draining (deficit risk: no bikes); positive
means it's filling (surplus risk: no open docks).

Raw monthly totals aren't directly comparable across day types -- a month
has far more weekdays than weekend days -- so counts are normalized by the
number of distinct calendar dates observed in that (month, day_type)
bucket, giving a per-day rate.

Season/month coverage in the output (seasons{}, months{}, granularity) is
derived entirely from whichever months are present in the input trips.
Nothing here assumes a full year is loaded: with one month, seasons{} has
exactly the season(s) that month belongs to, and months{} has exactly that
one key. Adding more months later requires no changes to this module.

Uses the has_valid_departure_station / has_valid_arrival_station flags from
pipeline/qc.py: a trip missing one side's station still contributes to the
other side's count (see qc.py's docstring for why).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# Fixed season map, see CLAUDE.md: winter Dec-Feb, spring Mar-May,
# summer Jun-Aug, fall Sep-Nov.
SEASON_OF = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}
SEASON_ORDER = ("winter", "spring", "summer", "fall")


def _day_type(timestamps: pd.Series) -> pd.Series:
    """Map timestamps to 'weekday' or 'weekend'."""
    return timestamps.dt.dayofweek.map(lambda d: "weekend" if d >= 5 else "weekday")


def _aggregate_side(
    trips: pd.DataFrame,
    station_id_col: str,
    station_name_col: str,
    lat_col: str,
    lng_col: str,
    time_col: str,
    valid_flag_col: str,
    sign: int,
) -> pd.DataFrame:
    """Group one side of trips (departures or arrivals) into signed counts.

    Only rows where valid_flag_col is True contribute -- e.g. a trip with
    no end station is excluded from the arrivals side but still included
    on the departures side.
    """
    side = trips.loc[
        trips[valid_flag_col], [station_id_col, station_name_col, lat_col, lng_col, time_col]
    ].copy()
    side.columns = ["station_id", "station_name", "lat", "lng", "ts"]
    side["hour"] = side["ts"].dt.hour
    side["month"] = side["ts"].dt.strftime("%Y-%m")
    side["day_type"] = _day_type(side["ts"])

    grouped = (
        side.groupby(["station_id", "station_name", "month", "day_type", "hour"])
        .agg(count=("ts", "size"), lat=("lat", "median"), lng=("lng", "median"))
        .reset_index()
    )
    grouped["count"] *= sign
    return grouped


def _distinct_dates_per_bucket(clean_trips: pd.DataFrame) -> pd.DataFrame:
    """Count distinct calendar dates observed per (month, day_type) bucket.

    This is the denominator that turns a monthly total into a per-day rate.
    Uses the union of trip start dates and end dates: a trip can start on
    one calendar date and end on the next (e.g. a ride just before
    midnight on the last day of the month), and arrivals are keyed by end
    date while departures are keyed by start date. Using start dates only
    leaves spillover months with no denominator at all (NaN net_per_day
    for that bucket) -- using the union covers both sides.
    """

    def _dates(ts_col: str) -> pd.DataFrame:
        ts = clean_trips[ts_col]
        return pd.DataFrame(
            {"month": ts.dt.strftime("%Y-%m"), "day_type": _day_type(ts), "date": ts.dt.normalize()}
        )

    dates = pd.concat([_dates("started_at"), _dates("ended_at")], ignore_index=True)
    dates = dates.drop_duplicates()
    return (
        dates.groupby(["month", "day_type"])["date"]
        .nunique()
        .rename("n_days")
        .reset_index()
    )


def compute_net_flow(clean_trips: pd.DataFrame) -> pd.DataFrame:
    """Net flow per (station, month, day_type, hour), normalized to a per-day rate.

    Expects clean_trips to already have has_valid_departure_station and
    has_valid_arrival_station columns (see pipeline/qc.py run_qc).
    """
    arrivals = _aggregate_side(
        clean_trips,
        "end_station_id", "end_station_name", "end_lat", "end_lng",
        "ended_at", "has_valid_arrival_station", sign=+1,
    )
    departures = _aggregate_side(
        clean_trips,
        "start_station_id", "start_station_name", "start_lat", "start_lng",
        "started_at", "has_valid_departure_station", sign=-1,
    )
    both = pd.concat([arrivals, departures], ignore_index=True)
    flows = (
        both.groupby(["station_id", "station_name", "month", "day_type", "hour"])
        .agg(net=("count", "sum"), lat=("lat", "median"), lng=("lng", "median"))
        .reset_index()
    )

    n_days = _distinct_dates_per_bucket(clean_trips)
    flows = flows.merge(n_days, on=["month", "day_type"], how="left")
    flows["net_per_day"] = (flows["net"] / flows["n_days"].clip(lower=1)).round(3)
    return flows


def add_season(flows: pd.DataFrame) -> pd.DataFrame:
    """Add a season column derived from month, using the fixed SEASON_OF map."""
    flows = flows.copy()
    flows["season"] = flows["month"].map(lambda m: SEASON_OF[int(m.split("-")[1])])
    return flows


def _curve_from(group: pd.DataFrame) -> dict:
    """Collapse (day_type, hour, net_per_day, n_days) rows into 24h weekday/weekend curves.

    Each contributing month is weighted by its own distinct-date count, so
    a 31-day month counts for more than a 28-day one. With a single month
    contributing, this reduces to that month's own curve.
    """
    curve = {"weekday": [0.0] * 24, "weekend": [0.0] * 24}
    for day_type in ("weekday", "weekend"):
        subset = group[group["day_type"] == day_type]
        if subset.empty:
            continue
        weighted = subset.groupby("hour").apply(
            lambda x: (x["net_per_day"] * x["n_days"]).sum() / x["n_days"].sum(),
            include_groups=False,
        )
        for hour, value in weighted.round(3).items():
            curve[day_type][int(hour)] = float(value)
    return curve


def export_flows(flows: pd.DataFrame, out_path: Path | None = None) -> dict:
    """Build the flows.json payload: per-station curves plus seasons{}/months{}.

    months{} and seasons{} only ever contain keys for what's actually in
    `flows` -- see module docstring. cluster/typology/equity fields from
    the full flows.json contract in CLAUDE.md are not added here; they
    belong to a later session's join step.
    """
    flows = add_season(flows)
    months_present = sorted(flows["month"].unique())
    seasons_present = [s for s in SEASON_ORDER if s in set(flows["season"])]

    stations = {}
    for station_id, group in flows.groupby("station_id"):
        name = group["station_name"].mode().iat[0]
        record = {
            "name": name,
            "lat": round(float(group["lat"].median()), 5),
            "lng": round(float(group["lng"].median()), 5),
            **_curve_from(group),  # all-period average across whatever months are present
            "seasons": {},
            "months": {},
        }
        for season, season_group in group.groupby("season"):
            record["seasons"][season] = _curve_from(season_group)
        for month, month_group in group.groupby("month"):
            record["months"][month] = _curve_from(month_group)
        stations[str(station_id)] = record

    payload = {
        "granularity": {"months": months_present, "seasons": seasons_present},
        "stations": stations,
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload))
    return payload


if __name__ == "__main__":
    import sys

    from pipeline.download import download_month, load_trips
    from pipeline.qc import run_qc

    year_month = sys.argv[1] if len(sys.argv) > 1 else "2026-04"
    zip_path = download_month(year_month)
    trips = load_trips(zip_path)
    clean, report = run_qc(trips)
    print(report.summary())

    flows = compute_net_flow(clean)
    out_path = Path(__file__).resolve().parent.parent / "data" / "flows.json"
    payload = export_flows(flows, out_path=out_path)
    print(f"\nmonths: {payload['granularity']['months']}")
    print(f"seasons: {payload['granularity']['seasons']}")
    print(f"stations: {len(payload['stations']):,}")
    print(f"wrote {out_path}")

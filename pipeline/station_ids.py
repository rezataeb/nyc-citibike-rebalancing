"""Canonical station ids, and a one-time repair of the daily flow table.

Two data problems found 2026-11-02 while building AI briefings (both were
also in live v1.0):

1. Split ids. Citi Bike's own trip files sometimes write a station id with
   its trailing zero dropped ("5980.1" for "5980.10") -- in every month,
   even with ids read as text (download.load_trips has forced str since
   71f168f, so this is the source data, not pandas). One station's trips
   were split across two records: 100 stations, 573,550 trip-ends.
2. A window leak. process_month never clipped rows to their own month, so
   trips that started Jun 30, 2025 in the July 2025 archive survived
   update_window.py's month drop: 422 rows dated 2025-06-30.

Method, stated plainly:
  - A one-decimal id "X.1" is merged into "X.10" ONLY when "X.10" is also
    present. Never padded blindly: 3576.1, 3593.1 and 6131.1 really are
    "X.1" in the live GBFS feed and have no twin.
  - Suffix ids are merged only from an explicit, user-approved list
    (SUFFIX_ID_MERGES): alternate ids at the same spot (0-33 m from their
    base) that the live feed does not know. 5348.06_old (an old location
    290 m away) and 5303.06_ (live in its own right) stay separate.
  - After remapping, rows for the same station/date/hour are summed, so no
    trip is lost or counted twice.
  - Rows outside TARGET_MONTHS are dropped.

Run once (rewrites daily_net_flow.parquet, flows.json, data_manifest.json):
    python3 -m pipeline.station_ids
then rerun the dependent steps (see build log).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

import pandas as pd

# Alternate trip-system ids at the same spot as their base id, absent from the
# live feed (approved 2026-11-02). Explicit list -- never matched by pattern.
SUFFIX_ID_MERGES = {
    "3184.07_OLD": "3184.07",  # 0 m, Jun 2026 only
    "5308.04_": "5308.04",  # 0 m
    "6517.08_": "6517.08",  # 12 m
    "6569.09_": "6569.09",  # 33 m
}

_ONE_DECIMAL = re.compile(r"^\d+\.\d$")


def canonical_id_map(ids: Iterable[str]) -> dict[str, str]:
    """Map each non-canonical id in `ids` to its canonical form.

    "X.1" -> "X.10" only when "X.10" is also in `ids`; suffix ids only from
    SUFFIX_ID_MERGES and only when their base is present. Ids not in the
    result are already canonical.
    """
    present = {i for i in ids if isinstance(i, str)}
    mapping = {
        i: i + "0" for i in present if _ONE_DECIMAL.match(i) and i + "0" in present
    }
    mapping.update(
        {alt: base for alt, base in SUFFIX_ID_MERGES.items() if alt in present and base in present}
    )
    return mapping


def canonicalize_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Replace non-canonical ids in `columns`, using every id seen across them."""
    seen = pd.concat([frame[c] for c in columns]).dropna().unique()
    mapping = canonical_id_map(seen)
    out = frame.copy()
    for column in columns:
        out[column] = out[column].replace(mapping)
    return out


def repair_daily(daily: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    """Canonical ids, merged duplicate rows, and only rows inside `months`.

    Merged rows sum their counts; the canonical station's own name and
    coordinates are kept (taken from its own rows, not the fragment's).
    """
    in_window = daily[daily["date"].dt.strftime("%Y-%m").isin(months)]
    mapping = canonical_id_map(in_window["station_id"].unique())
    canonical_meta = (
        in_window[~in_window["station_id"].isin(mapping)]
        .groupby("station_id")
        .agg(station_name=("station_name", lambda s: s.mode().iat[0]), lat=("lat", "median"), lng=("lng", "median"))
    )
    remapped = in_window.assign(station_id=in_window["station_id"].replace(mapping))
    summed = (
        remapped.groupby(["station_id", "date", "hour"], as_index=False)[["arrivals_count", "departures_count", "net"]]
        .sum()
    )
    return (
        summed.join(canonical_meta, on="station_id")
        [daily.columns.tolist()]
        .sort_values(["station_id", "date", "hour"])
        .reset_index(drop=True)
    )


def main() -> None:
    from datetime import datetime, timezone

    from pipeline.build_full_year import (
        DAILY_NET_FLOW_PATH, DATA_DIR, FLOWS_PATH, MANIFEST_PATH, TARGET_MONTHS, _coverage_by_station,
    )
    from pipeline.equity_join import run_equity_join
    from pipeline.flows import export_flows
    from pipeline.station_typology import apply_typology
    from pipeline.update_window import daily_parquet_to_monthly_frame

    daily = pd.read_parquet(DAILY_NET_FLOW_PATH)
    mapping = canonical_id_map(daily["station_id"].unique())
    repaired = repair_daily(daily, TARGET_MONTHS)
    print(f"merged ids: {len(mapping)} ({sum(1 for k in mapping if k in SUFFIX_ID_MERGES)} suffix)")
    print(f"rows: {len(daily):,} -> {len(repaired):,}; stations: {daily['station_id'].nunique():,} -> {repaired['station_id'].nunique():,}")
    print(f"dates: {repaired['date'].min().date()} .. {repaired['date'].max().date()}")
    for column in ("arrivals_count", "departures_count"):
        lost = int(daily[daily["date"].dt.strftime('%Y-%m').isin(TARGET_MONTHS)][column].sum() - repaired[column].sum())
        print(f"{column} lost in merge: {lost}")
    repaired.to_parquet(DAILY_NET_FLOW_PATH, index=False)

    export_flows(daily_parquet_to_monthly_frame(repaired), out_path=FLOWS_PATH)
    FLOWS_PATH.write_text(json.dumps(apply_typology(json.loads(FLOWS_PATH.read_text()))))
    run_equity_join(FLOWS_PATH)

    coverage = _coverage_by_station(repaired)
    manifest = json.loads(MANIFEST_PATH.read_text())
    manifest.update({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_stations_any_coverage": int(len(coverage)),
        "n_stations_full_coverage": int((coverage["months_present"].apply(len) == len(TARGET_MONTHS)).sum()),
        "station_id_repair": "pipeline/station_ids.py (2026-11-02)",
    })
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {DAILY_NET_FLOW_PATH.name}, {FLOWS_PATH.name}, {MANIFEST_PATH.name} in {DATA_DIR}")


if __name__ == "__main__":
    main()

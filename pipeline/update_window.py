"""Incremental data-window update: drop Jul/Aug 2025, add Jul/Aug 2026.

Avoids re-downloading the 10 already-processed months. Specifically:
  1. Downloads and QCs only 2026-07 and 2026-08 from Citi Bike S3.
  2. Loads the existing daily_net_flow.parquet, filters out rows whose
     date falls in 2025-07 or 2025-08, appends the two new months.
  3. Reconstructs the (station, month, day_type, hour) monthly frame from
     the updated daily table, then regenerates flows.json via the same
     export_flows -> apply_typology -> equity_join chain build_full_year uses.
  4. Writes an updated data_manifest.json.

Run this ONCE after updating TARGET_MONTHS / WEATHER_FETCH_* in the three
source files (build_full_year.py, elasticities.py, demand_model.py).
After this script succeeds, run:
  python3 -m pipeline.reproduce_all --skip-download
to rebuild elasticities, backtest, fleet_simulator, and the rest.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.build_full_year import (
    DATA_DIR,
    DAILY_NET_FLOW_PATH,
    FLOWS_PATH,
    MANIFEST_PATH,
    TARGET_MONTHS,
    _coverage_by_station,
    process_month,
)
from pipeline.equity_join import run_equity_join
from pipeline.flows import export_flows
from pipeline.station_typology import apply_typology

ADD_MONTHS = ["2026-07", "2026-08"]
DROP_MONTHS = ["2025-07", "2025-08"]


def daily_parquet_to_monthly_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct the (station, month, day_type, hour, net_per_day) frame
    that export_flows expects, from the daily_net_flow parquet format.

    The parquet stores: station_id, station_name, date, hour, lat, lng, net.
    export_flows needs: station_id, station_name, month, day_type, hour,
    lat, lng, net_per_day  (net averaged over all days in each bucket).
    """
    df = daily.copy()
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["day_type"] = df["date"].dt.dayofweek.map(
        lambda d: "weekend" if d >= 5 else "weekday"
    )

    # Count distinct dates per (month, day_type) — the denominator for net_per_day
    n_days = (
        df.groupby(["month", "day_type"])["date"]
        .nunique()
        .rename("n_days")
        .reset_index()
    )

    # Sum net trips per (station, month, day_type, hour)
    grouped = (
        df.groupby(["station_id", "station_name", "month", "day_type", "hour"])
        .agg(net=("net", "sum"), lat=("lat", "median"), lng=("lng", "median"))
        .reset_index()
    )

    # Attach n_days and compute per-day rate
    grouped = grouped.merge(n_days, on=["month", "day_type"], how="left")
    grouped["net_per_day"] = (grouped["net"] / grouped["n_days"].clip(lower=1)).round(3)
    return grouped


def main() -> None:
    assert ADD_MONTHS[0] in TARGET_MONTHS, (
        "TARGET_MONTHS in build_full_year.py still contains old window — "
        "update it to Sep 2025–Aug 2026 first."
    )
    assert DROP_MONTHS[0] not in TARGET_MONTHS, (
        f"{DROP_MONTHS[0]} is still in TARGET_MONTHS — remove it first."
    )

    print("=== Step 1: download + QC the two new months ===")
    new_daily_frames = []
    for ym in ADD_MONTHS:
        _monthly, daily, _schema = process_month(ym)
        new_daily_frames.append(daily)
    new_daily = pd.concat(new_daily_frames, ignore_index=True)

    print("\n=== Step 2: update daily_net_flow.parquet ===")
    existing_daily = pd.read_parquet(DAILY_NET_FLOW_PATH)
    print(f"  existing: {len(existing_daily):,} rows, "
          f"{existing_daily['date'].min().date()} .. {existing_daily['date'].max().date()}")

    drop_mask = existing_daily["date"].dt.strftime("%Y-%m").isin(DROP_MONTHS)
    trimmed = existing_daily[~drop_mask].copy()
    print(f"  dropped {drop_mask.sum():,} rows from {DROP_MONTHS}")

    combined = (
        pd.concat([trimmed, new_daily], ignore_index=True)
        .sort_values(["station_id", "date", "hour"])
        .reset_index(drop=True)
    )
    print(f"  combined: {len(combined):,} rows, "
          f"{combined['date'].min().date()} .. {combined['date'].max().date()}")
    combined.to_parquet(DAILY_NET_FLOW_PATH, index=False)
    print(f"  wrote {DAILY_NET_FLOW_PATH}")

    print("\n=== Step 3: rebuild flows.json ===")
    monthly_frame = daily_parquet_to_monthly_frame(combined)
    export_flows(monthly_frame, out_path=FLOWS_PATH)
    payload = apply_typology(json.loads(FLOWS_PATH.read_text()))
    FLOWS_PATH.write_text(json.dumps(payload))
    payload = run_equity_join(FLOWS_PATH)
    print(f"  wrote {FLOWS_PATH}  ({FLOWS_PATH.stat().st_size / 1e6:.1f} MB)")

    print("\n=== Step 4: update manifest ===")
    coverage = _coverage_by_station(combined)
    n_full = int((coverage["months_present"].apply(len) == len(TARGET_MONTHS)).sum())
    manifest = {
        "months": TARGET_MONTHS,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_stations_any_coverage": int(len(coverage)),
        "n_stations_full_coverage": n_full,
        "flows_path": str(FLOWS_PATH.relative_to(DATA_DIR.parent)),
        "daily_net_flow_path": str(DAILY_NET_FLOW_PATH.relative_to(DATA_DIR.parent)),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"  months: {manifest['months'][0]} .. {manifest['months'][-1]}")
    print(f"  stations any coverage:  {manifest['n_stations_any_coverage']:,}")
    print(f"  stations full coverage: {manifest['n_stations_full_coverage']:,}")

    print("\n=== Done — run `python3 -m pipeline.reproduce_all --skip-download` next ===")


if __name__ == "__main__":
    main()

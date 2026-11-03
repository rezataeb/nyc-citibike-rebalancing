"""Roll the trip window to pipeline/window.py's TARGET_MONTHS, incrementally.

Works out which months to add and drop by comparing data_manifest.json's
current months with TARGET_MONTHS, so rolling forward is one edit to
pipeline/window.py and one run of this script -- no month lists to type.
Avoids re-downloading months already processed:
  1. Downloads and QCs only the months to add, from Citi Bike's S3 archive.
  2. Loads daily_net_flow.parquet, drops the months that left the window,
     appends the new ones, and runs station_ids.repair_daily over the result
     (canonical ids across all months; rows outside the window dropped --
     the Jun 30, 2025 leak this guards against is in station_ids.py).
  3. Rebuilds flows.json via export_flows -> apply_typology -> equity_join.
  4. Writes data_manifest.json.
Then run:
  python3 -m pipeline.reproduce_all --skip-download
to rebuild elasticities, the backtest, the fleet simulator and the rest.
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
from pipeline.station_ids import repair_daily
from pipeline.station_typology import apply_typology


def plan_window(current: list[str], target: list[str]) -> tuple[list[str], list[str]]:
    """(months to add, months to drop) to turn `current` into `target`, each sorted."""
    return sorted(set(target) - set(current)), sorted(set(current) - set(target))


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
    current = json.loads(MANIFEST_PATH.read_text())["months"]
    add_months, drop_months = plan_window(current, TARGET_MONTHS)
    print(f"window: {current[0]}..{current[-1]} -> {TARGET_MONTHS[0]}..{TARGET_MONTHS[-1]}")
    print(f"  add {add_months or 'nothing'}, drop {drop_months or 'nothing'}")
    if not add_months and not drop_months:
        print("already at the target window -- nothing to do")
        return

    print("=== Step 1: download + QC the new months ===")
    new_daily_frames = []
    for ym in add_months:
        _monthly, daily, _schema = process_month(ym)
        new_daily_frames.append(daily)
    new_daily = pd.concat(new_daily_frames, ignore_index=True) if new_daily_frames else None

    print("\n=== Step 2: update daily_net_flow.parquet ===")
    existing_daily = pd.read_parquet(DAILY_NET_FLOW_PATH)
    print(f"  existing: {len(existing_daily):,} rows, "
          f"{existing_daily['date'].min().date()} .. {existing_daily['date'].max().date()}")

    drop_mask = existing_daily["date"].dt.strftime("%Y-%m").isin(drop_months)
    trimmed = existing_daily[~drop_mask].copy()
    print(f"  dropped {drop_mask.sum():,} rows from {drop_months}")

    combined = pd.concat([trimmed] + ([new_daily] if new_daily is not None else []), ignore_index=True)
    combined = (
        repair_daily(combined, TARGET_MONTHS)  # canonical ids across months; nothing outside the window
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
        "station_id_repair": "pipeline/station_ids.py, applied by update_window.py",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"  months: {manifest['months'][0]} .. {manifest['months'][-1]}")
    print(f"  stations any coverage:  {manifest['n_stations_any_coverage']:,}")
    print(f"  stations full coverage: {manifest['n_stations_full_coverage']:,}")

    print("\n=== Done — run `python3 -m pipeline.reproduce_all --skip-download` next ===")


if __name__ == "__main__":
    main()

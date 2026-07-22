"""Build a full year of flows.json plus a persisted daily net-flow table
from twelve months of raw Citi Bike trip archives.

Processes one month at a time -- download, QC, aggregate -- and deletes
that month's raw zip immediately after aggregating, before moving to the
next month. Peak disk usage stays at roughly one month's zip at a time,
never all twelve at once. The raw archives are public (Citi Bike S3, no
key) and can always be re-downloaded; nothing here is the only copy of
anything, so deleting them is safe (see PROGRESS.md's full-year data
expansion entry for the disk-budget reasoning).

Target window is the rolling most-recent full year (Jul 2025-Jun 2026),
not calendar-year 2026, because 2026-07's archive does not exist yet as of
this build (Citi Bike publishes with a lag) -- verified against the real
S3 bucket, not assumed.

Only the small aggregated outputs (flows.json, the daily parquet, the
manifest) are kept on disk. This module deliberately does not touch
backtest.py, elasticities.py, or gbm.py -- the full-year demand-model
rebuild that consumes this data is later, separate work.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.download import download_month, load_trips
from pipeline.equity_join import run_equity_join
from pipeline.flows import compute_daily_flow_components, compute_net_flow, export_flows
from pipeline.qc import run_qc
from pipeline.station_typology import apply_typology

TARGET_MONTHS = [
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FLOWS_PATH = DATA_DIR / "flows.json"
DAILY_NET_FLOW_PATH = DATA_DIR / "daily_net_flow.parquet"
MANIFEST_PATH = DATA_DIR / "data_manifest.json"


class SchemaDriftError(ValueError):
    """Raised when a target month's raw trip CSV columns don't match the first month's."""


def process_month(year_month: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Download, QC, and aggregate one month, then delete its raw zip.

    Returns (monthly_flows, daily_components, column_schema) so the caller
    can check schema consistency across months before concatenating anything.
    """
    zip_path = download_month(year_month)
    trips = load_trips(zip_path)
    schema = sorted(trips.columns)

    clean, qc_report = run_qc(trips)
    print(f"[{year_month}] {qc_report.summary()}")

    monthly = compute_net_flow(clean)
    daily = compute_daily_flow_components(clean)

    zip_path.unlink()
    print(f"[{year_month}] deleted {zip_path.name} after aggregation")

    return monthly, daily, schema


def _coverage_by_station(daily: pd.DataFrame) -> pd.DataFrame:
    """Distinct 'YYYY-MM' months present per station_id, from the daily table's
    own dates -- the source of truth for partial-year coverage, not a count
    tracked separately during the loop.
    """
    months = daily["date"].dt.strftime("%Y-%m")
    return (
        pd.DataFrame({"station_id": daily["station_id"], "month": months})
        .drop_duplicates()
        .groupby("station_id")["month"]
        .apply(lambda s: sorted(s))
        .rename("months_present")
        .reset_index()
    )


def build_full_year(months: list[str] = TARGET_MONTHS) -> dict:
    """Process every month in `months`, verify schema consistency, export a
    full-coverage flows.json (with typology/equity re-joined), and persist
    the daily net-flow table used by later modeling work.
    """
    monthly_frames = []
    daily_frames = []
    schemas: dict[str, list[str]] = {}

    for year_month in months:
        monthly, daily, schema = process_month(year_month)
        monthly_frames.append(monthly)
        daily_frames.append(daily)
        schemas[year_month] = schema

    first_month, first_schema = next(iter(schemas.items()))
    drifted = {m: s for m, s in schemas.items() if s != first_schema}
    if drifted:
        raise SchemaDriftError(
            f"Column schema for {sorted(drifted.keys())} does not match "
            f"{first_month}'s schema. Raw zips for every processed month "
            "have already been deleted after aggregation -- re-download "
            "the drifted months to inspect further."
        )

    all_monthly = pd.concat(monthly_frames, ignore_index=True)
    all_daily = pd.concat(daily_frames, ignore_index=True)
    coverage = _coverage_by_station(all_daily)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    all_daily.to_parquet(DAILY_NET_FLOW_PATH, index=False)

    export_flows(all_monthly, out_path=FLOWS_PATH)
    payload = apply_typology(json.loads(FLOWS_PATH.read_text()))
    FLOWS_PATH.write_text(json.dumps(payload))
    payload = run_equity_join(FLOWS_PATH)

    n_full_coverage = int((coverage["months_present"].apply(len) == len(months)).sum())
    manifest = {
        "months": months,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_stations_any_coverage": int(len(coverage)),
        "n_stations_full_coverage": n_full_coverage,
        "flows_path": str(FLOWS_PATH.relative_to(DATA_DIR.parent)),
        "daily_net_flow_path": str(DAILY_NET_FLOW_PATH.relative_to(DATA_DIR.parent)),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))

    return {"flows_payload": payload, "coverage": coverage, "manifest": manifest}


if __name__ == "__main__":
    result = build_full_year()
    manifest = result["manifest"]
    print(f"\nProcessed {len(manifest['months'])} months: "
          f"{manifest['months'][0]} .. {manifest['months'][-1]}")
    print(f"Stations with any coverage: {manifest['n_stations_any_coverage']:,}")
    print(f"Stations with full 12-month coverage: {manifest['n_stations_full_coverage']:,}")
    print(f"flows.json: {FLOWS_PATH}")
    print(f"daily net-flow table: {DAILY_NET_FLOW_PATH}")
    print(f"manifest: {MANIFEST_PATH}")

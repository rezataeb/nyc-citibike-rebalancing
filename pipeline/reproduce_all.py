"""Single entry point to reproduce this pipeline's derived artifacts from a clean clone.

Runs each pipeline step in the real dependency order a human would type at
the command line -- this script does not refactor or re-import any step's
internals, it just runs `python3 -m pipeline.<step>` in sequence and stops
on the first real failure (subprocess.run(..., check=True)).

Two modes:
- Full (default): re-downloads and re-aggregates all twelve months of real
  Citi Bike trip archives via build_full_year.py. Several GB of network
  traffic, realistically 30-60+ minutes. This is the real end-to-end proof
  the pipeline reproduces from nothing but public data -- run it if you
  want that proof, not routinely.
- --skip-download: skips build_full_year.py and reuses the already
  git-committed flows.json/daily_net_flow.parquet instead. This is the
  fast path and the one most reviewers actually want -- verifying that
  elasticities, the walk-forward model, and everything downstream really
  do derive from the committed data, without re-downloading it.

One real, unavoidable non-reproducibility caveat, stated here rather than
implied away: gbfs_logger.py --live pulls the CURRENT live GBFS feed, not
a historical snapshot -- there is no way to reproduce the exact station
roster/capacity a past run saw. Every run of this script (full or
--skip-download) refreshes live_status.json fresh before elasticities.py,
so elasticities.json's capacity-derived numbers will drift slightly from
the currently-committed version by design, not by bug. Everything else
(raw trip archives, Open-Meteo's historical weather archive, the fixed
weather grid) is real historical data and should reproduce identically.

data/gbfs_log/snapshots.csv (the continuously-collected live-density log
behind Investigator Phase 5) is a separate artifact this script does not
and cannot reproduce -- it only grows via the GitHub Actions cron
(.github/workflows/gbfs_snapshot.yml) accumulating real snapshots over
real elapsed time. It ships as-is in git.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FLOWS_PATH = DATA_DIR / "flows.json"
DAILY_NET_FLOW_PATH = DATA_DIR / "daily_net_flow.parquet"

# (module, extra CLI args, human description, real-cost note)
STEPS = [
    (
        "build_full_year",
        [],
        "Download + QC + aggregate 12 real months of trip archives",
        "several GB network, ~30-60+ min -- skipped by --skip-download",
    ),
    (
        "gbfs_logger",
        ["--live"],
        "Pull today's live GBFS feed for real station capacity",
        "one live API call, seconds -- always run, never skipped (see module docstring above on why this can't be made historically reproducible)",
    ),
    (
        "elasticities",
        [],
        "Fit capacity/temp/precip elasticities against real daily flow + real weather",
        "~18 real Open-Meteo calls (cached after first run), well under a minute",
    ),
    (
        "demand_model",
        [],
        "12-fold walk-forward validation (seasonal-naive vs. GAM vs. guarded GBM)",
        "CPU-heavy, realistically 20-40+ min",
    ),
    (
        "fleet_simulator",
        [],
        "Precompute 10 fleet-size (1-10 truck) scenarios",
        "seconds",
    ),
    (
        "plan_routes",
        [],
        "Build the depot-based single-shift route plan",
        "seconds",
    ),
    (
        "scenario_presets",
        [],
        "Write the fixed weather-scenario preset definitions",
        "seconds, no external data",
    ),
    (
        "spot_check",
        [],
        "Ground-truth checks against the freshly-reproduced data",
        "seconds -- exits nonzero if anything doesn't match real-world expectation",
    ),
]


def run_step(module: str, extra_args: list[str]) -> float:
    """Run one `python3 -m pipeline.<module>` step, streaming its real output. Returns elapsed seconds."""
    cmd = [sys.executable, "-m", f"pipeline.{module}", *extra_args]
    print(f"\n=== {' '.join(cmd)} ===")
    started = time.monotonic()
    subprocess.run(cmd, check=True)
    return time.monotonic() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip build_full_year.py; reuse the already git-committed flows.json/daily_net_flow.parquet instead of re-downloading 12 months of trip archives.",
    )
    args = parser.parse_args()

    steps = STEPS
    if args.skip_download:
        missing = [p for p in (FLOWS_PATH, DAILY_NET_FLOW_PATH) if not p.exists()]
        if missing:
            missing_str = ", ".join(str(p) for p in missing)
            raise SystemExit(
                f"--skip-download requires these already-committed files to exist, but they're missing: "
                f"{missing_str}. Run without --skip-download to build them from raw trip archives instead."
            )
        print(f"--skip-download: reusing existing {FLOWS_PATH.name} and {DAILY_NET_FLOW_PATH.name}, not re-downloading.")
        steps = [s for s in STEPS if s[0] != "build_full_year"]

    run_started = time.monotonic()
    for module, extra_args, description, cost_note in steps:
        print(f"\n{description} ({cost_note})")
        elapsed = run_step(module, extra_args)
        print(f"--- {module} done in {elapsed:.1f}s ---")

    total_elapsed = time.monotonic() - run_started
    print(f"\nAll steps completed in {total_elapsed / 60:.1f} min.")


if __name__ == "__main__":
    main()

"""Does capacity predict reliability, once demand is held constant?

The DOT-oversight reframe's one genuinely new analysis: every other
piece of this dashboard reuses an existing computation from a different
angle, but "where should the city add docks or stations" is a capital-
planning question only DOT owns, and nothing else here answers it.
"Patterns, not proof" throughout -- see the header this writes for the
dashboard, and every sentence below says associated/correlated, never
caused.

Method, stated plainly:
  - Per-station unusable rate: reliability.py's own already-validated
    output (data/reliability.json), same MIN_OBSERVATIONS floor it
    uses to call a station's own rate rankable -- not recomputed here.
  - Current per-station capacity: a fresh live GBFS station_information
    fetch (gbfs_logger.export_live_status()). capacity has never been
    in flows.json (see CLAUDE.md's own contract note on this) -- a live
    fetch is the only place it has ever come from in this project.
  - Demand control: L2 norm of each station's weekday net-flow curve
    from flows.json -- the exact volume measure station_typology.py
    already uses to separate real signal from noise, not a new metric
    invented for this analysis. log1p'd before entering the
    regression: raw demand is heavily right-skewed in the joined set
    (median ~2.2, max ~91 bikes/day), and an untransformed OLS fit
    would let a handful of extreme stations dominate the capacity
    coefficient's estimate.
  - NOT controlled for: borough/zone. Checked first, not assumed --
    it does not exist anywhere in this pipeline's outputs (flows.json's
    context{} block has NYCHA/school/subway distances, nothing
    borough-level). Adding it would mean a new spatial join against an
    external boundary file: new data acquisition, not reuse of what
    already exists, and out of scope for this pass. The finding below
    is "controlling for demand," never "controlling for demand and
    geography."

  - Why a regression, not a capacity-band x demand-stratum cross-tab:
    checked the actual join before picking a method (CLAUDE.md:
    verify before implementing, don't assume a method fits). Capacity
    and demand turned out to be too collinear here for a 3x3 table to
    have enough stations in every cell -- a "low demand, 40+ dock"
    cell has n=3 in the current data, nowhere near enough to trust.
    A joint linear fit (unusable_rate ~ capacity + log1p(demand)) uses
    every station's real value on a continuous scale instead of
    forcing them into sparse bins, and the two coefficients already
    control for each other. Plain OLS via the closed-form normal
    equations -- the same auditable, no-black-box method
    pipeline/elasticities.py already uses for its own weather
    regression, per CLAUDE.md's baseline-first style rule. The
    capacity-band table this module ALSO computes is shown on the
    dashboard purely as a readable descriptive summary, not as the
    thing the headline number is computed from.

Run:
    python3 pipeline/reliability_capacity.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pipeline.gbfs_logger import export_live_status

RELIABILITY_PATH = Path(__file__).resolve().parent.parent / "data" / "reliability.json"
LIVE_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "live_status.json"
FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "reliability_capacity.json"

MIN_OBSERVATIONS = 20  # matches reliability.py's own rankable floor

# Comparison band edges for the headline and the descriptive table below.
# Chosen from this join's own real distribution (median capacity ~24
# docks; these land near its 25th/85th percentiles), not arbitrary round
# numbers picked in advance -- see the module docstring's collinearity
# note for why bands this narrow would otherwise leave some cells too
# thin to trust.
SMALL_CAPACITY_CUTOFF = 20
LARGE_CAPACITY_CUTOFF = 40


def _join_stations(reliability: dict, live_status: dict, flows: dict) -> list[dict]:
    """One row per station with a rankable reliability rate, a valid live
    capacity reading, and a flows.json demand curve -- the intersection
    every number below is computed over."""
    joined = []
    for station_id, rel in reliability["stations"].items():
        if rel["n_observations"] < MIN_OBSERVATIONS:
            continue
        live = live_status["stations"].get(station_id)
        if live is None or not live.get("capacity"):
            continue
        flow = flows["stations"].get(station_id)
        if flow is None:
            continue
        weekday = np.array(flow["weekday"], dtype=float)
        joined.append(
            {
                "station_id": station_id,
                "rate": rel["unusable_rate"],
                "capacity": float(live["capacity"]),
                "demand": float(np.linalg.norm(weekday)),
            }
        )
    return joined


def _fit_ols(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Plain OLS: coefficients plus each one's own standard error, via the
    closed-form sigma^2 * (X'X)^-1 covariance -- the same formula
    pipeline/elasticities.py's own weather regression uses, kept
    self-contained here rather than importing a same-purpose private
    helper across pipeline modules. None on a rank-deficient or
    underdetermined fit, never a silently-wrong coefficient.

    The X @ coeffs matmul below reliably raises spurious divide-by-zero/
    overflow/invalid-value RuntimeWarnings on this machine's BLAS backend
    for this fit's shape, despite X and coeffs both being fully finite
    (checked directly: no NaN/Inf in either, and the result matches a
    manual X.dot(coeffs) exactly) -- a known class of false-positive FP
    exception flag from blocked/SIMD matrix-multiply kernels, not a real
    numerical problem. Scoped to just this one line so a genuine warning
    anywhere else in this function still surfaces normally.
    """
    coeffs, _residuals, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    n, p = X.shape
    if rank < p or n <= p:
        return None
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        fitted = X @ coeffs
    sigma_squared = float(np.sum((y - fitted) ** 2)) / (n - p)
    covariance = sigma_squared * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(covariance))
    return coeffs, se


def compute_reliability_capacity(
    reliability_path: Path = RELIABILITY_PATH,
    live_status_path: Path = LIVE_STATUS_PATH,
    flows_path: Path = FLOWS_PATH,
) -> dict:
    reliability = json.loads(reliability_path.read_text())
    live_status = json.loads(live_status_path.read_text())
    flows = json.loads(flows_path.read_text())

    joined = _join_stations(reliability, live_status, flows)
    n = len(joined)

    capacity = np.array([j["capacity"] for j in joined])
    demand = np.array([j["demand"] for j in joined])
    rate = np.array([j["rate"] for j in joined])
    log_demand = np.log1p(demand)

    X = np.column_stack([np.ones(n), capacity, log_demand])
    fit = _fit_ols(X, rate)
    if fit is None:
        return {
            "buildable": False,
            "reason": "OLS fit failed (rank-deficient or too few degrees of freedom) on the current join.",
            "n_joined": n,
        }
    coeffs, se = fit
    _b0, b_capacity, _b_log_demand = coeffs
    se_capacity = float(se[1])

    # A concrete, readable comparison for the headline sentence: the
    # model-implied gap between the large- and small-capacity cutoffs,
    # holding demand fixed -- reconstructs an intuitive "big stations vs
    # small stations" comparison from the fitted slope, without needing
    # a sparse cross-tab cell to actually contain enough stations.
    capacity_gap_docks = LARGE_CAPACITY_CUTOFF - SMALL_CAPACITY_CUTOFF
    reliability_gap_points = float(-b_capacity * capacity_gap_docks * 100)

    bands = {
        f"< {SMALL_CAPACITY_CUTOFF} docks": [j for j in joined if j["capacity"] < SMALL_CAPACITY_CUTOFF],
        f"{SMALL_CAPACITY_CUTOFF}-{LARGE_CAPACITY_CUTOFF - 1} docks": [
            j for j in joined if SMALL_CAPACITY_CUTOFF <= j["capacity"] < LARGE_CAPACITY_CUTOFF
        ],
        f"{LARGE_CAPACITY_CUTOFF}+ docks": [j for j in joined if j["capacity"] >= LARGE_CAPACITY_CUTOFF],
    }
    band_summary = [
        {
            "label": label,
            "n_stations": len(rows),
            "mean_unusable_rate": float(np.mean([r["rate"] for r in rows])) if rows else None,
        }
        for label, rows in bands.items()
    ]

    if reliability_gap_points > 0.5:
        headline = (
            f"After accounting for demand, stations with {LARGE_CAPACITY_CUTOFF}+ docks are associated "
            f"with unusable rates about {reliability_gap_points:.1f} percentage points lower than stations "
            f"with under {SMALL_CAPACITY_CUTOFF} docks."
        )
    else:
        headline = (
            f"After accounting for demand, capacity was not associated with a meaningfully lower unusable "
            f"rate in this window (model-implied gap: {reliability_gap_points:.1f} points)."
        )

    return {
        "buildable": True,
        "window": reliability["window"],
        "n_joined": n,
        "min_observations": MIN_OBSERVATIONS,
        "method": {
            "inputs": "reliability.py's per-station unusable rate; a live GBFS capacity fetch; flows.json's weekday-curve magnitude as a demand control",
            "model": "unusable_rate ~ capacity + log1p(demand), plain OLS",
            "controls": "demand only -- borough/zone is not available anywhere in this pipeline",
        },
        "capacity_coefficient_per_dock": float(b_capacity),
        "capacity_coefficient_se": se_capacity,
        "capacity_gap_docks": capacity_gap_docks,
        "reliability_gap_points": reliability_gap_points,
        "band_summary": band_summary,
        "headline": headline,
    }


if __name__ == "__main__":
    export_live_status()  # refresh capacity before computing -- never compute from a stale snapshot
    payload = compute_reliability_capacity()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))

    if payload["buildable"]:
        print(f"  n_joined       {payload['n_joined']:,} stations (>= {MIN_OBSERVATIONS} obs, valid live capacity, in flows.json)")
        print(
            f"  capacity coef  {payload['capacity_coefficient_per_dock']:.5f} "
            f"+/- {payload['capacity_coefficient_se']:.5f} (unusable-rate change per dock)"
        )
        for band in payload["band_summary"]:
            rate_pct = f"{band['mean_unusable_rate'] * 100:.1f}%" if band["mean_unusable_rate"] is not None else "n/a"
            print(f"  {band['label']:<16} n={band['n_stations']:>4}  mean unusable rate {rate_pct}")
        print(f"  headline       {payload['headline']}")
    else:
        print(f"  NOT buildable: {payload['reason']}")
    print(f"wrote {OUTPUT_PATH}")

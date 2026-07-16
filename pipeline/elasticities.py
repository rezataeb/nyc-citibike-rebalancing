"""Capacity + weather elasticities for Investigator Mode Phase 1.

Two genuinely different methods feed one output file -- see the
"method" field elasticities.json actually writes, which names both,
rather than implying one unified model produced everything:

- temp_elasticity / precip_elasticity: real partial dependence off
  Session 5's Feb+April HistGradientBoostingRegressor (pipeline/gbm.py).
  This module's __main__ loads gbm.py's persisted model artifact
  (MODEL_PATH) if present, or trains it once from real cached data and
  saves it before continuing (gbm.py's own get_or_train_model() does the
  same load-or-train, for other future callers) -- Session 4/5 never
  actually persisted a model, so there was nothing to "just load" the
  first time this ever runs (see PROGRESS.md Session 21).
- capacity_elasticity: capacity was NEVER one of that model's trained
  features (see FEATURE_COLUMNS in gbm.py), so its partial dependence
  cannot be computed from it without adding the feature and refitting --
  which the Investigator Mode Guideline's own Phase 1 framing says to
  avoid ("better than fitting a brand-new ... regression from scratch").
  Instead this is a separate, explicitly-labeled quadratic least-squares
  fit of real per-station flow magnitude (mean |weekday net_per_day|,
  from flows.json) against real per-station capacity (from
  live_status.json, Session 15's GBFS station_information pull) --
  cross-sectional across stations within a typology group, since a
  single station only ever has ONE capacity value over time and can't
  supply its own within-station regression. The quadratic term is what
  lets capacity_elasticity show a diminishing effect at already-high-
  capacity stations, evaluated locally (the fitted curve's derivative)
  at each group's mean capacity, or at a station's own real capacity for
  its by_station entry.

  A REAL CONFOUND WAS FOUND AND CONTROLLED FOR, not assumed away: a
  first pass without a busyness control produced a POSITIVE capacity
  elasticity in both typology groups -- the opposite of the contract's
  expected direction. Checked why rather than shipping it: real
  capacity-vs-magnitude correlation measured at r=0.69 (n=730,
  commuter_core) -- busier stations get assigned more docks by DOT *and*
  independently have bigger raw swings, so a bare capacity-vs-magnitude
  fit mostly recovers "busier stations are busier," not a within-station
  capacity effect. Fixed by adding real per-station ridership throughput
  (arrivals+departures/day, pipeline/flows.py's new compute_throughput(),
  built the same session from the already-cached Feb+April raw trip
  data) as a third regression term, and evaluating the capacity
  derivative holding throughput fixed. This controls for the ONE
  specific confound identified -- it does NOT make the estimate causal:
  it is still a cross-sectional comparison across different stations,
  not a real within-station "capacity changed, here's what happened"
  measurement (which this public-data-only stack has no way to observe --
  no station has had its capacity changed and re-measured). Said
  explicitly in the output's own notes field, not just here.

Both methods are normalized onto the same unitless scale the Guideline's
contract specifies -- percent change in typical |net flow| magnitude per
1-unit change in the feature -- but the DENOMINATOR differs by level:
by_typology entries divide by that typology's own mean magnitude;
by_station entries divide by that station's own mean magnitude (safe
because by_station is restricted to stations that already cleared
station_typology.py's LOW_VOLUME_THRESHOLD -- see below).

LOW-VOLUME CUTOFF, reused rather than reinvented: by_station entries
only exist for stations with a real (non -1) cluster, i.e. stations that
already passed station_typology.py's LOW_VOLUME_THRESHOLD (L2 norm of
the raw weekday curve >= 1.0 bikes/day) -- the exact same station set
Session 9's k-means clustered, not a second independently-derived
forecasting-specific number. Imported directly from station_typology.py,
not retyped, so the two thresholds can never silently drift apart.

CAPACITY IS NOT GENUINELY PER-STATION, and by_station entries say so
honestly rather than pretend otherwise: a station's by_station
capacity_elasticity is its typology group's fitted curve evaluated at
that station's own real capacity (a real, station-specific NUMBER), not
a station-specific fit (which is impossible with one capacity
observation per station). Only temp_elasticity/precip_elasticity in
by_station are genuinely fit per-station (partial dependence restricted
to that station's own Feb+April feature-panel rows).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import partial_dependence

from pipeline.gbm import (
    CATEGORICAL_COLUMNS,
    DEFAULT_CATEGORIES,
    DEFAULT_TRAIN_MONTHS,
    FEATURE_COLUMNS,
    MODEL_PATH,
    apply_shared_categories,
    build_training_data,
    load_model,
    save_model,
    train_gbm,
)
from pipeline.station_typology import LOW_SIGNAL_NAME, LOW_VOLUME_THRESHOLD

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
LIVE_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "live_status.json"
ELASTICITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "elasticities.json"

# Maps the real cluster_name strings station_typology.py writes onto
# flows.json (Session 9's actual k=2 run) to the Guideline contract's
# example slugs -- matched by name text, not assumed cluster index, so a
# future re-run of station_typology.py that happens to flip which index
# is "0" vs "1" doesn't silently mislabel a group here.
TYPOLOGY_SLUGS = {
    "Commuter core (fills AM, drains PM)": "commuter_core",
    "Residential feeder (drains AM, fills PM)": "residential_feeder",
}

PDP_GRID_RESOLUTION = 10  # only need enough grid points to fit a robust linear slope, not a full curve
METHOD_NOTE = (
    "temp_elasticity/precip_elasticity: partial dependence (sklearn "
    "partial_dependence, method='brute' so each group/station's own real "
    "feature-panel rows are genuinely averaged over, not the global "
    "training background) off pipeline/gbm.py's Session 5 Feb+April "
    "HistGradientBoostingRegressor -- the only two of these three "
    "features that model was actually trained on. "
    "capacity_elasticity: NOT from that model -- capacity was never one "
    "of its trained features. Instead, a separate quadratic least-squares "
    "fit of real per-station mean |weekday net flow| (flows.json) against "
    "real per-station capacity (live_status.json) AND real per-station "
    "ridership throughput (arrivals+departures/day, pipeline/flows.py's "
    "compute_throughput(), a busyness control -- see the confound caveat "
    "below), fit once per typology group (cross-sectional across "
    "stations, since a single station only has one capacity value and "
    "can't supply its own within-station regression), evaluated locally "
    "(the fitted curve's capacity derivative, holding throughput fixed) "
    "at each group's mean capacity for by_typology, or at each station's "
    "own real capacity for by_station."
)
CONFOUND_CAVEAT = (
    "capacity_elasticity controls for one specific, verified confound "
    "(station busyness: capacity and flow magnitude were both found to "
    "correlate with real ridership throughput, r=0.69 for commuter_core "
    "before this control was added) by including throughput as a third "
    "regression term. This does NOT make the estimate causal -- it is "
    "still a cross-sectional comparison across DIFFERENT stations at one "
    "point in time, not a within-station measurement of capacity actually "
    "changing at the same station over time (which no station in this "
    "public-data-only stack has done). Treat it as 'stations with more "
    "capacity than similarly-busy peers show X% different typical "
    "magnitude,' not as a validated forecast of what adding docks to a "
    "specific station would do."
)
DIRECTIONAL_ACCURACY_CAVEAT = (
    "The underlying Feb+April GBM's own Session 5 backtest got directional "
    "sign right only 49.9% of the time on June (a coin flip) because June's "
    "actual mean temperature fell outside the Feb+April training range and "
    "gradient-boosted trees cannot extrapolate past it. temp_elasticity/"
    "precip_elasticity inherit this weakness -- treat them as illustrative "
    "sensitivities from a directionally-unreliable model, not a validated "
    "forecast of what a real temperature/precipitation change would do."
)
SPARSE_GRID_CAVEAT = (
    "A further weakness beyond the directional-accuracy caveat above, "
    "found while building Investigator Mode Phase 4 (weather scenarios): "
    "temp_mean_c and precip_mm are each a per-(month, day_type) AGGREGATE, "
    "not a per-observation feature, so the entire Feb+April training panel "
    "contains only 5 distinct values of each (checked directly: temp_mean_c "
    "in {-4.4, -2.6, 11.1, 13.1, 18.7} degrees C). temp_elasticity/"
    "precip_elasticity are therefore linear-regression slopes fit through "
    "only 5 points, not a smooth curve -- and the partial dependence curve "
    "itself shows a sharp isolated jump at the single highest point (18.7C), "
    "consistent with a sparse-grid artifact rather than a genuine trend. "
    "Any consumer projecting a scenario from these elasticities should "
    "treat values near or beyond that top point as resting on the fit's "
    "least reliable segment, not simply 'outside the observed range.'"
)
# Rank-correlation thresholds for how confidently ceiling_effect_note() below
# describes the diminishing-effect evidence as "clean" vs "weak/inconsistent"
# -- a real, computed number per group, not a hardcoded description of one
# session's specific run (which would go stale the next time this pipeline
# runs against updated data).
CEILING_EFFECT_STRONG_THRESHOLD = -0.3


def ceiling_effect_note(slug: str, rank_correlation: float | None, n: int) -> str:
    """Explain the residual positive capacity_elasticity sign (after the
    throughput/busyness control above) as a plausible physical ceiling
    effect -- capacity mechanically bounds how large a raw net-flow swing
    CAN get (a 12-dock station physically cannot swing past +-12) -- with
    confidence calibrated to this group's OWN evidence (a real Spearman
    rank correlation between by_station capacity and capacity_elasticity,
    computed above), not asserted uniformly across both typology groups.
    """
    if rank_correlation is None or n < 3:
        return f"{slug}: too few by_station capacity_elasticity values (n={n}) to assess the ceiling-effect pattern."
    if rank_correlation <= CEILING_EFFECT_STRONG_THRESHOLD:
        strength = (
            f"a clean, consistent decay (rank correlation {rank_correlation:.2f} across "
            f"n={n} stations) -- reasonably strong evidence for the ceiling-effect explanation"
        )
    else:
        strength = (
            f"a weaker, less consistent pattern (rank correlation {rank_correlation:.2f} across "
            f"n={n} stations) -- the ceiling-effect explanation is plausible but not confidently "
            "established for this group; treat capacity_elasticity's sign here as less reliable"
        )
    return f"{slug}: {strength}."


def station_magnitude(weekday_curve: list[float]) -> float:
    """Mean absolute net flow over the raw weekday curve -- the same curve
    station_typology.py's L2 norm is computed from, kept consistent so
    "how big is this station's typical swing" means the same thing in both
    places.
    """
    return float(np.mean(np.abs(weekday_curve)))


NUMERIC_FEATURE_COLUMNS = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_COLUMNS]


def clean_feature_rows(X: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with NaN in any numeric feature column.

    A handful of rows carry a NaN temp_mean_c/precip_mm: the Feb/April raw
    trip files include a few midnight-crossing trips labeled with an
    adjacent month ('2026-01', '2026-05' -- see PROGRESS.md Sessions 3/5),
    and the weather fetch only covers 2026-02-01..2026-06-30, so those
    out-of-range months merge to NaN. HistGradientBoostingRegressor
    tolerates NaN natively (why training/predicting never broke on this),
    but partial_dependence's grid/slope fit does not -- confirmed by a real
    crash against real data, not assumed. Filtering here (not in gbm.py's
    training data) keeps the already-verified Session 5 model exactly as
    trained; this only affects which rows feed a PDP estimate.
    """
    return X.dropna(subset=NUMERIC_FEATURE_COLUMNS)


def pdp_slope(model, X: pd.DataFrame, feature: str, categories: dict[str, list]) -> float | None:
    """Linear-regression slope of `feature`'s partial dependence curve over
    its own grid, restricted to the rows in X (method='brute' -- unlike the
    default 'recursion' method, this genuinely marginalizes over X's own
    other-feature values rather than the whole training set's background,
    which is what makes a per-station or per-typology-group subset
    meaningful here rather than nearly identical regardless of which rows
    are passed in).

    Returns None if X has fewer than 2 clean (non-NaN) rows (not enough to
    mean anything).
    """
    X = clean_feature_rows(X)
    if len(X) < 2:
        return None
    X_typed = apply_shared_categories(X, categories)
    result = partial_dependence(
        model, X_typed[FEATURE_COLUMNS], features=[feature],
        method="brute", kind="average", grid_resolution=PDP_GRID_RESOLUTION,
    )
    grid = np.asarray(result["grid_values"][0], dtype=float)
    average = np.asarray(result["average"][0], dtype=float)
    if len(grid) < 2:
        return None
    slope, _intercept = np.polyfit(grid, average, 1)
    return float(slope)


def load_station_throughput(train_months: list[str] = DEFAULT_TRAIN_MONTHS) -> pd.Series:
    """station_id -> n_days-weighted mean throughput_per_day across train_months.

    A busyness proxy independent of net-flow DIRECTION (throughput sums
    both sides; net flow differences them), used only as a regression
    control for the capacity/magnitude confound -- see module docstring.
    Reuses pipeline.flows.compute_throughput, which itself reuses the
    exact aggregation compute_net_flow relies on (station_id typing,
    midnight-crossing-safe date handling, both real bugs fixed in
    Sessions 3/5) -- not a new one-off aggregation.
    """
    from pipeline.download import download_month, load_trips
    from pipeline.flows import compute_throughput
    from pipeline.qc import run_qc

    def _load_clean_trips(year_month: str) -> pd.DataFrame:
        zip_path = download_month(year_month)
        trips = load_trips(zip_path)
        clean, _report = run_qc(trips)
        return clean

    all_throughput = pd.concat(
        [compute_throughput(_load_clean_trips(m)) for m in train_months], ignore_index=True
    )

    def _weighted_mean(group: pd.DataFrame) -> float:
        return (group["throughput_per_day"] * group["n_days"]).sum() / group["n_days"].sum()

    return all_throughput.groupby("station_id").apply(_weighted_mean, include_groups=False)


def fit_capacity_curve(
    capacities: np.ndarray, magnitudes: np.ndarray, throughput: np.ndarray
) -> tuple[float, float, float, float] | None:
    """Least-squares fit: magnitude ~ b0 + b1*capacity + b2*capacity^2 + b3*throughput.

    The quadratic capacity term is deliberate, not decoration -- a
    straight-line fit cannot show the "diminishing effect at already-high-
    capacity stations" the Guideline explicitly asks to sanity-check for,
    since a line's slope is constant everywhere by construction. The
    throughput term controls for the real busyness confound found this
    session (see CONFOUND_CAVEAT) -- it doesn't need its own coefficient
    reported anywhere, since capacity_local_slope only ever takes the
    derivative w.r.t. capacity (throughput held fixed by construction of
    a partial derivative, whatever its own coefficient turns out to be).

    Returns None if there are fewer than 8 distinct capacity values (need
    real degrees of freedom for 4 coefficients, more headroom than the
    3-coefficient version needed) or the fit is rank-deficient (e.g.
    capacity and throughput too collinear to separate).
    """
    if len(np.unique(capacities)) < 8:
        return None
    X = np.column_stack([np.ones_like(capacities), capacities, capacities**2, throughput])
    coeffs, _residuals, rank, _singular_values = np.linalg.lstsq(X, magnitudes, rcond=None)
    if rank < 4:
        return None
    b0, b1, b2, b3 = coeffs
    return float(b0), float(b1), float(b2), float(b3)


def capacity_local_slope(b1: float, b2: float, capacity: float) -> float:
    """d(magnitude)/d(capacity) of the quadratic fit, evaluated at `capacity`
    (throughput's own term drops out of a partial derivative w.r.t. capacity)."""
    return b1 + 2 * b2 * capacity


def build_elasticities(
    flows_payload: dict, live_payload: dict, model, train_features: pd.DataFrame,
    throughput_by_id: pd.Series, categories: dict[str, list] = DEFAULT_CATEGORIES,
) -> dict:
    """Build the full elasticities.json payload from real data + a fitted model.

    Only processes station_ids present in flows_payload["stations"] --
    that dict is the dashboard's actual station roster, so a station
    train_features happens to include (a fresh Feb+April repull can have a
    slightly different roster than whatever pull flows.json was last built
    from) but flows.json doesn't know about has nowhere in the output to
    attach to.
    """
    stations = flows_payload["stations"]
    live_stations = live_payload["stations"]

    capacity_by_id = {
        sid: rec["capacity"] for sid, rec in live_stations.items() if rec.get("capacity", 0) > 0
    }

    # Group real (non -1) stations by typology, same exclusion station_typology.py
    # itself applies -- a low-signal station never got a real cluster shape to
    # begin with, so it has no group to compute a typology-level elasticity for.
    groups: dict[str, list[str]] = {slug: [] for slug in TYPOLOGY_SLUGS.values()}
    for sid, rec in stations.items():
        slug = TYPOLOGY_SLUGS.get(rec.get("cluster_name"))
        if slug is not None:
            groups[slug].append(sid)

    magnitude_by_id = {sid: station_magnitude(stations[sid]["weekday"]) for sid in stations}

    by_typology: dict[str, dict] = {}
    capacity_curve_by_slug: dict[str, tuple[float, float, float] | None] = {}

    for slug, station_ids in groups.items():
        if not station_ids:
            continue
        group_features = train_features[train_features["station_id"].isin(station_ids)]
        group_magnitude = float(np.mean([magnitude_by_id[sid] for sid in station_ids]))

        temp_slope = pdp_slope(model, group_features, "temp_mean_c", categories)
        precip_slope = pdp_slope(model, group_features, "precip_mm", categories)

        # Only stations with BOTH a real capacity match and a real throughput
        # estimate can feed the capacity regression -- fewer than
        # n_stations_with_capacity alone if throughput is missing for some
        # (e.g. zero Feb+April rows for that station).
        capacity_ids = [sid for sid in station_ids if sid in capacity_by_id and sid in throughput_by_id.index]
        capacities = np.array([capacity_by_id[sid] for sid in capacity_ids], dtype=float)
        magnitudes_for_capacity = np.array([magnitude_by_id[sid] for sid in capacity_ids], dtype=float)
        throughput_for_capacity = np.array([throughput_by_id[sid] for sid in capacity_ids], dtype=float)

        # Collinearity sanity check (requested verification, not just "the
        # code ran"): if capacity and the busyness control track each other
        # too closely, the regression can't separate their individual
        # effects and the capacity coefficient becomes unstable.
        capacity_throughput_corr = (
            float(np.corrcoef(capacities, throughput_for_capacity)[0, 1]) if len(capacity_ids) >= 2 else None
        )

        curve = fit_capacity_curve(capacities, magnitudes_for_capacity, throughput_for_capacity)
        capacity_curve_by_slug[slug] = curve

        entry = {
            "capacity_elasticity": (
                round(capacity_local_slope(curve[1], curve[2], float(np.mean(capacities))) / group_magnitude, 4)
                if curve is not None else None
            ),
            "temp_elasticity": round(temp_slope / group_magnitude, 4) if temp_slope is not None else None,
            "precip_elasticity": round(precip_slope / group_magnitude, 4) if precip_slope is not None else None,
            "n_stations": len(station_ids),
            "n_stations_with_capacity": int(len(capacity_ids)),
            "capacity_throughput_correlation": (
                round(capacity_throughput_corr, 4) if capacity_throughput_corr is not None else None
            ),
        }
        by_typology[slug] = entry

    by_station: dict[str, dict] = {}
    for sid, rec in stations.items():
        slug = TYPOLOGY_SLUGS.get(rec.get("cluster_name"))
        if slug is None:
            continue  # low-signal (cluster -1) or unrecognized label -- no group to fall back to
        station_features = clean_feature_rows(train_features[train_features["station_id"] == sid])
        n_obs = len(station_features)
        if n_obs == 0:
            continue  # no real (non-NaN-weather) Feb+April rows for this station -- nothing to fit, skip rather than fabricate

        temp_slope = pdp_slope(model, station_features, "temp_mean_c", categories)
        precip_slope = pdp_slope(model, station_features, "precip_mm", categories)
        magnitude = magnitude_by_id[sid]
        if magnitude == 0:
            continue  # a genuinely flat curve -- dividing by zero would fabricate an infinite elasticity

        entry = {
            "temp_elasticity": round(temp_slope / magnitude, 4) if temp_slope is not None else None,
            "precip_elasticity": round(precip_slope / magnitude, 4) if precip_slope is not None else None,
            "n_obs": n_obs,
        }
        curve = capacity_curve_by_slug.get(slug)
        if curve is not None and sid in capacity_by_id:
            entry["capacity_elasticity"] = round(
                capacity_local_slope(curve[1], curve[2], float(capacity_by_id[sid])) / magnitude, 4
            )
        by_station[sid] = entry

    # Real, computed-per-group evidence for the ceiling-effect explanation of
    # the residual positive capacity_elasticity sign, not an eyeballed
    # 3-station spot check -- Spearman rank correlation between every
    # by_station station's own real capacity and its own fitted
    # capacity_elasticity in this group. Strongly negative == a clean,
    # monotonic decay (bigger capacity -> smaller elasticity), consistent
    # with capacity mechanically bounding swing size; weak/inconsistent ==
    # the same explanation is plausible but not confidently established for
    # that group specifically.
    ceiling_effect_notes = []
    for slug in by_typology:
        pairs = [
            (float(capacity_by_id[sid]), by_station[sid]["capacity_elasticity"])
            for sid, rec in stations.items()
            if TYPOLOGY_SLUGS.get(rec.get("cluster_name")) == slug
            and sid in by_station and "capacity_elasticity" in by_station[sid]
        ]
        rank_corr = (
            pd.Series([p[0] for p in pairs]).corr(pd.Series([p[1] for p in pairs]), method="spearman")
            if len(pairs) >= 3 else None
        )
        rank_corr = float(rank_corr) if rank_corr is not None and not np.isnan(rank_corr) else None
        by_typology[slug]["capacity_elasticity_rank_correlation"] = (
            round(rank_corr, 4) if rank_corr is not None else None
        )
        ceiling_effect_notes.append(ceiling_effect_note(slug, rank_corr, len(pairs)))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": METHOD_NOTE,
        "by_typology": by_typology,
        "by_station": by_station,
        "notes": (
            f"by_station entries only exist for stations meeting station_typology.py's "
            f"LOW_VOLUME_THRESHOLD ({LOW_VOLUME_THRESHOLD} bikes/day, L2 norm of the raw "
            f"weekday curve) -- borrowed directly from the typology module for consistency "
            f"with which stations got a real cluster shape (cluster != -1, '{LOW_SIGNAL_NAME}' "
            "excluded), not a separately-derived forecasting-specific number. Sparse stations "
            "fall back to their by_typology value in the dashboard. capacity_elasticity in "
            "by_station entries is a station-specific NUMBER (its group's fitted curve "
            "evaluated at that station's own real capacity) but not a station-specific FIT -- "
            "see the top-level 'method' field. " + CONFOUND_CAVEAT + " "
            "Ceiling-effect evidence by group (a real Spearman rank correlation between "
            "by_station capacity and capacity_elasticity, not asserted uniformly): "
            + " ".join(ceiling_effect_notes) + " " + DIRECTIONAL_ACCURACY_CAVEAT
            + " " + SPARSE_GRID_CAVEAT
        ),
    }


if __name__ == "__main__":
    flows_payload = json.loads(FLOWS_PATH.read_text())
    live_payload = json.loads(LIVE_STATUS_PATH.read_text())

    # train_features is needed regardless (PDP requires the real feature
    # panel, not just a fitted model), so build it once and only train+save
    # a model if get_or_train_model() would otherwise have to rebuild the
    # exact same panel a second time internally to do the same thing.
    _, _, train_features, _ = build_training_data()
    if MODEL_PATH.exists():
        model = load_model()
        print(f"Loaded existing model from {MODEL_PATH}")
    else:
        model = train_gbm(train_features, DEFAULT_CATEGORIES)
        save_model(model)
        print(f"Trained and saved model to {MODEL_PATH}")

    print("Computing real per-station ridership throughput (busyness control)...")
    throughput_by_id = load_station_throughput()

    payload = build_elasticities(flows_payload, live_payload, model, train_features, throughput_by_id)

    ELASTICITIES_PATH.write_text(json.dumps(payload, indent=2))

    for slug, entry in payload["by_typology"].items():
        print(f"{slug}: {entry}")
    print(f"by_station: {len(payload['by_station'])} stations")
    print(f"wrote {ELASTICITIES_PATH}")

"""Capacity + weather elasticities for Investigator Mode Phase 1.

Two genuinely different methods feed one output file -- see the
"method" field elasticities.json actually writes, which names both,
rather than implying one unified model produced everything:

- temp_elasticity / precip_elasticity: a DIRECT linear regression of
  real per-(station, date) daily flow magnitude against real per-date
  temperature/precipitation, jointly (magnitude ~ b0 + b1*temp_c +
  b2*precip_mm), NOT partial dependence off pipeline/gbm.py's GBM.
  Session 25 found the original PDP-based approach was fit through only
  5 distinct temp/precip values total, because gbm.py's own weather
  features are each a per-MONTH aggregate -- the partial dependence
  curve itself showed a sharp, likely-artifactual jump at the single
  highest observed point. Session 27 replaced it: pipeline/flows.py's
  new compute_daily_net_flow() reaggregates the SAME already-cached
  Feb/April/June trip data at real calendar-date grain instead of
  monthly grain, joined against real daily temp/precip from Open-Meteo
  (a cheap API call, not a bulk download -- no new trip data was
  downloaded for this fix). This gives up to 90 real distinct days
  instead of 5 monthly-aggregate points. gbm.py's own model is no
  longer a runtime dependency of this module at all -- it's still a
  real, separately-useful, already-characterized artifact (Session 5's
  own documented 49.9%-directional-accuracy backtest, still shown in
  the dashboard's model-eval footer), just not what temp/precip
  elasticity is computed from anymore.
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
observation per station). temp_elasticity/precip_elasticity in
by_station ARE genuinely fit per-station -- that station's own real
daily (date, magnitude) observations, its own regression, not a group
value evaluated locally -- but only when there are enough of them
(MIN_DAILY_OBSERVATIONS); a station with too few real days omits these
two fields and falls back to its by_typology value in the dashboard,
same "by_station entries only exist for stations meeting the low-volume
threshold" contract extended one level further (station has a real
cluster, but not necessarily enough real days for its OWN fit).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# DEFAULT_TRAIN_MONTHS is still imported for load_station_throughput's own
# purpose (capacity's busyness control, unchanged by this session's fix --
# capacity's regression already used hundreds of real per-station points,
# never had a sparse-grid problem). Nothing else from gbm.py is needed
# anymore: temp/precip elasticity no longer goes through that model at
# all -- see the module docstring.
from pipeline.gbm import DEFAULT_TRAIN_MONTHS
from pipeline.station_typology import LOW_SIGNAL_NAME, LOW_VOLUME_THRESHOLD

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
LIVE_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "live_status.json"
ELASTICITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "elasticities.json"

# ALL cached real months feed the daily weather regression, not just
# gbm.py's Feb+April training split -- there's no train/test-holdout
# concept to preserve here (this fits a direct historical regression, not
# a forecast to be honestly evaluated on unseen data), so using June too
# is strictly more real data with a wider real temperature range, at zero
# additional download cost (already cached).
DAILY_REGRESSION_MONTHS = ["2026-02", "2026-04", "2026-06"]
WEATHER_FETCH_START = "2026-02-01"
WEATHER_FETCH_END = "2026-06-30"  # matches gbm.py's own cached Open-Meteo pull -- reuses the same cache file, no new fetch
MIN_DAILY_OBSERVATIONS = 10  # real degrees-of-freedom floor for a 3-coefficient (intercept+temp+precip) fit

# Maps the real cluster_name strings station_typology.py writes onto
# flows.json (Session 9's actual k=2 run) to the Guideline contract's
# example slugs -- matched by name text, not assumed cluster index, so a
# future re-run of station_typology.py that happens to flip which index
# is "0" vs "1" doesn't silently mislabel a group here.
TYPOLOGY_SLUGS = {
    "Commuter core (fills AM, drains PM)": "commuter_core",
    "Residential feeder (drains AM, fills PM)": "residential_feeder",
}

METHOD_NOTE = (
    "temp_elasticity/precip_elasticity: a direct joint linear regression "
    "of real per-(station, date) daily flow magnitude against real daily "
    "temp_c/precip_mm (Open-Meteo), fit once per typology group (pooling "
    "every station's every real day -- up to ~2,000 stations x 90 days) "
    "for by_typology, or per-station (that station's own up to 90 real "
    "days only) for by_station when there are enough of them "
    "(MIN_DAILY_OBSERVATIONS). Uses ALL of Feb/April/June (not just "
    "gbm.py's Feb+April training split) -- there's no forecast to "
    "honestly evaluate on held-out data here, just a historical "
    "association to estimate, so more real days is strictly better. "
    "Replaced Session 25's partial-dependence-off-a-monthly-aggregate "
    "approach entirely -- see the module docstring. "
    "capacity_elasticity: a separate quadratic least-squares "
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
DAILY_REGRESSION_CAVEAT = (
    "Session 25 originally found temp_elasticity/precip_elasticity fit "
    "through only 5 sparse, monthly-aggregate points with a likely-"
    "artifactual jump at the top one. Session 27 fixed the underlying "
    "data granularity (real daily observations instead of monthly "
    "aggregates), NOT just the caveat -- but real limitations remain, "
    "stated plainly rather than implied resolved: these are still up to "
    "90 days from 3 NON-CONTIGUOUS months (Feb/April/June), not a "
    "continuous year, so intervening months (Jan, Mar, May, and Jul "
    "onward) contribute nothing; and temp_c/precip_mm are each ONE "
    "city-wide daily value (Open-Meteo's Central Park reference point, "
    "per pipeline/weather.py), not per-station, so real local weather "
    "variation across NYC's boroughs is not captured. A joint fit "
    "(temp and precip together, not two separate univariate fits) "
    "controls for the two being correlated with each other (a rainy day "
    "is often also cooler), but not for anything else true of those "
    "specific calendar days that isn't captured by date-of-year "
    "position or day-of-week (already implicit in which real dates got "
    "sampled) -- still an observational association, not a controlled "
    "experiment."
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


def load_daily_weather_panel(months: list[str] = DAILY_REGRESSION_MONTHS) -> pd.DataFrame:
    """Real per-(station, date) daily flow magnitude joined against real
    daily temp_c/precip_mm -- the direct replacement for Session 25's
    PDP-off-a-monthly-aggregate approach (see module docstring).

    Reuses pipeline.flows.compute_daily_net_flow (station_id typing and
    midnight-crossing-safe date handling already fixed in Session 5, same
    reuse pattern load_station_throughput below already established for
    compute_throughput) and pipeline.weather.fetch_daily_weather (the
    exact real Open-Meteo pull gbm.py already caches, just joined at
    daily instead of monthly grain here -- no new fetch).

    Magnitude per (station, date) is the mean absolute hourly net flow
    that day -- the same "mean of |curve|" definition station_magnitude()
    already uses, kept consistent so "how big is this station's typical
    swing" means the same thing everywhere in this file.
    """
    from pipeline.download import download_month, load_trips
    from pipeline.flows import compute_daily_net_flow
    from pipeline.qc import run_qc
    from pipeline.weather import fetch_daily_weather

    def _load_clean_trips(year_month: str) -> pd.DataFrame:
        zip_path = download_month(year_month)
        trips = load_trips(zip_path)
        clean, _report = run_qc(trips)
        return clean

    daily = pd.concat([compute_daily_net_flow(_load_clean_trips(m)) for m in months], ignore_index=True)
    daily_magnitude = (
        daily.groupby(["station_id", "date"])["net"]
        .apply(lambda x: float(np.mean(np.abs(x))))
        .rename("magnitude")
        .reset_index()
    )

    weather = fetch_daily_weather(WEATHER_FETCH_START, WEATHER_FETCH_END)
    # Inner join: a handful of midnight-crossing spillover dates (e.g.
    # 2026-01-31, outside the fetched weather range) simply have no
    # weather match and are dropped -- graceful, not fabricated, same
    # rule as every other NaN-adjacent gap in this project.
    return daily_magnitude.merge(weather[["date", "temp_mean_c", "precip_mm"]], on="date", how="inner")


def fit_daily_weather_regression(
    magnitudes: np.ndarray, temps: np.ndarray, precips: np.ndarray, n_distinct_days: int
) -> tuple[float, float, float] | None:
    """Joint least-squares fit: magnitude ~ b0 + b1*temp_c + b2*precip_mm.

    Joint (not two separate univariate fits) so each coefficient controls
    for the other -- a rainy day is often also a cooler day, and fitting
    temp alone would let some of precip's real effect leak into the temp
    coefficient. Linear, not quadratic like fit_capacity_curve -- there's
    no equivalent "diminishing effect" hypothesis to check here, and a
    plain linear fit is the more auditable default per CLAUDE.md's
    baseline-first style rule.

    Returns None if fewer than MIN_DAILY_OBSERVATIONS distinct days are
    available -- real degrees-of-freedom floor for a 3-coefficient fit,
    checked against DISTINCT DAYS (what actually drives the weather
    axis's variance), not row count (which could be inflated by pooling
    many stations on the same handful of days).
    """
    if n_distinct_days < MIN_DAILY_OBSERVATIONS:
        return None
    X = np.column_stack([np.ones_like(temps), temps, precips])
    coeffs, _residuals, rank, _singular_values = np.linalg.lstsq(X, magnitudes, rcond=None)
    if rank < 3:
        return None
    b0, b1, b2 = coeffs
    return float(b0), float(b1), float(b2)


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
    flows_payload: dict, live_payload: dict, daily_panel: pd.DataFrame, throughput_by_id: pd.Series,
) -> dict:
    """Build the full elasticities.json payload from real data.

    Only processes station_ids present in flows_payload["stations"] --
    that dict is the dashboard's actual station roster, so a station
    daily_panel/throughput_by_id happens to include (a fresh repull can
    have a slightly different roster than whatever pull flows.json was
    last built from) but flows.json doesn't know about has nowhere in the
    output to attach to.
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
        group_panel = daily_panel[daily_panel["station_id"].isin(station_ids)]
        group_magnitude = float(np.mean([magnitude_by_id[sid] for sid in station_ids]))

        group_curve = fit_daily_weather_regression(
            group_panel["magnitude"].to_numpy(), group_panel["temp_mean_c"].to_numpy(),
            group_panel["precip_mm"].to_numpy(), group_panel["date"].nunique(),
        )
        temp_slope = group_curve[1] if group_curve is not None else None
        precip_slope = group_curve[2] if group_curve is not None else None

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
            "n_daily_observations": int(len(group_panel)),
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
        magnitude = magnitude_by_id[sid]
        if magnitude == 0:
            continue  # a genuinely flat curve -- dividing by zero would fabricate an infinite elasticity

        # Each row of daily_panel is already one (station, date) pair (the
        # groupby in load_daily_weather_panel collapsed hour-level rows
        # down to one magnitude per day), so len() here IS the real
        # distinct-day count for this specific station -- no separate
        # nunique() needed.
        station_panel = daily_panel[daily_panel["station_id"] == sid]
        n_obs = len(station_panel)
        station_curve = fit_daily_weather_regression(
            station_panel["magnitude"].to_numpy(), station_panel["temp_mean_c"].to_numpy(),
            station_panel["precip_mm"].to_numpy(), n_obs,
        )
        temp_slope = station_curve[1] if station_curve is not None else None
        precip_slope = station_curve[2] if station_curve is not None else None

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
            + " ".join(ceiling_effect_notes) + " " + DAILY_REGRESSION_CAVEAT
        ),
    }


if __name__ == "__main__":
    flows_payload = json.loads(FLOWS_PATH.read_text())
    live_payload = json.loads(LIVE_STATUS_PATH.read_text())

    print("Computing real per-station ridership throughput (busyness control)...")
    throughput_by_id = load_station_throughput()

    print("Computing real per-(station, date) daily flow magnitude vs. real daily weather...")
    daily_panel = load_daily_weather_panel()

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    ELASTICITIES_PATH.write_text(json.dumps(payload, indent=2))

    for slug, entry in payload["by_typology"].items():
        print(f"{slug}: {entry}")
    print(f"by_station: {len(payload['by_station'])} stations")
    print(f"wrote {ELASTICITIES_PATH}")

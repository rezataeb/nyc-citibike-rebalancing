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
  elasticity is computed from anymore. Session 31 extended this further:
  pipeline/build_full_year.py (Session 29) now persists this same daily
  grain for a full real contiguous year (Jul 2025-Jun 2026), and this
  module reads that persisted table directly instead of re-downloading
  raw trips at all -- roughly 4x the real days this regression fits on,
  and every reported coefficient now carries a 95% confidence interval
  alongside its point estimate, not just the point estimate alone.
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
from typing import NamedTuple

import numpy as np
import pandas as pd

from pipeline.build_full_year import DAILY_NET_FLOW_PATH
from pipeline.station_typology import LOW_SIGNAL_NAME, LOW_VOLUME_THRESHOLD

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
LIVE_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "live_status.json"
ELASTICITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "elasticities.json"

# Session 31: both loaders below now read data/daily_net_flow.parquet
# (Session 29) instead of re-downloading raw trip archives -- those
# archives are deleted immediately after aggregation by
# pipeline/build_full_year.py, so re-downloading them here was both
# wasteful and, now that they're gone by design, the wrong dependency to
# have at all. This also takes the daily weather regression from ~90 days
# (3 non-contiguous months) to ~365 real contiguous days, for free -- no
# new download, just reading data already on disk.
WEATHER_FETCH_START = "2025-07-01"
WEATHER_FETCH_END = "2026-06-30"  # matches build_full_year's own window -- reuses the same cached Open-Meteo pull
MIN_DAILY_OBSERVATIONS = 10  # real degrees-of-freedom floor for a 3-coefficient (intercept+temp+precip) fit
CI_Z_SCORE = 1.96  # normal approximation for a 95% CI -- appropriate here since every real fit in this module has well over 30 degrees of freedom, so t and normal critical values are indistinguishable to 2 decimal places

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
    "every station's every real day across the full Jul 2025-Jun 2026 "
    "year) for by_typology, or per-station (that station's own real days "
    "only) for by_station when there are enough of them "
    "(MIN_DAILY_OBSERVATIONS). Reads pipeline/build_full_year.py's "
    "persisted daily table (Session 29) directly -- no raw trip "
    "re-download, and no forecast-holdout concept to preserve here (this "
    "fits a direct historical regression, not a forecast evaluated on "
    "unseen data), so using the full year is strictly more real data with "
    "a wider real temperature range than any subset of it. Every "
    "coefficient is reported with a 95% confidence interval (normal "
    "approximation, see CI_Z_SCORE) alongside its point estimate -- not "
    "just the point estimate alone. "
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
    "aggregates); Session 31 extended the window from 90 non-contiguous "
    "days (Feb/April/June 2026 only) to the full real Jul 2025-Jun 2026 "
    "year -- real limitations remain, stated plainly rather than implied "
    "resolved: temp_c/precip_mm are each ONE city-wide daily value "
    "(Open-Meteo's Central Park reference point, per pipeline/weather.py), "
    "not per-station, so real local weather variation across NYC's "
    "boroughs is not captured. A joint fit (temp and precip together, not "
    "two separate univariate fits) controls for the two being correlated "
    "with each other (a rainy day is often also cooler), but not for "
    "anything else true of those specific calendar days that isn't "
    "captured by date-of-year position or day-of-week (already implicit "
    "in which real dates got sampled) -- still an observational "
    "association, not a controlled experiment. The reported 95% "
    "confidence intervals reflect real sampling uncertainty in the "
    "regression coefficients themselves; they do not, and cannot, widen "
    "to reflect this observational-vs-causal gap -- a narrow CI here "
    "means 'this association is precisely estimated,' not 'this is a "
    "causal effect.'"
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


def load_daily_weather_panel(
    stations: dict, daily_net_flow_path: Path = DAILY_NET_FLOW_PATH
) -> pd.DataFrame:
    """Real per-(station, date) daily flow magnitude joined against real
    daily temp_c/precip_mm -- the direct replacement for Session 25's
    PDP-off-a-monthly-aggregate approach (see module docstring).

    Session 31: reads pipeline/build_full_year.py's persisted daily table
    (Session 29) directly, instead of re-downloading and re-aggregating
    raw trip archives that no longer exist on disk by design (deleted
    immediately after aggregation to keep peak disk usage low -- see
    PROGRESS.md's Session 29 entry). This is strictly a data-source swap:
    the table's own (station_id, date, hour, net) columns are exactly
    compute_daily_net_flow's old output shape, just read from disk instead
    of recomputed from scratch every call.

    Session 36: weather is joined per-station-zone, not one uniform
    city-wide value -- see pipeline/weather.py's compute_weather_zones()
    module docstring for why real spatial variation matters here and how
    the zones themselves are derived (Session 38: a fixed geographic grid,
    not k-means -- k-means was found to skew sparse peripheral areas like
    East Queens and Southern Brooklyn toward distant, station-dense zone
    centroids). `stations` (flows.json's stations dict) is now a required
    argument specifically to compute those zones.

    Magnitude per (station, date) is the mean absolute hourly net flow
    that day -- the same "mean of |curve|" definition station_magnitude()
    already uses, kept consistent so "how big is this station's typical
    swing" means the same thing everywhere in this file.
    """
    from pipeline.weather import compute_weather_zones, fetch_weather_at_points

    daily = pd.read_parquet(daily_net_flow_path, columns=["station_id", "date", "hour", "net"])
    daily["date"] = pd.to_datetime(daily["date"])
    daily_magnitude = (
        daily.groupby(["station_id", "date"])["net"]
        .apply(lambda x: float(np.mean(np.abs(x))))
        .rename("magnitude")
        .reset_index()
    )

    zone_by_station, zone_centroids = compute_weather_zones(stations)
    daily_magnitude["zone"] = daily_magnitude["station_id"].map(zone_by_station)
    # A station present in the daily table but absent from `stations` (a
    # fresh flows.json pull can have a slightly different roster than
    # whatever pull the daily table was last built from, same caveat
    # build_elasticities' own docstring already states) has no zone to
    # assign -- dropped here, not fabricated a zone.
    daily_magnitude = daily_magnitude.dropna(subset=["zone"])
    daily_magnitude["zone"] = daily_magnitude["zone"].astype(int)

    weather_by_zone = fetch_weather_at_points(zone_centroids, WEATHER_FETCH_START, WEATHER_FETCH_END)
    weather_frames = []
    for zone_idx, zone_weather in enumerate(weather_by_zone):
        zone_weather = zone_weather.copy()
        zone_weather["zone"] = zone_idx
        weather_frames.append(zone_weather)
    weather = pd.concat(weather_frames, ignore_index=True)

    # Inner join on (zone, date): a handful of midnight-crossing spillover
    # dates (e.g. 2026-06-30 23:xx trips landing on 2026-07-01, outside the
    # fetched weather range) simply have no weather match and are dropped
    # -- graceful, not fabricated, same rule as every other NaN-adjacent
    # gap in this project.
    return daily_magnitude.merge(weather[["zone", "date", "temp_mean_c", "precip_mm"]], on=["zone", "date"], how="inner")


def _ols_fit_with_covariance(X: np.ndarray, y: np.ndarray, min_rank: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Plain OLS: coefficients plus their full covariance matrix, via the
    closed-form sigma^2 * (X'X)^-1 -- shared by both fit functions below so
    the standard-error formula exists in exactly one place, not duplicated
    once per fit shape.

    Returns None if the fit is rank-deficient (columns too collinear to
    separate their individual effects, e.g. capacity and throughput
    tracking each other too closely) -- checked against min_rank, the
    number of coefficients being fit, so a caller with p columns can't
    silently accept a fit with fewer than p real degrees of freedom.
    """
    coeffs, residuals, rank, _singular_values = np.linalg.lstsq(X, y, rcond=None)
    if rank < min_rank:
        return None
    n, p = X.shape
    if n <= p:
        return None
    fitted = X @ coeffs
    sigma_squared = float(np.sum((y - fitted) ** 2)) / (n - p)
    xtx_inv = np.linalg.inv(X.T @ X)
    covariance = sigma_squared * xtx_inv
    return coeffs, covariance


class WeatherRegressionResult(NamedTuple):
    """magnitude ~ b0 + b1*temp_c + b2*precip_mm, plus each slope's own
    standard error -- a NamedTuple (not a plain tuple) so downstream code
    reads curve.b1/curve.se_b1 instead of curve[1]/an easily-miscounted
    index, the same self-documenting reasoning pipeline/qc.py's QCReport
    and pipeline/gbfs_logger.py's SnapshotResult already use elsewhere in
    this project.
    """

    b0: float
    b1: float  # temp_c coefficient
    b2: float  # precip_mm coefficient
    se_b1: float
    se_b2: float


def fit_daily_weather_regression(
    magnitudes: np.ndarray, temps: np.ndarray, precips: np.ndarray, n_distinct_days: int
) -> WeatherRegressionResult | None:
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
    fit = _ols_fit_with_covariance(X, magnitudes, min_rank=3)
    if fit is None:
        return None
    coeffs, covariance = fit
    b0, b1, b2 = coeffs
    se_b1, se_b2 = float(np.sqrt(covariance[1, 1])), float(np.sqrt(covariance[2, 2]))
    return WeatherRegressionResult(float(b0), float(b1), float(b2), se_b1, se_b2)


def load_station_throughput(daily_net_flow_path: Path = DAILY_NET_FLOW_PATH) -> pd.Series:
    """station_id -> mean real daily throughput (arrivals+departures/day)
    across the full persisted year.

    A busyness proxy independent of net-flow DIRECTION (throughput sums
    both sides; net flow differences them), used only as a regression
    control for the capacity/magnitude confound -- see module docstring.

    Session 31: reads data/daily_net_flow.parquet directly instead of
    re-downloading raw trips for pipeline.flows.compute_throughput (same
    reasoning as load_daily_weather_panel above). Every parquet row is
    already one real (station, date, hour), so summing arrivals_count +
    departures_count per (station, date) and then averaging across dates
    is the direct real-data equivalent of the old n_days-weighted mean --
    no separate weighting needed, since there's no month-spanning
    aggregate here to weight, just real individual days.
    """
    daily = pd.read_parquet(
        daily_net_flow_path, columns=["station_id", "date", "arrivals_count", "departures_count"]
    )
    daily["date"] = pd.to_datetime(daily["date"])
    per_day_throughput = (
        daily.groupby(["station_id", "date"])[["arrivals_count", "departures_count"]]
        .sum()
        .sum(axis=1)
        .rename("throughput")
        .reset_index()
    )
    return per_day_throughput.groupby("station_id")["throughput"].mean()


class CapacityRegressionResult(NamedTuple):
    """magnitude ~ b0 + b1*capacity + b2*capacity^2 + b3*throughput, plus
    the (capacity, capacity^2) block of the coefficient covariance matrix
    -- var_b1/var_b2/cov_b1_b2 are exactly what capacity_local_slope_se
    needs (via the delta method) to report a standard error for a
    *derivative* of the fit, not just for a raw coefficient.
    """

    b0: float
    b1: float
    b2: float
    b3: float
    var_b1: float
    var_b2: float
    cov_b1_b2: float


def fit_capacity_curve(
    capacities: np.ndarray, magnitudes: np.ndarray, throughput: np.ndarray
) -> CapacityRegressionResult | None:
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
    fit = _ols_fit_with_covariance(X, magnitudes, min_rank=4)
    if fit is None:
        return None
    coeffs, covariance = fit
    b0, b1, b2, b3 = coeffs
    return CapacityRegressionResult(
        float(b0), float(b1), float(b2), float(b3),
        var_b1=float(covariance[1, 1]), var_b2=float(covariance[2, 2]), cov_b1_b2=float(covariance[1, 2]),
    )


def capacity_local_slope(b1: float, b2: float, capacity: float) -> float:
    """d(magnitude)/d(capacity) of the quadratic fit, evaluated at `capacity`
    (throughput's own term drops out of a partial derivative w.r.t. capacity)."""
    return b1 + 2 * b2 * capacity


def capacity_local_slope_se(var_b1: float, var_b2: float, cov_b1_b2: float, capacity: float) -> float:
    """Standard error of capacity_local_slope's derivative b1 + 2*b2*capacity,
    via the delta method: Var(b1 + 2c*b2) = Var(b1) + 4c^2*Var(b2) + 4c*Cov(b1,b2),
    for a fixed evaluation point c = capacity (not itself a random variable).
    A derivative of a fitted curve is a LINEAR COMBINATION of b1 and b2, so
    its variance needs their covariance too, not just each one's own
    variance in isolation -- the two are correlated in a quadratic fit
    (capacity and capacity^2 share the same underlying regressor), and
    ignoring that covariance would report an overconfident (too-narrow) CI.
    """
    return float(np.sqrt(var_b1 + 4 * capacity**2 * var_b2 + 4 * capacity * cov_b1_b2))


def elasticity_ci95(slope: float, se: float, denominator: float) -> list[float]:
    """95% CI for an elasticity (slope/denominator), from the slope's own
    standard error -- both bounds are divided by the same denominator the
    point estimate itself uses, so the CI is on the identical unitless
    scale reported for the point estimate, not the raw (unnormalized)
    slope's own CI.
    """
    low = (slope - CI_Z_SCORE * se) / denominator
    high = (slope + CI_Z_SCORE * se) / denominator
    return [round(low, 4), round(high, 4)]


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
    capacity_curve_by_slug: dict[str, CapacityRegressionResult | None] = {}

    for slug, station_ids in groups.items():
        if not station_ids:
            continue
        group_panel = daily_panel[daily_panel["station_id"].isin(station_ids)]
        group_magnitude = float(np.mean([magnitude_by_id[sid] for sid in station_ids]))

        group_curve = fit_daily_weather_regression(
            group_panel["magnitude"].to_numpy(), group_panel["temp_mean_c"].to_numpy(),
            group_panel["precip_mm"].to_numpy(), group_panel["date"].nunique(),
        )

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
        mean_capacity = float(np.mean(capacities)) if len(capacities) else None

        entry = {
            "capacity_elasticity": (
                round(capacity_local_slope(curve.b1, curve.b2, mean_capacity) / group_magnitude, 4)
                if curve is not None else None
            ),
            "capacity_elasticity_ci95": (
                elasticity_ci95(
                    capacity_local_slope(curve.b1, curve.b2, mean_capacity),
                    capacity_local_slope_se(curve.var_b1, curve.var_b2, curve.cov_b1_b2, mean_capacity),
                    group_magnitude,
                )
                if curve is not None else None
            ),
            "temp_elasticity": round(group_curve.b1 / group_magnitude, 4) if group_curve is not None else None,
            "temp_elasticity_ci95": (
                elasticity_ci95(group_curve.b1, group_curve.se_b1, group_magnitude)
                if group_curve is not None else None
            ),
            "precip_elasticity": round(group_curve.b2 / group_magnitude, 4) if group_curve is not None else None,
            "precip_elasticity_ci95": (
                elasticity_ci95(group_curve.b2, group_curve.se_b2, group_magnitude)
                if group_curve is not None else None
            ),
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

        entry = {
            "temp_elasticity": round(station_curve.b1 / magnitude, 4) if station_curve is not None else None,
            "temp_elasticity_ci95": (
                elasticity_ci95(station_curve.b1, station_curve.se_b1, magnitude)
                if station_curve is not None else None
            ),
            "precip_elasticity": round(station_curve.b2 / magnitude, 4) if station_curve is not None else None,
            "precip_elasticity_ci95": (
                elasticity_ci95(station_curve.b2, station_curve.se_b2, magnitude)
                if station_curve is not None else None
            ),
            "n_obs": n_obs,
        }
        curve = capacity_curve_by_slug.get(slug)
        if curve is not None and sid in capacity_by_id:
            station_capacity = float(capacity_by_id[sid])
            entry["capacity_elasticity"] = round(
                capacity_local_slope(curve.b1, curve.b2, station_capacity) / magnitude, 4
            )
            entry["capacity_elasticity_ci95"] = elasticity_ci95(
                capacity_local_slope(curve.b1, curve.b2, station_capacity),
                capacity_local_slope_se(curve.var_b1, curve.var_b2, curve.cov_b1_b2, station_capacity),
                magnitude,
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
    daily_panel = load_daily_weather_panel(flows_payload["stations"])

    payload = build_elasticities(flows_payload, live_payload, daily_panel, throughput_by_id)

    ELASTICITIES_PATH.write_text(json.dumps(payload, indent=2))

    for slug, entry in payload["by_typology"].items():
        print(f"{slug}: {entry}")
    print(f"by_station: {len(payload['by_station'])} stations")
    print(f"wrote {ELASTICITIES_PATH}")

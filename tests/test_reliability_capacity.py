"""Tests for pipeline/reliability_capacity.py."""

import json

import numpy as np
import pytest

from pipeline.reliability_capacity import (
    LARGE_CAPACITY_CUTOFF,
    MIN_OBSERVATIONS,
    SMALL_CAPACITY_CUTOFF,
    compute_reliability_capacity,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload))
    return path


def _reliability_payload(stations):
    """stations: {station_id: (unusable_rate, n_observations)}."""
    return {
        "window": {"start": "2026-07-13T00:00:00+00:00", "end": "2026-09-13T00:00:00+00:00", "days": 61},
        "stations": {
            sid: {"unusable_rate": rate, "n_observations": n_obs, "n_unusable": round(rate * n_obs)}
            for sid, (rate, n_obs) in stations.items()
        },
    }


def _live_status_payload(stations):
    """stations: {station_id: capacity}."""
    return {
        "last_updated": "2026-09-14T00:00:00+00:00",
        "n_dropped": 0,
        "stations": {sid: {"capacity": cap, "bikes_available": 5, "docks_available": 5} for sid, cap in stations.items()},
    }


def _flows_payload(stations):
    """stations: {station_id: weekday_curve (list of 24 floats)}."""
    return {"stations": {sid: {"weekday": curve, "weekend": curve} for sid, curve in stations.items()}}


def _write_fixture(tmp_path, reliability_stations, live_stations, flow_stations):
    reliability_path = _write_json(tmp_path / "reliability.json", _reliability_payload(reliability_stations))
    live_status_path = _write_json(tmp_path / "live_status.json", _live_status_payload(live_stations))
    flows_path = _write_json(tmp_path / "flows.json", _flows_payload(flow_stations))
    return reliability_path, live_status_path, flows_path


def _flat_curve(magnitude):
    """A 24-hour curve whose L2 norm is exactly `magnitude` (one nonzero hour)."""
    curve = [0.0] * 24
    curve[0] = magnitude
    return curve


def _synthetic_join(n_per_capacity=40, small_cap=15, large_cap=50, rate_gap=0.05, noise_seed=0):
    """Stations split evenly between two capacity levels, with the large-
    capacity group's rate uniformly lower by rate_gap. Demand varies
    per-station (2.0-6.0 bikes/day, alternating which capacity group gets
    the higher value) so it carries real, capacity-uncorrelated variance
    -- a demand column that's constant across every station would be
    collinear with the intercept and make the fit rank-deficient by
    construction, which is a fixture bug, not something under test here.
    Plus a deterministic small amount of per-station noise (so the fit
    isn't a literally perfect line, which would make its own SE
    degenerate)."""
    rng = np.random.default_rng(noise_seed)
    reliability, live, flows = {}, {}, {}
    base_rate = 0.15
    for i in range(n_per_capacity):
        small_id, large_id = f"S{i}", f"L{i}"
        noise = rng.normal(0, 0.01, size=2)
        reliability[small_id] = (max(0.0, min(1.0, base_rate + noise[0])), 50)
        reliability[large_id] = (max(0.0, min(1.0, base_rate - rate_gap + noise[1])), 50)
        live[small_id] = small_cap
        live[large_id] = large_cap
        demand_a = 2.0 + 4.0 * (i % 5) / 4.0
        demand_b = 2.0 + 4.0 * ((i + 2) % 5) / 4.0
        flows[small_id] = _flat_curve(demand_a)
        flows[large_id] = _flat_curve(demand_b)
    return reliability, live, flows


def test_capacity_coefficient_has_correct_sign_and_magnitude(tmp_path):
    """Construct data where larger capacity is CLEANLY associated with a
    lower unusable rate (demand held constant across both groups) and
    check the fitted slope recovers that direction, not just a plausible
    number."""
    reliability, live, flows = _synthetic_join(n_per_capacity=60, small_cap=15, large_cap=50, rate_gap=0.06)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["buildable"] is True
    assert payload["capacity_coefficient_per_dock"] < 0, "more capacity should predict a LOWER unusable rate"
    # SE should be small relative to the coefficient -- a real, non-noise signal.
    assert abs(payload["capacity_coefficient_per_dock"]) > 2 * payload["capacity_coefficient_se"]
    assert payload["reliability_gap_points"] > 0
    assert "associated" in payload["headline"]
    assert "caused" not in payload["headline"].lower()


def test_thin_stations_excluded_by_min_observations(tmp_path):
    """A station below MIN_OBSERVATIONS must not enter the join at all,
    matching reliability.py's own rankable floor."""
    reliability = {
        "thin": (0.1, MIN_OBSERVATIONS - 1),
        "rankable": (0.1, MIN_OBSERVATIONS),
    }
    live = {"thin": 20, "rankable": 20}
    flows = {"thin": _flat_curve(3.0), "rankable": _flat_curve(3.0)}
    # Need enough total rows for a 3-parameter fit; pad with more rankable stations.
    for i in range(10):
        reliability[f"pad{i}"] = (0.1 + 0.001 * i, MIN_OBSERVATIONS)
        live[f"pad{i}"] = 20 + i
        flows[f"pad{i}"] = _flat_curve(3.0 + i)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["n_joined"] == 11, "the thin station must not be counted"


def test_missing_or_zero_capacity_excluded(tmp_path):
    """A station with no live match, or capacity 0/None, must be dropped
    rather than treated as a real zero-dock station."""
    reliability = {"has_cap": (0.1, 50), "no_live_match": (0.1, 50), "zero_cap": (0.1, 50)}
    live = {"has_cap": 20, "zero_cap": 0}  # no_live_match deliberately absent
    flows = {"has_cap": _flat_curve(3.0), "no_live_match": _flat_curve(3.0), "zero_cap": _flat_curve(3.0)}
    for i in range(10):
        reliability[f"pad{i}"] = (0.1 + 0.001 * i, 50)
        live[f"pad{i}"] = 20 + i
        flows[f"pad{i}"] = _flat_curve(3.0 + i)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["n_joined"] == 11, "only has_cap plus the 10 padding stations should survive"


def test_missing_flows_entry_excluded(tmp_path):
    """A station with reliability + live data but no flows.json entry has
    no demand control available and must be dropped, not defaulted."""
    reliability = {"in_flows": (0.1, 50), "not_in_flows": (0.1, 50)}
    live = {"in_flows": 20, "not_in_flows": 20}
    flows = {"in_flows": _flat_curve(3.0)}
    for i in range(10):
        reliability[f"pad{i}"] = (0.1 + 0.001 * i, 50)
        live[f"pad{i}"] = 20 + i
        flows[f"pad{i}"] = _flat_curve(3.0 + i)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["n_joined"] == 11


def test_not_buildable_when_too_few_stations_for_the_model(tmp_path):
    """Fewer joined stations than fitted parameters (intercept + capacity +
    demand = 3) must report buildable:False with a reason, never a
    degenerate or fabricated fit."""
    reliability = {"A": (0.1, 50), "B": (0.1, 50)}
    live = {"A": 20, "B": 25}
    flows = {"A": _flat_curve(3.0), "B": _flat_curve(4.0)}
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["buildable"] is False
    assert payload["reason"]
    assert payload["n_joined"] == 2


def test_band_summary_buckets_match_the_cutoffs(tmp_path):
    """Each station lands in exactly the capacity band its own capacity implies."""
    reliability, live, flows = _synthetic_join(n_per_capacity=30, small_cap=SMALL_CAPACITY_CUTOFF - 1, large_cap=LARGE_CAPACITY_CUTOFF + 5)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    labels = {band["label"]: band["n_stations"] for band in payload["band_summary"]}
    assert labels[f"< {SMALL_CAPACITY_CUTOFF} docks"] == 30
    assert labels[f"{LARGE_CAPACITY_CUTOFF}+ docks"] == 30
    assert labels[f"{SMALL_CAPACITY_CUTOFF}-{LARGE_CAPACITY_CUTOFF - 1} docks"] == 0


def test_method_metadata_names_inputs_window_and_the_missing_control(tmp_path):
    """The payload must self-document enough for the dashboard to render an
    honest method note without hardcoding it -- including the one control
    this analysis does NOT have (borough/zone)."""
    reliability, live, flows = _synthetic_join(n_per_capacity=30)
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["window"]["days"] == 61
    assert payload["min_observations"] == MIN_OBSERVATIONS
    assert "demand" in payload["method"]["inputs"]
    assert "borough" in payload["method"]["controls"].lower() or "zone" in payload["method"]["controls"].lower()


def test_headline_never_claims_causation(tmp_path):
    """Even in the no-detectable-effect branch, the wording must stay
    associational -- this is a non-negotiable labeling constraint, not a
    style preference."""
    # Construct data with NO real capacity/rate relationship (same rate regardless of capacity).
    reliability, live, flows = {}, {}, {}
    rng = np.random.default_rng(1)
    for i in range(60):
        sid = f"N{i}"
        reliability[sid] = (float(max(0.0, min(1.0, 0.12 + rng.normal(0, 0.01)))), 50)
        live[sid] = int(15 + (i % 5) * 10)
        flows[sid] = _flat_curve(3.0 + (i % 7))
    paths = _write_fixture(tmp_path, reliability, live, flows)

    payload = compute_reliability_capacity(*paths)

    assert payload["buildable"] is True
    assert "caused" not in payload["headline"].lower()
    assert "causes" not in payload["headline"].lower()

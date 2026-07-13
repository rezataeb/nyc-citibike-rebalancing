"""Tests for pipeline/station_typology.py."""

import numpy as np
import pytest

from pipeline.plan_routes import AM_HOURS
from pipeline.station_typology import (
    LOW_SIGNAL_NAME,
    PM_HOURS,
    apply_typology,
    build_shape_matrix,
    build_typology,
    low_volume_note,
    name_cluster,
    select_k,
)


def _station(weekday):
    return {"name": "S", "lat": 40.7, "lng": -74.0, "weekday": list(weekday), "weekend": [0.0] * 24}


def _curve(am_value, pm_value, jitter=0.0, seed=None):
    curve = [0.0] * 24
    for h in AM_HOURS:
        curve[h] = am_value
    for h in PM_HOURS:
        curve[h] = pm_value
    if jitter:
        rng = np.random.default_rng(seed)
        curve = [c + rng.normal(scale=jitter) for c in curve]
    return curve


# ---- build_shape_matrix ------------------------------------------------------


def test_build_shape_matrix_normalizes_included_and_splits_low_volume():
    stations = {
        "busy": _station(_curve(5.0, -5.0)),
        "quiet": _station([0.05] * 24),  # L2 norm ~0.245, below threshold
    }
    vectors, included, excluded = build_shape_matrix(stations, threshold=1.0)

    assert included == ["busy"]
    assert excluded == ["quiet"]
    assert vectors.shape == (1, 24)
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)


# ---- select_k -----------------------------------------------------------------


def test_select_k_prefers_two_well_separated_clusters():
    rng = np.random.default_rng(0)
    base_a = np.eye(24)[0]
    base_b = np.eye(24)[12]
    group_a = [base_a + rng.normal(scale=0.02, size=24) for _ in range(4)]
    group_b = [base_b + rng.normal(scale=0.02, size=24) for _ in range(4)]
    vectors = np.array([v / np.linalg.norm(v) for v in group_a + group_b])

    best_k, scores = select_k(vectors, k_range=range(2, 5))

    assert best_k == 2
    assert set(scores.keys()) == {2, 3, 4}


def test_select_k_raises_when_no_k_fits_the_data():
    vectors = np.array([[1.0] + [0.0] * 23])  # 1 point -- no k >= 2 possible
    with pytest.raises(ValueError):
        select_k(vectors, k_range=range(2, 5))


# ---- name_cluster ---------------------------------------------------------------


def test_name_cluster_labels_residential_feeder():
    centroid = np.zeros(24)
    for h in AM_HOURS:
        centroid[h] = -1.0
    for h in PM_HOURS:
        centroid[h] = 1.0
    assert name_cluster(centroid) == "Residential feeder (drains AM, fills PM)"


def test_name_cluster_labels_commuter_core():
    centroid = np.zeros(24)
    for h in AM_HOURS:
        centroid[h] = 1.0
    for h in PM_HOURS:
        centroid[h] = -1.0
    assert name_cluster(centroid) == "Commuter core (fills AM, drains PM)"


def test_name_cluster_labels_mixed_when_am_pm_dont_differ():
    centroid = np.full(24, 0.1)
    for h in AM_HOURS:
        centroid[h] = 0.1
    for h in PM_HOURS:
        centroid[h] = 0.1
    centroid[0] = 0.05  # give it nonzero range without an AM/PM direction
    assert name_cluster(centroid) == "Mixed/flat rhythm"


def test_name_cluster_labels_flat_when_centroid_has_no_range():
    assert name_cluster(np.zeros(24)) == "Flat (no rhythm)"


# ---- low_volume_note ------------------------------------------------------------


def test_low_volume_note_mentions_threshold_and_counts():
    note = low_volume_note(threshold=1.0, n_excluded=3, n_clustered=97)
    assert "3" in note
    assert "100" in note  # 3 + 97
    assert "1.0" in note


# ---- build_typology / apply_typology ---------------------------------------------


def test_build_typology_separates_rhythms_and_excludes_low_volume():
    stations = {}
    for i in range(4):
        stations[f"feeder_{i}"] = _station(_curve(-5, 5, jitter=0.1, seed=i))
    for i in range(4):
        stations[f"core_{i}"] = _station(_curve(5, -5, jitter=0.1, seed=100 + i))
    stations["quiet"] = _station([0.05] * 24)

    assignments, metadata = build_typology(stations, low_volume_threshold=1.0, k_range=range(2, 4))

    assert assignments["quiet"] == (-1, LOW_SIGNAL_NAME)
    assert metadata["n_excluded_low_signal"] == 1
    assert metadata["n_clustered"] == 8
    assert metadata["k"] == 2

    feeder_clusters = {assignments[f"feeder_{i}"][0] for i in range(4)}
    core_clusters = {assignments[f"core_{i}"][0] for i in range(4)}
    assert len(feeder_clusters) == 1
    assert len(core_clusters) == 1
    assert feeder_clusters != core_clusters
    assert "Residential feeder" in assignments["feeder_0"][1]
    assert "Commuter core" in assignments["core_0"][1]

    total_in_clusters = sum(c["n_stations"] for c in metadata["clusters"])
    assert total_in_clusters == metadata["n_clustered"]


def test_build_typology_raises_when_too_few_stations_pass_threshold():
    stations = {"only_one": _station(_curve(5, -5))}
    with pytest.raises(ValueError):
        build_typology(stations, low_volume_threshold=1.0, k_range=range(2, 4))


def test_apply_typology_writes_cluster_fields_and_metadata_onto_payload():
    stations = {}
    for i in range(4):
        stations[f"feeder_{i}"] = _station(_curve(-5, 5, jitter=0.1, seed=i))
    for i in range(4):
        stations[f"core_{i}"] = _station(_curve(5, -5, jitter=0.1, seed=100 + i))
    stations["quiet"] = _station([0.05] * 24)
    payload = {"stations": stations}

    result = apply_typology(payload, low_volume_threshold=1.0, k_range=range(2, 4))

    assert "typology" in result
    assert result["stations"]["quiet"]["cluster"] == -1
    assert result["stations"]["quiet"]["cluster_name"] == LOW_SIGNAL_NAME
    assert result["stations"]["feeder_0"]["cluster"] >= 0
    assert isinstance(result["stations"]["feeder_0"]["cluster_name"], str)

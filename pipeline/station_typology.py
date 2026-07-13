"""K-means station taxonomy on L2-normalized weekday flow shape.

Clusters stations by the *shape* of their all-period-average weekday
net-flow curve, not its magnitude: each 24-hour curve is L2-normalized to
a unit vector before clustering, so two stations with the same commute
rhythm (e.g. drains sharply AM, fills sharply PM) land near each other
regardless of whether one moves 5 bikes/day or 50. Clustering on raw
curves would let k-means' Euclidean distance be dominated by volume,
which answers "how busy" rather than the question this module actually
wants answered: "when does this station drain vs. fill."

k-means, not something fancier: a few thousand 24-dimensional unit
vectors, expected to fall into a handful of qualitatively distinct
diurnal patterns, is exactly the well-behaved, roughly-convex problem
k-means suits. DBSCAN can leave stations unassigned as noise (bad here --
every station needs a type); hierarchical clustering scales worse with no
cleaner k-selection story; GMM's soft/probabilistic membership buys
nothing when the output is one hard label per station.

k is chosen by silhouette score, not the elbow/inertia method: inertia
only measures cluster cohesion and improves monotonically as k grows
(trivially minimized at k=n), while silhouette (b - a) / max(a, b) also
rewards separation from the *next-nearest* cluster, giving a real interior
maximum to search over k=2..8 rather than an eyeballed guess.

LOW-VOLUME EXCLUSION -- read before trusting a cluster label: a station
whose raw weekday curve barely deviates from flat (L2 norm below
LOW_VOLUME_THRESHOLD, in bikes/day) would L2-normalize into an almost
arbitrary unit vector -- dividing near-zero, noisy values by a near-zero
norm amplifies noise into a "shape" that isn't really there. Such
stations are excluded from clustering entirely and assigned cluster -1,
per flows.json's own contract (CLAUDE.md: "cluster: int, -1 = low
signal"). The threshold and how many stations it excluded are written
into flows.json's own "typology" block (low_volume_threshold,
n_excluded_low_signal, low_volume_note) -- visible in the output itself,
the same treatment equity_join gave the school dataset's vintage label
and plan_routes gave the depot/max-stops assumptions -- not just this
docstring.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from pipeline.plan_routes import AM_HOURS

# On macOS with numpy built against Apple's Accelerate BLAS (numpy 2.x's
# default there), KMeans/silhouette_score's internal matmul calls raise
# spurious "overflow/divide by zero/invalid value encountered in matmul"
# RuntimeWarnings on well-formed float64 data -- verified against real
# flows.json data (Session 9): input vectors are confirmed finite and
# unit-norm, warning count scales linearly with k rather than tracking any
# particular station, and resulting clusters are well-balanced with a
# finite, sane silhouette score. This is a known cosmetic BLAS false
# positive, not evidence of corrupted input; suppressed narrowly by
# message so an unrelated real RuntimeWarning would still surface.
# warnings.filterwarnings' `message` is matched via re.match (anchored at the
# start of the string), so this needs a leading .* -- the actual messages
# start with "divide by zero"/"overflow"/"invalid value", not this phrase.
_BLAS_FALSE_POSITIVE_PATTERN = ".*encountered in matmul"

PM_HOURS = (16, 17, 18, 19)
LOW_VOLUME_THRESHOLD = 1.0  # L2 norm of the raw weekday curve, bikes/day
K_RANGE = range(2, 9)  # search k=2..8: a small, human-nameable number of types
RANDOM_STATE = 0
NAME_MARGIN_FRACTION = 0.15  # how much AM/PM must differ, relative to the centroid's own range, to name a direction
LOW_SIGNAL_NAME = "Low signal (excluded from clustering)"

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"


def build_shape_matrix(stations: dict, threshold: float) -> tuple[np.ndarray, list[str], list[str]]:
    """Split stations into (L2-normalized shape vectors, included ids, excluded low-signal ids)."""
    included_ids: list[str] = []
    excluded_ids: list[str] = []
    vectors: list[np.ndarray] = []
    for station_id, record in stations.items():
        curve = np.array(record["weekday"], dtype=float)
        norm = float(np.linalg.norm(curve))
        if norm < threshold:
            excluded_ids.append(station_id)
            continue
        vectors.append(curve / norm)
        included_ids.append(station_id)
    matrix = np.array(vectors) if vectors else np.empty((0, 24))
    return matrix, included_ids, excluded_ids


def select_k(vectors: np.ndarray, k_range: range = K_RANGE) -> tuple[int, dict[int, float]]:
    """Fit k-means for each candidate k, return (silhouette-maximizing k, {k: score})."""
    scores: dict[int, float] = {}
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=_BLAS_FALSE_POSITIVE_PATTERN, category=RuntimeWarning)
        for k in k_range:
            if k >= len(vectors):
                break
            km = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
            labels = km.fit_predict(vectors)
            scores[k] = float(silhouette_score(vectors, labels))
    if not scores:
        raise ValueError(f"not enough stations ({len(vectors)}) to try any k in {k_range}")
    best_k = max(scores, key=scores.get)
    return best_k, scores


def name_cluster(centroid: np.ndarray) -> str:
    """Auto-name a cluster from its centroid shape: compare AM vs. PM mean net flow."""
    am_mean = centroid[list(AM_HOURS)].mean()
    pm_mean = centroid[list(PM_HOURS)].mean()
    centroid_range = centroid.max() - centroid.min()
    if centroid_range == 0:
        return "Flat (no rhythm)"
    diff = pm_mean - am_mean
    margin = NAME_MARGIN_FRACTION * centroid_range
    if diff > margin:
        return "Residential feeder (drains AM, fills PM)"
    if diff < -margin:
        return "Commuter core (fills AM, drains PM)"
    return "Mixed/flat rhythm"


def low_volume_note(threshold: float, n_excluded: int, n_clustered: int) -> str:
    return (
        f"{n_excluded} of {n_excluded + n_clustered} stations were excluded from "
        f"typology clustering as low-signal (raw weekday curve L2 norm < {threshold} "
        "bikes/day) and assigned cluster -1 -- their curves are too close to flat/noisy "
        "to normalize into a meaningful shape."
    )


def build_typology(stations: dict, low_volume_threshold: float = LOW_VOLUME_THRESHOLD, k_range: range = K_RANGE) -> tuple[dict, dict]:
    """Cluster stations by weekday flow shape; return (station_id -> (cluster, cluster_name), typology metadata)."""
    vectors, included_ids, excluded_ids = build_shape_matrix(stations, low_volume_threshold)
    if len(included_ids) < 2:
        raise ValueError(f"only {len(included_ids)} stations pass the low-volume threshold; not enough to cluster")

    best_k, scores = select_k(vectors, k_range)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=_BLAS_FALSE_POSITIVE_PATTERN, category=RuntimeWarning)
        km = KMeans(n_clusters=best_k, random_state=RANDOM_STATE, n_init=10)
        labels = km.fit_predict(vectors)

    cluster_names = {i: name_cluster(km.cluster_centers_[i]) for i in range(best_k)}
    cluster_counts = {i: int((labels == i).sum()) for i in range(best_k)}

    assignments: dict[str, tuple[int, str]] = {}
    for station_id, label in zip(included_ids, labels):
        assignments[station_id] = (int(label), cluster_names[int(label)])
    for station_id in excluded_ids:
        assignments[station_id] = (-1, LOW_SIGNAL_NAME)

    metadata = {
        "k": best_k,
        "silhouette": round(scores[best_k], 4),
        "silhouette_by_k": {str(k): round(v, 4) for k, v in scores.items()},
        "low_volume_threshold": low_volume_threshold,
        "n_excluded_low_signal": len(excluded_ids),
        "n_clustered": len(included_ids),
        "low_volume_note": low_volume_note(low_volume_threshold, len(excluded_ids), len(included_ids)),
        "clusters": [
            {"cluster": i, "name": cluster_names[i], "n_stations": cluster_counts[i]} for i in range(best_k)
        ],
    }
    return assignments, metadata


def apply_typology(payload: dict, low_volume_threshold: float = LOW_VOLUME_THRESHOLD, k_range: range = K_RANGE) -> dict:
    """Write cluster/cluster_name onto every station and a typology{} metadata block, in place."""
    assignments, metadata = build_typology(payload["stations"], low_volume_threshold, k_range)
    for station_id, (cluster, name) in assignments.items():
        payload["stations"][station_id]["cluster"] = cluster
        payload["stations"][station_id]["cluster_name"] = name
    payload["typology"] = metadata
    return payload


if __name__ == "__main__":
    import sys

    flows_path = Path(sys.argv[1]) if len(sys.argv) > 1 else FLOWS_PATH
    payload = json.loads(flows_path.read_text())
    payload = apply_typology(payload)

    flows_path.write_text(json.dumps(payload))

    typology = payload["typology"]
    print(f"k={typology['k']}  silhouette={typology['silhouette']}")
    print(typology["low_volume_note"])
    for cluster in typology["clusters"]:
        print(f"  cluster {cluster['cluster']}: {cluster['name']} ({cluster['n_stations']} stations)")
    print(f"wrote {flows_path}")

"""Tests for pipeline/reliability.py."""

import textwrap

import pytest

from pipeline.reliability import MIN_OBSERVATIONS, compute_reliability


HEADER = "station_id,timestamp,bikes_available,docks_available,is_renting,is_returning"


def _write_log(tmp_path, rows):
    """Write a snapshot log with the real column order and return its path."""
    path = tmp_path / "snapshots.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    return path


def test_unusable_counts_zero_bikes_and_zero_docks(tmp_path):
    """Both extremes are failures: no bikes to take, or no dock to return to."""
    log = _write_log(
        tmp_path,
        [
            "A,2026-07-13 18:00:00+00:00,0,30,1,1",  # empty  -> unusable
            "B,2026-07-13 18:00:00+00:00,25,0,1,1",  # full   -> unusable
            "C,2026-07-13 18:00:00+00:00,10,10,1,1",  # healthy
        ],
    )
    payload = compute_reliability(log)

    assert payload["system"]["n_observations"] == 3
    assert payload["system"]["n_unusable"] == 2
    assert payload["system"]["n_empty"] == 1
    assert payload["system"]["n_full"] == 1
    assert payload["system"]["unusable_rate"] == pytest.approx(2 / 3)


def test_offline_observations_leave_numerator_and_denominator(tmp_path):
    """The fixed QC rule: offline stations are out of failure denominators.

    The offline row below reads 0 bikes AND 0 docks -- exactly the shape a
    naive filter would score as the worst possible failure. It must instead
    vanish from both sides, leaving the rate driven only by the online rows.
    """
    log = _write_log(
        tmp_path,
        [
            "A,2026-07-13 18:00:00+00:00,0,0,0,0",  # offline
            "B,2026-07-13 18:00:00+00:00,0,20,1,1",  # online, empty
            "C,2026-07-13 18:00:00+00:00,10,10,1,1",  # online, healthy
        ],
    )
    payload = compute_reliability(log)

    assert payload["system"]["n_offline_excluded"] == 1
    assert payload["system"]["n_observations"] == 2
    assert payload["system"]["unusable_rate"] == pytest.approx(0.5)
    assert "A" not in payload["stations"], "offline-only station should not get a rate"


def test_partially_offline_is_still_offline(tmp_path):
    """is_renting OR is_returning being 0 is enough -- the station is not fully usable."""
    log = _write_log(
        tmp_path,
        [
            "A,2026-07-13 18:00:00+00:00,5,5,1,0",
            "B,2026-07-13 18:00:00+00:00,5,5,0,1",
            "C,2026-07-13 18:00:00+00:00,5,5,1,1",
        ],
    )
    payload = compute_reliability(log)

    assert payload["system"]["n_offline_excluded"] == 2
    assert payload["system"]["n_observations"] == 1


def test_rate_is_observation_weighted_not_station_weighted(tmp_path):
    """A station observed more often contributes more chances to be caught out.

    Station A is observed 3 times and always unusable; B once and healthy.
    Observation-weighted that is 3/4 = 75%. Station-weighted it would be
    1/2 = 50%. The distinction is the whole point of the metric, so it is
    pinned rather than left to inference.
    """
    log = _write_log(
        tmp_path,
        [
            "A,2026-07-13 18:00:00+00:00,0,30,1,1",
            "A,2026-07-13 19:00:00+00:00,0,30,1,1",
            "A,2026-07-13 20:00:00+00:00,0,30,1,1",
            "B,2026-07-13 18:00:00+00:00,10,10,1,1",
        ],
    )
    payload = compute_reliability(log)

    assert payload["system"]["unusable_rate"] == pytest.approx(0.75)
    assert payload["stations"]["A"]["unusable_rate"] == pytest.approx(1.0)
    assert payload["stations"]["B"]["unusable_rate"] == pytest.approx(0.0)


def test_window_records_span_and_sampling_gaps(tmp_path):
    """The window block is what lets the dashboard label the tile honestly.

    The gap figures are not decoration: they are the evidence for why this
    module reports a rate rather than the outage hours the SLA is written in.
    """
    log = _write_log(
        tmp_path,
        [
            "A,2026-07-13 18:00:00+00:00,5,5,1,1",
            "A,2026-07-13 19:00:00+00:00,5,5,1,1",  # 60 min gap
            "A,2026-07-13 21:00:00+00:00,5,5,1,1",  # 120 min gap
        ],
    )
    payload = compute_reliability(log)
    window = payload["window"]

    assert window["n_snapshots"] == 3
    assert window["start"].startswith("2026-07-13T18:00")
    assert window["end"].startswith("2026-07-13T21:00")
    assert window["median_gap_minutes"] == pytest.approx(90.0)
    assert window["max_gap_minutes"] == pytest.approx(120.0)


def test_rankable_excludes_thin_stations_without_dropping_their_observations(tmp_path):
    """Thin stations are excluded from per-station ranking, not from the system rate."""
    rows = [f"A,2026-07-13 {h:02d}:00:00+00:00,0,30,1,1" for h in range(MIN_OBSERVATIONS)]
    rows.append("B,2026-07-14 06:00:00+00:00,10,10,1,1")  # 1 observation only
    payload = compute_reliability(_write_log(tmp_path, rows))

    assert payload["per_station_summary"]["n_stations"] == 2
    assert payload["per_station_summary"]["n_rankable"] == 1, "B is below the observation floor"
    assert "B" in payload["stations"], "thin stations still get a rate, they just aren't ranked"
    # B's single healthy observation is still in the system denominator.
    assert payload["system"]["n_observations"] == MIN_OBSERVATIONS + 1


def test_caveat_refuses_the_outage_hours_framing(tmp_path):
    """The payload must carry its own disclaimer -- the dashboard renders it.

    This is the guard against the metric quietly drifting back into being
    presented as SLA outage hours or penalty dollars, which the ~hourly
    sampling cadence cannot support.
    """
    log = _write_log(tmp_path, ["A,2026-07-13 18:00:00+00:00,5,5,1,1"])
    caveat = compute_reliability(log)["caveat"]

    assert "NOT outage hours" in caveat
    assert "$" not in caveat

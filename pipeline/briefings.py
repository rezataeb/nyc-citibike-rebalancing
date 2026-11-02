"""Per-station facts records for AI-written station briefings (Feature 1, step 1).

A briefing is two or three plain sentences about one station. The model
that writes it (a later step) sees ONLY the facts record built here, and
the number checker (also a later step) accepts only numbers that appear in
that record. So this module is where every number in a briefing comes
from -- it never calls a model.

Method, stated plainly:
  - Every number is computed and rounded by code into the exact form a
    sentence may use ("13.0", "8 AM", "307"). The model never does
    arithmetic.
  - Missing data is an explicit status, never an absent key the model
    could fill in: reliability is "ok", "not_rankable" (fewer than
    reliability.MIN_OBSERVATIONS online observations) or "no_data"
    (station absent from reliability.json).
  - Flow peaks come from flows.json's 24-hour net-flow curves (arrivals -
    departures, bikes/day; positive = filling, negative = draining).
    Weekday peaks are withheld for the typology's low-signal stations
    (cluster -1); weekend peaks are withheld when the weekend curve is
    weaker than the same cutoff the typology uses
    (station_typology.LOW_VOLUME_THRESHOLD, L2 norm in bikes/day).
  - weekend_differs is True only when the weekend fill or drain peak moves
    2+ hours from the weekday one; a one-hour move is hourly jitter.
  - typology_matches_peak checks the station's own weekday peaks against
    its typology name read literally; when False, the briefing must say
    the station is "grouped with" that type, not that it is one.
  - EXCLUDED_STATIONS lists explicit ids that get no briefing, each with a
    reason. Never matched by name pattern.
  - Unusable-rate rank is 1 = most often unusable, among every rankable
    station in reliability.json -- the same n_rankable reliability.json
    publishes, which includes stations that are not in flows.json.
    Ties share a rank (competition ranking: 1, 2, 2, 4).
  - Windows are read from the data files, never typed in: the trip window
    from data_manifest.json's months, the reliability window from
    reliability.json's window block.
  - facts_hash is a SHA-256 of the record with sorted keys, so a later
    test can flag any briefing whose underlying facts have changed.

Run (prints a summary and one sample record; writes nothing):
    python3 -m pipeline.briefings
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path

from pipeline.equity_join import NYCHA_THRESHOLD_M, SCHOOL_THRESHOLD_M, SUBWAY_THRESHOLD_M
from pipeline.reliability import MIN_OBSERVATIONS
from pipeline.station_typology import LOW_VOLUME_THRESHOLD

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FLOWS_PATH = DATA_DIR / "flows.json"
RELIABILITY_PATH = DATA_DIR / "reliability.json"
MANIFEST_PATH = DATA_DIR / "data_manifest.json"

LOW_SIGNAL_CLUSTER = -1
MIN_PEAK_SHIFT_HOURS = 2  # a weekend peak must move this far to count as a different pattern
MORNING_HOURS = range(5, 12)  # 5 AM - 11 AM: what a reader takes "AM" in a typology name to mean

# Which peak each directional typology name puts in the morning and which after noon.
TYPOLOGY_PEAK_SIDES = {
    "Commuter core (fills AM, drains PM)": {"am": "fill", "pm": "drain"},
    "Residential feeder (drains AM, fills PM)": {"am": "drain", "pm": "fill"},
}

# Stations that get no briefing, each with its reason. Explicit ids only --
# never matched by name pattern. Contents need the user's approval.
EXCLUDED_STATIONS: dict[str, str] = {}


def hour_label(hour: int) -> str:
    """0 -> '12 AM', 8 -> '8 AM', 12 -> '12 PM', 17 -> '5 PM'."""
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


def curve_strength(curve: list[float]) -> float:
    """L2 norm of a 24-hour curve -- the typology's own volume measure."""
    return math.sqrt(sum(value * value for value in curve))


def peak_hours(curve: list[float]) -> tuple[int | None, int | None]:
    """(fill hour, drain hour) of one 24-hour net-flow curve, 0-23.

    A side whose biggest value rounds to 0.0 (or has the wrong sign) is None:
    a curve that never drains has no drain peak.
    """
    fill_hour = max(range(24), key=lambda h: curve[h])
    drain_hour = min(range(24), key=lambda h: curve[h])
    return (
        fill_hour if round(curve[fill_hour], 1) > 0 else None,
        drain_hour if round(-curve[drain_hour], 1) > 0 else None,
    )


def flow_peaks(curve: list[float]) -> dict:
    """Biggest fill and drain hours as sentence-ready labels and sizes.

    Drain sizes are reported as positive magnitudes ("drains 0.4 bikes/day").
    """
    fill_hour, drain_hour = peak_hours(curve)
    return {
        "fill_peak_hour": hour_label(fill_hour) if fill_hour is not None else None,
        "fill_bikes_per_day": round(curve[fill_hour], 1) if fill_hour is not None else None,
        "drain_peak_hour": hour_label(drain_hour) if drain_hour is not None else None,
        "drain_bikes_per_day": round(-curve[drain_hour], 1) if drain_hour is not None else None,
    }


def hour_gap(a: int, b: int) -> int:
    """Hours between two clock hours, around the clock: 23 and 1 are 2 apart."""
    gap = abs(a - b) % 24
    return min(gap, 24 - gap)


def day_type_flow(curve: list[float], low_signal: bool) -> dict:
    """Flow facts for one day type; no peaks at all when the signal is too weak."""
    if low_signal:
        return {"low_signal": True}
    return {"low_signal": False, **flow_peaks(curve)}


def weekend_differs(
    weekday_curve: list[float],
    weekend_curve: list[float],
    weekday_low_signal: bool,
    weekend_low_signal: bool,
    min_shift: int = MIN_PEAK_SHIFT_HOURS,
) -> bool | None:
    """True when the weekend fill or drain peak moves min_shift+ hours from the weekday one.

    A one-hour move is hourly jitter, not a different pattern. A peak missing
    on either side is not compared. None when either day type is low-signal:
    there is nothing to compare.
    """
    if weekday_low_signal or weekend_low_signal:
        return None
    return any(
        a is not None and b is not None and hour_gap(a, b) >= min_shift
        for a, b in zip(peak_hours(weekday_curve), peak_hours(weekend_curve))
    )


def typology_matches_peak(typology: str, weekday_curve: list[float], weekday_low_signal: bool) -> bool | None:
    """Does the station's own weekday peak agree with its typology name, read literally?

    Typology names say when a station fills and drains ("fills AM, drains PM").
    They come from the cluster's average shape, so one station's own peaks can
    disagree. A reader takes the name literally, so this checks the stated
    peaks against it: the AM side must peak in the morning (MORNING_HOURS),
    the PM side at noon or later. False means the briefing must say the
    station is *grouped with* that type, not that it *is* one. None for
    low-signal stations and typology names without a direction.
    """
    sides = TYPOLOGY_PEAK_SIDES.get(typology)
    if weekday_low_signal or sides is None:
        return None
    fill_hour, drain_hour = peak_hours(weekday_curve)
    hours = {"fill": fill_hour, "drain": drain_hour}
    am_hour, pm_hour = hours[sides["am"]], hours[sides["pm"]]
    return am_hour is not None and pm_hour is not None and am_hour in MORNING_HOURS and pm_hour >= 12


def rank_unusable(reliability_stations: dict, min_observations: int = MIN_OBSERVATIONS) -> dict[str, int]:
    """Competition rank of each rankable station's unusable rate; 1 = worst."""
    rates = {
        station_id: record["unusable_rate"]
        for station_id, record in reliability_stations.items()
        if record["n_observations"] >= min_observations
    }
    ordered = sorted(rates.values(), reverse=True)
    first_position = {}
    for position, rate in enumerate(ordered, start=1):
        first_position.setdefault(rate, position)
    return {station_id: first_position[rate] for station_id, rate in rates.items()}


def reliability_facts(station_id: str, reliability: dict, ranks: dict[str, int]) -> dict:
    """Unusable rate, rank and the system rate for comparison, or an explicit status."""
    system_pct = round(reliability["system"]["unusable_rate"] * 100, 1)
    record = reliability["stations"].get(station_id)
    if record is None:
        return {"status": "no_data"}
    if station_id not in ranks:
        return {"status": "not_rankable", "min_observations": MIN_OBSERVATIONS}
    return {
        "status": "ok",
        "unusable_pct": round(record["unusable_rate"] * 100, 1),
        "rank": ranks[station_id],
        "n_ranked": len(ranks),
        "system_unusable_pct": system_pct,
    }


def context_facts(context: dict) -> dict:
    """Nearest subway plus housing / school / transit-gap flags, with their thresholds.

    Thresholds are included so a sentence like "within 300 m" uses a number
    that is in the record. Nearest NYCHA / school names appear only when near.
    transit_gap is equity_join's flag: nearest subway beyond the threshold.
    """
    near_nycha = bool(context["near_nycha"])
    near_school = bool(context["near_school"])
    return {
        "nearest_subway": context["subway_nearest"],
        "subway_distance_m": int(context["subway_dist_m"]),
        "near_nycha": near_nycha,
        "nycha_name": context["nycha_nearest"] if near_nycha else None,
        "near_school": near_school,
        "school_name": context["school_nearest"] if near_school else None,
        "transit_gap": bool(context["transit_gap"]),
        "nycha_threshold_m": NYCHA_THRESHOLD_M,
        "school_threshold_m": SCHOOL_THRESHOLD_M,
        "transit_gap_threshold_m": SUBWAY_THRESHOLD_M,
    }


def trip_window_label(months: list[str]) -> str:
    """['2025-09', ..., '2026-08'] -> 'Sep 2025 – Aug 2026'."""
    first, last = (datetime.strptime(m, "%Y-%m") for m in (min(months), max(months)))
    return f"{first:%b %Y} – {last:%b %Y}"


def reliability_window_label(window: dict) -> str:
    """reliability.json window block -> 'Jul 13 – Oct 27, 2026' (year shown once if shared)."""
    start = date.fromisoformat(window["start"][:10])
    end = date.fromisoformat(window["end"][:10])
    if start.year == end.year:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"


def station_facts(
    station_id: str,
    station: dict,
    reliability: dict,
    ranks: dict[str, int],
    windows: dict[str, str],
) -> dict:
    """The full facts record for one station -- the only input its briefing may use."""
    weekday_low = station["cluster"] == LOW_SIGNAL_CLUSTER
    weekend_low = curve_strength(station["weekend"]) < LOW_VOLUME_THRESHOLD
    return {
        "station_id": station_id,
        "name": station["name"],
        "typology": station["cluster_name"],
        "typology_matches_peak": typology_matches_peak(station["cluster_name"], station["weekday"], weekday_low),
        "weekday_flow": day_type_flow(station["weekday"], weekday_low),
        "weekend_flow": day_type_flow(station["weekend"], weekend_low),
        "weekend_differs": weekend_differs(station["weekday"], station["weekend"], weekday_low, weekend_low),
        "reliability": reliability_facts(station_id, reliability, ranks),
        "context": context_facts(station["context"]),
        "trip_window": windows["trip"],
        "reliability_window": windows["reliability"],
    }


def facts_hash(facts: dict) -> str:
    """SHA-256 of the record with sorted keys -- independent of key order."""
    canonical = json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_all_facts(
    flows: dict,
    reliability: dict,
    manifest: dict,
    excluded: dict[str, str] = EXCLUDED_STATIONS,
) -> dict[str, dict]:
    """Facts records for every station in flows.json except `excluded`, keyed by station id."""
    ranks = rank_unusable(reliability["stations"])
    windows = {
        "trip": trip_window_label(manifest["months"]),
        "reliability": reliability_window_label(reliability["window"]),
    }
    return {
        station_id: station_facts(station_id, station, reliability, ranks, windows)
        for station_id, station in flows["stations"].items()
        if station_id not in excluded
    }


def main() -> None:
    flows = json.loads(FLOWS_PATH.read_text())
    reliability = json.loads(RELIABILITY_PATH.read_text())
    manifest = json.loads(MANIFEST_PATH.read_text())
    all_facts = build_all_facts(flows, reliability, manifest)

    def count(predicate) -> int:
        return sum(1 for facts in all_facts.values() if predicate(facts))

    print(f"stations: {len(all_facts)}")
    for status in ("ok", "not_rankable", "no_data"):
        print(f"  reliability {status:<13} {count(lambda f: f['reliability']['status'] == status)}")
    print(f"  weekday low-signal        {count(lambda f: f['weekday_flow']['low_signal'])}")
    print(f"  weekend low-signal        {count(lambda f: f['weekend_flow']['low_signal'])}")
    print(f"  weekend peaks differ      {count(lambda f: f['weekend_differs'] is True)}")
    print(f"  weekend peaks same        {count(lambda f: f['weekend_differs'] is False)}")
    print(f"  typology matches peak     {count(lambda f: f['typology_matches_peak'] is True)}")
    print(f"  typology contradicts peak {count(lambda f: f['typology_matches_peak'] is False)}")
    print(f"  excluded                  {len(EXCLUDED_STATIONS)}")
    sample_id = next(iter(all_facts))
    sample = all_facts[sample_id]
    print(f"\nsample ({sample_id}), hash {facts_hash(sample)[:12]}…:")
    print(json.dumps(sample, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

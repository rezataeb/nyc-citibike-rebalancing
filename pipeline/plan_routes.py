"""Plan overnight truck tours: greedy nearest-neighbor over forecast AM deficit/surplus stations.

Reads flows.json's forecast curves directly -- station lat/lng, capacity,
and the forecasted weekday net-flow curve -- with no dependency on
gbfs_logger.py/empty_minutes.py's failure-minutes log (that measures a
different thing: observed ground-truth failures, not forecast). A
station's "AM net" is the sum of its forecast net_per_day over hours
6-9 (AM_HOURS below): negative means it's expected to drain before the
commute, positive means it's expected to fill. Stations past a small
magnitude threshold are flagged deficit/surplus and handed to a greedy
nearest-neighbor router: one truck at a time, alternating pickup/dropoff
based on its current load, moving to whichever unfinished flagged station
is nearest given what it can currently usefully do.

STOPGAP ASSUMPTION -- read before trusting a route: each truck's tour
starts at whichever surplus station has the most remaining forecast
surplus *at the moment that truck begins*, not a real depot or garage.
No real depot/garage location data exists in this public-data-only
stack, so this is a placeholder starting rule, not a modeled truck
origin. This is carried into data/route.json's own summary block
(the "depot_assumption" key) precisely so it's visible in the output
itself, not just here -- this is exactly the kind of thing a DOT
reviewer would reasonably ask about.

TOUR CAP -- a single truck's greedy loop has no inherent stopping point
short of running out of reachable candidates: with no distance or time
budget, one truck can and did (see PROGRESS.md, Session 8) plan a
2,142-stop tour spanning the entire city in one night, which no real
truck could drive. --max-stops caps each truck's tour at a rough
single-shift stop count (default MAX_STOPS_DEFAULT below); once a truck
hits it, its tour ends there and remaining flagged stations stay
unserviced. This is a basic realism fix, not an optimization pass --
finding genuinely better routes *within* whatever budget is set is
still the OR-Tools upgrade path's job, tracked separately. Whether a
truck's tour ended because it hit the cap (more work may have been
left undone) or because it genuinely ran out of reachable work
(nothing more to do) is a materially different outcome, so each truck's
"capped" flag and a run-level "any_truck_capped" summary are written
into data/route.json alongside "max_stops_note" -- visible in the
output, the same treatment as the depot assumption above.

Fleet-size logic (how many trucks a real deployment would need) is
explicitly out of scope: --trucks just runs that many sequential
single-truck tours over whatever candidates remain, with no
coordination between them. The gap between n_*_flagged and
n_*_serviced in the output is exactly the number that question would
start from.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from pipeline.equity_join import haversine_distance_m

AM_HOURS = (6, 7, 8, 9)
DEFICIT_THRESHOLD_BIKES = -1.0
SURPLUS_THRESHOLD_BIKES = 1.0
DEFAULT_CAPACITY = 20
DEFAULT_TRUCKS = 1
DEFAULT_MAX_STOPS = 45  # rough single-shift estimate, not a modeled time/distance budget
SEASON_NAMES = {"winter", "spring", "summer", "fall"}

DEPOT_ASSUMPTION_NOTE = (
    "Each truck's tour starts at whichever surplus station has the largest "
    "remaining forecast surplus when that truck begins -- a stopgap "
    "simplifying assumption, not a modeled depot or garage location. No "
    "real depot location data exists in this public-data-only stack."
)


def max_stops_note(max_stops: int) -> str:
    return (
        f"Each truck's tour is capped at {max_stops} stops (a rough "
        "single-shift estimate, not a modeled time/distance budget). If a "
        "truck's 'capped' flag is true, its tour ended because it hit this "
        "cap, not because routing ran out of reachable work -- some "
        "flagged stations may remain unserviced for that reason alone."
    )

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
ROUTE_PATH = Path(__file__).resolve().parent.parent / "data" / "route.json"


@dataclass
class StationCandidate:
    """One flagged station: its forecast AM magnitude, and how much of it is still unresolved."""

    station_id: str
    name: str
    lat: float
    lng: float
    kind: str  # "deficit" or "surplus"
    am_net: float  # raw forecast AM net flow (bikes), informational
    magnitude: int  # round(abs(am_net)) -- the original bikes needed/available
    remaining: int  # magnitude still unresolved; mutated as trucks run


@dataclass
class Stop:
    """One stop on a truck's tour."""

    station_id: str
    name: str
    lat: float
    lng: float
    action: str  # "pickup" or "dropoff"
    amount: int
    running_load: int


@dataclass
class TourResult:
    """One truck's completed tour, and why it stopped.

    capped=True means the tour ended because it hit max_stops -- more
    reachable work may have existed. capped=False means it ended because
    no reachable candidate remained -- it genuinely ran out of useful work.
    """

    stops: list[Stop]
    capped: bool


def validate_period(payload: dict, period: str | None) -> None:
    """Raise if the requested period isn't actually present in flows.json's granularity block."""
    if period is None:
        return
    granularity = payload["granularity"]
    if period in SEASON_NAMES:
        if period not in granularity["seasons"]:
            raise ValueError(f"period {period!r} not in flows.json granularity.seasons: {granularity['seasons']}")
    elif period not in granularity["months"]:
        raise ValueError(f"period {period!r} not in flows.json granularity.months: {granularity['months']}")


def select_curve(record: dict, period: str | None) -> dict | None:
    """Return the weekday/weekend curve dict for one station at the given period.

    None if the station has no data for that period at all -- e.g. a
    low-volume station with zero trips that month/season, per CLAUDE.md's
    "low-volume stations excluded from forecasting" rule; such a station
    simply can't be flagged, rather than crashing or defaulting to zero.
    """
    if period is None:
        return record
    if period in SEASON_NAMES:
        return record.get("seasons", {}).get(period)
    return record.get("months", {}).get(period)


def build_candidates(stations: dict, period: str | None) -> list[StationCandidate]:
    """Flag every station whose AM-window forecast net flow exceeds the deficit/surplus threshold."""
    candidates = []
    for station_id, record in stations.items():
        curve = select_curve(record, period)
        if curve is None:
            continue
        am_net = sum(curve["weekday"][h] for h in AM_HOURS)
        if am_net <= DEFICIT_THRESHOLD_BIKES:
            kind = "deficit"
        elif am_net >= SURPLUS_THRESHOLD_BIKES:
            kind = "surplus"
        else:
            continue
        magnitude = round(abs(am_net))
        candidates.append(
            StationCandidate(
                station_id=station_id,
                name=record["name"],
                lat=record["lat"],
                lng=record["lng"],
                kind=kind,
                am_net=am_net,
                magnitude=magnitude,
                remaining=magnitude,
            )
        )
    return candidates


def plan_single_tour(candidates: list[StationCandidate], capacity: int, max_stops: int = DEFAULT_MAX_STOPS) -> TourResult:
    """Greedy nearest-neighbor tour for one truck over candidates with remaining > 0.

    Mutates each candidate's `.remaining` in place, so a second truck (if
    --trucks > 1) sees exactly what the first one left unresolved. See
    module docstring for the starting-point assumption and the max_stops cap.
    """
    surplus_open = [c for c in candidates if c.kind == "surplus" and c.remaining > 0]
    if not surplus_open or max_stops <= 0:
        return TourResult(stops=[], capped=False)

    start = max(surplus_open, key=lambda c: c.remaining)
    stops: list[Stop] = []
    load = min(capacity, start.remaining)
    start.remaining -= load
    stops.append(Stop(start.station_id, start.name, start.lat, start.lng, "pickup", load, load))
    lat, lng = start.lat, start.lng

    while len(stops) < max_stops:
        if load <= 0:
            reachable = [c for c in candidates if c.kind == "surplus" and c.remaining > 0]
        elif load >= capacity:
            reachable = [c for c in candidates if c.kind == "deficit" and c.remaining > 0]
        else:
            reachable = [c for c in candidates if c.remaining > 0]
        if not reachable:
            return TourResult(stops=stops, capped=False)

        distances = haversine_distance_m(
            lat, lng, np.array([c.lat for c in reachable]), np.array([c.lng for c in reachable])
        )
        nxt = reachable[int(np.argmin(distances))]

        if nxt.kind == "surplus":
            amount = min(capacity - load, nxt.remaining)
            nxt.remaining -= amount
            load += amount
            stops.append(Stop(nxt.station_id, nxt.name, nxt.lat, nxt.lng, "pickup", amount, load))
        else:
            amount = min(load, nxt.remaining)
            nxt.remaining -= amount
            load -= amount
            stops.append(Stop(nxt.station_id, nxt.name, nxt.lat, nxt.lng, "dropoff", amount, load))
        lat, lng = nxt.lat, nxt.lng

    return TourResult(stops=stops, capped=True)


def build_route_payload(
    payload: dict, period: str | None, capacity: int, n_trucks: int, max_stops: int = DEFAULT_MAX_STOPS
) -> dict:
    """Flag AM deficit/surplus candidates and run n_trucks sequential single-truck tours."""
    validate_period(payload, period)
    candidates = build_candidates(payload["stations"], period)

    trucks_out = []
    for truck_index in range(n_trucks):
        result = plan_single_tour(candidates, capacity, max_stops)
        if not result.stops:
            break
        trucks_out.append(
            {"truck": truck_index + 1, "capped": result.capped, "stops": [asdict(s) for s in result.stops]}
        )

    return {
        "period": period if period is not None else "all",
        "am_hours": list(AM_HOURS),
        "capacity": capacity,
        "n_trucks_requested": n_trucks,
        "n_trucks_used": len(trucks_out),
        "depot_assumption": DEPOT_ASSUMPTION_NOTE,
        "max_stops": max_stops,
        "max_stops_note": max_stops_note(max_stops),
        "any_truck_capped": any(t["capped"] for t in trucks_out),
        "n_deficit_flagged": sum(1 for c in candidates if c.kind == "deficit"),
        "n_surplus_flagged": sum(1 for c in candidates if c.kind == "surplus"),
        "n_deficit_serviced": sum(1 for c in candidates if c.kind == "deficit" and c.remaining == 0),
        "n_surplus_serviced": sum(1 for c in candidates if c.kind == "surplus" and c.remaining == 0),
        "trucks": trucks_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flows", type=Path, default=FLOWS_PATH)
    parser.add_argument("--period", default=None, help="winter|spring|summer|fall|YYYY-MM; omit for the all-period average")
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    parser.add_argument("--trucks", type=int, default=DEFAULT_TRUCKS)
    parser.add_argument("--max-stops", type=int, default=DEFAULT_MAX_STOPS, help="per-truck tour cap, a rough single-shift stop estimate")
    parser.add_argument("--out", type=Path, default=ROUTE_PATH)
    args = parser.parse_args()

    payload = json.loads(args.flows.read_text())
    route_payload = build_route_payload(payload, args.period, args.capacity, args.trucks, args.max_stops)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(route_payload))

    print(
        f"period: {route_payload['period']}  capacity: {args.capacity}  "
        f"trucks requested: {args.trucks}  max stops/truck: {args.max_stops}"
    )
    print(f"flagged: {route_payload['n_deficit_flagged']} deficit, {route_payload['n_surplus_flagged']} surplus")
    print(
        f"serviced by {route_payload['n_trucks_used']} truck(s): "
        f"{route_payload['n_deficit_serviced']} deficit, {route_payload['n_surplus_serviced']} surplus"
    )
    if route_payload["any_truck_capped"]:
        print("NOTE: at least one truck hit the max-stops cap -- some unserviced stations may just be untried, not unreachable")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

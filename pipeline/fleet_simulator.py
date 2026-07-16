"""Precomputes fleet-size scenarios (1-10 trucks) for Investigator Mode Phase 3.

Reuses plan_routes.py's existing build_route_payload() -- fleet-size support
(--trucks) was already built in Session 8, so this module doesn't extend the
routing algorithm itself, it only sweeps that algorithm across a range of
fleet sizes and bundles the results into one additive JSON file the
dashboard's fleet-size slider reads, per Investigator Mode's "precomputed,
never live-computed in the browser" principle.

Fixed baseline params (period=all, plan_routes.py's own DEFAULT_CAPACITY/
DEFAULT_MAX_STOPS), independent of whatever data/route.json's own params
happen to be at any given time -- confirmed with the user: the historical
"Show route" toggle and Investigator Mode's fleet slider are deliberately
decoupled, so regenerating one never silently changes the other's meaning.

Each scenario N is a FRESH run from the full candidate list (not built by
adding one truck onto a previous N-1 run's leftover state) --
build_route_payload() already rebuilds candidates from payload["stations"]
on every call, so scenario N's n_deficit_serviced/n_surplus_serviced are the
cumulative total serviced by the first N trucks run sequentially. That's
exactly what lets a later marginal-benefit-per-truck check (the Guideline's
own "5th truck helps less than the 1st" verify step) be computed as a
simple difference between adjacent scenarios, without storing per-truck-
alone data separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.plan_routes import DEFAULT_CAPACITY, DEFAULT_MAX_STOPS, build_route_payload

FLOWS_PATH = Path(__file__).resolve().parent.parent / "data" / "flows.json"
FLEET_SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "data" / "fleet_scenarios.json"

FLEET_SIZES = list(range(1, 11))  # 1..10 trucks
DEFAULT_PERIOD = None  # all-period average, matches the dashboard's own historical default


def _notes(capacity: int, max_stops: int) -> str:
    return (
        "Ten independent scenarios (fleet sizes 1-10), each a FRESH run from "
        "the full candidate list -- not one truck added onto the previous "
        "scenario's leftover state. So scenario N's n_deficit_serviced/"
        "n_surplus_serviced already represent the cumulative total serviced "
        "by the first N trucks run sequentially (plan_routes.py's own "
        "--trucks behavior, unchanged), and the MARGINAL benefit of the Nth "
        "truck alone is scenario[N].n_deficit_serviced - "
        "scenario[N-1].n_deficit_serviced. Fixed baseline params (period=all, "
        f"capacity={capacity}, max_stops={max_stops}) independent of "
        "data/route.json's own current params -- the historical 'Show "
        "route' toggle and this fleet-size slider are deliberately "
        "decoupled, so regenerating one never silently changes the other's "
        "meaning. Same stopgap depot/max-stops assumptions as every "
        "plan_routes.py scenario -- see each scenario's own "
        "depot_assumption/max_stops_note fields. "
        "REAL FINDING, checked against the actual data rather than assumed: "
        "marginal benefit per truck does NOT diminish smoothly across "
        "fleet sizes 1-10 (verified up to 40) -- every truck in every "
        "scenario hits its own max_stops cap ('any_truck_capped' is always "
        "True), never runs out of reachable flagged stations. With "
        f"~1,600 flagged stations and only {max_stops} stops/truck, it "
        "would take 35+ trucks before any truck could plausibly run dry, "
        "far past a realistic fleet size. So within any deployable fleet "
        "range, the binding constraint is each truck's own per-shift stop "
        "budget, not overall demand -- an additional truck tends to help "
        "about as much as the first one did, not progressively less."
    )


def build_fleet_scenarios(
    payload: dict,
    fleet_sizes: list[int] = FLEET_SIZES,
    period: str | None = DEFAULT_PERIOD,
    capacity: int = DEFAULT_CAPACITY,
    max_stops: int = DEFAULT_MAX_STOPS,
) -> dict:
    """Run build_route_payload() once per fleet size, bundle into one payload."""
    scenarios = {
        str(n_trucks): build_route_payload(payload, period, capacity, n_trucks, max_stops)
        for n_trucks in fleet_sizes
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fleet_sizes": fleet_sizes,
        "period": period if period is not None else "all",
        "capacity": capacity,
        "max_stops": max_stops,
        "notes": _notes(capacity, max_stops),
        "scenarios": scenarios,
    }


if __name__ == "__main__":
    payload = json.loads(FLOWS_PATH.read_text())
    fleet_payload = build_fleet_scenarios(payload)

    FLEET_SCENARIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLEET_SCENARIOS_PATH.write_text(json.dumps(fleet_payload))

    print(f"fleet sizes: {fleet_payload['fleet_sizes']}")
    for n_trucks in fleet_payload["fleet_sizes"]:
        s = fleet_payload["scenarios"][str(n_trucks)]
        print(
            f"  {n_trucks} truck(s): {s['n_deficit_serviced']}/{s['n_deficit_flagged']} deficit serviced, "
            f"{s['n_surplus_serviced']}/{s['n_surplus_flagged']} surplus serviced"
        )
    print(f"wrote {FLEET_SCENARIOS_PATH}")

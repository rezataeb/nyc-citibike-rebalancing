# Phase 5 (dock capacity sandbox) — closed, not viable

**Status: closed.** Not deferred, not waiting on more data. Investigated
directly against the real GBFS snapshot log (Session 71-72) and found the
input this feature needs was never being recorded, and can't realistically
be recorded going forward either. This page records why, so the decision
doesn't get silently revisited without new information.

## What Phase 5 was supposed to be

A "what if" tool on the dashboard: pick a station, imagine changing how
many docks it has, see a projection of how that changes its risk of
running empty (no bikes to rent) or full (no dock to return to). A
planning tool for real capacity decisions, not just a report of what
already happened.

## Two things this needs, checked against the real data

**1. Can the snapshot log measure outage duration?** Snapshots are taken
roughly once an hour (median gap ~55–90 minutes depending on how it's
measured). Checked: when a station is caught unusable in one snapshot, is
it still unusable next time we check?

Result: **67.9%** of the time, yes — most caught outages are real and
often last well over an hour, not one-off noise. But that same fact cuts
the other way for *duration*: we can only catch an outage that happens to
be in progress exactly when we poll. A 15-minute outage has a real chance
of starting and ending entirely between two polls and never being seen at
all. Long outages get caught reliably; short ones get systematically
missed. So a *duration* estimate (minutes empty) built from this log would
be biased toward looking longer than reality — usable for a *rate*
("how often is this station a problem"), not usable for "how many
minutes was it broken."

Method, for anyone re-checking this: for every online, unusable
observation with a next reading for the same station within 70 minutes,
check whether that next reading is also unusable. Computed against the
full committed log as of 2026-09-13 (2,440,748 rows across
`snapshots.csv` + `snapshots_2026-09-13.csv`, 2,481 stations with online
observations): 298,794 unusable observations, 199,552 with a qualifying
next reading, 135,563 of those (67.9%) still unusable.

**2. Do we have any historical record of a station's capacity actually
changing?** This is the real blocker. `pipeline/gbfs_logger.py`'s
`parse_snapshot()` has only ever logged `station_id, timestamp,
bikes_available, docks_available, is_renting, is_returning` — it fetches
`station_information` (which carries the real `capacity` field) on every
poll, but only ever used it for the id crosswalk, never logged the
capacity value itself. There is no historical capacity column in this log,
for any station, at any point.

The closest available proxy — `bikes_available + docks_available` at each
snapshot — looked like it varied a lot (92% of stations showed what
looked like a real swing, median range 8 docks). Checked directly against
a real station rather than trusting that number: station `6433.01`'s
`bikes_available + docks_available` bounced between 16 and 20 across 981
snapshots, but its actual, currently-logged `capacity` field is a fixed
19 the entire time. The "variation" is docks going temporarily out of
service and back — ordinary operations, not the station getting resized.
That pattern held broadly, not just for this one station.

So the input variable this feature needs to learn from — capacity
actually changing — isn't in the data, and isn't a sampling-density
problem: the field was never captured at all. Even fixed going forward,
real physical redocking is a rare event; there's no realistic timeframe
in which enough of it would happen to fit anything meaningful.

## Decision

**Closed as not viable**, not left open as deferred. The persistence
finding above is real and worth keeping visible (it's on the dashboard
itself, in Live Docks' "Data notes"); the capacity-history gap is not
something more waiting, or more snapshots, would fix.

## Considered, not built in this pass

A related but genuinely different feature remains possible: instead of "what
if we changed this station's capacity," ask "do stations that already
have different capacities show different reliability, network-wide,
controlling for location and demand" — using `pipeline/reliability.py`'s
existing rate methodology against each station's real (current, static)
`capacity` field from `live_status.json`. This is a cross-sectional
comparison across stations, not a change-over-time projection, so it
doesn't hit the blocker above. It was intentionally not built in this
pass — noted here so a future session knows it's an option, not
forgotten.

# Citi Bike Rebalancing Explorer

## Running the dashboard

`dashboard.html` fetches its data files (`data/flows.json`,
`data/live_status.json`) with `fetch()`, which browsers block when a page is
opened directly from disk (`file://...`) for security reasons -- the fetch
rejects before a response ever comes back. **Double-clicking `dashboard.html`
will not work.**

Serve the `app/` directory over plain HTTP instead:

```
python3 -m http.server 8000
```

then open **http://localhost:8000/dashboard.html** in a browser.

Once this project is hosted on GitHub Pages (or any other real HTTP host),
opening the page's URL directly will work with no extra steps -- the
`file://` restriction only affects local, on-disk viewing.

## Investigator Mode

A collapsed-by-default panel ("Investigator mode") that turns the map from
a report into a "what if" sandbox. Every control reads a precomputed JSON
file -- nothing here re-runs a model live in the browser. All three
controls below have their own graceful degradation: a control simply
doesn't appear if its data file failed to load.

### Equity thresholds
Two sliders (NYCHA/school proximity, default 300m; subway gap, default
800m) recompute live station counts directly from each station's raw
distances already in `data/flows.json`'s `context` block -- not the fixed-
threshold flags baked in at build time. Reports a combined ("NYCHA or
school") and overlap ("NYCHA and school") count alongside the two
individual ones, so the flagged population is legible, not just three
numbers that could hide overlap. No map recoloring or filtering -- this is
a live count only, a deliberate scope boundary (see `CLAUDE.md`'s standing
exclusion of a map-level equity filter).

### Fleet simulator
A slider (1-10 trucks) reads one of ten precomputed scenarios in
`data/fleet_scenarios.json` (`pipeline/fleet_simulator.py`) and swaps them
into the same route layer/toggle the historical "Show route" control uses.
**Caveat, checked against real data rather than assumed:** marginal
benefit per truck does not diminish smoothly across any realistic fleet
size -- every truck in every scenario (1 through 40, checked) hits its own
45-stop shift cap before ever running out of flagged stations. The binding
constraint is each truck's own per-shift stop budget, not overall demand.

### Weather scenario
A preset dropdown (`rain_day`, `snow_day`, `ideal`) plus manual
temperature/precipitation sliders project each station's baseline flow
through its own capacity/temp/precip elasticity
(`pipeline/elasticities.py`, `data/elasticities.json`) -- station-specific
if available, else its typology group's, else left unadjusted for stations
with neither. **Estimated from historical elasticity, not a
weather-specific model** -- shown explicitly in the panel itself. A
`heat_wave` preset was deliberately excluded: the elasticity fit has only
5 distinct training values for temp/precip, and the top observed point
shows a sharp, likely-artifactual jump, so a 95°F scenario would
extrapolate from that specific unreliable segment.

### Diff bar, save/load preset, reset to baseline
Once any control moves from its own default, a persistent diff bar
(visible even with the panel collapsed) summarizes baseline vs. scenario
for all three controls together. "Save preset" produces a shareable URL
encoding the current state; opening that URL re-applies it automatically.
"Reset to baseline" returns all three controls to their defaults.

### Dock capacity sandbox (deferred)
The Guideline's original Phase 5 (adjust a station's capacity, project a
change in empty-minutes risk) is deferred, not built as a scoped/proxy
version -- see `PROGRESS.md`'s Deferred list for the two independent
blockers (GBFS log density far short of the statistical target, and no
risk-based elasticity has been fit against real empty/full observations).
No dock-capacity JSON file exists yet; the fourth `investigatorState`
field (`dockOverrides`) isn't part of the current schema, and existing
graceful degradation means a future addition won't break anything saved
today -- a preset saved now simply has no `dockOverrides` key, which loads
as "no overrides" once that field exists.

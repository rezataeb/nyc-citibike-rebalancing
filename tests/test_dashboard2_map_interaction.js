// Node smoke test for dashboard2.html's map interaction after Session 49:
//   - clustering removed (one dot per station, at every zoom)
//   - the drawn rebalancing route removed entirely
//   - click tolerance added (a near-miss selects the nearest station)
//   - zoom-aware over-plot compensation added
// Run with:
//   node tests/test_dashboard2_map_interaction.js
//
// Replaces test_dashboard2_view_mode.js (Session 47) and
// test_dashboard2_route_source.js (Session 48). Both were deleted rather
// than left passing-but-meaningless: they pinned an individual/grouped view
// mode and a route layer that no longer exist. The absence assertions below
// are what remains of them, and they matter more now -- they are what stops
// either feature reappearing by accident.
//
// Sandbox lives in tests/dashboard_harness.js.

const assert = require('assert');
const {
  readDashboard2,
  loadDashboard2,
  FAKE_FLOWS,
  FAKE_FLEET_SCENARIOS,
} = require('./dashboard_harness');

// Mirrors the harness's own projection so the test can state click
// distances in pixels rather than in opaque decimal degrees.
function degreesForPixels(px, zoom) {
  return px / ((256 * Math.pow(2, zoom)) / 360);
}

async function main() {
  const html = readDashboard2();

  // ================================================================
  // 1. Source-level absence. The DOM stub auto-creates any element and
  // would happily invent a deleted control, so removal is asserted
  // against the raw file.
  // ================================================================
  const mustBeGone = [
    ['leaflet.markercluster', 'the markercluster library must not be loaded any more'],
    ['MarkerCluster.css', 'markercluster stylesheets must be gone'],
    ['markerClusterGroup', 'no cluster group should be constructed'],
    ['function setViewMode', 'setViewMode should be gone with clustering'],
    ['function autoViewModeForZoom', 'autoViewModeForZoom should be gone with clustering'],
    ['function refreshClusters', 'refreshClusters should be gone with clustering'],
    ['function clusterIconCreateFunction', 'cluster icon builder should be gone'],
    ['id="route-toggle-btn"', 'the Show route button must be gone'],
    ['id="legend-route-row"', 'the legend route row must be gone'],
    ['function buildRouteLayer', 'the route layer builder must be gone'],
    ['function applyFleetScenarioToRoute', 'the fleet-to-route bridge must be gone'],
  ];
  for (const [needle, message] of mustBeGone) {
    assert.ok(!html.includes(needle), `${message} (found "${needle}")`);
  }
  // ...and the replacements are present.
  assert.ok(html.includes('function overplotAlphaForZoom'), 'over-plot compensation should exist');
  assert.ok(html.includes('function nearestStationWithin'), 'click tolerance should exist');

  const { dash, sandbox, map } = await loadDashboard2({ fleetScenarios: FAKE_FLEET_SCENARIOS });
  const state = dash.getState();

  // ================================================================
  // 2. One layer, always attached, holding every station.
  // ================================================================
  assert.ok(state.stationLayer, 'a single stationLayer should exist');
  assert.ok(!('clusterLayer' in state), 'clusterLayer must not exist on state any more');
  assert.ok(!('individualLayer' in state), 'individualLayer must not exist on state any more');
  assert.ok(!('viewMode' in state), 'viewMode must not exist on state any more');
  assert.ok(map.hasLayer(state.stationLayer), 'the station layer should be on the map at load');
  assert.strictEqual(Object.keys(state.markers).length, 3, 'all three stations should have markers');
  assert.strictEqual(state.stationLayer._layers.length, 3, 'the layer should hold all three markers');

  // ================================================================
  // 3. Zoom no longer swaps anything -- the same layer object stays put.
  // This is the direct replacement for the old view-mode test.
  // ================================================================
  for (const zoom of [9, 13, 14, 18, 10]) {
    map._zoomTo(zoom);
    assert.ok(map.hasLayer(state.stationLayer), `station layer must stay attached at z${zoom}`);
    assert.strictEqual(dash.getState().stationLayer, state.stationLayer, `no layer swap should happen at z${zoom}`);
  }
  const attachedGroups = map._layers.filter(l => l && l._kind === 'group');
  assert.strictEqual(attachedGroups.length, 1, 'exactly one layer group should ever be attached -- no second route/cluster layer');

  // ================================================================
  // 4. Over-plot compensation.
  // ================================================================
  assert.strictEqual(dash.overplotAlphaForZoom(14), 1, 'no fade at the z14 threshold');
  assert.strictEqual(dash.overplotAlphaForZoom(18), 1, 'no fade when zoomed in past it');
  assert.ok(dash.overplotAlphaForZoom(11) < 1, 'faded at z11');
  assert.strictEqual(dash.overplotAlphaForZoom(9), dash.overplotAlphaForZoom(11), 'fade floors out at/below z11');
  const midAlpha = dash.overplotAlphaForZoom(12.5);
  assert.ok(
    midAlpha > dash.overplotAlphaForZoom(11) && midAlpha < 1,
    'the fade should ramp between z11 and z14, not jump'
  );

  // And it actually reaches the markers: same station, same data, lower
  // fill opacity when zoomed out than when zoomed in.
  map._zoomTo(16);
  const styleZoomedIn = dash.markerStyleFor('A');
  map._zoomTo(10);
  const styleZoomedOut = dash.markerStyleFor('A');
  assert.ok(
    styleZoomedOut.fillOpacity < styleZoomedIn.fillOpacity,
    `markers should be more transparent zoomed out (z10 ${styleZoomedOut.fillOpacity} vs z16 ${styleZoomedIn.fillOpacity})`
  );

  // The selected station is exempt -- fading the one mark the user is
  // tracking would defeat the point of selection styling.
  dash.selectStation('A');
  map._zoomTo(10);
  const selectedStyle = dash.markerStyleFor('A');
  assert.strictEqual(selectedStyle.opacity, 1, 'the selected marker must never be faded');
  const unselectedStyle = dash.markerStyleFor('B');
  assert.ok(unselectedStyle.opacity < 1, 'a non-selected marker at the same zoom should still be faded');
  dash.closeStation();

  // ================================================================
  // 5. Click tolerance -- the "needs an exact click" fix.
  // ================================================================
  map._zoomTo(11);
  const stationA = FAKE_FLOWS.stations.A;

  // A near-miss inside the tolerance selects the station.
  const nearOffset = degreesForPixels(6, 11); // ~6px away, comfortably inside 14px
  map._clickAt(stationA.lat, stationA.lng + nearOffset);
  assert.strictEqual(dash.getState().selectedId, 'A', 'a click ~6px from station A should select it');

  dash.closeStation();
  assert.strictEqual(dash.getState().selectedId, null, 'precondition: nothing selected');

  // A click well outside the tolerance selects nothing -- the tolerance is
  // a tolerance, not "always snap to the nearest station on the map".
  const farOffset = degreesForPixels(60, 11);
  map._clickAt(stationA.lat, stationA.lng + farOffset);
  assert.strictEqual(dash.getState().selectedId, null, 'a click ~60px from any station should select nothing');

  // Just outside the boundary, to pin the threshold rather than only
  // testing values far from it.
  map._clickAt(stationA.lat, stationA.lng + degreesForPixels(20, 11));
  assert.strictEqual(dash.getState().selectedId, null, 'a click ~20px away is beyond the 14px tolerance');

  // The pure helper directly, including that it returns the NEAREST
  // candidate rather than the first one within range.
  assert.strictEqual(
    dash.nearestStationWithin({ lat: stationA.lat, lng: stationA.lng }, dash.CLICK_TOLERANCE_PX), 'A',
    'a click exactly on station A resolves to A'
  );
  assert.strictEqual(
    dash.nearestStationWithin({ lat: 0, lng: 0 }, dash.CLICK_TOLERANCE_PX), null,
    'a click nowhere near any station resolves to null'
  );
  // A generous tolerance covering more than one station must still pick the
  // closest -- B is nearer to this point than A or C.
  const stationB = FAKE_FLOWS.stations.B;
  assert.strictEqual(
    dash.nearestStationWithin({ lat: stationB.lat, lng: stationB.lng }, 100000), 'B',
    'with every station in range, the nearest one wins'
  );

  // An exact hit still goes through the marker's own click handler, which
  // is untouched -- the map handler is only a fallback for misses.
  dash.closeStation();
  state.markers.C._fire('click');
  assert.strictEqual(dash.getState().selectedId, 'C', "a direct marker click should still select that marker's station");
  dash.closeStation();

  // ================================================================
  // 6. The route is gone, and the fleet slider now only moves numbers.
  // ================================================================
  assert.ok(!('route' in state), 'state.route must not exist any more');
  assert.ok(!('routeLayer' in state), 'state.routeLayer must not exist any more');
  assert.ok(!('routeVisible' in state), 'state.routeVisible must not exist any more');

  const layersBefore = map._layers.length;
  const statsBefore = sandbox._elements['fleet-stats'].textContent;
  dash.setFleetSize(7);
  assert.strictEqual(dash.getState().investigatorState.fleetSize, 7, 'the slider should still drive fleet size');
  assert.notStrictEqual(sandbox._elements['fleet-stats'].textContent, statsBefore, 'the counts should update');
  assert.ok(
    sandbox._elements['fleet-stats'].textContent.includes('7'),
    'the stats line should reflect the 7-truck scenario'
  );
  assert.strictEqual(
    map._layers.length, layersBefore,
    'moving the fleet slider must not add or remove any map layer now that the route is gone'
  );

  console.log('dashboard2 map-interaction smoke test passed (Session 49: no clustering, no drawn route, click tolerance, over-plot fade).');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});

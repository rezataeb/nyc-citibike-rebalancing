// Node smoke test for dashboard2.html's hover tolerance (Session 64).
//
// Session 49 added a map-level click tolerance so a near-miss click still
// selects the nearest station (station dots are 2.5-8px at city zoom).
// Hover had no equivalent: a near-miss mouse position showed no tooltip at
// all, even though it has the exact same "aiming at a tiny target" problem.
// This test exercises the map-level mousemove handler added to close that
// gap, using the same nearestStationWithin()/CLICK_TOLERANCE_PX plumbing
// click tolerance already uses.
//
// Run with:
//   node tests/test_dashboard2_hover_tolerance.js
//
// Sandbox lives in tests/dashboard_harness.js.

const assert = require('assert');
const {
  readDashboard2,
  loadDashboard2,
  FAKE_FLOWS,
} = require('./dashboard_harness');

function degreesForPixels(px, zoom) {
  return px / ((256 * Math.pow(2, zoom)) / 360);
}

async function main() {
  const html = readDashboard2();
  assert.ok(html.includes('map.on(\'mousemove\''), 'a map-level mousemove handler should exist for hover tolerance');

  const { dash, map } = await loadDashboard2();
  const state = dash.getState();
  map._zoomTo(11);

  const stationA = FAKE_FLOWS.stations.A;
  const stationB = FAKE_FLOWS.stations.B;
  const plainRadiusA = dash.radiusFor('A');
  const hoverRadiusA = plainRadiusA + dash.MARKER_HOVER_RADIUS_DELTA_PX;

  // ================================================================
  // 1. A near-miss inside the tolerance still shows the tooltip and bumps
  // the radius, exactly like an exact hit would via the marker's own
  // native mouseover.
  // ================================================================
  const nearOffset = degreesForPixels(6, 11); // ~6px away, comfortably inside 14px
  map._mousemoveAt(stationA.lat, stationA.lng + nearOffset);
  assert.strictEqual(state.markers.A._radius, hoverRadiusA, 'a near-miss ~6px from A should apply the hover radius bump');
  assert.strictEqual(state.markers.A._tooltipOpen, true, 'a near-miss should open A\'s tooltip');
  assert.strictEqual(state.markers.A._tooltipContent, dash.stationHoverHtml('A'), 'the tooltip content should be A\'s hover text');

  // ================================================================
  // 2. Moving well outside the tolerance clears the hover state entirely --
  // this is a tolerance, not "always highlight the nearest station".
  // ================================================================
  const farOffset = degreesForPixels(60, 11);
  map._mousemoveAt(stationA.lat, stationA.lng + farOffset);
  assert.strictEqual(state.markers.A._radius, plainRadiusA, 'moving ~60px away should clear the radius bump');
  assert.strictEqual(state.markers.A._tooltipOpen, false, 'moving ~60px away should close the tooltip');

  // ================================================================
  // 3. Moving from inside A's tolerance zone straight into B's clears A and
  // applies B -- only one station is ever "tolerance-hovered" at a time.
  // ================================================================
  map._mousemoveAt(stationA.lat, stationA.lng + nearOffset);
  assert.strictEqual(state.markers.A._tooltipOpen, true, 'precondition: A is tolerance-hovered');

  const plainRadiusB = dash.radiusFor('B');
  const hoverRadiusB = plainRadiusB + dash.MARKER_HOVER_RADIUS_DELTA_PX;
  map._mousemoveAt(stationB.lat, stationB.lng + degreesForPixels(6, 11));
  assert.strictEqual(state.markers.A._radius, plainRadiusA, 'switching to B should clear A\'s radius bump');
  assert.strictEqual(state.markers.A._tooltipOpen, false, 'switching to B should close A\'s tooltip');
  assert.strictEqual(state.markers.B._radius, hoverRadiusB, 'B should now carry the hover radius bump');
  assert.strictEqual(state.markers.B._tooltipOpen, true, 'B\'s tooltip should now be open');

  // ================================================================
  // 4. The cursor leaving the map entirely clears whatever was
  // tolerance-hovered, the same way it would leaving a real marker.
  // ================================================================
  map._mouseoutMap();
  assert.strictEqual(state.markers.B._radius, plainRadiusB, 'map mouseout should clear B\'s radius bump');
  assert.strictEqual(state.markers.B._tooltipOpen, false, 'map mouseout should close B\'s tooltip');

  // ================================================================
  // 5. An exact hit still goes through the marker's own native
  // mouseover/mouseout, untouched by this change -- the map handler only
  // ever supplements a near-miss, it doesn't replace direct marker events.
  // ================================================================
  state.markers.C._fire('mouseover');
  assert.strictEqual(state.markers.C._radius, dash.radiusFor('C') + dash.MARKER_HOVER_RADIUS_DELTA_PX, 'a direct marker mouseover should still bump its own radius');
  state.markers.C._fire('mouseout');
  assert.strictEqual(state.markers.C._radius, dash.radiusFor('C'), 'a direct marker mouseout should still reset its own radius');

  console.log('dashboard2 hover-tolerance smoke test passed (Session 64: near-miss hover matches near-miss click).');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});

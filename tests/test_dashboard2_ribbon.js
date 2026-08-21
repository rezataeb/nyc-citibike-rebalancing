// Node test for dashboard2.html's metric ribbon (Session 50).
//
// The ribbon replaced a four-tile rail readout in which three of four tiles
// ignored the hour slider. That is the specific regression this file exists to
// prevent, so the central assertion is not "the tiles have values" but "the
// values CHANGE when the slice changes." A ribbon that rendered plausible
// numbers and then sat still would pass a naive test and reintroduce the exact
// bug the redesign was for.
//
// Run with:
//   node tests/test_dashboard2_ribbon.js
//
// Sandbox lives in tests/dashboard_harness.js.

const assert = require('assert');
const {
  loadDashboard2,
  readDashboard2,
  makeCurve,
  makeContext,
  FAKE_FLOWS,
  FAKE_LIVE,
  FAKE_RELIABILITY,
} = require('./dashboard_harness');

const value = (sandbox, i) => sandbox._elements[`ribbon-value-${i}`].textContent;
const label = (sandbox, i) => sandbox._elements[`ribbon-label-${i}`].innerHTML;
const tooltip = (sandbox, i) => sandbox._elements[`ribbon-tile-${i}`].title;

async function testHistoricalTilesTrackTheHourSlider() {
  const { dash, sandbox } = await loadDashboard2();

  // FAKE_FLOWS weekday curves: A +12 @8, -6 @17; B -9 @8, +4 @17; C +1 @8.
  // At hour 8 that is |12|+|9|+|1| = 22 out of place, 1 station needing bikes
  // (B, -9) and 2 needing docks (A, C), and 9 bikes to move.
  dash.renderHour(8);
  assert.strictEqual(value(sandbox, 1), '22', 'hour 8 bikes-out-of-place');
  assert.strictEqual(value(sandbox, 2), '1 / 2', 'hour 8 need-bikes / need-docks');
  assert.strictEqual(value(sandbox, 3), '9', 'hour 8 bikes-to-move');

  // At hour 17: A -6, B +4, C 0. Only nonzero stations are counted on either
  // side, so C (exactly 0) is in neither -- 1 needing bikes, 1 needing docks.
  dash.renderHour(17);
  assert.strictEqual(value(sandbox, 1), '10', 'hour 17 bikes-out-of-place');
  assert.strictEqual(value(sandbox, 2), '1 / 1', 'hour 17 need-bikes / need-docks');
  assert.strictEqual(value(sandbox, 3), '6', 'hour 17 bikes-to-move');

  console.log('  historical tiles recompute per hour -- the §10B regression');
}

async function testHistoricalTilesTrackDayType() {
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);
  const weekday = [1, 2, 3].map(i => value(sandbox, i));

  // Weekend curves at hour 8: A +2, B -1, C +0.5 -- a different slice of the
  // same stations, so every tile must move even though the hour did not.
  dash.setDayType('weekend');
  const weekend = [1, 2, 3].map(i => value(sandbox, i));

  assert.notDeepStrictEqual(weekend, weekday, 'day-type switch left the ribbon unchanged');
  assert.strictEqual(value(sandbox, 1), '4', 'weekend hour 8 bikes-out-of-place (|2|+|1|+|0.5| = 3.5, rounded)');
  assert.strictEqual(value(sandbox, 2), '1 / 2', 'weekend hour 8 need-bikes / need-docks');

  console.log('  historical tiles recompute per day type');
}

async function testLiveModeSwapsAllThreeTiles() {
  const { dash, sandbox } = await loadDashboard2({ reliability: FAKE_RELIABILITY });
  dash.renderHour(8);
  const historical = [1, 2, 3].map(i => value(sandbox, i));

  dash.setMode('live');
  const live = [1, 2, 3].map(i => value(sandbox, i));
  assert.notDeepStrictEqual(live, historical, 'mode switch left the ribbon unchanged');

  // All three fake stations have usable live data. A is empty (0 bikes), B is
  // full (0 docks), C is neither -- but A and B are ~5 km apart, far beyond
  // the 500 m adjacency radius, so NEITHER is in breach. 3 of 3 meeting.
  assert.strictEqual(value(sandbox, 1), '100.0%', 'no unusable station has an unusable neighbour within 500 m');

  // Median fill across A (0/30 = 0%), B (25/25 = 100%), C (10/20 = 50%) is 50%.
  assert.strictEqual(value(sandbox, 2), '50%', 'median fill');
  assert.ok(label(sandbox, 2).includes('1 empty'), 'empty count on the fill tile');
  assert.ok(label(sandbox, 2).includes('1 full'), 'full count on the fill tile');

  assert.strictEqual(value(sandbox, 3), '25.0%', 'observed unusable rate from reliability.json');

  console.log('  live mode swaps all three tiles');
}

async function testAdjacencyRuleActuallyBites() {
  // Same two failing stations as the test above (A empty, B full), moved to
  // ~55 m apart. This case and that one are the pair that proves the
  // adjacency rule is load-bearing: identical availability, different
  // geometry, 100% there and 33.3% here. Either test alone would also pass
  // against an implementation that ignored distance entirely.
  const flows = JSON.parse(JSON.stringify(FAKE_FLOWS));
  flows.stations.B.lat = 40.7505; // ~55 m from A at 40.7500
  flows.stations.B.lng = -73.98;

  const { dash, sandbox } = await loadDashboard2({ flows, reliability: FAKE_RELIABILITY });
  dash.setMode('live');

  const standard = dash.computeLiveStandard();
  assert.strictEqual(standard.total, 3, 'three stations have usable live data');
  assert.strictEqual(standard.nUnusable, 2, 'A empty and B full are both unusable');
  assert.strictEqual(standard.inBreach, 2, 'adjacent unusable pair puts both in breach');
  assert.strictEqual(value(sandbox, 1), '33.3%', 'one of three stations meets the standard');

  console.log('  adjacency rule changes the result -- it is not decorative');
}

async function testOfflineStationsLeaveBothSides() {
  // The QC rule: offline stations are excluded from failure denominators. C is
  // the only usable-and-healthy station, so marking it offline must drop the
  // denominator to 2, not leave it at 3 with C counted as a pass.
  const live = JSON.parse(JSON.stringify(FAKE_LIVE));
  live.stations.C.is_renting = 0;

  const { dash } = await loadDashboard2({ live, reliability: FAKE_RELIABILITY });
  dash.setMode('live');

  const standard = dash.computeLiveStandard();
  assert.strictEqual(standard.total, 2, 'offline station left the denominator');

  const fill = dash.computeFillDistribution();
  assert.strictEqual(fill.total, 2, 'offline station left the fill distribution too');

  console.log('  offline stations leave both numerator and denominator');
}

async function testMissingReliabilityDegradesGracefully() {
  // reliability.json does not exist until pipeline/reliability.py has run.
  // The harness 404s it by default, so this is the no-file path.
  const { dash, sandbox } = await loadDashboard2();
  dash.setMode('live');

  assert.strictEqual(value(sandbox, 3), '—', 'missing reliability.json shows an em dash, not NaN or 0');
  assert.ok(
    /pipeline\/reliability\.py/.test(tooltip(sandbox, 3)),
    'the empty tile says how to populate it'
  );
  // The other two tiles are unaffected -- one missing optional file must not
  // take the ribbon down with it.
  assert.strictEqual(value(sandbox, 1), '100.0%', 'hero tile still renders without reliability.json');

  console.log('  missing reliability.json degrades to one em dash, not a broken ribbon');
}

async function testHistoricalRateTileIsLabelledAsAWindow() {
  // The one tile that is not a "now" reading sits between two that are. If it
  // ever loses its window stamp it becomes a live-looking number that is up to
  // a month stale -- the single most misleading thing this ribbon could do.
  const { dash, sandbox } = await loadDashboard2({ reliability: FAKE_RELIABILITY });
  dash.setMode('live');

  assert.ok(label(sandbox, 3).includes('Jul 13–Jul 26'), 'window range is on the tile face');
  assert.ok(/not outage hours|frequency, not outage hours/.test(tooltip(sandbox, 3)),
    'tooltip states this is a frequency, not outage hours');
  assert.ok(!/\$/.test(label(sandbox, 3) + tooltip(sandbox, 3)),
    'no dollar figure anywhere on the tile -- the cadence cannot support a penalty claim');

  console.log('  historical-rate tile carries its window and refuses the $ claim');
}

function testTheOldReadoutIsGoneFromTheMarkup() {
  // Asserted against the raw HTML, not the DOM stub: the stub auto-creates
  // whatever id is asked of it, so it can never prove an element's absence
  // (harness gotcha 1).
  const html = readDashboard2();
  for (const id of ['readout-deficit', 'readout-equity', 'readout-subway-gap', 'readout-filter']) {
    assert.ok(!html.includes(`id="${id}"`), `${id} still in the markup`);
  }
  assert.ok(!/class="readout"/.test(html), 'the old .readout container is still in the markup');
  // Scoped to rendered text (>...<), not the whole file: the renderRibbon()
  // comment block quotes this wording to explain why it was removed, and an
  // assertion that forbade naming it would forbid documenting it.
  assert.ok(!/>current map filter</.test(html),
    'the misleading "current map filter" label is still rendered as element text');

  console.log('  the old rail readout is gone from the markup, not just hidden');
}

async function testRibbonTilesAreNotHardcodedInMarkup() {
  // Every tile is filled by renderRibbon(). A number baked into the HTML would
  // be a number nobody computed -- and would survive even if renderRibbon()
  // broke, which is precisely how a stale metric ships unnoticed.
  const html = readDashboard2();
  const ribbon = html.slice(html.indexOf('<div class="ribbon"'), html.indexOf('</header>'));
  assert.ok(!/<b class="ribbon__value"[^>]*>[^<]+</.test(ribbon), 'a ribbon value is hardcoded in the markup');
  assert.ok(!/<span class="ribbon__label"[^>]*>[^<]+</.test(ribbon), 'a ribbon label is hardcoded in the markup');

  console.log('  no tile content is baked into the markup');
}

(async () => {
  await testHistoricalTilesTrackTheHourSlider();
  await testHistoricalTilesTrackDayType();
  await testLiveModeSwapsAllThreeTiles();
  await testAdjacencyRuleActuallyBites();
  await testOfflineStationsLeaveBothSides();
  await testMissingReliabilityDegradesGracefully();
  await testHistoricalRateTileIsLabelledAsAWindow();
  testTheOldReadoutIsGoneFromTheMarkup();
  await testRibbonTilesAreNotHardcodedInMarkup();
  console.log('dashboard2 ribbon test passed (Session 50: mode-aware, slice-aware metric ribbon).');
})();

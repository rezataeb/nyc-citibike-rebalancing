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
  assert.strictEqual(value(sandbox, 2), '1', 'hour 8 stations needing bikes');
  assert.strictEqual(value(sandbox, 3), '2', 'hour 8 stations needing docks');
  assert.strictEqual(value(sandbox, 4), '9', 'hour 8 bikes-to-move');

  // At hour 17: A -6, B +4, C 0. Only nonzero stations are counted on either
  // side, so C (exactly 0) is in neither -- 1 needing bikes, 1 needing docks.
  dash.renderHour(17);
  assert.strictEqual(value(sandbox, 1), '10', 'hour 17 bikes-out-of-place');
  assert.strictEqual(value(sandbox, 2), '1', 'hour 17 stations needing bikes');
  assert.strictEqual(value(sandbox, 3), '1', 'hour 17 stations needing docks');
  assert.strictEqual(value(sandbox, 4), '6', 'hour 17 bikes-to-move');

  console.log('  historical tiles recompute per hour -- the §10B regression');
}

async function testHistoricalTilesTrackDayType() {
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);
  const weekday = [1, 2, 3, 4].map(i => value(sandbox, i));

  // Weekend curves at hour 8: A +2, B -1, C +0.5 -- a different slice of the
  // same stations, so every tile must move even though the hour did not.
  dash.setDayType('weekend');
  const weekend = [1, 2, 3, 4].map(i => value(sandbox, i));

  assert.notDeepStrictEqual(weekend, weekday, 'day-type switch left the ribbon unchanged');
  assert.strictEqual(value(sandbox, 1), '4', 'weekend hour 8 bikes-out-of-place (|2|+|1|+|0.5| = 3.5, rounded)');
  assert.strictEqual(value(sandbox, 2), '1', 'weekend hour 8 stations needing bikes');
  assert.strictEqual(value(sandbox, 3), '2', 'weekend hour 8 stations needing docks');

  console.log('  historical tiles recompute per day type');
}

async function testLiveModeSwapsEveryTile() {
  const { dash, sandbox } = await loadDashboard2({ reliability: FAKE_RELIABILITY });
  dash.renderHour(8);
  const historical = [1, 2, 3, 4].map(i => value(sandbox, i));

  dash.setMode('live');
  const live = [1, 2, 3, 4].map(i => value(sandbox, i));
  assert.notDeepStrictEqual(live, historical, 'mode switch left the ribbon unchanged');

  // All four tiles must change. All three fake stations have usable live data. A is empty (0 bikes), B is
  // full (0 docks), C is neither -- but A and B are ~5 km apart, far beyond
  // the 500 m adjacency radius, so NEITHER is in breach.
  assert.strictEqual(value(sandbox, 1), '0', 'no unusable station has an unusable neighbour within 500 m');
  assert.ok(label(sandbox, 1).includes('100.0% meeting'), 'the compliance % survives as the hero caption');

  assert.strictEqual(value(sandbox, 2), '1', 'one station empty');
  assert.strictEqual(value(sandbox, 3), '1', 'one station full');
  // Median fill across A (0/30 = 0%), B (25/25 = 100%), C (10/20 = 50%) is 50%.
  assert.strictEqual(value(sandbox, 4), '50%', 'median fill');

  console.log('  live mode swaps every tile');
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
  assert.strictEqual(value(sandbox, 1), '2', 'hero reports the double-outage count');
  assert.ok(label(sandbox, 1).includes('33.3% meeting'), 'and the compliance % as caption');

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
  // Session 52 moved the observed-rate figure out of the ribbon and into the
  // distribution card, where its historical window sits beside a distribution
  // rather than beside four live counts. Without reliability.json the line is
  // hidden outright -- no em dash, no placeholder rate.
  const { dash, sandbox } = await loadDashboard2();
  dash.setMode('live');

  assert.ok(
    sandbox._elements['distribution-rate'].classList.contains('hidden'),
    'no reliability.json -> the rate line is hidden, not filled with a placeholder'
  );
  // Every ribbon tile is unaffected: one missing optional file must not take
  // the rest of the dashboard down with it.
  assert.strictEqual(value(sandbox, 1), '0', 'ribbon still renders without reliability.json');
  assert.strictEqual(value(sandbox, 4), '50%', 'and so does the last tile');

  console.log('  missing reliability.json hides one line, nothing else');
}

async function testHistoricalRateIsLabelledAsAWindow() {
  // The observed rate is the only non-"now" figure shown in live mode. If it
  // ever loses its window stamp it becomes a live-looking number that is weeks
  // stale -- the single most misleading thing this dashboard could show.
  const { dash, sandbox } = await loadDashboard2({ reliability: FAKE_RELIABILITY });
  dash.setMode('live');

  const rate = sandbox._elements['distribution-rate'];
  assert.ok(!rate.classList.contains('hidden'), 'rate line shows when the data exists');
  assert.ok(rate.innerHTML.includes('25.0%'), 'reports the payload rate');
  assert.ok(rate.innerHTML.includes('Jul 13–Jul 26'), 'window range is on its face');
  assert.ok(/not outage hours/.test(rate.title), 'states it is a frequency, not outage hours');
  assert.ok(!/\$/.test(rate.innerHTML + rate.title),
    'no dollar figure -- the cadence cannot support a penalty claim');

  console.log('  observed-rate line carries its window and refuses the $ claim');
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


async function testDistributionBinsEveryStationTheMapDraws() {
  // The ribbon reports the tails; the distribution is the shape they are tails
  // of. It must cover the same population the map draws, and its buckets must
  // come from the same classifier that colours the markers -- a second,
  // independently-derived binning could drift from the map it describes.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);

  const historical = dash.computeDistribution();
  assert.strictEqual(historical.total, 3, 'all three fixture stations are binned');
  assert.strictEqual(historical.counts.reduce((a, b) => a + b, 0), 3, 'counts sum to the total');
  assert.strictEqual(historical.excluded, 0, 'no station lacks all-period data');

  const key = sandbox._elements['distribution-key'].innerHTML;
  assert.ok(/near balanced/.test(key), 'historical buckets are net-flow buckets');
  assert.strictEqual(sandbox._elements['distribution-total'].textContent, '3 stations');

  console.log('  distribution bins every station, historical mode');
}

async function testDistributionSwitchesQuantityWithMode() {
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);
  const before = sandbox._elements['distribution-title'].textContent;

  dash.setMode('live');
  const after = sandbox._elements['distribution-title'].textContent;
  assert.notStrictEqual(before, after, 'the distributed quantity changes with mode');
  assert.strictEqual(after, 'Dock fill distribution');

  // A empty (0/30), B full (25/25), C mid (10/20) -> one in each end bucket
  // and one in the middle.
  const live = dash.computeDistribution();
  // Spread into a test-realm array first: values built inside the vm sandbox
  // carry that realm's Array prototype, and deepStrictEqual compares
  // prototypes -- it fails on two identical-looking [1,0,1,0,1]s otherwise.
  assert.deepStrictEqual([...live.counts], [1, 0, 1, 0, 1], 'fill buckets: empty / mid / full');

  console.log('  distribution swaps quantity with mode');
}

async function testDistributionStatesWhatItExcluded() {
  // A distribution over an unstated subset is a quiet denominator change.
  const live = JSON.parse(JSON.stringify(FAKE_LIVE));
  live.stations.C.is_renting = 0;

  const { dash, sandbox } = await loadDashboard2({ live });
  dash.setMode('live');

  const result = dash.computeDistribution();
  assert.strictEqual(result.total, 2, 'offline station is out of the distribution');
  assert.strictEqual(result.excluded, 1, 'and counted as excluded rather than dropped');
  assert.ok(
    /1 of 3 excluded/.test(sandbox._elements['distribution-note'].textContent),
    'the exclusion is stated on the card, not left implicit'
  );

  console.log('  distribution states its own exclusions');
}

async function testDistributionEndsAgreeWithTheRibbonTiles() {
  // "full now 220" beside "full 26" actually shipped in a screenshot. The
  // distribution had binned its end buckets by fill PERCENTAGE while the
  // ribbon counted 0 docks -- and capacity is not bikes + docks when docks
  // are out of service, so the two diverge. Same word, two questions.
  // These counts must be the same number or one of them is lying.
  const { dash, sandbox } = await loadDashboard2();
  dash.setMode('live');

  const counts = dash.computeDistribution().counts;
  const fill = dash.computeFillDistribution();

  // The relationship is exact, but it is NOT plain equality on both ends, and
  // assuming it was hid a real case: a station with 0 bikes AND 0 docks (which
  // happens whenever bikes + docks < capacity, i.e. docks out of service) is
  // un-rentable and un-returnable, so both ribbon tiles count it -- while the
  // distribution, which must partition, can only put it in one bucket. Against
  // the live feed that gap was 5 stations: ribbon "full 183", distribution
  // "full 178". Both correct; the invariant just has a term in it.
  assert.strictEqual(String(counts[0]), value(sandbox, 2), 'distribution "empty" == ribbon "empty now"');
  assert.strictEqual(
    String(counts[4] + fill.nBoth), value(sandbox, 3),
    'distribution "full" + both-zero stations == ribbon "full now"'
  );
  assert.strictEqual(
    counts.reduce((a, b) => a + b, 0), fill.total,
    'buckets still partition the usable set exactly once'
  );

  console.log('  distribution ends agree with the ribbon tiles');
}

async function testLegendKeepsTheSaturationFactOnHover() {
  // The scale ends are the 95th percentile of |value|, not the data's range:
  // the real max is 86.2, and at 8am weekday ~24% of stations sit past +/-2.7,
  // all painted the same saturated colour. The visible key is deliberately
  // plain (bare numbers, no inequality signs, no explanatory caption), so the
  // qualifier lives in the tooltip -- available, not clutter. What must never
  // happen is the fact disappearing entirely.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);

  assert.strictEqual(sandbox._elements['legend-tick-low'].textContent[0], '-', 'plain negative number, no <=');
  assert.strictEqual(sandbox._elements['legend-tick-high'].textContent[0], '+', 'plain positive number, no >=');
  assert.ok(!/\u2264|\u2265/.test(
    sandbox._elements['legend-tick-low'].textContent + sandbox._elements['legend-tick-high'].textContent
  ), 'no inequality signs on the key');

  const tip = sandbox._elements['legend-card'].title;
  assert.ok(/95th percentile/.test(tip), 'tooltip says what the ends actually are');
  assert.ok(/saturates/.test(tip), 'and that colour saturates past them');

  console.log('  legend key is plain; the saturation fact survives on hover');
}

async function testLegendCaptionIsNotOverwrittenByRenderHour() {
  // renderHour() used to write #legend-caption immediately AFTER calling
  // renderLegendScale(), so the computed note was clobbered on every render
  // and never appeared on screen. Ordering bug, invisible to any test that
  // only called renderLegendScale() directly.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);
  assert.strictEqual(
    sandbox._elements['legend-caption'].textContent,
    'Click a station for its daily flow',
    'historical caption comes from the single owner, not from a competing write in renderHour()'
  );

  dash.setMode('live');
  assert.strictEqual(
    sandbox._elements['legend-caption'].textContent,
    'Click a station for its live status',
    'live mode gets its own caption from the same single owner'
  );

  console.log('  legend caption has one owner, and it survives renderHour');
}

async function testHistoricalSharesUseTheCoverageDenominator() {
  // Percentages belong on the two STATION-count tiles and nowhere else: tiles
  // 1 and 4 count bikes, which have no station total to divide by.
  // The denominator is stations with a curve for THIS period, not all of them
  // -- period coverage varies per station, so a fixed total would overstate it.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);

  // All 3 fixture stations have all-period data: 1 needs bikes, 2 need docks.
  assert.ok(label(sandbox, 2).includes('33.3%'), '1 of 3 stations needs bikes');
  assert.ok(label(sandbox, 3).includes('66.7%'), '2 of 3 stations need docks');
  assert.ok(!/%/.test(label(sandbox, 1)), 'no share on the bikes-out-of-place tile');
  assert.ok(!/%/.test(label(sandbox, 4)), 'no share on the bikes-to-move tile');

  assert.strictEqual(dash.computeSliceTotals().withData, 3, 'denominator is the covered set');

  console.log('  historical shares use the coverage denominator, bikes tiles stay counts');
}

(async () => {
  await testHistoricalTilesTrackTheHourSlider();
  await testHistoricalTilesTrackDayType();
  await testHistoricalSharesUseTheCoverageDenominator();
  await testLiveModeSwapsEveryTile();
  await testDistributionBinsEveryStationTheMapDraws();
  await testDistributionSwitchesQuantityWithMode();
  await testDistributionStatesWhatItExcluded();
  await testDistributionEndsAgreeWithTheRibbonTiles();
  await testLegendKeepsTheSaturationFactOnHover();
  await testLegendCaptionIsNotOverwrittenByRenderHour();
  await testAdjacencyRuleActuallyBites();
  await testOfflineStationsLeaveBothSides();
  await testMissingReliabilityDegradesGracefully();
  await testHistoricalRateIsLabelledAsAWindow();
  testTheOldReadoutIsGoneFromTheMarkup();
  await testRibbonTilesAreNotHardcodedInMarkup();
  console.log('dashboard2 ribbon test passed (Session 50: mode-aware, slice-aware metric ribbon).');
})();

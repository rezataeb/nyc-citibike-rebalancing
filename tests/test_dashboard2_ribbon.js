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
  // At hour 8 that is |12|+|9|+|1| = 22 out of place, 9 bikes to move.
  // Tiles 2/3 are computeDistribution()'s big-deficit/big-surplus counts
  // (Session 65) -- with only 3 stations spread across the domain, B's -9
  // lands in "big deficit" and A/C's positive values both land in "big
  // surplus" (verified against the real computeDistribution() output, not
  // assumed from the raw values).
  dash.renderHour(8);
  assert.strictEqual(value(sandbox, 1), '22', 'hour 8 bikes-out-of-place');
  assert.strictEqual(value(sandbox, 2), '1', 'hour 8 big-deficit stations');
  assert.strictEqual(value(sandbox, 3), '2', 'hour 8 big-surplus stations');
  assert.strictEqual(value(sandbox, 4), '9', 'hour 8 bikes-to-move');

  // At hour 17: A -6, B +4, C 0 -- C's exact zero falls in "near balanced",
  // counted in neither tile.
  dash.renderHour(17);
  assert.strictEqual(value(sandbox, 1), '10', 'hour 17 bikes-out-of-place');
  assert.strictEqual(value(sandbox, 2), '1', 'hour 17 big-deficit stations');
  assert.strictEqual(value(sandbox, 3), '1', 'hour 17 big-surplus stations');
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
  assert.strictEqual(value(sandbox, 2), '1', 'weekend hour 8 big-deficit stations');
  assert.strictEqual(value(sandbox, 3), '2', 'weekend hour 8 big-surplus stations');

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
  assert.strictEqual(value(sandbox, 1), '100.0%', 'service availability: nothing is in double-outage');
  assert.strictEqual(value(sandbox, 2), '1', 'one station empty');
  assert.strictEqual(value(sandbox, 3), '1', 'one station full');
  assert.strictEqual(value(sandbox, 4), '0', 'no unusable station has an unusable neighbour within 500 m');

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
  assert.strictEqual(value(sandbox, 4), '2', 'double-outage tile reports the count');
  assert.strictEqual(value(sandbox, 1), '33.3%', 'and compliance falls to one of three');

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
  assert.strictEqual(value(sandbox, 1), '100.0%', 'ribbon still renders without reliability.json');
  assert.strictEqual(value(sandbox, 4), '0', 'and so does the last tile');

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
  // Session 69: the "frequency, not outage hours" detail moved off
  // #distribution-rate's hover-only title (a DOT reviewer skimming a
  // printout never sees a tooltip) into the visible-on-expand Data notes
  // body, alongside the rest of live mode's methodology.
  const notesBody = sandbox._elements['distribution-notes-body'];
  assert.ok(/not outage hours/.test(notesBody.textContent), 'states it is a frequency, not outage hours');
  assert.ok(!/\$/.test(rate.innerHTML + notesBody.textContent),
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
  // Session 69: live mode's exclusion accounting is in the collapsed Data
  // notes body now, not the always-visible #distribution-note (which this
  // mode empties -- see renderDistribution()).
  assert.ok(
    /1 of 3 excluded/.test(sandbox._elements['distribution-notes-body'].textContent),
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
  // Session 67: the visible label dropped its "X.X%" span entirely --
  // user-reported that a bare percentage next to a label read like a
  // database column, not a sentence. The exact share still lives in the
  // tile's tooltip (title attribute), which is what this test now checks;
  // the coverage-denominator invariant it originally existed to pin
  // (period coverage varies per station, so the denominator must be
  // "stations with a curve for THIS period", not a fixed 2,516) is still
  // real and still worth asserting, just against the tooltip now.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);

  // All 3 fixture stations have all-period data: 1 in big deficit, 2 in big
  // surplus (see computeDistribution() -- same bucketing the map itself uses).
  assert.ok(tooltip(sandbox, 2).includes('33.3%'), '1 of 3 stations is big-deficit, stated in the tooltip');
  assert.ok(tooltip(sandbox, 3).includes('66.7%'), '2 of 3 stations are big-surplus, stated in the tooltip');
  assert.ok(!/%/.test(label(sandbox, 1)), 'no share on the bikes-out-of-place tile');
  assert.ok(!/%/.test(label(sandbox, 4)), 'no share on the bikes-to-move tile');
  assert.ok(!/%/.test(label(sandbox, 2)), 'tile 2\'s visible label is plain English, not a database column');
  assert.ok(!/%/.test(label(sandbox, 3)), 'tile 3\'s visible label is plain English, not a database column');
  assert.strictEqual(label(sandbox, 2), 'stations heading toward empty', 'tile 2 reads as a plain sentence with the number above it');
  assert.strictEqual(label(sandbox, 3), 'stations heading toward full', 'tile 3 reads as a plain sentence with the number above it');

  assert.strictEqual(dash.computeSliceTotals().withData, 3, 'denominator is the covered set');

  console.log('  historical shares use the coverage denominator, bikes tiles stay counts');
}

async function testStatusLivesInADotNotInColouredNumerals() {
  // The session rule: colour identifies status in a mark beside the text,
  // never in the colour of the text itself. A coloured numeral is invisible
  // to anyone who cannot separate the hues, and it makes a measurement look
  // like a status word.
  const { dash, sandbox } = await loadDashboard2();
  dash.setMode('live');

  const dot = i => sandbox._elements[`ribbon-dot-${i}`];
  for (const i of [1, 2, 3, 4]) {
    assert.ok(dot(i).classList.contains('shown'), `tile ${i} shows a status dot in live mode`);
  }
  // Red for "cannot rent", blue for "cannot return" -- the same two colours
  // the map uses, so the strip and the dots on the map agree.
  assert.strictEqual(dot(2).style.background, 'rgba(227, 73, 72, 1)', 'empty = deficit red');
  assert.strictEqual(dot(3).style.background, 'rgba(42, 120, 214, 1)', 'full = surplus blue');
  assert.strictEqual(dot(1).style.background, dot(4).style.background,
    'compliance and double-outage share a dark dot -- the dot marks status, not identity');

  // The numbers themselves carry no colour at all.
  for (const i of [1, 2, 3, 4]) {
    assert.ok(!sandbox._elements[`ribbon-value-${i}`].style.color,
      `tile ${i}'s numeral must not be coloured`);
  }

  console.log('  status lives in a dot; numerals stay uncoloured');
}

async function testHistoricalDotsMarkOnlyTheSevereTiles() {
  // Session 65: tiles 2/3 changed from "stations need bikes/docks" (every
  // draining/filling station) to "big deficit/big surplus" (the map's own
  // most-severe band) -- a real severity cut, so those two now carry a
  // status dot too, same red/blue the map and live mode already use.
  // Tiles 1/4 are still plain bike counts with no natural severity band,
  // and stay dot-less.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);

  const dot = i => sandbox._elements[`ribbon-dot-${i}`];
  assert.ok(!dot(1).classList.contains('shown'), 'tile 1 (bikes out of place) shows no dot');
  assert.ok(dot(2).classList.contains('shown'), 'tile 2 (big deficit stations) shows a dot');
  assert.ok(dot(3).classList.contains('shown'), 'tile 3 (big surplus stations) shows a dot');
  assert.ok(!dot(4).classList.contains('shown'), 'tile 4 (bikes to move) shows no dot');
  assert.strictEqual(dot(2).style.background, 'rgba(227, 73, 72, 1)', 'big deficit = deficit red');
  assert.strictEqual(dot(3).style.background, 'rgba(42, 120, 214, 1)', 'big surplus = surplus blue');

  // Numerals still carry no colour, same rule as every other tile.
  for (const i of [1, 2, 3, 4]) {
    assert.ok(!sandbox._elements[`ribbon-value-${i}`].style.color,
      `tile ${i}'s numeral must not be coloured`);
  }
  console.log('  historical dots mark only the severe tiles (2/3), not the plain bike counts (1/4)');
}

async function testHoverCardIsModeAwareAndNeverInventsOccupancy() {
  // Live mode has real occupancy from GBFS; historical mode does NOT --
  // flows.json carries no capacity and no dock counts, only net flow. The card
  // must report what each mode actually knows rather than dressing a
  // climatology figure up as a dock reading.
  const { dash } = await loadDashboard2();

  dash.renderHour(8);
  const hist = dash.stationHoverHtml('A');
  assert.ok(hist.includes('Station A'), 'names the station');
  assert.ok(/bikes\/day/.test(hist), 'historical reports net flow, its real quantity');
  assert.ok(!/% full|you can take|free to return/.test(hist),
    'and never claims a fill % or dock count it cannot know');

  dash.setMode('live');
  const live = dash.stationHoverHtml('A');
  // FAKE_LIVE station A: 0 bikes, 30 open docks, capacity 30.
  assert.ok(live.includes('Station A'), 'names the station');
  assert.ok(live.includes('0% full'), 'fill percentage');
  assert.ok(live.includes('0 of 30 docks have a bike you can take'), 'the two numbers that PRODUCE the percentage');
  assert.ok(live.includes('30 docks free to return to'), 'free docks stated separately');

  console.log('  hover card is mode-aware and invents no occupancy');
}

async function testHoverPercentageIsExplainedByTheNumbersBesideIt() {
  // The first version read "7 bikes / 5 docks - 58% full - 12 docks total",
  // inviting the reader to check 7 + 5 = 12. Against the real feed that holds
  // for only 9.5% of stations -- capacity is nominal, while bikes and open
  // docks exclude disabled bikes and out-of-service docks. Station B is built
  // so the two would disagree: 25 bikes, 0 open docks, capacity 25 would sum
  // correctly, so it is given a deliberately inconsistent reading here.
  const live = JSON.parse(JSON.stringify(FAKE_LIVE));
  live.stations.B = { capacity: 40, bikes_available: 10, docks_available: 12, is_renting: 1, is_returning: 1 };

  const { dash } = await loadDashboard2({ live });
  dash.setMode('live');
  const html = dash.stationHoverHtml('B');

  // 10 / 40 = 25%. The percentage line names 10 and 40 and nothing else, so
  // the arithmetic a reader can check is arithmetic that is actually true.
  assert.ok(html.includes('25% full'), 'percentage');
  assert.ok(html.includes('10 of 40 docks have a bike you can take'), 'and its own numerator and denominator');
  // 10 + 12 != 40, so those two must never appear in one line implying a sum.
  assert.ok(!/10 bikes \/ 12 docks/.test(html), 'no implied bikes + docks = capacity');

  console.log('  hover percentage is explained by the numbers printed beside it');
}

async function testHoverCardEscapesStationNames() {
  // Real station names contain ampersands ("W 43 St & 10 Ave"). The card is
  // HTML now, so an unescaped name would inject markup into the tooltip.
  const flows = JSON.parse(JSON.stringify(FAKE_FLOWS));
  flows.stations.A.name = 'W 43 St & 10 Ave <script>';
  const { dash } = await loadDashboard2({ flows });
  const html = dash.stationHoverHtml('A');

  assert.ok(html.includes('W 43 St &amp; 10 Ave'), 'ampersand escaped');
  assert.ok(html.includes('&lt;script&gt;'), 'angle brackets escaped');
  assert.ok(!/<script>/.test(html), 'no raw markup from station data');

  console.log('  hover card escapes station names');
}

async function testHoverCardDegradesForStationsWithoutData() {
  const live = JSON.parse(JSON.stringify(FAKE_LIVE));
  delete live.stations.C;
  const { dash } = await loadDashboard2({ live });
  dash.setMode('live');

  const html = dash.stationHoverHtml('C');
  assert.ok(/No live data/.test(html), 'says there is no reading rather than computing one');
  assert.ok(!/NaN|undefined/.test(html), 'and never leaks NaN/undefined into the UI');

  console.log('  hover card degrades cleanly where there is no reading');
}


async function testHoverSaysRENTABLEBikesNotJustBikes() {
  // "N of M docks hold a bike" was false, not merely loose. Station A here has
  // 4 rentable bikes in a 12-dock station -- but seven more docks hold BROKEN
  // bikes (the real Shore Rd & 4 Ave shape). Eleven of twelve docks hold a
  // bike; only four hold one anyone can rent, and num_bikes_available counts
  // the latter. The label has to say which.
  const live = JSON.parse(JSON.stringify(FAKE_LIVE));
  live.stations.A = { capacity: 12, bikes_available: 4, docks_available: 1, is_renting: 1, is_returning: 1 };

  const { dash } = await loadDashboard2({ live });
  dash.setMode('live');
  const html = dash.stationHoverHtml('A');

  assert.ok(html.includes('4 of 12 docks have a bike you can take'), 'says rentable, not merely present');
  assert.ok(!/docks hold a bike/.test(html), 'the false phrasing must not come back');
  assert.ok(html.includes('33% full'), '4/12 -- the denominator is capacity, stated as such');
  assert.ok(html.includes('1 dock free to return to'), 'singular for one, and free means empty AND working');

  console.log('  hover says rentable bikes, not merely bikes present');
}

(async () => {
  await testHistoricalTilesTrackTheHourSlider();
  await testHistoricalTilesTrackDayType();
  await testHistoricalSharesUseTheCoverageDenominator();
  await testLiveModeSwapsEveryTile();
  await testStatusLivesInADotNotInColouredNumerals();
  await testHistoricalDotsMarkOnlyTheSevereTiles();
  await testHoverCardIsModeAwareAndNeverInventsOccupancy();
  await testHoverPercentageIsExplainedByTheNumbersBesideIt();
  await testHoverSaysRENTABLEBikesNotJustBikes();
  await testHoverCardEscapesStationNames();
  await testHoverCardDegradesForStationsWithoutData();
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

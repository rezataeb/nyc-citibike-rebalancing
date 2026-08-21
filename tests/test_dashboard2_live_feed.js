// Node test for dashboard2.html's real GBFS live feed (Session 54).
//
// "Live Docks" used to mean one frozen export of data/live_status.json, weeks
// old by the time anyone opened the page. The dashboard now reads Citi Bike's
// GBFS endpoints directly (they serve access-control-allow-origin: * and
// declare ttl: 60), keeping the snapshot as a labelled fallback.
//
// The thing this file most exists to protect is the LABEL. A stale number that
// says "live" is worse than no live mode at all, so every test here checks not
// just that a figure appeared but that the dashboard said where it came from.
//
// Run with:
//   node tests/test_dashboard2_live_feed.js

const assert = require('assert');
const { loadDashboard2, FAKE_LIVE_FEED, FAKE_LIVE, FAKE_FLOWS, makeCurve, makeContext } = require('./dashboard_harness');

const text = (sandbox, id) => sandbox._elements[id].textContent;

async function testCrosswalkMapsGbfsIdsToProjectStationIds() {
  // station_status carries GBFS's own station_id; only station_information has
  // short_name, which is the numbering flows.json uses. Getting this wrong
  // yields an empty live map with no error anywhere.
  const { dash } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });
  const state = dash.getState();

  assert.deepStrictEqual(Object.keys(state.liveStations).sort(), ['A', 'B', 'C', 'E'],
    'stations are keyed by short_name, not by GBFS station_id');
  assert.strictEqual(state.liveStations.A.capacity, 30, 'capacity comes from station_information');
  assert.strictEqual(state.liveStations.A.bikes_available, 5, 'counts come from station_status');

  console.log('  crosswalk maps GBFS ids to project station ids');
}

async function testStationsMissingFromInformationAreDroppedNotGuessed() {
  // g-d is in status but has no information entry, so there is no short_name
  // to join on. It must be dropped and counted, never invented.
  const { dash } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });
  const payload = dash.buildLiveFeedPayload(FAKE_LIVE_FEED.status, FAKE_LIVE_FEED.information);

  assert.strictEqual(payload.n_dropped, 1, 'the unmatched station is counted');
  assert.strictEqual(Object.keys(payload.stations).length, 4, 'and not carried into the payload');

  console.log('  unmatched stations are dropped and counted, not guessed');
}

async function testFeedIsPreferredAndLabelledAsLive() {
  const { dash, sandbox } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });
  dash.setMode('live');

  assert.strictEqual(dash.getState().liveSource, 'feed', 'the feed wins when it is reachable');
  const stamp = text(sandbox, 'live-as-of-timestamp');
  assert.ok(/^Live as of/.test(stamp), 'labelled as live');
  assert.ok(/refreshing every 60s/.test(stamp), 'and says how often it refreshes');
  assert.ok(!/Snapshot/.test(stamp), 'not labelled as a snapshot');

  console.log('  feed is preferred and labelled as live');
}

async function testSnapshotFallbackIsLabelledAsASnapshot() {
  // The harness 404s the GBFS urls by default, which is exactly the outage
  // case: a third-party service this project does not control going away.
  // The dashboard must still work AND must stop claiming to be live.
  const { dash, sandbox } = await loadDashboard2();
  dash.setMode('live');

  assert.strictEqual(dash.getState().liveSource, 'snapshot', 'falls back to data/live_status.json');
  assert.strictEqual(dash.getState().liveStations.A.bikes_available, FAKE_LIVE.stations.A.bikes_available,
    'the snapshot really is what got loaded');

  const stamp = text(sandbox, 'live-as-of-timestamp');
  assert.ok(/^Snapshot from/.test(stamp), 'labelled as a snapshot, not as live');
  assert.ok(/unavailable/.test(stamp), 'and says why');
  assert.ok(!/^Live as of/.test(stamp), 'must never call a stale reading live');

  console.log('  snapshot fallback is labelled as a snapshot');
}

async function testRefreshInvalidatesTheNearestNeighbourCache() {
  // computeLiveStandard() memoises a ~2,400-station nearest-neighbour pass.
  // Its own comment justified that by the snapshot being immutable -- which
  // polling makes false. A stale cache would freeze the hero tile at load-time
  // values while every other number moved, which is worse than no refresh.
  const { dash, sandbox } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });
  dash.setMode('live');

  // Feed state: A 5/30 ok, B empty, C full. B and C are ~5 km apart, so
  // neither is in breach.
  assert.strictEqual(dash.computeLiveStandard().inBreach, 0);
  assert.strictEqual(text(sandbox, 'ribbon-value-2'), '1', 'one empty at load');

  // A new reading in which A has also gone empty.
  dash.applyLiveFeedPayload({
    last_updated: '2026-08-21T04:00:00+00:00',
    stations: {
      A: { capacity: 30, bikes_available: 0, docks_available: 30, is_renting: 1, is_returning: 1 },
      B: { capacity: 25, bikes_available: 0, docks_available: 25, is_renting: 1, is_returning: 1 },
      C: { capacity: 20, bikes_available: 20, docks_available: 0, is_renting: 1, is_returning: 1 },
    },
    source: 'feed',
  });

  assert.strictEqual(text(sandbox, 'ribbon-value-2'), '2', 'the ribbon followed the new reading');
  assert.strictEqual(dash.computeLiveStandard().nUnusable, 3, 'and so did the memoised standard');

  console.log('  refresh invalidates the nearest-neighbour cache');
}

async function testPollingOnlyRunsWhileLiveModeIsShowing() {
  // Polling a third-party service to update numbers nobody is looking at is
  // just someone else's bandwidth.
  const { dash, sandbox } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });

  // The harness's setInterval stub returns 0 and never fires, so this asserts
  // the wiring by observing refreshLiveFeed()'s own mode guard directly.
  const before = sandbox._fetchedUrls.filter(u => u.includes('station_status')).length;
  dash.setMode('historical');
  dash.refreshLiveFeed();
  assert.strictEqual(
    sandbox._fetchedUrls.filter(u => u.includes('station_status')).length, before,
    'a refresh in historical mode must not hit the network'
  );

  dash.setMode('live');
  dash.refreshLiveFeed();
  assert.ok(
    sandbox._fetchedUrls.filter(u => u.includes('station_status')).length > before,
    'a refresh in live mode does'
  );

  console.log('  polling only runs while live mode is showing');
}

async function testTimestampIsFormattedFromTheFeedsEpoch() {
  // GBFS reports last_updated as a unix epoch; live_status.json carries an ISO
  // string. Both must reach formatAsOf() in the same shape or the "as of" line
  // prints an epoch integer at the reader.
  const { dash } = await loadDashboard2({ liveFeed: FAKE_LIVE_FEED });
  const payload = dash.buildLiveFeedPayload(FAKE_LIVE_FEED.status, FAKE_LIVE_FEED.information);

  assert.ok(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$/.test(payload.last_updated),
    `epoch should become an ISO string, got ${payload.last_updated}`);
  assert.ok(!/UTC undefined|NaN/.test(dash.formatAsOf(payload.last_updated)), 'and format cleanly');

  console.log('  feed epoch becomes the same ISO shape the snapshot uses');
}

async function testBothZeroStationIsCountedInBothTilesButBinnedOnce() {
  // Station E has 0 bikes and 0 docks. It is un-rentable AND un-returnable, so
  // both ribbon tiles must count it -- but the distribution partitions, so it
  // lands in exactly one bucket. This case only exists in the real feed (5
  // stations at the time of writing) and made the two surfaces read as
  // contradictory: "full 183" beside "full 178".
  // Station E needs a flows.json entry to be visible at all: every live figure
  // iterates the flows station set, so a feed-only station is invisible by
  // design (it has no history, no coordinates, no equity context). Given its
  // own fixture here rather than added to FAKE_FLOWS, which every other test
  // counts on being exactly three stations.
  const flows = JSON.parse(JSON.stringify(FAKE_FLOWS));
  flows.stations.E = {
    name: 'Station E', lat: 40.70, lng: -73.90,
    weekday: makeCurve({ 8: 1 }), weekend: makeCurve({ 8: 1 }),
    seasons: {}, months: {}, cluster: 1, cluster_name: 'Residential feeder (drains AM, fills PM)',
    context: makeContext(),
  };

  const { dash, sandbox } = await loadDashboard2({ flows, liveFeed: FAKE_LIVE_FEED });
  dash.setMode('live');

  const fill = dash.computeFillDistribution();
  assert.strictEqual(fill.nBoth, 1, 'the both-zero station is identified');
  assert.strictEqual(String(fill.nEmpty), text(sandbox, 'ribbon-value-2'), 'counted as empty');
  assert.strictEqual(String(fill.nFull), text(sandbox, 'ribbon-value-3'), 'and as full');

  const counts = dash.computeDistribution().counts;
  assert.strictEqual(counts.reduce((a, b) => a + b, 0), fill.total, 'buckets partition exactly once');
  assert.ok(
    /0 bikes and 0 docks at once/.test(text(sandbox, 'distribution-note')),
    'and the card explains the overlap rather than leaving two numbers to clash'
  );

  console.log('  both-zero station: counted twice, binned once, explained');
}

(async () => {
  await testCrosswalkMapsGbfsIdsToProjectStationIds();
  await testStationsMissingFromInformationAreDroppedNotGuessed();
  await testFeedIsPreferredAndLabelledAsLive();
  await testSnapshotFallbackIsLabelledAsASnapshot();
  await testRefreshInvalidatesTheNearestNeighbourCache();
  await testPollingOnlyRunsWhileLiveModeIsShowing();
  await testTimestampIsFormattedFromTheFeedsEpoch();
  await testBothZeroStationIsCountedInBothTilesButBinnedOnce();
  console.log('dashboard2 live feed test passed (Session 54: real GBFS, labelled fallback).');
})();

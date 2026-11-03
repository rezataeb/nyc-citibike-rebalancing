// Node test for the Ask panel's ready-made, time-aware questions.
//
// What this file guards: the questions follow New York time (not the
// viewer's machine time zone), read the same calendar month and day type
// from the data year, only appear when data backs them (the "right now"
// question needs the live feed, not the saved snapshot), and say in their
// answer exactly which month, day type and hour they used.
//
// Run with:
//   node tests/test_dashboard2_ask_timed.js
//
// Sandbox lives in tests/dashboard_harness.js.

const assert = require('assert');
const { loadDashboard2, FAKE_FLOWS, FAKE_LIVE_FEED, makeCurve } = require('./dashboard_harness');

const el = (sandbox, id) => sandbox._elements[id];
const hidden = (sandbox, id) => el(sandbox, id)._classes.has('hidden');
const lastChild = node => node._children[node._children.length - 1];
const listed = sandbox => lastChild(el(sandbox, 'ask-body'))._children.map(li => li._children[0]._children.map(c => c.textContent));

// Two data-year months; November weekday 9 AM: A fills most, B drains most.
const months = { '2025-11': { weekday: makeCurve({ 9: 4.2, 17: -1 }), weekend: makeCurve({ 9: 0.5 }) } };
const monthsB = { '2025-11': { weekday: makeCurve({ 9: -3.1 }), weekend: makeCurve({ 9: -0.2 }) } };
const FLOWS = {
  ...FAKE_FLOWS,
  granularity: { months: ['2025-11', '2026-04'], seasons: ['fall', 'spring'] },
  stations: {
    A: { ...FAKE_FLOWS.stations.A, months },
    B: { ...FAKE_FLOWS.stations.B, months: monthsB },
    C: { ...FAKE_FLOWS.stations.C, months: {} },
  },
};
// Tue Nov 10, 2026, 8:30 AM New York (13:30 UTC) -> the next hour is 9-10 AM.
const TUE_0830_NYC = Date.UTC(2026, 10, 10, 13, 30);

async function load(options = {}) {
  const loaded = await loadDashboard2({ flows: FLOWS, ...options });
  await loaded.dash.ready;
  loaded.dash.setAskNow(TUE_0830_NYC); // the minute timer would relabel with this clock; call it like the timer does
  loaded.dash.updateTimedChips();
  return loaded;
}

async function testQuestionsAppearOnLoadWithoutTheTimer() {
  const { dash, sandbox } = await loadDashboard2({ flows: FLOWS });
  await dash.ready;
  // No manual refresh here: loading the data must show them (real clock; this fixture's data year has Nov and Apr only,
  // so assert on the visibility rule rather than a fixed month).
  const ctx = dash.nextHourContext();
  assert.strictEqual(!hidden(sandbox, 'ask-chip-next-drain'), Boolean(ctx.monthKey));
  assert.ok(el(sandbox, 'ask-chip-next-drain').textContent === (ctx.monthKey ? 'Where do bikes run out next hour?' : ''));
}

function testClockIsNewYorkTime() {
  return load().then(({ dash }) => {
    const c = dash.nycClock(new Date(TUE_0830_NYC));
    assert.deepStrictEqual({ ...c }, { hour: 8, dayType: 'weekday', month: 11 });
    // Sat Nov 14, 2026 01:30 UTC is still Friday 8:30 PM in New York.
    assert.strictEqual(dash.nycClock(new Date(Date.UTC(2026, 10, 14, 1, 30))).dayType, 'weekday');
    assert.strictEqual(dash.hourRangeLabel(9), '9–10 AM');
    assert.strictEqual(dash.hourRangeLabel(11), '11 AM–12 PM');
    assert.strictEqual(dash.hourRangeLabel(23), '11 PM–12 AM');
  });
}

async function testSameCalendarMonthFromTheDataYear() {
  const { dash } = await load();
  assert.strictEqual(dash.monthKeyFor(11, ['2025-11', '2026-04']), '2025-11', 'November 2026 reads November 2025');
  assert.strictEqual(dash.monthKeyFor(6, ['2025-11', '2026-04']), null, 'no June in the data -> no answer');
}

async function testNextHourQuestionsRankTheRightWay() {
  const { dash, sandbox } = await load();
  assert.ok(!hidden(sandbox, 'ask-chip-next-drain') && !hidden(sandbox, 'ask-chip-next-fill'));
  dash.renderAskTimed('next-drain');
  assert.strictEqual(el(sandbox, 'ask-question').textContent, 'Usually lose the most bikes 9–10 AM on November weekdays');
  assert.deepStrictEqual(listed(sandbox), [['Station B', '−3.1']], 'only stations that lose bikes in that hour');
  const source = el(sandbox, 'ask-source').textContent;
  assert.ok(source.includes('November 2025') && source.includes('not a live reading'), source);
  dash.renderAskTimed('next-fill');
  assert.deepStrictEqual(listed(sandbox), [['Station A', '+4.2']]);
}

async function testHiddenWhenTheMonthIsNotInTheData() {
  const { dash, sandbox } = await load();
  dash.setAskNow(Date.UTC(2026, 5, 9, 13, 30)); // June: not in this fixture's data year
  dash.updateTimedChips();
  assert.ok(hidden(sandbox, 'ask-chip-next-drain') && hidden(sandbox, 'ask-chip-next-fill'), 'no data-backed answer, no question');
}

async function testLiveQuestionNeedsTheLiveFeed() {
  const snapshot = await load(); // the feed 404s -> saved snapshot, which is not "right now"
  assert.ok(hidden(snapshot.sandbox, 'ask-chip-live-empty'));

  const { dash, sandbox } = await load({ liveFeed: FAKE_LIVE_FEED });
  assert.ok(!hidden(sandbox, 'ask-chip-live-empty'));
  dash.renderAskTimed('live-empty');
  assert.strictEqual(el(sandbox, 'ask-question').textContent, '1 station has no bikes right now');
  assert.deepStrictEqual(listed(sandbox), [['Station B']]);
  assert.ok(el(sandbox, 'ask-source').textContent.startsWith('Live feed'));
}


(async () => {
  await testClockIsNewYorkTime();
  await testSameCalendarMonthFromTheDataYear();
  await testNextHourQuestionsRankTheRightWay();
  await testHiddenWhenTheMonthIsNotInTheData();
  await testLiveQuestionNeedsTheLiveFeed();
  await testQuestionsAppearOnLoadWithoutTheTimer();
  console.log('ask timed questions test passed (New York clock, data-year month, ranking, data-backed visibility, live feed).');
})().catch(error => { console.error(error); process.exit(1); });

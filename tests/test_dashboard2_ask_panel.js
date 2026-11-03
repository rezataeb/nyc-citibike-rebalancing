// Node test for the Ask panel (AI version, Feature 1).
//
// The panel answers station questions with the precomputed AI briefings in
// data/briefings.json and three list questions computed from
// reliability.json. What this file guards: default state set in JS (the
// CLAUDE.md standing rule), a map click and a typed question landing in the
// same answer, honest messages when a file is missing or a station has no
// briefing, and lists that only contain stations matching the question.
//
// Run with:
//   node tests/test_dashboard2_ask_panel.js
//
// Sandbox lives in tests/dashboard_harness.js.

const assert = require('assert');
const { loadDashboard2, FAKE_RELIABILITY } = require('./dashboard_harness');

// A and C have briefings; B stands in for an excluded (New Jersey) station.
const FAKE_BRIEFINGS = {
  meta: { model: 'claude-haiku-5-5', generated: '2026-11-02', trip_window: 'Sep 2025 – Aug 2026',
          reliability_window: 'Jul 13 – Oct 27, 2026', n: 2, sources: { model: 1, model_retry: 0, template: 1 } },
  stations: {
    A: { text: 'Station A is a commuter core station.', source: 'model', facts_hash: 'a' },
    C: { text: 'Station C has too few trips to show a clear weekday pattern.', source: 'template', facts_hash: 'c' },
  },
};

const el = (sandbox, id) => sandbox._elements[id];
const hidden = (sandbox, id) => el(sandbox, id)._classes.has('hidden');
const lastChild = node => node._children[node._children.length - 1];
// Station names in the list currently shown in the answer body.
const listedNames = sandbox => lastChild(el(sandbox, 'ask-body'))._children.map(li => li._children[0]._children[0].textContent);

async function load(options = {}) {
  const loaded = await loadDashboard2({ briefings: FAKE_BRIEFINGS, reliability: FAKE_RELIABILITY, ...options });
  await loaded.dash.briefingsReady;
  return loaded;
}

async function testDefaultStateIsSetInJs() {
  const { sandbox } = await load();
  assert.ok(hidden(sandbox, 'ask-answer'), 'no answer before a question');
  assert.ok(hidden(sandbox, 'ask-suggestions'), 'no suggestion list before typing');
  assert.ok(hidden(sandbox, 'ask-back'), 'no back link before a list');
  assert.strictEqual(el(sandbox, 'ask-input').value, '', 'input cleared in JS');
}

async function testMapClickShowsTheStationsBriefing() {
  const { dash, sandbox } = await load();
  dash.selectStation('A');
  assert.ok(!hidden(sandbox, 'ask-answer'));
  assert.strictEqual(el(sandbox, 'ask-question').textContent, 'About Station A');
  assert.strictEqual(el(sandbox, 'ask-body').textContent, FAKE_BRIEFINGS.stations.A.text);
  const source = el(sandbox, 'ask-source').textContent;
  assert.ok(source.includes('AI-written') && source.includes('Sep 2025 – Aug 2026') && source.includes('Jul 13 – Oct 27, 2026'), source);
}

async function testTemplateBriefingIsLabelledAsTemplate() {
  const { dash, sandbox } = await load();
  dash.selectStation('C');
  assert.ok(el(sandbox, 'ask-source').textContent.startsWith('Written by a fixed template'));
}

async function testStationWithoutBriefingSaysSo() {
  const { dash, sandbox } = await load();
  dash.selectStation('B');
  assert.ok(el(sandbox, 'ask-body').textContent.startsWith('There is no briefing for Station B'));
  assert.strictEqual(el(sandbox, 'ask-source').textContent, '');
}

async function testMissingBriefingsFileSaysSo() {
  const { dash, sandbox } = await load({ briefings: null });
  dash.selectStation('A');
  assert.ok(el(sandbox, 'ask-body').textContent.startsWith('Station briefings did not load'));
}

async function testTypedQuestionFindsTheStation() {
  const { dash, sandbox } = await load();
  assert.strictEqual(dash.askSubmit('Tell me about station a?'), 'station');
  assert.strictEqual(dash.getState().selectedId, 'A');
  assert.strictEqual(el(sandbox, 'ask-body').textContent, FAKE_BRIEFINGS.stations.A.text);
  assert.strictEqual(el(sandbox, 'ask-input').value, '', 'input cleared after an answer');
}

async function testAmbiguousNameOffersAChoice() {
  const { dash, sandbox } = await load();
  assert.strictEqual(dash.askSubmit('Station'), 'choose');
  assert.deepStrictEqual(listedNames(sandbox), ['Station A', 'Station B', 'Station C']);
}

async function testListsOnlyHoldMatchingBriefedStations() {
  const { dash, sandbox } = await load();
  dash.renderAskList('unusable');
  assert.deepStrictEqual(listedNames(sandbox), ['Station A', 'Station C'], 'B (no briefing) left out; highest rate first');
  const firstValue = lastChild(el(sandbox, 'ask-body'))._children[0]._children[0]._children[1].textContent;
  assert.strictEqual(firstValue, '40.0%');
  dash.renderAskList('nycha');
  assert.deepStrictEqual(listedNames(sandbox), ['Station A']);
  assert.strictEqual(el(sandbox, 'ask-question').textContent, 'Most often unusable near NYCHA housing (within 300 m)');
  dash.renderAskList('gap');
  assert.deepStrictEqual(listedNames(sandbox), ['Station C']);
}

async function testTypedListQuestionRoutesToTheList() {
  const { dash, sandbox } = await load();
  assert.strictEqual(dash.askSubmit('Which stations near NYCHA are most often unusable?'), 'list');
  assert.deepStrictEqual(listedNames(sandbox), ['Station A']);
}

async function testBackLinkReturnsToTheList() {
  const { dash, sandbox } = await load();
  dash.renderAskList('gap');
  dash.selectStation('C');
  assert.ok(!hidden(sandbox, 'ask-back'));
  el(sandbox, 'ask-back').click();
  assert.deepStrictEqual(listedNames(sandbox), ['Station C']);
  assert.ok(hidden(sandbox, 'ask-back'), 'no back link on the list itself');
}

async function testUnsupportedQuestionIsHonest() {
  const { dash, sandbox } = await load();
  assert.strictEqual(dash.askSubmit('What will the weather be tomorrow?'), 'unsupported');
  assert.ok(el(sandbox, 'ask-body').textContent.includes('Open-ended questions are coming next'));
}

async function testMissingReliabilityDegradesTheListsOnly() {
  const { dash, sandbox } = await load({ reliability: null });
  dash.renderAskList('unusable');
  assert.ok(el(sandbox, 'ask-body').textContent.includes('did not load'));
}

function testNameMatchingIgnoresCaseAmpersandAndPunctuation() {
  return load().then(({ dash }) => {
    assert.strictEqual(dash.normalizeName('W 21 St & 6 Ave'), dash.normalizeName('w 21 st and 6 ave?'));
    const stations = { x: { name: 'W 21 St & 6 Ave' }, y: { name: 'W 21 St & 6 Ave North' }, z: { name: 'E 6 Ave & W 21 St' } };
    assert.deepStrictEqual([...dash.matchStations('w 21 st & 6 ave', stations)], ['x', 'y'], 'exact first, then prefix');
  });
}

(async () => {
  await testDefaultStateIsSetInJs();
  await testMapClickShowsTheStationsBriefing();
  await testTemplateBriefingIsLabelledAsTemplate();
  await testStationWithoutBriefingSaysSo();
  await testMissingBriefingsFileSaysSo();
  await testTypedQuestionFindsTheStation();
  await testAmbiguousNameOffersAChoice();
  await testListsOnlyHoldMatchingBriefedStations();
  await testTypedListQuestionRoutesToTheList();
  await testBackLinkReturnsToTheList();
  await testUnsupportedQuestionIsHonest();
  await testMissingReliabilityDegradesTheListsOnly();
  await testNameMatchingIgnoresCaseAmpersandAndPunctuation();
  console.log('dashboard2 ask panel test passed (AI version, Feature 1).');
})().catch(error => { console.error(error); process.exit(1); });

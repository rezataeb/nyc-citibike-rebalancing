// Node test for dashboard2.html's consolidated time-slice dock (Session 51).
//
// The day-type toggle and period dropdown moved out of the floating
// #map-controls-bar card and into #timebar, so every control that picks a time
// slice sits on the slider itself. That left #map-controls-bar holding only
// #live-as-of, which means it must be HIDDEN in historical mode or it renders
// as an empty white rectangle over the map.
//
// That last point is why this file exists. This project's standing rule --
// a control's visible state is set in JS on load, never implied by markup --
// has been broken four times (Sessions 19, 20A, 20B, and nearly here). The
// harness's DOM stub does not reflect static class attributes, so a test that
// only checked the markup would prove nothing either way.
//
// Run with:
//   node tests/test_dashboard2_timebar.js

const assert = require('assert');
const { loadDashboard2, readDashboard2 } = require('./dashboard_harness');

const hidden = (sandbox, id) => sandbox._elements[id].classList.contains('hidden');

// This card's visibility has flipped twice as the mode toggle moved in (S52)
// and back out to the masthead (S53). It holds only #live-as-of again, so it
// is live-mode-only. The invariant that has held throughout, and is the real
// subject here: the state is established in JS by setMode(), never by a class
// sitting in the markup.
async function testFloatingCardIsLiveModeOnly() {
  const { dash, sandbox } = await loadDashboard2();
  assert.ok(hidden(sandbox, 'map-controls-bar'), 'empty in historical mode -- hidden on load');

  dash.setMode('live');
  assert.ok(!hidden(sandbox, 'map-controls-bar'), 'carries the live "as of" stamp');
  dash.setMode('historical');
  assert.ok(hidden(sandbox, 'map-controls-bar'), 'hidden again on the way back');
}

async function testTimebarIsHistoricalOnly() {
  const { dash, sandbox } = await loadDashboard2();
  assert.ok(!hidden(sandbox, 'timebar'), '#timebar is the historical-mode control dock');
  dash.setMode('live');
  assert.ok(hidden(sandbox, 'timebar'), 'no hour concept in live mode');
  dash.setMode('historical');
  assert.ok(!hidden(sandbox, 'timebar'), 'switching back restores the dock');
}

// The toggle moved out of the masthead into the map card (design "D"), and its
// labels are title case. Both are asserted against raw HTML: containment and
// literal text are markup facts the DOM stub cannot speak to.
// Session 53: title, the four metric cards and the mode toggle share ONE
// masthead row. Labels stay title case wherever the toggle lives.
function testMastheadHoldsTitleRibbonAndToggleInOneRow() {
  const html = readDashboard2();
  const header = html.slice(html.indexOf('<header id="topbar">'), html.indexOf('</header>'));

  for (const id of ['title', 'ribbon', 'mode-toggle']) {
    assert.ok(header.includes(`id="${id}"`), `#${id} belongs in the masthead row`);
  }
  assert.ok(header.indexOf('id="ribbon"') > header.indexOf('id="title"'), 'ribbon sits after the title');
  assert.ok(header.indexOf('id="mode-toggle"') > header.indexOf('id="ribbon"'), 'toggle sits after the ribbon');
  assert.ok(!html.includes('id="ribbon-strip"'), 'the separate full-width strip is gone');

  assert.ok(/>Historical Flow</.test(html), 'title case: "Historical Flow"');
  assert.ok(/>Live Docks</.test(html), 'title case: "Live Docks"');
  assert.ok(!/>Historical flow</.test(html) && !/>Live docks</.test(html), 'no sentence-case leftovers');
}

async function testSliceControlsLiveInsideTheTimebar() {
  // Asserted against the raw HTML: this is a containment fact, and the DOM
  // stub is flat -- it would happily hand back both ids from anywhere.
  const html = readDashboard2();
  const timebar = html.slice(html.indexOf('<div id="timebar">'), html.indexOf('<!-- Legend is the FIRST child'));

  for (const id of ['historical-controls', 'day-toggle', 'period-select', 'hour-play-btn', 'hour-slider']) {
    assert.ok(timebar.includes(`id="${id}"`), `#${id} should now live inside #timebar`);
  }
  // Sliced to the card's own span rather than matched with a lazy regex from
  // its opening tag: #map-controls-bar precedes #timebar in the document, so
  // [\s\S]*? would run straight past the card and match the control's NEW
  // home, passing whether or not the move happened.
  const card = html.slice(html.indexOf('<div id="map-controls-bar">'), html.indexOf('<div id="timebar">'));
  for (const id of ['period-select', 'day-toggle', 'historical-controls']) {
    assert.ok(!card.includes(`id="${id}"`), `#${id} should have left the floating map-controls card`);
  }
  assert.ok(card.includes('id="live-as-of"'), 'the card still carries the live "as of" stamp');
}

function testDayTypeButtonsUseWholeWords() {
  const html = readDashboard2();
  assert.ok(/id="day-weekday"[^>]*>Weekday</.test(html), 'day-type buttons spell out "Weekday"');
  assert.ok(/id="day-weekend"[^>]*>Weekend</.test(html), 'day-type buttons spell out "Weekend"');
  // The mockups used WD/WE. Legible only to someone who already knows.
  assert.ok(!/>WD</.test(html) && !/>WE</.test(html), 'no WD/WE abbreviations');
}

async function testPeriodDropdownKeepsEveryPeriodTheDataHas() {
  // The design mockups all reduced this to two chips (Jul/Jan). flows.json's
  // granularity block is the source of truth, and the control must offer all
  // of it -- dropping nine months to fit a chip row is a capability loss
  // wearing a styling change's clothes.
  const { sandbox } = await loadDashboard2();
  const select = sandbox._elements['period-select'];
  const values = select._children.map(opt => opt.value); // harness stub records appends in _children

  assert.ok(values.includes('all'), 'all-period average is offered');
  for (const season of ['winter', 'spring', 'summer', 'fall']) {
    // FAKE_FLOWS only declares spring, so assert against what it declares.
    if (season !== 'spring') continue;
    assert.ok(values.includes(`season:${season}`), `${season} is offered`);
  }
  assert.ok(values.includes('month:2026-04'), 'months from granularity are offered');
  assert.ok(values.includes('month:2026-05'), 'every declared month is offered, not a two-chip subset');
}

async function testModelNoteReportsRealMeasuredError() {
  const { dash, sandbox } = await loadDashboard2();

  // No model_performance.json (the harness 404s it): the note must be hidden
  // rather than showing an accuracy claim with no measurement behind it.
  assert.ok(hidden(sandbox, 'timebar-model-note'), 'no measurement -> no note');

  dash.getState().modelPerformance = { aggregate: { naive_mean_mae: 1.9538937401113634 } };
  dash.renderTimebarModelNote();

  const note = sandbox._elements['timebar-model-note'];
  assert.ok(!hidden(sandbox, 'timebar-model-note'), 'note appears once there is a real figure');
  assert.ok(note.textContent.includes('1.95'), 'prints the measured walk-forward MAE');
  assert.ok(note.textContent.includes('bikes/day'), 'states the unit the curve is actually in');
  assert.ok(
    !/^Model:/.test(note.textContent),
    'must not open with "Model:" -- the curve is an observation, not a model output'
  );
}

async function testTickScaleSpansTheRealSliderRange() {
  const html = readDashboard2();
  const ticks = html.slice(html.indexOf('id="timebar-ticks"'), html.indexOf('id="timebar-model-note"'));
  for (const label of ['00:00', '06:00', '12:00', '18:00', '23:00']) {
    assert.ok(ticks.includes(label), `tick ${label} present`);
  }
  // The slider is 0..23 inclusive. A scale ending at 24:00 would misrepresent it.
  assert.ok(!ticks.includes('24:00'), 'scale ends at 23:00, matching the slider max');
  assert.ok(/id="hour-slider"[^>]*max="23"/.test(html), 'slider max is still 23');
}

// Regression guard for a bug that shipped silently (Session 51). `.hidden` is
// a class (specificity 0,0,1,0); #timebar, #map-controls-bar,
// #historical-controls and #status-detail-toggle each declare `display` at ID
// specificity (0,1,0,0), so a plain `.hidden { display: none }` lost to every
// one of them and hid nothing. #timebar was visible in Live mode -- which has
// no hour concept -- for as long as it had `display:flex`.
//
// The DOM stub cannot catch this: it records classList, not computed style, so
// every classList-based assertion above passed throughout. This one reads the
// stylesheet text instead, which is the only place the evidence exists.
function testHiddenUtilityOutranksIdLevelDisplayRules() {
  const html = readDashboard2();
  const css = html.slice(0, html.indexOf('</style>'));

  const hiddenRule = /\.hidden\s*\{([^}]*)\}/.exec(css);
  assert.ok(hiddenRule, '.hidden rule exists');
  assert.ok(
    /display\s*:\s*none\s*!important/.test(hiddenRule[1]),
    '.hidden must use !important -- several elements it hides declare display at ID specificity, '
      + 'which outranks a bare class rule and silently defeats it'
  );
}

(async () => {
  await testFloatingCardIsLiveModeOnly();
  await testTimebarIsHistoricalOnly();
  testMastheadHoldsTitleRibbonAndToggleInOneRow();
  await testSliceControlsLiveInsideTheTimebar();
  testDayTypeButtonsUseWholeWords();
  await testPeriodDropdownKeepsEveryPeriodTheDataHas();
  await testModelNoteReportsRealMeasuredError();
  await testTickScaleSpansTheRealSliderRange();
  testHiddenUtilityOutranksIdLevelDisplayRules();
  console.log('dashboard2 timebar test passed (Session 51: consolidated time-slice dock).');
})();

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

// Session 60: #map-controls-bar is gone. It had been emptied by attrition --
// day toggle, period dropdown, view-mode toggle and Show-route button all
// removed or relocated across Sessions 49/51/52/55 -- until its only content
// was the "Live as of" stamp, which now lives in #timebar with historical
// mode's controls. The dock itself is always visible; only its contents swap.
function testTheEmptiedMapCardIsGoneEntirely() {
  const html = readDashboard2();
  assert.ok(!html.includes('<div id="map-controls-bar">'), 'the container is gone from the markup');
  assert.ok(!/^\s*#map-controls-bar \{/m.test(html), 'and its CSS rules went with it');
}

async function testDockIsAlwaysVisibleAndSwapsContents() {
  const { dash, sandbox } = await loadDashboard2();

  // Historical: hour controls shown, live stamp hidden.
  assert.ok(!hidden(sandbox, 'timebar'), 'the dock is visible on load');
  assert.ok(!hidden(sandbox, 'historical-controls'), 'slice controls shown');
  assert.ok(!hidden(sandbox, 'timebar-scrub'), 'scrubber shown');
  assert.ok(hidden(sandbox, 'live-as-of'), 'live stamp hidden');

  dash.setMode('live');
  // Live: the dock stays, its contents invert. It must NOT disappear -- that
  // is what used to send the live stamp to a separate card in another corner.
  assert.ok(!hidden(sandbox, 'timebar'), 'the dock is still visible in live mode');
  assert.ok(hidden(sandbox, 'historical-controls'), 'slice controls hidden -- no hour concept in live');
  assert.ok(hidden(sandbox, 'timebar-scrub'), 'scrubber hidden');
  assert.ok(hidden(sandbox, 'timebar-ticks'), 'tick scale hidden');
  assert.ok(!hidden(sandbox, 'live-as-of'), 'live stamp shown');

  dash.setMode('historical');
  assert.ok(!hidden(sandbox, 'timebar-scrub'), 'and back again');
  assert.ok(hidden(sandbox, 'live-as-of'), 'live stamp hidden again');
}

// Session 58/59 order: identity, then mode, then the numbers that mode
// produces. The four cards carry different metrics per mode, so the toggle has
// to be readable before them -- and it sits under the title, inside the title
// block, rather than off in the masthead's far corner.
function testMastheadHoldsTitleToggleAndRibbonInReadingOrder() {
  const html = readDashboard2();
  const header = html.slice(html.indexOf('<header id="topbar">'), html.indexOf('</header>'));

  for (const id of ['title', 'mode-toggle', 'ribbon']) {
    assert.ok(header.includes(`id="${id}"`), `#${id} belongs in the masthead row`);
  }
  assert.ok(header.indexOf('id="mode-toggle"') > header.indexOf('id="title"'), 'toggle sits after the title');
  assert.ok(header.indexOf('id="ribbon"') > header.indexOf('id="mode-toggle"'), 'ribbon sits after the toggle');
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

  for (const id of ['historical-controls', 'day-toggle', 'period-select', 'hour-play-btn', 'hour-slider', 'live-as-of']) {
    assert.ok(timebar.includes(`id="${id}"`), `#${id} should now live inside #timebar`);
  }
  // Sliced to the card's own span rather than matched with a lazy regex from
  // its opening tag: #map-controls-bar precedes #timebar in the document, so
  // [\s\S]*? would run straight past the card and match the control's NEW
  // home, passing whether or not the move happened.
  assert.ok(timebar.includes('id="live-as-of"'), 'the live stamp shares the dock now');
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
  // Options live inside <optgroup>s since Session 58, so this walks one level
  // deeper. Flattening here rather than asserting the grouping itself: the
  // point of the test is that no period was DROPPED to tidy the list, which
  // is a claim about the full set regardless of how it is grouped.
  const values = select._children.flatMap(child => (child._children.length ? child._children : [child]))
    .map(opt => opt.value);

  assert.ok(values.includes('all'), 'all-period average is offered');
  for (const season of ['winter', 'spring', 'summer', 'fall']) {
    // FAKE_FLOWS only declares spring, so assert against what it declares.
    if (season !== 'spring') continue;
    assert.ok(values.includes(`season:${season}`), `${season} is offered`);
  }
  assert.ok(values.includes('month:2026-04'), 'months from granularity are offered');
  assert.ok(values.includes('month:2026-05'), 'every declared month is offered, not a two-chip subset');
}

async function testClimatologyNoteReportsRealMeasuredError() {
  const { dash, sandbox } = await loadDashboard2();

  // Session 57 moved this out of the timebar and into the Model Performance
  // disclosure -- it is a statement about model accuracy, so it belongs with
  // the other accuracy numbers rather than as small print under a control.
  // No model_performance.json (the harness 404s it): the note must be hidden
  // rather than showing an accuracy claim with no measurement behind it.
  assert.ok(hidden(sandbox, 'model-climatology-note'), 'no measurement -> no note');

  dash.getState().modelPerformance = { aggregate: { naive_mean_mae: 1.9538937401113634 } };
  dash.renderClimatologyNote();

  const note = sandbox._elements['model-climatology-note'];
  assert.ok(!hidden(sandbox, 'model-climatology-note'), 'note appears once there is a real figure');
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
  const timebar = html.slice(html.indexOf('<div id="timebar">'), html.indexOf('<!-- Legend is the FIRST child'));
  assert.ok(!timebar.includes('model-climatology-note'), 'the climatology note has left the timebar');
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

async function testPeriodOptionsAreGroupedWithoutLosingAny() {
  // Grouping is labelling, not filtering. The count of real periods offered
  // must equal what flows.json's granularity block declares, plus 'all'.
  const { sandbox } = await loadDashboard2();
  const select = sandbox._elements['period-select'];

  const groups = select._children.filter(child => child._children.length);
  assert.deepStrictEqual(groups.map(g => g.label), ['Seasons', 'Months'], 'two labelled groups');

  const flat = select._children.flatMap(c => (c._children.length ? c._children : [c]));
  // FAKE_FLOWS declares 1 season and 2 months, plus the all-period option.
  assert.strictEqual(flat.length, 1 + 1 + 2, 'every declared period is still offered');
  assert.strictEqual(flat[0].value, 'all', 'all-period stays first and ungrouped');
  assert.strictEqual(flat[0].textContent, 'All months (average)', 'renamed from "All-period average"');

  console.log('  period options are grouped, and none were dropped');
}

async function testLegendMidTickMatchesTheScaleItSitsOn() {
  // The middle tick was the literal string "0" hardcoded in markup and never
  // touched by renderLegendScale(), so live mode read "0% full / 0 / 100% full"
  // -- a bare zero between two percentages, meaning nothing. It is also the
  // exact shape of bug the standing JS-defaults rule exists to prevent.
  const { dash, sandbox } = await loadDashboard2();
  dash.renderHour(8);
  assert.strictEqual(sandbox._elements['legend-tick-mid'].textContent, '0',
    'net flow midpoint is a real zero: as many bikes arriving as leaving');

  dash.setMode('live');
  assert.strictEqual(sandbox._elements['legend-tick-mid'].textContent, '50%',
    'a fill scale running 0%..100% has its midpoint at 50%, not at 0');

  const html = readDashboard2();
  assert.ok(/id="legend-tick-mid"><\/span>/.test(html),
    'and the markup ships it empty -- the value is set in JS, never implied');
}

(async () => {
  testTheEmptiedMapCardIsGoneEntirely();
  await testDockIsAlwaysVisibleAndSwapsContents();
  testMastheadHoldsTitleToggleAndRibbonInReadingOrder();
  await testSliceControlsLiveInsideTheTimebar();
  testDayTypeButtonsUseWholeWords();
  await testPeriodDropdownKeepsEveryPeriodTheDataHas();
  await testPeriodOptionsAreGroupedWithoutLosingAny();
  await testClimatologyNoteReportsRealMeasuredError();
  await testTickScaleSpansTheRealSliderRange();
  testHiddenUtilityOutranksIdLevelDisplayRules();
  await testLegendMidTickMatchesTheScaleItSitsOn();
  console.log('dashboard2 timebar test passed (Session 51: consolidated time-slice dock).');
})();

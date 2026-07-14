// Node smoke test for dashboard.html's 24h time slider (Session 11, first
// interaction control). No npm dependencies, no build step -- consistent
// with the dashboard's own zero-toolchain design. Run with:
//   node tests/test_dashboard_slider.js
//
// Stubs the DOM and Leaflet's global `L` well enough to execute
// dashboard.html's real inline <script> unmodified inside Node's `vm`
// module, then drives it exactly like a browser would: fetch a fake
// flows.json, simulate a slider drag, and assert on the resulting marker
// styles and text -- not on the color algorithm's exact output (that's
// self-consistency-checked against the same divergingColor() function the
// dashboard itself uses), but on whether the wiring is actually correct.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function extractInlineScript(html) {
  // dashboard.html has two <script> tags: the Leaflet CDN one (has a src=
  // attribute, no body) and the dashboard's own inline one. Match the one
  // with no src attribute.
  const match = html.match(/<script>\n([\s\S]*?)<\/script>/);
  if (!match) throw new Error('could not find inline <script> block in dashboard.html');
  return match[1];
}

function zeros() {
  return new Array(24).fill(0);
}

function makeCurve(overrides) {
  const curve = zeros();
  for (const hour in overrides) curve[hour] = overrides[hour];
  return curve;
}

const FAKE_PAYLOAD = {
  granularity: { months: ['2026-04'], seasons: ['spring'] },
  stations: {
    A: {
      name: 'Station A', lat: 40.75, lng: -73.98,
      weekday: makeCurve({ 8: 10, 14: -3 }), weekend: makeCurve({ 8: 2, 14: -1 }),
      cluster: 1, cluster_name: 'Test cluster A',
      context: {
        near_nycha: 1, near_school: 0, nycha_dist_m: 120, nycha_nearest: 'Test NYCHA A',
        school_dist_m: 900, school_nearest: 'Test School A',
        subway_dist_m: 200, subway_nearest: 'Test Subway A', transit_gap: 0,
      },
    },
    B: {
      name: 'Station B', lat: 40.76, lng: -73.99,
      weekday: makeCurve({ 8: -20, 14: 5 }), weekend: makeCurve({ 8: -4, 14: 1 }),
      cluster: 2, cluster_name: 'Test cluster B',
      context: {
        near_nycha: 0, near_school: 1, nycha_dist_m: 1000, nycha_nearest: 'Test NYCHA B',
        school_dist_m: 150, school_nearest: 'Test School B',
        subway_dist_m: 900, subway_nearest: 'Test Subway B', transit_gap: 1,
      },
    },
    C: {
      name: 'Station C', lat: 40.70, lng: -73.95,
      weekday: makeCurve({ 8: 1, 14: 0.5 }), weekend: makeCurve({ 8: 0.2, 14: 0.1 }),
      cluster: 3, cluster_name: 'Test cluster C',
      context: {
        near_nycha: 0, near_school: 0, nycha_dist_m: 2000, nycha_nearest: 'Test NYCHA C',
        school_dist_m: 2000, school_nearest: 'Test School C',
        subway_dist_m: 100, subway_nearest: 'Test Subway C', transit_gap: 0,
      },
    },
  },
};

function makeElementStub(id) {
  const el = {
    id,
    textContent: '',
    innerHTML: '',
    style: {},
    value: undefined,
    _listeners: {},
    _classes: new Set(),
    _attrs: {},
    _children: [],
    addEventListener(event, handler) { el._listeners[event] = handler; },
    setAttribute(name, value) { el._attrs[name] = value; },
    getAttribute(name) { return el._attrs[name]; },
    appendChild(child) { el._children.push(child); return child; },
    classList: {
      toggle(cls, force) {
        if (force) el._classes.add(cls); else el._classes.delete(cls);
      },
      add(cls) { el._classes.add(cls); },
      remove(cls) { el._classes.delete(cls); },
      contains(cls) { return el._classes.has(cls); },
    },
  };
  return el;
}

function buildSandbox() {
  const elements = {};
  const getElementById = id => {
    if (!elements[id]) elements[id] = makeElementStub(id);
    return elements[id];
  };
  elements['hour-slider'] = makeElementStub('hour-slider');
  elements['hour-slider'].value = '8';

  const boundsStub = { extend() { return this; } };
  const layerStub = { addTo() { return this; } };

  function makeMarkerStub(latlng, opts) {
    const marker = {
      _latlng: latlng,
      _opts: Object.assign({}, opts),
      _listeners: {},
      addTo() { return marker; },
      setStyle(newOpts) { Object.assign(marker._opts, newOpts); return marker; },
      on(event, handler) { marker._listeners[event] = handler; return marker; },
    };
    return marker;
  }

  const L = {
    map() { return { setView() { return this; }, fitBounds() { return this; } }; },
    tileLayer() { return layerStub; },
    latLngBounds() { return boundsStub; },
    circleMarker(latlng, opts) { return makeMarkerStub(latlng, opts); },
  };

  const sandbox = {
    document: {
      getElementById,
      createElement() {
        const div = { textContent: '', _children: [], appendChild(child) { div._children.push(child); return child; } };
        return div;
      },
    },
    L,
    fetch() {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(FAKE_PAYLOAD) });
    },
    // Synchronous stub: a real browser defers to the next paint, but for a
    // smoke test we only care that the callback eventually runs with the
    // latest queued value, not real frame timing.
    requestAnimationFrame(cb) { cb(); return 0; },
    console,
    Math,
    Object,
    Number,
    Promise,
    globalThis: undefined, // filled in below once the context exists
  };
  sandbox.globalThis = sandbox;
  sandbox._elements = elements; // exposed for assertions, not read by the dashboard script itself
  return sandbox;
}

async function main() {
  const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
  const script = extractInlineScript(html);

  const sandbox = buildSandbox();
  const context = vm.createContext(sandbox);
  vm.runInContext(script, context, { filename: 'dashboard.html (inline script)' });

  const dash = context.__dashboard;
  assert.ok(dash, '__dashboard test hook was not exposed by the inline script');

  // --- pure function checks ---
  assert.strictEqual(dash.formatHourLabel(0), '12:00 AM');
  assert.strictEqual(dash.formatHourLabel(8), '8:00 AM');
  assert.strictEqual(dash.formatHourLabel(12), '12:00 PM');
  assert.strictEqual(dash.formatHourLabel(13), '1:00 PM');
  assert.strictEqual(dash.formatHourLabel(23), '11:00 PM');

  assert.strictEqual(dash.divergingColor(0, 10), dash.divergingColor(0, 10)); // stable/deterministic
  const surplusColor = dash.divergingColor(999, 10); // clamped to t=1
  const deficitColor = dash.divergingColor(-999, 10); // clamped to t=-1
  assert.notStrictEqual(surplusColor, deficitColor, 'surplus and deficit extremes must render as different colors');

  // --- wait for the fetch().then() chain the dashboard kicks off on load ---
  await dash.ready;

  const stateAt8 = dash.getState();
  assert.strictEqual(Object.keys(stateAt8.markers).length, 3, 'expected one marker per fake station');
  assert.strictEqual(stateAt8.currentHour, 8);

  for (const id of ['A', 'B', 'C']) {
    const expected = dash.divergingColor(FAKE_PAYLOAD.stations[id].weekday[8], stateAt8.domainMax);
    assert.strictEqual(
      stateAt8.markers[id]._opts.fillColor, expected,
      `marker ${id} fillColor at hour 8 should come from divergingColor(weekday[8], domainMax)`
    );
  }

  const domainMaxAt8 = stateAt8.domainMax;
  const elements = sandbox._elements;
  assert.strictEqual(elements['title-subtitle'].textContent, 'Net flow at 8:00 AM, weekday (all-period average)');
  assert.strictEqual(elements['hour-label'].textContent, '8:00 AM');

  // --- simulate dragging the slider to 2pm (hour 14) ---
  const inputHandler = elements['hour-slider']._listeners.input;
  assert.ok(inputHandler, 'no input listener was registered on the hour slider');
  inputHandler({ target: { value: '14' } });

  const stateAt14 = dash.getState();
  assert.strictEqual(stateAt14.currentHour, 14);
  assert.strictEqual(
    stateAt14.domainMax, domainMaxAt8,
    'domainMax must stay fixed across hours -- recomputing it per hour would make color intensity ' +
    'incomparable across the slider, defeating the point of a fixed color scale'
  );

  for (const id of ['A', 'B', 'C']) {
    const expected = dash.divergingColor(FAKE_PAYLOAD.stations[id].weekday[14], stateAt14.domainMax);
    assert.strictEqual(
      stateAt14.markers[id]._opts.fillColor, expected,
      `marker ${id} fillColor at hour 14 should come from divergingColor(weekday[14], domainMax)`
    );
  }

  assert.strictEqual(elements['title-subtitle'].textContent, 'Net flow at 2:00 PM, weekday (all-period average)');
  assert.strictEqual(elements['legend-title'].textContent, 'Net flow at 2:00 PM weekday (bikes/day)');
  assert.strictEqual(elements['hour-label'].textContent, '2:00 PM');

  // --- simulate clicking the "Weekend" day-type toggle, still at hour 14 ---
  const weekendClick = elements['day-weekend']._listeners.click;
  assert.ok(weekendClick, 'no click listener was registered on the weekend toggle button');
  weekendClick();

  const stateWeekend = dash.getState();
  assert.strictEqual(stateWeekend.dayType, 'weekend');
  assert.strictEqual(stateWeekend.currentHour, 14, 'switching day type must not change the selected hour');
  assert.strictEqual(
    stateWeekend.domainMax, domainMaxAt8,
    'domainMax must stay fixed across the day-type toggle -- recomputing it per day type would let a ' +
    'moderate weekend imbalance rescale to look as severe as a genuine weekday peak'
  );

  for (const id of ['A', 'B', 'C']) {
    const expected = dash.divergingColor(FAKE_PAYLOAD.stations[id].weekend[14], stateWeekend.domainMax);
    assert.strictEqual(
      stateWeekend.markers[id]._opts.fillColor, expected,
      `marker ${id} fillColor should come from divergingColor(weekend[14], domainMax) after the toggle`
    );
  }

  assert.strictEqual(elements['title-subtitle'].textContent, 'Net flow at 2:00 PM, weekend (all-period average)');
  assert.strictEqual(elements['legend-title'].textContent, 'Net flow at 2:00 PM weekend (bikes/day)');
  assert.ok(elements['day-weekend'].classList.contains('active'), 'weekend button should be marked active after toggling');
  assert.ok(!elements['day-weekday'].classList.contains('active'), 'weekday button should no longer be active after toggling to weekend');

  // --- toggle back to weekday, still at hour 14, and confirm domainMax is still untouched ---
  const weekdayClick = elements['day-weekday']._listeners.click;
  weekdayClick();
  const stateBackToWeekday = dash.getState();
  assert.strictEqual(stateBackToWeekday.dayType, 'weekday');
  assert.strictEqual(stateBackToWeekday.domainMax, domainMaxAt8, 'domainMax must remain fixed after toggling back');
  assert.strictEqual(
    stateBackToWeekday.markers.B._opts.fillColor,
    dash.divergingColor(FAKE_PAYLOAD.stations.B.weekday[14], domainMaxAt8),
    'toggling back to weekday should recolor markers from the weekday curve again'
  );

  // --- Session 13: station click -> detail panel. Still at hour 14, weekday. ---

  const markerBClick = stateBackToWeekday.markers.B._listeners.click;
  assert.ok(markerBClick, 'no click listener was registered on station B\'s marker');
  markerBClick();

  const stateSelected = dash.getState();
  assert.strictEqual(stateSelected.selectedId, 'B');
  assert.strictEqual(elements['detail-name'].textContent, 'Station B');
  assert.strictEqual(elements['detail-cluster'].textContent, 'Test cluster B');
  assert.strictEqual(elements['detail-daylabel'].textContent, 'weekday');
  assert.ok(elements['detail'].classList.contains('visible'), 'detail panel should be visible after selecting a station');
  assert.strictEqual(stateSelected.markers.B._opts.color, '#2a2a28', 'selected marker should get the highlight stroke color');
  assert.strictEqual(stateSelected.markers.B._opts.weight, 2, 'selected marker should get the highlight stroke weight');

  const contextChildren = elements['detail-context']._children;
  assert.strictEqual(contextChildren.length, 2, 'station B is near a school and has a subway gap -- expected 2 context lines');
  assert.strictEqual(
    contextChildren[0].textContent,
    'Nearest subway: Test Subway B (900 m) — beyond the 800 m subway-gap threshold'
  );
  assert.strictEqual(contextChildren[1].textContent, 'Within 300 m of school: Test School B (150 m)');

  // persistent dot must be drawn from B's weekday[14] against the SAME pooled
  // domainMax used everywhere else -- checked via the actual rendered markup,
  // using the dashboard's own stripX/stripY/divergingColor so this doesn't
  // duplicate the pixel-mapping math, just confirms it was actually applied.
  function assertStripDot(stationId, hour, dayType, domainMax) {
    const curve = FAKE_PAYLOAD.stations[stationId][dayType];
    const expectedX = dash.stripX(hour);
    const expectedY = dash.stripY(curve[hour], domainMax);
    const expectedFill = dash.divergingColor(curve[hour], domainMax);
    const html = elements['detail-strip'].innerHTML;
    assert.ok(html.includes(`id="strip-dot" cx="${expectedX}" cy="${expectedY}"`), `strip-dot should be positioned at hour ${hour}'s ${dayType} value`);
    assert.ok(html.includes(`fill="${expectedFill}"`), `strip-dot fill should come from divergingColor(${dayType}[${hour}], domainMax)`);
  }
  assertStripDot('B', 14, 'weekday', domainMaxAt8);

  // --- slider-sync-while-open: drag to hour 20 while the panel is open ---
  inputHandler({ target: { value: '20' } });
  const stateAfterSliderWhileOpen = dash.getState();
  assert.strictEqual(stateAfterSliderWhileOpen.selectedId, 'B', 'selection should survive a slider drag');
  assert.strictEqual(stateAfterSliderWhileOpen.domainMax, domainMaxAt8, 'domainMax must still be untouched');
  assertStripDot('B', 20, 'weekday', domainMaxAt8);

  // --- day-type-toggle-sync-while-open: toggle to weekend while still open ---
  weekendClick();
  const stateAfterToggleWhileOpen = dash.getState();
  assert.strictEqual(stateAfterToggleWhileOpen.selectedId, 'B', 'selection should survive a day-type toggle');
  assert.strictEqual(elements['detail-daylabel'].textContent, 'weekend');
  assertStripDot('B', 20, 'weekend', domainMaxAt8);

  // --- read-only hover preview must not touch global hour/dayType state ---
  dash.previewStripHour(5);
  const stateAfterPreview = dash.getState();
  assert.strictEqual(stateAfterPreview.currentHour, 20, 'hover preview must not mutate state.currentHour');
  assert.strictEqual(stateAfterPreview.dayType, 'weekend', 'hover preview must not mutate state.dayType');
  const expectedPreviewValue = FAKE_PAYLOAD.stations.B.weekend[5]; // 0 -> "+0.0"
  assert.strictEqual(
    elements['strip-tooltip-value'].textContent,
    `${expectedPreviewValue >= 0 ? '+' : ''}${expectedPreviewValue.toFixed(1)}`
  );
  assert.strictEqual(elements['strip-tooltip-hour'].textContent, dash.formatHourLabel(5));
  assert.strictEqual(Number(elements['strip-crosshair']._attrs.x1), dash.stripX(5));
  assert.notStrictEqual(elements['strip-crosshair'].style.display, 'none', 'crosshair should be visible during preview');

  dash.clearStripPreview();
  assert.strictEqual(elements['strip-crosshair'].style.display, 'none', 'crosshair should hide again once the pointer leaves');

  // --- close clears both the panel and the marker highlight ---
  dash.closeStation();
  const stateAfterClose = dash.getState();
  assert.strictEqual(stateAfterClose.selectedId, null);
  assert.ok(!elements['detail'].classList.contains('visible'), 'detail panel should be hidden after closing');
  assert.strictEqual(stateAfterClose.markers.B._opts.color, '#ffffff', 'closing should revert the marker highlight color');
  assert.strictEqual(stateAfterClose.markers.B._opts.weight, 0.75, 'closing should revert the marker highlight weight');

  console.log('All dashboard slider smoke tests passed.');
}

main().catch(err => {
  console.error('FAILED:', err.message);
  process.exit(1);
});

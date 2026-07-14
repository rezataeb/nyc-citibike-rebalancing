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
    A: { name: 'Station A', lat: 40.75, lng: -73.98, weekday: makeCurve({ 8: 10, 14: -3 }), weekend: zeros() },
    B: { name: 'Station B', lat: 40.76, lng: -73.99, weekday: makeCurve({ 8: -20, 14: 5 }), weekend: zeros() },
    C: { name: 'Station C', lat: 40.70, lng: -73.95, weekday: makeCurve({ 8: 1, 14: 0.5 }), weekend: zeros() },
  },
};

function makeElementStub(id) {
  return {
    id,
    textContent: '',
    style: {},
    value: undefined,
    _listeners: {},
    addEventListener(event, handler) { this._listeners[event] = handler; },
  };
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
    return {
      _latlng: latlng,
      _opts: Object.assign({}, opts),
      addTo() { return this; },
      setStyle(newOpts) { Object.assign(this._opts, newOpts); return this; },
    };
  }

  const L = {
    map() { return { setView() { return this; }, fitBounds() { return this; } }; },
    tileLayer() { return layerStub; },
    latLngBounds() { return boundsStub; },
    circleMarker(latlng, opts) { return makeMarkerStub(latlng, opts); },
  };

  const sandbox = {
    document: { getElementById },
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

  console.log('All dashboard slider smoke tests passed.');
}

main().catch(err => {
  console.error('FAILED:', err.message);
  process.exit(1);
});

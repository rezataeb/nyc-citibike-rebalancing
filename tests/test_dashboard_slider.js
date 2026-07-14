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
  // Two months, mirroring the real data/flows.json's own granularity shape,
  // where coverage genuinely varies by station (only 365 of 2,347 real
  // stations have 2026-05). Station B below deliberately has no '2026-05'
  // bucket, so it can exercise the no-data path.
  granularity: { months: ['2026-04', '2026-05'], seasons: ['spring'] },
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
      seasons: { spring: { weekday: makeCurve({ 8: 10, 14: -3 }), weekend: makeCurve({ 8: 2, 14: -1 }) } },
      months: {
        '2026-04': { weekday: makeCurve({ 8: 10, 14: -3 }), weekend: makeCurve({ 8: 2, 14: -1 }) },
        '2026-05': { weekday: makeCurve({ 8: 15, 14: -5 }), weekend: makeCurve({ 8: 3, 14: -2 }) },
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
      seasons: { spring: { weekday: makeCurve({ 8: -20, 14: 5 }), weekend: makeCurve({ 8: -4, 14: 1 }) } },
      months: {
        '2026-04': { weekday: makeCurve({ 8: -20, 14: 5 }), weekend: makeCurve({ 8: -4, 14: 1 }) },
        // no '2026-05' -- this station has no data for that month, on purpose.
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
      seasons: { spring: { weekday: makeCurve({ 8: 1, 14: 0.5 }), weekend: makeCurve({ 8: 0.2, 14: 0.1 }) } },
      months: {
        '2026-04': { weekday: makeCurve({ 8: 1, 14: 0.5 }), weekend: makeCurve({ 8: 0.2, 14: 0.1 }) },
        '2026-05': { weekday: makeCurve({ 8: 2, 14: 1 }), weekend: makeCurve({ 8: 0.5, 14: 0.2 }) },
      },
    },
  },
};

// Fake live_status.json payload -- station A is a normal 50%-full reading
// (neutral gray under the deviation-from-50 color mapping), B is a normal
// but low reading (10% full, red end), and C is deliberately ABSENT so it
// exercises the "no live match" no-data bucket, same convention as a
// missing historical period.
const FAKE_LIVE_PAYLOAD = {
  last_updated: '2026-07-14T04:30:08+00:00',
  n_dropped: 0,
  stations: {
    A: { capacity: 20, bikes_available: 10, docks_available: 10, is_renting: true, is_returning: true },
    B: { capacity: 20, bikes_available: 2, docks_available: 18, is_renting: true, is_returning: true },
  },
};

// Fake route.json payload -- one truck, three stops, station A visited
// TWICE (mirroring the real data/route.json, where a truck can and does
// revisit a station) so the visit-index/offset logic actually gets
// exercised, not just assumed safe for the single-visit case. Mixes
// pickup and dropoff so both stop-icon shapes get built.
const FAKE_ROUTE_PAYLOAD = {
  period: 'all',
  trucks: [
    {
      truck: 1,
      capped: false,
      stops: [
        { action: 'pickup', amount: 5, lat: 40.75, lng: -73.98, name: 'Station A', running_load: 5, station_id: 'A' },
        { action: 'dropoff', amount: 3, lat: 40.76, lng: -73.99, name: 'Station B', running_load: 2, station_id: 'B' },
        { action: 'pickup', amount: 2, lat: 40.75, lng: -73.98, name: 'Station A', running_load: 4, station_id: 'A' },
      ],
    },
  ],
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

  // A single map stub object (not a new one per L.map() call, since the
  // dashboard only calls L.map() once) so setViewMode()'s map.removeLayer/
  // addLayer calls are observable via mapStub._layers in assertions.
  // addLayer/removeLayer/hasLayer track _addedToMap on cluster-kind layers
  // specifically -- this mirrors real Leaflet.markercluster, whose
  // refreshClusters() throws (reading this._topClusterLevel) until the
  // group has actually been through its own onAdd via map.addLayer(). An
  // earlier version of this stub let refreshClusters() succeed
  // unconditionally, which masked a real bug: renderHour() called it before
  // the cluster group was ever added to the map (on the very first render,
  // before any view-mode switch), throwing in the real browser every time.
  const mapStub = {
    setView() { return mapStub; },
    fitBounds() { return mapStub; },
    _layers: [],
    addLayer(layer) {
      mapStub._layers.push(layer);
      if (layer._kind === 'cluster') layer._addedToMap = true;
      return mapStub;
    },
    removeLayer(layer) {
      mapStub._layers = mapStub._layers.filter(l => l !== layer);
      if (layer._kind === 'cluster') layer._addedToMap = false;
      return mapStub;
    },
    hasLayer(layer) { return mapStub._layers.includes(layer); },
  };

  const L = {
    map() { return mapStub; },
    tileLayer() { return layerStub; },
    latLngBounds() { return boundsStub; },
    circleMarker(latlng, opts) { return makeMarkerStub(latlng, opts); },
    marker(latlng, opts) { return makeMarkerStub(latlng, opts); }, // route stop markers only need .on()/_opts, same shape as circleMarker for test purposes
    polyline(latlngs, opts) { return { _kind: 'polyline', _latlngs: latlngs, _opts: opts }; },
    // Individual view's container -- just holds the marker list, no behavior
    // this test needs beyond identity (so mapStub._layers can tell the two
    // containers apart).
    layerGroup(layers) { return { _kind: 'individual', _layers: layers }; },
    // Grouped view's container. refreshClusters() throws unless the group
    // has been added to the map -- see mapStub's comment above -- so this
    // stub actually exercises the map.hasLayer() guard in
    // dashboard.html's refreshClusters(), not just its own call count.
    markerClusterGroup(opts) {
      const group = {
        _kind: 'cluster',
        _opts: opts,
        _layers: [],
        _refreshCount: 0,
        _addedToMap: false,
        addLayers(layers) { group._layers.push(...layers); },
        refreshClusters() {
          if (!group._addedToMap) {
            throw new TypeError("Cannot read properties of undefined (reading 'getAllChildMarkers')");
          }
          group._refreshCount += 1;
        },
      };
      return group;
    },
    divIcon(opts) { return opts; },
    point(x, y) { return { x, y }; },
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
    fetch(url) {
      let payload = FAKE_PAYLOAD;
      if (url.includes('live_status')) payload = FAKE_LIVE_PAYLOAD;
      else if (url.includes('route')) payload = FAKE_ROUTE_PAYLOAD;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
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
  sandbox._map = mapStub; // exposed for assertions -- lets tests see which layer is actually attached
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
  assert.deepStrictEqual(stateAt8.liveStations, FAKE_LIVE_PAYLOAD.stations, 'live_status.json should be fetched alongside flows.json at load');
  assert.strictEqual(stateAt8.liveAsOf, FAKE_LIVE_PAYLOAD.last_updated);

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
  assert.strictEqual(elements['title-meta'].textContent, ' · 3 stations', 'station count should be merged into the subtitle line, not a separate boxed row');
  assert.ok(elements['status'].classList.contains('hidden'), 'the boxed status banner should be hidden once data has loaded successfully');

  // --- Session 19: pristine state, before any station has ever been selected ---
  assert.ok(elements['tab-legend'].classList.contains('active'), 'Legend tab should be active by default');
  assert.ok(elements['detail'].classList.contains('hidden'), 'Station detail pane should be hidden by default');
  assert.strictEqual(elements['detail-name'].textContent, 'No station selected', 'the detail pane must show a placeholder, not be blank, even before the first click');

  // Manually switching to the Station detail tab before ever selecting a
  // station must work (the tab is always reachable, never disabled) and
  // must show that same placeholder, not blank/stale content.
  const tabDetailClick = elements['tab-detail']._listeners.click;
  assert.ok(tabDetailClick, 'no click listener registered on the Station detail tab');
  tabDetailClick();
  assert.ok(!elements['detail'].classList.contains('hidden'), 'Station detail pane should show once its tab is clicked');
  assert.ok(elements['legend'].classList.contains('hidden'), 'Legend pane should hide while Station detail is active');
  assert.strictEqual(elements['detail-name'].textContent, 'No station selected', 'still the placeholder -- no station has been clicked on the map yet');

  const tabLegendClick = elements['tab-legend']._listeners.click;
  tabLegendClick();
  assert.ok(!elements['legend'].classList.contains('hidden'), 'switching back to Legend should show it again');
  assert.ok(elements['detail'].classList.contains('hidden'), 'and hide Station detail again');

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
  assert.strictEqual(elements['legend-title'].textContent, 'Net flow at 2:00 PM weekday, all-period average (bikes/day)');
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
  assert.strictEqual(elements['legend-title'].textContent, 'Net flow at 2:00 PM weekend, all-period average (bikes/day)');
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
  assert.strictEqual(elements['detail-daylabel'].textContent, 'weekday, all-period average');
  assert.ok(!elements['detail'].classList.contains('hidden'), 'detail pane should be visible after selecting a station');
  assert.ok(elements['tab-detail'].classList.contains('active'), 'selecting a station should switch to the Station detail tab');
  assert.ok(!elements['tab-legend'].classList.contains('active'), 'and away from the Legend tab');
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
  assert.strictEqual(elements['detail-daylabel'].textContent, 'weekend, all-period average');
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
  assert.ok(elements['detail'].classList.contains('hidden'), 'detail pane should be hidden after closing');
  assert.ok(elements['tab-legend'].classList.contains('active'), 'closing should switch back to the Legend tab');
  assert.strictEqual(elements['detail-name'].textContent, 'No station selected', 'closing should restore the placeholder, not stale content');
  assert.strictEqual(stateAfterClose.markers.B._opts.color, '#ffffff', 'closing should revert the marker highlight color');
  assert.strictEqual(stateAfterClose.markers.B._opts.weight, 0.75, 'closing should revert the marker highlight weight');

  // --- Session 14: season/month period selector. Still at hour 20, weekend. ---

  const periodOptions = elements['period-select']._children;
  assert.strictEqual(periodOptions.length, 4, 'expected all + 1 season + 2 months as period options, built from granularity');
  assert.strictEqual(periodOptions[0].value, 'all');
  assert.strictEqual(periodOptions[0].textContent, 'All-period average');
  assert.strictEqual(periodOptions[1].value, 'season:spring');
  assert.strictEqual(periodOptions[1].textContent, 'Spring');
  assert.strictEqual(periodOptions[2].value, 'month:2026-04');
  assert.strictEqual(periodOptions[2].textContent, 'April 2026');
  assert.strictEqual(periodOptions[3].value, 'month:2026-05');
  assert.strictEqual(periodOptions[3].textContent, 'May 2026');

  // --- switch to May 2026, a month station B has no data for ---
  const periodChange = elements['period-select']._listeners.change;
  assert.ok(periodChange, 'no change listener registered on the period select');
  periodChange({ target: { value: 'month:2026-05' } });

  const stateMay = dash.getState();
  assert.strictEqual(stateMay.period, 'month:2026-05');
  assert.strictEqual(
    stateMay.domainMax, domainMaxAt8,
    'domainMax must stay fixed across a period switch too -- same pooled-domain invariant a third time'
  );

  assert.strictEqual(
    stateMay.markers.A._opts.fillColor,
    dash.divergingColor(FAKE_PAYLOAD.stations.A.months['2026-05'].weekend[20], domainMaxAt8),
    'station A has May 2026 data -- its marker should recolor from that month\'s weekend curve'
  );
  assert.strictEqual(
    stateMay.markers.C._opts.fillColor,
    dash.divergingColor(FAKE_PAYLOAD.stations.C.months['2026-05'].weekend[20], domainMaxAt8),
    'station C has May 2026 data -- its marker should recolor from that month\'s weekend curve'
  );
  assert.strictEqual(stateMay.markers.B._opts.fillOpacity, 0, 'station B has no May 2026 data -- its marker should be hollow, not hidden');
  assert.strictEqual(stateMay.markers.B._opts.color, '#b5b3ad', 'a no-data marker should get the gray no-data stroke');
  assert.strictEqual(stateMay.markers.B._opts.weight, 1, 'a no-data marker should get the heavier no-data stroke weight');

  assert.strictEqual(
    elements['title-meta'].textContent, ' · 2 of 3 stations have data for May 2026',
    'the coverage message (now merged into the subtitle, not a boxed row) must surface the coverage gap, not hide it'
  );
  assert.ok(elements['status'].classList.contains('hidden'), 'the boxed status banner should stay hidden on the success path');
  assert.strictEqual(elements['title-subtitle'].textContent, 'Net flow at 8:00 PM, weekend (May 2026)');
  assert.strictEqual(elements['legend-title'].textContent, 'Net flow at 8:00 PM weekend, May 2026 (bikes/day)');

  // --- selecting the no-data station during May 2026 must show an explicit no-data state ---
  const selectBDuringMay = stateMay.markers.B._listeners.click;
  selectBDuringMay();
  const stateBDuringMay = dash.getState();
  assert.strictEqual(stateBDuringMay.selectedId, 'B');
  assert.strictEqual(elements['detail-daylabel'].textContent, 'weekend, May 2026');
  assert.ok(
    elements['detail-strip'].innerHTML.includes('No data for May 2026'),
    'the panel must say so explicitly, never a blank chart or a silent fallback to all-period average'
  );
  assert.ok(!elements['detail-strip'].innerHTML.includes('id="strip-dot"'), 'no strip marks should be drawn for a no-data period');
  assert.strictEqual(
    stateBDuringMay.markers.B._opts.color, '#2a2a28',
    'a selected no-data marker still gets the selection stroke, composed with the hollow fill'
  );
  assert.strictEqual(stateBDuringMay.markers.B._opts.fillOpacity, 0);

  // hover preview must stay a no-op for a no-data station/period -- never throw, never draw
  dash.clearStripPreview();
  dash.previewStripHour(9);
  assert.strictEqual(
    elements['strip-crosshair'].style.display, 'none',
    'previewStripHour must no-op when the selected station has no data for the current period'
  );

  // --- selecting station A (which DOES have May data) instead should show a real chart ---
  const selectADuringMay = stateBDuringMay.markers.A._listeners.click;
  selectADuringMay();
  const stateADuringMay = dash.getState();
  assert.strictEqual(stateADuringMay.selectedId, 'A');
  assert.ok(elements['detail-strip'].innerHTML.includes('id="strip-dot"'), 'station A has May 2026 data -- its strip should draw normally');
  assert.strictEqual(
    stateADuringMay.markers.B._opts.color, '#b5b3ad',
    'deselecting station B (by selecting A instead) should revert it to the plain no-data stroke, not the default white stroke'
  );

  // --- switching back to all-period average recovers B's marker and the status line ---
  periodChange({ target: { value: 'all' } });
  const stateBackToAll = dash.getState();
  assert.strictEqual(stateBackToAll.domainMax, domainMaxAt8);
  assert.strictEqual(
    stateBackToAll.markers.B._opts.fillColor,
    dash.divergingColor(FAKE_PAYLOAD.stations.B.weekend[20], domainMaxAt8),
    'switching back to all-period average should recolor station B normally again'
  );
  assert.strictEqual(stateBackToAll.markers.B._opts.fillOpacity, 0.85);
  assert.strictEqual(stateBackToAll.markers.B._opts.color, '#ffffff', 'station B should revert to the plain default stroke once data exists again');
  assert.strictEqual(elements['title-meta'].textContent, ' · 3 stations');
  assert.ok(!elements['detail-strip'].innerHTML.includes('No data'), 'station A (still selected) has all-period data -- no no-data message should remain');

  // --- Session 15: live GBFS mode. Entering with period='all', dayType='weekend', hour=20, selectedId='A'. ---

  const modeLiveClick = elements['mode-live']._listeners.click;
  assert.ok(modeLiveClick, 'no click listener registered on the live mode button');
  modeLiveClick();

  const stateLive = dash.getState();
  assert.strictEqual(stateLive.mode, 'live');
  assert.ok(elements['historical-controls'].classList.contains('hidden'), 'historical-only controls card should hide in live mode');
  assert.ok(!elements['live-as-of'].classList.contains('hidden'), 'the live as-of card should show in live mode');
  assert.ok(elements['mode-live'].classList.contains('active'));
  assert.ok(!elements['mode-historical'].classList.contains('active'));

  assert.strictEqual(elements['title-subtitle'].textContent, 'Live dock status');
  assert.strictEqual(elements['legend-title'].textContent, 'Live dock status (% full)');
  assert.strictEqual(elements['legend-label-low'].textContent, 'Empty (0% full)');
  assert.strictEqual(elements['legend-label-mid'].textContent, '50%');
  assert.strictEqual(elements['legend-label-high'].textContent, 'Full (100% full)');
  assert.strictEqual(elements['legend-note'].textContent, '50% is a neutral reference point, not a per-station target.');
  assert.strictEqual(elements['live-as-of'].textContent, `Live as of ${dash.formatAsOf(FAKE_LIVE_PAYLOAD.last_updated)}`);

  // A: 10/20 = 50% full -> neutral gray (deviation 0). B: 2/20 = 10% full -> red end. C: no live match -> hollow.
  assert.strictEqual(
    stateLive.markers.A._opts.fillColor, dash.divergingColor(0, 50),
    'station A is 50% full -> deviation-from-50 of 0 -> neutral gray'
  );
  assert.strictEqual(
    stateLive.markers.B._opts.fillColor, dash.divergingColor(10 - 50, 50),
    'station B is 10% full -> deviation-from-50 of -40 -> toward the red end'
  );
  assert.strictEqual(stateLive.markers.C._opts.fillOpacity, 0, 'station C has no live match -- hollow, not hidden');
  assert.strictEqual(stateLive.markers.C._opts.color, '#b5b3ad', 'station C should get the plain no-data stroke (not selected)');

  // A is still selected from the historical-mode steps above -- selection survives a mode switch.
  assert.strictEqual(stateLive.selectedId, 'A');
  assert.strictEqual(stateLive.markers.A._opts.color, '#2a2a28', 'station A keeps the selection stroke in live mode too');
  assert.strictEqual(stateLive.markers.A._opts.fillOpacity, 0.85, 'station A has live data -- not hollow, even while selected');

  assert.strictEqual(
    elements['title-meta'].textContent, ' · 2 of 3 stations have live data',
    'the coverage message must state live coverage explicitly, computed from the real fake payload, not hardcoded'
  );

  // Detail panel should already reflect A's live reading (updateStationDetail runs from renderHour's hook).
  assert.strictEqual(elements['detail-daylabel'].textContent, `live, as of ${dash.formatAsOf(FAKE_LIVE_PAYLOAD.last_updated)}`);
  assert.ok(elements['detail-strip'].innerHTML.includes('10 bikes / 10 docks'));
  assert.ok(elements['detail-strip'].innerHTML.includes('capacity 20'));

  // Hover preview must stay a no-op in live mode, even though A (the selected station) does have live data --
  // live mode simply has no rhythm strip/crosshair concept at all.
  dash.clearStripPreview();
  dash.previewStripHour(5);
  assert.strictEqual(elements['strip-crosshair'].style.display, 'none', 'previewStripHour must no-op unconditionally in live mode');

  // --- selecting the no-live-match station (C) shows the explicit no-live-data panel state ---
  const selectCLive = stateLive.markers.C._listeners.click;
  selectCLive();
  const stateCLive = dash.getState();
  assert.strictEqual(stateCLive.selectedId, 'C');
  assert.ok(elements['detail-strip'].innerHTML.includes('No live data'));
  assert.strictEqual(stateCLive.markers.C._opts.color, '#2a2a28', 'a selected no-live-data marker still gets the selection stroke');
  assert.strictEqual(stateCLive.markers.C._opts.fillOpacity, 0, 'and stays hollow while selected');
  assert.strictEqual(
    stateCLive.markers.A._opts.color, '#ffffff',
    'deselecting station A (by selecting C instead) should revert it to the plain default stroke -- it has live data, not the no-data gray'
  );

  // --- switching back to historical mode restores the historical controls, text, and marker logic ---
  const modeHistoricalClick = elements['mode-historical']._listeners.click;
  modeHistoricalClick();
  const stateBackToHistorical = dash.getState();
  assert.strictEqual(stateBackToHistorical.mode, 'historical');
  assert.ok(!elements['historical-controls'].classList.contains('hidden'), 'historical controls card should reappear');
  assert.ok(elements['live-as-of'].classList.contains('hidden'), 'the live as-of card should hide again');
  assert.strictEqual(elements['legend-label-low'].textContent, 'Deficit (no bikes)');
  assert.strictEqual(elements['legend-label-mid'].textContent, '0');
  assert.strictEqual(elements['legend-label-high'].textContent, 'Surplus (no docks)');
  assert.strictEqual(
    elements['title-subtitle'].textContent, 'Net flow at 8:00 PM, weekend (all-period average)',
    'historical text should reflect whatever hour/dayType/period were already set, unchanged by the live-mode detour'
  );

  // Station C (no live match, but flows.json always has all-period data) should recolor normally again.
  assert.strictEqual(
    stateBackToHistorical.markers.C._opts.fillColor,
    dash.divergingColor(FAKE_PAYLOAD.stations.C.weekend[20], domainMaxAt8),
    'station C should recolor from its historical weekend curve again, now that mode is historical'
  );
  assert.strictEqual(stateBackToHistorical.markers.C._opts.fillOpacity, 0.85);
  assert.strictEqual(elements['title-meta'].textContent, ' · 3 stations', 'coverage message should revert to the plain historical count (period is still "all")');
  assert.ok(
    elements['detail-strip'].innerHTML.includes('id="strip-dot"'),
    'station C (still selected) has all-period historical data -- the rhythm strip should draw normally again'
  );

  // --- Session 16: marker clustering. Entering with mode='historical', period='all', dayType='weekend', hour=20. ---

  // computeClusterColor: pure, no Leaflet -- empty -> null (never zero-filled), otherwise divergingColor(mean, domainMax).
  assert.strictEqual(dash.computeClusterColor([], 10), null, 'no valid children -> no data, not an averaged-in zero');
  assert.strictEqual(dash.computeClusterColor([10, -10], 10), dash.divergingColor(0, 10), 'mean of [10, -10] is 0');
  assert.strictEqual(dash.computeClusterColor([10, 20], 10), dash.divergingColor(15, 10), 'mean of [10, 20] is 15');

  // computeLiveClusterColor: same shape, fixed 50-point domain, known 0%/50%/100% inputs.
  assert.strictEqual(dash.computeLiveClusterColor([]), null, 'no valid live children -> no data');
  assert.strictEqual(dash.computeLiveClusterColor([0]), dash.divergingColor(-50, 50), '0% full -> full red-end deviation');
  assert.strictEqual(dash.computeLiveClusterColor([50]), dash.divergingColor(0, 50), '50% full -> neutral gray');
  assert.strictEqual(dash.computeLiveClusterColor([100]), dash.divergingColor(50, 50), '100% full -> full blue-end deviation');
  assert.strictEqual(
    dash.computeLiveClusterColor([0, 100]), dash.divergingColor(0, 50),
    'mean of [0, 100] is 50 -> deviation 0 -> same gray as a single 50% reading'
  );

  // clusterIconHTML: pure string builder, no Leaflet.
  assert.ok(dash.clusterIconHTML(null, 5).includes('background:transparent'), 'no-data cluster icon should be hollow');
  assert.ok(dash.clusterIconHTML(null, 5).includes('>5<'), 'no-data cluster icon should still show the child count');
  assert.ok(dash.clusterIconHTML('rgb(1, 2, 3)', 7).includes('background:rgb(1, 2, 3)'));
  assert.ok(dash.clusterIconHTML('rgb(1, 2, 3)', 7).includes('>7<'));

  // A lightweight FAKE cluster -- just the two methods markercluster's real
  // cluster object exposes -- not the library's actual spatial clustering
  // geometry, per the "unit-testable without stubbing markercluster's
  // clustering geometry" requirement.
  function fakeCluster(stationIds) {
    const markers = stationIds.map(stationId => ({ stationId }));
    return { getAllChildMarkers: () => markers, getChildCount: () => markers.length };
  }

  const clusterState = dash.getState();

  // --- Regression test for the reported bug: renderHour() must NOT throw
  // while still in individual view (clusterLayer never added to the map
  // yet) -- this is exactly what crashed on every page load before the
  // map.hasLayer() guard was added, aborting the load .then() callback
  // before it ever reached the addEventListener calls below it. ---
  assert.strictEqual(clusterState.viewMode, 'individual', 'entering this block still in individual view, as at initial load');
  assert.doesNotThrow(() => dash.renderHour(8), 'renderHour must not throw while the cluster layer is not attached to the map');
  assert.strictEqual(clusterState.clusterLayer._refreshCount, 0, 'refreshClusters must be a no-op (not even attempted) while the cluster layer is unattached');

  // --- view-mode toggle: explicit removeLayer(old) before addLayer(new), never both attached at once ---
  const mapLayersBefore = sandbox._map._layers.slice();
  assert.ok(mapLayersBefore.includes(clusterState.individualLayer), 'individual view should be the one attached to the map by default');
  assert.ok(!mapLayersBefore.includes(clusterState.clusterLayer), 'the cluster layer should not be attached while in individual view');

  const viewGroupedClick = elements['view-grouped']._listeners.click;
  assert.ok(viewGroupedClick, 'no click listener registered on the grouped view button');
  viewGroupedClick();

  assert.strictEqual(dash.getState().viewMode, 'grouped');
  assert.ok(elements['view-grouped'].classList.contains('active'));
  assert.ok(!elements['view-individual'].classList.contains('active'));
  assert.ok(!sandbox._map._layers.includes(clusterState.individualLayer), 'individual layer should be removed from the map when switching to grouped view');
  assert.ok(sandbox._map._layers.includes(clusterState.clusterLayer), 'cluster layer should be added to the map when switching to grouped view');
  assert.strictEqual(
    sandbox._map._layers.filter(l => l === clusterState.individualLayer || l === clusterState.clusterLayer).length, 1,
    'exactly one of the two view containers should ever be attached to the map at once'
  );

  // --- Now that the cluster layer is actually attached, refreshClusters() should really run. ---
  const refreshCountBefore1 = clusterState.clusterLayer._refreshCount;

  // --- renderHour (the slider's own path) is trigger point #1 for refreshClusters() ---
  dash.renderHour(8);
  assert.strictEqual(
    dash.getState().clusterLayer._refreshCount, refreshCountBefore1 + 1,
    'renderHour must call refreshClusters() exactly once now that grouped view is active'
  );

  // historical clusterIconCreateFunction, at hour 8, dayType weekend, period 'all':
  // A.weekend[8]=2, B.weekend[8]=-4, C.weekend[8]=0.2 -- mean = -0.6.
  let icon = dash.clusterIconCreateFunction(fakeCluster(['A', 'B', 'C']));
  assert.strictEqual(icon.html, dash.clusterIconHTML(dash.divergingColor(-0.6, domainMaxAt8), 3));

  // --- setPeriod is trigger point #2, and also sets up the no-data cluster case: ---
  // station B has no 'month:2026-05' bucket (see the fixture), so a cluster
  // containing only B has zero valid children this period -- hollow, not zero-filled.
  const refreshCountBefore2 = dash.getState().clusterLayer._refreshCount;
  dash.setPeriod('month:2026-05');
  assert.strictEqual(dash.getState().clusterLayer._refreshCount, refreshCountBefore2 + 1, 'setPeriod must call refreshClusters() exactly once');

  // Also confirms issue 3's fix: the header/legend text and the underlying
  // state.period used for coloring come from the exact same value -- there
  // is no separate "displayed period" to drift out of sync with it.
  assert.strictEqual(elements['title-subtitle'].textContent, 'Net flow at 8:00 AM, weekend (May 2026)');

  icon = dash.clusterIconCreateFunction(fakeCluster(['B']));
  assert.strictEqual(icon.html, dash.clusterIconHTML(null, 1), 'a cluster whose only child has no data for the period should render hollow');

  // Mixed cluster: B (no May data) must be EXCLUDED from the mean, not averaged in as 0.
  // A.months['2026-05'].weekend[8]=3, C.months['2026-05'].weekend[8]=0.5 -- mean of just those two = 1.75.
  icon = dash.clusterIconCreateFunction(fakeCluster(['A', 'B', 'C']));
  assert.strictEqual(
    icon.html, dash.clusterIconHTML(dash.divergingColor(1.75, domainMaxAt8), 3),
    'the no-data child must be excluded from the mean entirely, not counted as a zero'
  );

  // --- setDayType is trigger point #3 ---
  const refreshCountBefore3 = dash.getState().clusterLayer._refreshCount;
  dash.setDayType('weekday');
  assert.strictEqual(dash.getState().clusterLayer._refreshCount, refreshCountBefore3 + 1, 'setDayType must call refreshClusters() exactly once');

  // --- setMode is trigger point #4, and switches cluster coloring to the live scale ---
  const refreshCountBefore4 = dash.getState().clusterLayer._refreshCount;
  dash.setMode('live');
  assert.strictEqual(dash.getState().clusterLayer._refreshCount, refreshCountBefore4 + 1, 'setMode must call refreshClusters() exactly once');

  // live clusterIconCreateFunction: A is 50% full, B is 10% full (both usable).
  icon = dash.clusterIconCreateFunction(fakeCluster(['A', 'B']));
  assert.strictEqual(icon.html, dash.clusterIconHTML(dash.computeLiveClusterColor([50, 10]), 2));

  // live no-data cluster: station C has no live match at all.
  icon = dash.clusterIconCreateFunction(fakeCluster(['C']));
  assert.strictEqual(icon.html, dash.clusterIconHTML(null, 1), 'a cluster whose only child has no live match should render hollow');

  dash.setMode('historical'); // restore, so nothing below depends on live mode by accident
  dash.setPeriod('all');

  // --- switching back to individual view detaches the cluster layer again ---
  const viewIndividualClick = elements['view-individual']._listeners.click;
  viewIndividualClick();
  assert.strictEqual(dash.getState().viewMode, 'individual');
  assert.ok(sandbox._map._layers.includes(clusterState.individualLayer), 'switching back to individual view should reattach the individual layer');
  assert.ok(!sandbox._map._layers.includes(clusterState.clusterLayer), 'and detach the cluster layer');

  // And once detached, refreshClusters() must go back to being a safe no-op
  // rather than throwing -- the exact scenario that crashed every page load.
  const refreshCountBeforeFinal = dash.getState().clusterLayer._refreshCount;
  assert.doesNotThrow(() => dash.renderHour(9), 'renderHour must not throw again after returning to individual view');
  assert.strictEqual(dash.getState().clusterLayer._refreshCount, refreshCountBeforeFinal, 'refreshClusters must not touch the cluster group once it is detached');

  // --- Session 20: rebalancing-route layer ---

  // Pure functions first -- no Leaflet involved at all.
  // Per-property, not deepStrictEqual against a fresh outer-realm literal:
  // computeStopOffset() builds its return object inside the vm context, so
  // a structurally-identical object literal from this (outer) realm isn't
  // "reference-equal" to Node's assert -- primitives compare fine across
  // realms, objects/arrays constructed on each side don't.
  const offset0 = dash.computeStopOffset(0);
  assert.strictEqual(offset0.dLat, 0, 'a stop\'s first visit gets no lat offset');
  assert.strictEqual(offset0.dLng, 0, 'a stop\'s first visit gets no lng offset');
  const offset1 = dash.computeStopOffset(1);
  assert.ok(offset1.dLat !== 0 || offset1.dLng !== 0, 'a repeat visit must be offset from the real coordinate');
  const offset2 = dash.computeStopOffset(2);
  assert.notDeepStrictEqual(offset1, offset2, 'different repeat visits must land at different offsets, never stacked on each other');

  assert.deepStrictEqual(
    dash.assignVisitIndices([{ station_id: 'A' }, { station_id: 'B' }, { station_id: 'A' }]),
    [0, 0, 1],
    'A\'s first/second visits get indices 0 then 1; B\'s own first visit (in between) is independently 0, not affected by A\'s count'
  );

  assert.ok(dash.buildStopIconHTML('pickup', 3, '#7c4dbd').includes('border-radius:50%'), 'pickup should be a circle');
  assert.ok(dash.buildStopIconHTML('pickup', 3, '#7c4dbd').includes('>3<'), 'the stop-icon HTML should show the visit-order number');
  assert.ok(dash.buildStopIconHTML('dropoff', 5, '#7c4dbd').includes('rotate(45deg)'), 'dropoff should be a rotated diamond, not a circle');
  assert.ok(!dash.buildStopIconHTML('dropoff', 5, '#7c4dbd').includes('border-radius:50%'), 'dropoff must not look like a pickup circle -- shape, not color, carries this distinction');

  // route.json loaded successfully (FAKE_ROUTE_PAYLOAD) -- the toggle should be revealed, the layer built.
  const routeState = dash.getState();
  assert.ok(routeState.route, 'route.json should have loaded');
  assert.ok(routeState.routeLayer, 'the route layer should be built once route.json loads');
  assert.strictEqual(routeState.routeLayer._layers.length, 4, '3 stop markers + 1 polyline for one truck with 3 stops');
  assert.ok(!elements['route-toggle-wrap'].classList.contains('hidden'), 'the route toggle should be revealed once route.json loads');

  const stopMarkers = routeState.routeLayer._layers.filter(l => l._kind !== 'polyline');
  assert.strictEqual(stopMarkers.length, 3, 'one marker per stop, including repeat visits');
  // Same cross-realm reasoning as offset0 above -- compare the two
  // coordinate values, not the array object itself, against an outer-realm literal.
  assert.strictEqual(stopMarkers[0]._latlng[0], 40.75, 'station A\'s first visit should sit at its real latitude, unoffset');
  assert.strictEqual(stopMarkers[0]._latlng[1], -73.98, 'station A\'s first visit should sit at its real longitude, unoffset');
  assert.notDeepStrictEqual(
    stopMarkers[2]._latlng, stopMarkers[0]._latlng,
    'station A\'s second visit (a real repeat in the fixture, mirroring the real data) must render at an offset position, not exactly on top of the first'
  );

  // Clicking a route stop marker opens that station's detail panel, same as any other marker.
  const routeStopClick = stopMarkers[1]._listeners.click; // the dropoff at station B
  assert.ok(routeStopClick, 'no click listener registered on a route stop marker');
  routeStopClick();
  assert.strictEqual(dash.getState().selectedId, 'B', 'clicking a route stop should select its station, same as any other marker click');
  assert.strictEqual(dash.getState().infoTab, 'detail', 'clicking a route stop should switch to the Station detail tab too, same as any other selection');

  // --- route toggle: off by default ---
  assert.strictEqual(routeState.routeVisible, false, 'route should be off by default');
  assert.ok(!sandbox._map._layers.includes(routeState.routeLayer), 'route layer should not be on the map before the toggle is used');

  const routeToggleClick = elements['route-toggle-btn']._listeners.click;
  assert.ok(routeToggleClick, 'no click listener registered on the route toggle button');
  routeToggleClick();
  assert.strictEqual(dash.getState().routeVisible, true);
  assert.ok(sandbox._map._layers.includes(routeState.routeLayer), 'route layer should be added to the map once toggled on');
  assert.strictEqual(elements['route-toggle-btn'].textContent, 'Hide route');
  assert.ok(elements['route-toggle-btn'].classList.contains('active'));

  // --- switching to live mode removes the route from the map, without clearing the user's preference ---
  const modeLiveClick2 = elements['mode-live']._listeners.click;
  modeLiveClick2();
  assert.ok(!sandbox._map._layers.includes(routeState.routeLayer), 'route layer must leave the map in live mode -- route planning is historical-only');
  assert.strictEqual(dash.getState().routeVisible, true, 'the toggle preference itself must not be cleared by switching modes');

  const modeHistoricalClick2 = elements['mode-historical']._listeners.click;
  modeHistoricalClick2();
  assert.ok(sandbox._map._layers.includes(routeState.routeLayer), 'returning to historical mode should reapply the route automatically, without re-clicking the toggle');

  // --- turning the toggle off removes it again ---
  routeToggleClick();
  assert.strictEqual(dash.getState().routeVisible, false);
  assert.ok(!sandbox._map._layers.includes(routeState.routeLayer));
  assert.strictEqual(elements['route-toggle-btn'].textContent, 'Show route');

  // --- Session 20, Part B: model-eval footer, collapsed by default ---
  assert.strictEqual(dash.getState().modelEvalExpanded, false, 'model-eval footer should be collapsed by default');
  assert.ok(elements['model-eval-content'].classList.contains('hidden'), 'model-eval content should be hidden by default');
  assert.strictEqual(elements['model-eval-toggle'].textContent, 'Model performance ▸');

  const modelEvalClick = elements['model-eval-toggle']._listeners.click;
  assert.ok(modelEvalClick, 'no click listener registered on the model-eval toggle');
  modelEvalClick();
  assert.strictEqual(dash.getState().modelEvalExpanded, true);
  assert.ok(!elements['model-eval-content'].classList.contains('hidden'), 'model-eval content should show once expanded');
  assert.strictEqual(elements['model-eval-toggle'].textContent, 'Model performance ▾');

  modelEvalClick();
  assert.strictEqual(dash.getState().modelEvalExpanded, false);
  assert.ok(elements['model-eval-content'].classList.contains('hidden'), 'model-eval content should collapse again on a second click');
  assert.strictEqual(elements['model-eval-toggle'].textContent, 'Model performance ▸');

  console.log('All dashboard slider smoke tests passed.');
}

// Separate scenario, separate sandbox: fetch() rejecting the way it does
// under file:// (no response ever comes back) should produce the
// actionable "serve this over http" message, not a raw/undefined error --
// the exact bug reported ("Failed to load data/flows.json: undefined").
async function testFileProtocolFetchFailure() {
  const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
  const script = extractInlineScript(html);

  const sandbox = buildSandbox();
  sandbox.location = { protocol: 'file:' };
  sandbox.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

  const context = vm.createContext(sandbox);
  vm.runInContext(script, context, { filename: 'dashboard.html (inline script, file:// scenario)' });

  await context.__dashboard.ready;

  assert.strictEqual(
    sandbox._elements['status'].textContent,
    'This dashboard needs to be served over http, not opened directly. Run: python3 -m http.server, then open http://localhost:8000/dashboard.html',
    'a file:// fetch failure should show the actionable serving instructions, not a raw/undefined error'
  );
  assert.ok(
    sandbox._elements['status'].classList.contains('error'),
    'the banner should get the .error modifier class -- distinct visual treatment from the informational/loading state'
  );
  console.log('file:// fetch-failure smoke test passed.');
}

// Separate scenario: route.json specifically 404s while flows.json and
// live_status.json both succeed -- confirms graceful degradation (the
// route control simply never appears, nothing else on the dashboard is
// affected) rather than either crashing or silently substituting
// something in the missing file's place.
async function testRouteJsonMissing() {
  const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
  const script = extractInlineScript(html);

  const sandbox = buildSandbox();
  sandbox.fetch = url => {
    if (url.includes('route')) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('not found')) });
    }
    const payload = url.includes('live_status') ? FAKE_LIVE_PAYLOAD : FAKE_PAYLOAD;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  };

  const context = vm.createContext(sandbox);
  vm.runInContext(script, context, { filename: 'dashboard.html (inline script, route.json missing scenario)' });

  await context.__dashboard.ready;

  const state = context.__dashboard.getState();
  assert.strictEqual(state.route, null, 'a route.json fetch failure should resolve to null, not throw or reject the whole load');
  assert.strictEqual(state.routeLayer, null, 'no route layer should be built when route.json is missing');
  assert.ok(
    sandbox._elements['route-toggle-wrap'].classList.contains('hidden'),
    'the route toggle control must not appear at all when route.json is missing -- graceful degradation, same rule as every other analysis layer'
  );
  assert.strictEqual(Object.keys(state.markers).length, 3, 'the rest of the dashboard should load completely normally even though route.json failed');
  assert.ok(
    sandbox._elements['status'].classList.contains('hidden'),
    'route.json being missing alone must not trigger the fatal error banner -- only flows.json/live_status.json failures do that'
  );
  console.log('route.json-missing graceful-degradation smoke test passed.');
}

main()
  .then(testFileProtocolFetchFailure)
  .then(testRouteJsonMissing)
  .catch(err => {
    console.error('FAILED:', err.message);
    process.exit(1);
  });

// Shared Node test harness for dashboard2.html.
//
// Stubs the DOM, Leaflet, and the browser globals well enough to execute
// dashboard2.html's real inline <script> unmodified inside Node's `vm`,
// then hands back the running dashboard so a test can drive it like a
// browser would. Zero npm dependencies, no build step -- same approach as
// the older tests/test_dashboard_slider.js, which is NOT built on this
// module (it reads dashboard.html and has its own, differently-shaped
// sandbox; leave it alone).
//
// Extracted when the second dashboard2 test appeared (Session 48), and kept
// when Session 49 replaced both early tests with one map-interaction suite.
// If a new test needs a capability this doesn't have, add it here with a
// comment saying which dashboard behavior demanded it.
//
// TWO GOTCHAS THIS HARNESS EXISTS TO MANAGE (both cost real debugging time):
//
// 1. getElementById AUTO-CREATES elements. That is what makes the stub
//    small, but it means the stub can never prove a control is *gone* --
//    ask for a deleted button and it invents one. Two tools for that:
//    assert against the raw HTML string (see readDashboard2), and use
//    sandbox._requestedIds, which logs every id the script ever looked up.
//
// 2. dashboard2's top-level init .catch() LOGS AND CONTINUES rather than
//    rejecting. A gap in this stub therefore shows up as a passing test
//    running against half-initialized state, not as a failure. loadDashboard2()
//    asserts nothing was console.error'd; do not remove that check.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// Session 64: dashboard2.html was promoted to dashboard.html (the old
// pre-v2 dashboard.html retired) -- see PROGRESS.md. Kept the DASHBOARD2_*
// naming here and in every test_dashboard2_*.js file rather than a
// blanket rename: those names now identify which generation of the
// dashboard this harness/suite was built for (Session 47+), same reason
// this project already keeps other Session-scoped names after later
// renames (see the Investigator -> Scenario Planner note).
const DASHBOARD2_PATH = path.join(__dirname, '..', 'dashboard.html');

function readDashboard2() {
  return fs.readFileSync(DASHBOARD2_PATH, 'utf8');
}

function extractInlineScript(html) {
  // dashboard2.html has three <script> tags: two Leaflet CDN ones (both
  // carry src=, no body) and the dashboard's own inline one. Match the
  // first bare <script>.
  const match = html.match(/<script>\n([\s\S]*?)<\/script>/);
  if (!match) throw new Error('could not find inline <script> block in dashboard2.html');
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

function makeContext(overrides) {
  return Object.assign({
    near_nycha: 0, near_school: 0,
    nycha_dist_m: 900, nycha_nearest: 'Test NYCHA',
    school_dist_m: 850, school_nearest: 'Test School',
    subway_dist_m: 300, subway_nearest: 'Test Station', transit_gap: 0,
  }, overrides);
}

// Three stations: enough spread for percentile/computeDomainMax to have a
// real domain, and far enough apart (tens of pixels at z11) that
// click-tolerance tests can target one without ambiguity.
const FAKE_FLOWS = {
  granularity: { months: ['2026-04', '2026-05'], seasons: ['spring'] },
  equity_join: {
    layers: {
      nycha: { domain: 'data.cityofnewyork.us', dataset_id: 'phvi-damg', n_records: 216, threshold_m: 300 },
      school: { domain: 'data.cityofnewyork.us', dataset_id: 'wg9x-4ke6', n_records: 1899, threshold_m: 300 },
      subway: { domain: 'data.ny.gov', dataset_id: 'i9wp-a4ja', n_records: 2120, threshold_m: 800 },
    },
  },
  stations: {
    A: {
      name: 'Station A', lat: 40.75, lng: -73.98,
      weekday: makeCurve({ 8: 12, 17: -6 }), weekend: makeCurve({ 8: 2 }),
      seasons: {}, months: {}, cluster: 0, cluster_name: 'Commuter core (fills AM, drains PM)',
      context: makeContext({ near_nycha: 1, nycha_dist_m: 120 }),
    },
    B: {
      name: 'Station B', lat: 40.71, lng: -74.01,
      weekday: makeCurve({ 8: -9, 17: 4 }), weekend: makeCurve({ 8: -1 }),
      seasons: {}, months: {}, cluster: 1, cluster_name: 'Residential feeder (drains AM, fills PM)',
      context: makeContext({ near_school: 1, school_dist_m: 90 }),
    },
    C: {
      name: 'Station C', lat: 40.68, lng: -73.95,
      weekday: makeCurve({ 8: 1 }), weekend: makeCurve({ 8: 0.5 }),
      seasons: {}, months: {}, cluster: -1, cluster_name: 'Low signal (excluded from clustering)',
      context: makeContext({ subway_dist_m: 1200, transit_gap: 1 }),
    },
  },
};

const FAKE_LIVE = {
  last_updated: '2026-07-23T23:07:17+00:00',
  n_dropped: 0,
  stations: {
    A: { capacity: 30, bikes_available: 0, docks_available: 30, is_renting: 1, is_returning: 1 },
    B: { capacity: 25, bikes_available: 25, docks_available: 0, is_renting: 1, is_returning: 1 },
    C: { capacity: 20, bikes_available: 10, docks_available: 10, is_renting: 1, is_returning: 1 },
  },
};

// Raw GBFS shapes, deliberately NOT the flattened live_status.json shape: the
// crosswalk between them (station_status has GBFS ids, station_information has
// the "6433.01" short_name flows.json uses) is the part worth testing.
// Station D exists in status but not information -- the drop path.
const FAKE_LIVE_FEED = {
  status: {
    last_updated: 1787000000, // 2026-08-21T03:33:20Z
    ttl: 60,
    data: {
      stations: [
        { station_id: 'g-a', num_bikes_available: 5, num_docks_available: 25, is_renting: 1, is_returning: 1 },
        { station_id: 'g-b', num_bikes_available: 0, num_docks_available: 25, is_renting: 1, is_returning: 1 },
        { station_id: 'g-c', num_bikes_available: 20, num_docks_available: 0, is_renting: 1, is_returning: 1 },
        { station_id: 'g-d', num_bikes_available: 3, num_docks_available: 3, is_renting: 1, is_returning: 1 },
        // 0 bikes AND 0 docks -- possible whenever bikes + docks < capacity
        // (docks out of service). Un-rentable and un-returnable at once, and
        // the case that made the ribbon and the distribution disagree.
        { station_id: 'g-e', num_bikes_available: 0, num_docks_available: 0, is_renting: 1, is_returning: 1 },
      ],
    },
  },
  information: {
    data: {
      stations: [
        { station_id: 'g-a', short_name: 'A', capacity: 30 },
        { station_id: 'g-b', short_name: 'B', capacity: 25 },
        { station_id: 'g-c', short_name: 'C', capacity: 20 },
        { station_id: 'g-e', short_name: 'E', capacity: 15 },
      ],
    },
  },
};

// Mirrors data/reliability.json's real shape (pipeline/reliability.py). The
// rates are deliberately round numbers unlike the real 12.2%, so a test can
// tell a computed value apart from a hardcoded one at a glance.
const FAKE_RELIABILITY = {
  window: {
    start: '2026-07-13T18:22:42+00:00',
    end: '2026-07-26T15:07:26+00:00',
    days: 12,
    n_snapshots: 190,
    median_gap_minutes: 85.3,
    max_gap_minutes: 236.0,
  },
  system: {
    unusable_rate: 0.25,
    n_observations: 4000,
    n_unusable: 1000,
    n_empty: 600,
    n_full: 400,
    n_offline_excluded: 111,
  },
  per_station_summary: {
    n_stations: 3, n_rankable: 3, min_observations: 20,
    median_rate: 0.2, max_rate: 0.4,
  },
  stations: {
    A: { unusable_rate: 0.4, n_observations: 100, n_unusable: 40 },
    B: { unusable_rate: 0.2, n_observations: 100, n_unusable: 20 },
    C: { unusable_rate: 0.1, n_observations: 100, n_unusable: 10 },
  },
  caveat: 'Test fixture caveat.',
};

// Mirrors data/fleet_scenarios.json's real shape. Since Session 49 removed
// the drawn route, only the per-scenario COUNTS are read by the dashboard
// (renderFleetStats); the per-stop trucks[] array is kept because the real
// artifact has it and a payload that omitted it would be an unfaithful
// fixture. Every scenario N services N stops, so a test can tell scenarios
// apart by shape alone.
function makeFleetScenario(n) {
  const stops = [];
  const ids = ['A', 'B', 'C'];
  for (let i = 0; i < n; i += 1) {
    const id = ids[i % ids.length];
    stops.push({
      station_id: id,
      name: FAKE_FLOWS.stations[id].name,
      lat: FAKE_FLOWS.stations[id].lat,
      lng: FAKE_FLOWS.stations[id].lng,
      action: i % 2 === 0 ? 'pickup' : 'dropoff',
      amount: 5,
      running_load: 5,
    });
  }
  return {
    period: 'all', am_hours: [6, 7, 8, 9], capacity: 20, max_stops: 45,
    n_trucks_requested: n, n_trucks_used: n,
    n_deficit_flagged: 30, n_deficit_serviced: n,
    n_surplus_flagged: 20, n_surplus_serviced: n,
    any_truck_capped: false,
    depot_assumption: 'Test depot assumption',
    max_stops_note: 'Test max-stops note',
    trucks: [{ truck: 1, capped: false, stops }],
  };
}

const FAKE_FLEET_SCENARIOS = {
  generated_at: '2026-07-23T23:44:40.461367+00:00',
  fleet_sizes: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  period: 'all', capacity: 20, max_stops: 45,
  notes: 'Test fleet scenarios payload',
  scenarios: Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => [String(n), makeFleetScenario(n)])),
};

function makeElementStub(id) {
  const el = {
    id,
    textContent: '',
    innerHTML: '',
    style: {},
    value: undefined,
    disabled: false,
    tabIndex: 0,
    _listeners: {},
    _classes: new Set(),
    _attrs: {},
    _children: [],
    addEventListener(event, handler) { el._listeners[event] = handler; },
    removeEventListener(event) { delete el._listeners[event]; },
    setAttribute(name, value) { el._attrs[name] = value; },
    getAttribute(name) { return el._attrs[name]; },
    removeAttribute(name) { delete el._attrs[name]; },
    appendChild(child) { el._children.push(child); return child; },
    focus() {},
    click() { if (el._listeners.click) el._listeners.click(); },
    getBoundingClientRect() { return { top: 0, left: 0, width: 280, height: 40, right: 280, bottom: 40 }; },
    classList: {
      toggle(cls, force) { if (force) el._classes.add(cls); else el._classes.delete(cls); },
      add(cls) { el._classes.add(cls); },
      remove(cls) { el._classes.delete(cls); },
      contains(cls) { return el._classes.has(cls); },
    },
  };
  return el;
}

// options:
//   flows / live / fleetScenarios / reliability -- payload, or null to make that fetch 404
//   liveFeed -- { status, information } GBFS payloads, or null (default) so the
//               live feed 404s and the dashboard falls back to `live`
//   startZoom -- initial map zoom (default 11, the dashboard's own city-wide default)
function buildSandbox(options = {}) {
  const {
    flows = FAKE_FLOWS,
    live = FAKE_LIVE,
    fleetScenarios = null, // default off: most tests don't need it, and 404ing it exercises graceful degradation
    reliability = null,    // same -- default off, so every existing test keeps exercising the ribbon's missing-file path
    liveFeed = null,       // default off: the GBFS feed 404s, so tests exercise the snapshot fallback unless they opt in
    startZoom = 11,
  } = options;

  const elements = {};
  const errors = [];
  const requestedIds = [];
  const fetchedUrls = [];
  const getElementById = id => {
    requestedIds.push(id);
    if (!elements[id]) elements[id] = makeElementStub(id);
    return elements[id];
  };
  elements['hour-slider'] = makeElementStub('hour-slider');
  elements['hour-slider'].value = '8';

  const boundsStub = { extend() { return this; }, isValid() { return true; }, pad() { return this; } };
  const layerStub = { addTo() { return this; } };

  function makeMarkerStub(latlng, opts) {
    const marker = {
      _latlng: latlng,
      _opts: Object.assign({}, opts),
      _radius: opts && opts.radius,
      _listeners: {},
      addTo() { return marker; },
      setStyle(newOpts) { Object.assign(marker._opts, newOpts); return marker; },
      setRadius(radius) { marker._radius = radius; return marker; }, // dashboard2's zoomend handler resizes every marker
      getRadius() { return marker._radius; },
      getLatLng() { return { lat: latlng[0], lng: latlng[1] }; }, // click-tolerance search reads this for every marker
      _fire(event, payload) { if (marker._listeners[event]) marker._listeners[event](payload); },
      on(event, handler) { marker._listeners[event] = handler; return marker; },
      bindTooltip() { return marker; },
      setTooltipContent(content) { marker._tooltipContent = content; return marker; },
      // Session 64: the hover-tolerance mousemove handler calls these
      // directly (a near-miss never fires the marker's own native
      // mouseover/mouseout, so it can't rely on Leaflet's built-in tooltip
      // show/hide) -- tracked on the stub so a test can assert open/closed.
      openTooltip(latlng) { marker._tooltipOpen = true; marker._tooltipLatLng = latlng; return marker; },
      closeTooltip() { marker._tooltipOpen = false; return marker; },
    };
    return marker;
  }

  const mapStub = {
    _zoom: startZoom,
    _layers: [],
    _handlers: {},
    setView() { return mapStub; },
    fitBounds() { return mapStub; },
    setMaxBounds() { return mapStub; },
    // dashboard2 clamps with map.setMinZoom(map.getBoundsZoom(bounds) - 1).
    // 10 puts minZoom at 9 -- below every zoom the tests drive, so the
    // clamp never interferes.
    getBoundsZoom() { return 10; },
    setMinZoom(zoom) { mapStub._minZoom = zoom; return mapStub; },
    getZoom() { return mapStub._zoom; },
    // Deterministic stand-in for Leaflet's projection, enough for the
    // click-tolerance search to measure real screen distances that shrink
    // and grow with zoom the way the browser's do. Uses one scale for both
    // axes (real Mercator stretches latitude by 1/cos(lat)); at NYC's
    // latitude that is a ~30% distortion, which does not matter for
    // "is this within N pixels" assertions but would if a test ever cared
    // about exact geometry.
    latLngToContainerPoint(latlng) {
      const lat = Array.isArray(latlng) ? latlng[0] : latlng.lat;
      const lng = Array.isArray(latlng) ? latlng[1] : latlng.lng;
      const pxPerDegree = (256 * Math.pow(2, mapStub._zoom)) / 360;
      return { x: (lng + 180) * pxPerDegree, y: (90 - lat) * pxPerDegree };
    },
    on(event, handler) { mapStub._handlers[event] = handler; return mapStub; },
    // Test-side helper: dispatch a map click at a lat/lng, as Leaflet does
    // for a click that did NOT land on an interactive layer.
    _clickAt(lat, lng) {
      if (mapStub._handlers.click) mapStub._handlers.click({ latlng: { lat, lng } });
    },
    // Test-side helper: dispatch a map mousemove, as Leaflet does for cursor
    // movement that isn't over an interactive layer -- exercises the
    // hover-tolerance handler the same way _clickAt exercises click tolerance.
    _mousemoveAt(lat, lng) {
      if (mapStub._handlers.mousemove) mapStub._handlers.mousemove({ latlng: { lat, lng } });
    },
    // Test-side helper: dispatch the map's own mouseout (cursor leaves the
    // map entirely), distinct from a marker's mouseout.
    _mouseoutMap() {
      if (mapStub._handlers.mouseout) mapStub._handlers.mouseout();
    },
    // Test-side helper: set the zoom and dispatch zoomend as Leaflet would.
    _zoomTo(zoom) {
      mapStub._zoom = zoom;
      if (mapStub._handlers.zoomend) mapStub._handlers.zoomend();
    },
    // Session 64: the CARTO tile source was replaced with two stacked Esri
    // tile layers (base + a separate label overlay), and the label layer
    // needs its own pane (see dashboard2's own comment there for why) --
    // real Leaflet's createPane returns a DOM element; a plain object with
    // a style bag is enough for the two property assignments dashboard2
    // makes on it.
    createPane() { return { style: {} }; },
    addLayer(layer) { mapStub._layers.push(layer); return mapStub; },
    removeLayer(layer) { mapStub._layers = mapStub._layers.filter(l => l !== layer); return mapStub; },
    hasLayer(layer) { return mapStub._layers.includes(layer); },
    getContainer() { return { clientWidth: 900, clientHeight: 600 }; },
  };

  const L = {
    map() { return mapStub; },
    tileLayer() { return layerStub; },
    latLngBounds() { return boundsStub; },
    circleMarker(latlng, opts) { return makeMarkerStub(latlng, opts); },
    marker(latlng, opts) { return makeMarkerStub(latlng, opts); },
    polyline(latlngs, opts) { return { _kind: 'polyline', _latlngs: latlngs, _opts: opts }; },
    layerGroup(layers) { return { _kind: 'group', _layers: layers }; },
    // NOTE: there is deliberately no markerClusterGroup stub. Session 49
    // removed clustering from dashboard2 entirely, so calling it would be a
    // regression -- and with no stub, such a regression fails loudly here
    // instead of silently working against a fake.
    divIcon(opts) { return opts; },
    point(x, y) { return { x, y }; },
    control: { zoom(opts) { return { _opts: opts, addTo() { return this; } }; } },
  };

  const notFound = () => Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('no body')) });
  const found = payload => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });

  const sandbox = {
    document: {
      getElementById,
      activeElement: null,
      // Full element stub, not a thin object: dashboard2 builds worst-list
      // rows with createElement and then calls addEventListener on them.
      createElement(tag) { return makeElementStub(`<${tag}>`); },
      addEventListener() {},
    },
    window: {
      innerWidth: 1440,
      addEventListener() {},
      matchMedia() { return { matches: false, addEventListener() {} }; },
    },
    location: { protocol: 'http:', search: '', origin: 'http://localhost:8899', pathname: '/dashboard2.html' },
    history: { replaceState() {} },
    L,
    URL,
    URLSearchParams,
    Date,
    fetch(url) {
      fetchedUrls.push(url);
      if (url.includes('flows')) return flows ? found(flows) : notFound();
      if (url.includes('live_status')) return live ? found(live) : notFound();
      if (url.includes('fleet_scenarios')) return fleetScenarios ? found(fleetScenarios) : notFound();
      if (url.includes('station_status')) return liveFeed ? found(liveFeed.status) : notFound();
      if (url.includes('station_information')) return liveFeed ? found(liveFeed.information) : notFound();
      if (url.includes('reliability')) return reliability ? found(reliability) : notFound();
      // Every other optional file 404s, exercising dashboard2's own
      // graceful-degradation paths on each load.
      return notFound();
    },
    requestAnimationFrame(cb) { cb(); return 0; },
    setInterval() { return 0; },
    clearInterval() {},
    setTimeout(cb) { cb(); return 0; },
    clearTimeout() {},
    // Recording console -- see gotcha 2 in this file's header.
    console: Object.assign(Object.create(console), {
      error(...args) { errors.push(args); console.error(...args); },
    }),
    Math, Object, Number, String, Array, JSON, Promise,
    isNaN, parseFloat, parseInt,
    globalThis: undefined,
  };
  sandbox.globalThis = sandbox;
  sandbox._elements = elements;
  sandbox._requestedIds = requestedIds;
  sandbox._fetchedUrls = fetchedUrls;
  sandbox._errors = errors;
  sandbox._map = mapStub;
  return sandbox;
}

// Runs dashboard2's inline script in a fresh sandbox, waits for its async
// init chain to settle, and asserts the init completed cleanly. Returns
// { dash, sandbox, map, html }.
async function loadDashboard2(options = {}) {
  const html = readDashboard2();
  const script = extractInlineScript(html);
  const sandbox = buildSandbox(options);
  const context = vm.createContext(sandbox);
  vm.runInContext(script, context, { filename: 'dashboard2.html (inline script)' });

  const dash = context.__dashboard;
  assert.ok(dash, '__dashboard test hook was not exposed by dashboard2.html');

  // Two turns is enough for the Promise.all chain plus its .then().
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));

  assert.deepStrictEqual(
    sandbox._errors, [],
    'dashboard2 init logged an error -- assertions would run on half-initialized state'
  );

  return { dash, sandbox, map: sandbox._map, html };
}

module.exports = {
  DASHBOARD2_PATH,
  readDashboard2,
  extractInlineScript,
  zeros,
  makeCurve,
  makeContext,
  makeElementStub,
  makeFleetScenario,
  buildSandbox,
  loadDashboard2,
  FAKE_FLOWS,
  FAKE_LIVE,
  FAKE_FLEET_SCENARIOS,
  FAKE_RELIABILITY,
  FAKE_LIVE_FEED,
};

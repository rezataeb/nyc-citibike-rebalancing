// Node test for video.html: the clickable end card that copies a video's
// last slide. What this guards: the card's default hidden state is set in JS
// (CLAUDE.md standing rule), the page picks the right video (data-video, or
// ?v= override), the card appears on each video's last slide and after the
// end, seeking back hides it, Replay restarts, and the links point where the
// slide says they do.
//
// Run with:
//   node tests/test_video_page.js

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'video.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function makeElement() {
  const classes = new Set();
  const listeners = {};
  return {
    children: [],
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: c => classes.has(c),
    },
    addEventListener: (e, fn) => { (listeners[e] = listeners[e] || []).push(fn); },
    appendChild(child) { this.children.push(child); },
    fire: e => (listeners[e] || []).forEach(fn => fn()),
  };
}

function load({ dataVideo = 'hiring', search = '' } = {}) {
  const ids = ['tour', 'endcard', 'replay', 'tour-title', 'tour-sub'];
  const elements = Object.fromEntries(ids.map(id => [id, makeElement()]));
  Object.assign(elements.tour, { currentTime: 0, duration: 58.67, ended: false, played: 0, play() { this.played += 1; } });
  const document = { getElementById: id => elements[id], createElement: () => ({}), body: { dataset: { video: dataVideo } } };
  const sandbox = { document, location: { search }, URLSearchParams, module: { exports: {} }, isFinite };
  vm.runInNewContext(script, sandbox);
  return { elements, api: sandbox.module.exports };
}

function testDefaultStateIsSetInJs() {
  assert.ok(!/id="endcard"[^>]*class="[^"]*hidden/.test(html) && !/class="[^"]*hidden[^"]*"[^>]*id="endcard"/.test(html),
    'the markup must not carry the hidden class; JS sets it');
  const { elements } = load();
  assert.ok(elements.endcard.classList.contains('hidden'), 'end card hidden on load');
}

function testVideoChoice() {
  const { api } = load();
  assert.strictEqual(api.pickVideo('', 'hiring'), 'hiring');
  assert.strictEqual(api.pickVideo('', 'dot'), 'dot', 'data-video picks the default');
  assert.strictEqual(api.pickVideo('?v=dot', 'hiring'), 'dot', '?v= overrides');
  assert.strictEqual(api.pickVideo('?v=nope', 'dot'), 'dot', 'an unknown ?v= is ignored');
  assert.strictEqual(api.pickVideo('', 'nope'), 'hiring', 'an unknown default falls back to hiring');

  const dot = load({ dataVideo: 'dot' }).elements;
  assert.strictEqual(dot.tour.src, 'media/dot.mp4');
  assert.strictEqual(dot.tour.poster, 'media/dot_poster.jpg');
  assert.strictEqual(dot.tour.children[0].src, 'media/dot.vtt', 'captions track for the same video');
  assert.strictEqual(dot['tour-title'].textContent, 'A 90-second tour');
  assert.strictEqual(load().elements.tour.src, 'media/hiring.mp4');
  assert.ok(html.includes('<body data-video="hiring">'), 'the repo page defaults to the hiring video');
}

function testRule() {
  const { api } = load();
  const d = 58.67;
  const lead = api.endCardLead('hiring');
  assert.strictEqual(lead, 7, 'hiring: 8 s last slide, card 1 s in');
  assert.strictEqual(api.endCardLead('dot'), 4, 'dot: 5 s last slide, card 1 s in');
  assert.strictEqual(api.endCardShouldShow(10, d, false, lead), false);
  assert.strictEqual(api.endCardShouldShow(d - lead - 0.1, d, false, lead), false);
  assert.strictEqual(api.endCardShouldShow(d - lead, d, false, lead), true);
  assert.strictEqual(api.endCardShouldShow(0, d, true, lead), true, 'always shown once ended');
  assert.strictEqual(api.endCardShouldShow(5, NaN, false, lead), false, 'no duration yet (metadata not loaded)');
}

function testPlaybackShowsAndHides() {
  const { elements } = load();
  const { tour, endcard } = elements;
  tour.currentTime = 30; tour.fire('timeupdate');
  assert.ok(endcard.classList.contains('hidden'), 'hidden mid-video');
  tour.currentTime = 54; tour.fire('timeupdate');
  assert.ok(!endcard.classList.contains('hidden'), 'shown on the last slide');
  tour.currentTime = 20; tour.fire('seeked');
  assert.ok(endcard.classList.contains('hidden'), 'seeking back hides it');
  tour.currentTime = 58.67; tour.ended = true; tour.fire('ended');
  assert.ok(!endcard.classList.contains('hidden'), 'shown after the end');
  tour.ended = false;
  elements.replay.fire('click');
  assert.strictEqual(tour.currentTime, 0, 'replay rewinds');
  assert.strictEqual(tour.played, 1, 'replay plays');
  assert.ok(endcard.classList.contains('hidden'), 'replay hides the card');
}

function testLinksMatchTheSlide() {
  const card = html.match(/<div class="endcard"[\s\S]*?<\/div>\s*<\/div>/)[0];
  assert.ok(card.includes('Try it for free'), 'the card says what the slide says');
  for (const href of ['href="https://nyc-citibike-rebalancing.vercel.app/"', 'href="https://www.linkedin.com/in/reza-taeb/"', 'href="mailto:taeb.reza@gmail.com"',
    'href="https://github.com/rezataeb/nyc-citibike-rebalancing"']) {
    assert.ok(card.includes(href), `end card links ${href}`);
  }
  assert.ok(card.includes('Reza Taeb'), 'name on the card');
}

testDefaultStateIsSetInJs();
testVideoChoice();
testRule();
testPlaybackShowsAndHides();
testLinksMatchTheSlide();
console.log('video page test passed (end card default, video choice, rule, playback, links).');

// Node test for video.html: the clickable end card that copies the video's
// last slide. What this guards: the card's default hidden state is set in JS
// (CLAUDE.md standing rule), it appears on the last slide and after the end,
// seeking back hides it, Replay restarts, and the links point where the
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
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: c => classes.has(c),
    },
    addEventListener: (e, fn) => { (listeners[e] = listeners[e] || []).push(fn); },
    fire: e => (listeners[e] || []).forEach(fn => fn()),
  };
}

function load() {
  const elements = { tour: makeElement(), endcard: makeElement(), replay: makeElement() };
  Object.assign(elements.tour, { currentTime: 0, duration: 58.67, ended: false, played: 0, play() { this.played += 1; } });
  const sandbox = { document: { getElementById: id => elements[id] }, module: { exports: {} }, isFinite };
  vm.runInNewContext(script, sandbox);
  return { elements, api: sandbox.module.exports };
}

function testDefaultStateIsSetInJs() {
  assert.ok(!/id="endcard"[^>]*class="[^"]*hidden/.test(html) && !/class="[^"]*hidden[^"]*"[^>]*id="endcard"/.test(html),
    'the markup must not carry the hidden class; JS sets it');
  const { elements } = load();
  assert.ok(elements.endcard.classList.contains('hidden'), 'end card hidden on load');
}

function testRule() {
  const { api } = load();
  const d = 58.67;
  assert.strictEqual(api.endCardShouldShow(10, d, false), false);
  assert.strictEqual(api.endCardShouldShow(d - api.END_CARD_LEAD_S - 0.1, d, false), false);
  assert.strictEqual(api.endCardShouldShow(d - api.END_CARD_LEAD_S, d, false), true);
  assert.strictEqual(api.endCardShouldShow(0, d, true), true, 'always shown once ended');
  assert.strictEqual(api.endCardShouldShow(5, NaN, false), false, 'no duration yet (metadata not loaded)');
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
  assert.ok(html.includes('src="media/hiring.mp4"') && html.includes('src="media/hiring.vtt"'), 'video and captions wired');
}

testDefaultStateIsSetInJs();
testRule();
testPlaybackShowsAndHides();
testLinksMatchTheSlide();
console.log('video page test passed (end card default, rule, playback, links).');

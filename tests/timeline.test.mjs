import test from "node:test";
import assert from "node:assert/strict";
import {
  buildTimeline, findActive, wordsToLight, seekTargetFor, sentenceIndex, sentenceStartAt,
  stepSpeed, cycleSpeed, formatTime, formatSpeed, parsePosition, parsePrefs, isTypingTarget, clamp,
} from "../js/timeline.js";

// [text, page, flags, q, s, e, g]
const words = [
  ["Title", 1, 0, "n"],
  ["Do", 1, 0, "w", 1.0, 1.2],
  ["nothing", 1, 0, "w", 1.25, 1.6],
  ["↳", 1, 0, "x"],
  ["Hormozi", 1, 0, "a", 2.0, 2.5, 1],
  ["plan", 1, 0, "a", 2.0, 2.5, 1],
  ["now.", 1, 0, "w", 2.6, 2.9],
  ["Later", 1, 0, "w", 6.0, 6.3],
];
const sentences = [[0, 1], [1, 7, 1.0, 2.9], [7, 8, 6.0, 6.3]];
const tl = buildTimeline(words);

test("timeline holds only timed words, sorted", () => {
  assert.deepEqual(tl.idx, [1, 2, 4, 5, 6, 7]);
  assert.deepEqual([...tl.starts], [1.0, 1.25, 2.0, 2.0, 2.6, 6.0]);
});

test("active word follows the playback position", () => {
  assert.equal(findActive(tl, 0.5), -1);         // before speech
  assert.equal(findActive(tl, 1.0), 1);
  assert.equal(findActive(tl, 1.3), 2);
  assert.equal(findActive(tl, 2.7), 6);
  assert.equal(findActive(tl, 1.22), 2, "highlight leads by a few ms");
});

test("short gaps hold the word, long pauses clear it", () => {
  assert.equal(findActive(tl, 3.3), 6);          // 0.4 s after "now." ends
  assert.equal(findActive(tl, 4.5), -1);         // long pause
  assert.equal(findActive(tl, 6.1), 7);
});

test("approximate groups light together", () => {
  const wi = findActive(tl, 2.2);
  assert.deepEqual(wordsToLight(tl, words, wi), [4, 5]);
  assert.deepEqual(wordsToLight(tl, words, 2), [2]);
});

test("clicks seek only to audio-backed words", () => {
  assert.equal(seekTargetFor(words, 0), null);   // not narrated
  assert.equal(seekTargetFor(words, 3), null);   // symbol
  assert.equal(seekTargetFor(words, 2), 1.25 - 0.03);
  assert.equal(seekTargetFor(words, 5), 2.0 - 0.03);
});

test("repeat-sentence target", () => {
  const so = sentenceIndex(sentences, words.length);
  assert.equal(so[3], 1);
  assert.ok(Math.abs(sentenceStartAt(sentences, so, tl, words, 2.7) - 0.97) < 1e-9);
  assert.ok(Math.abs(sentenceStartAt(sentences, so, tl, words, 6.2) - 5.97) < 1e-9);
});

test("speeds", () => {
  assert.equal(stepSpeed(1, -1), 1);
  assert.equal(stepSpeed(1.5, 1), 1.75);
  assert.equal(stepSpeed(2, 1), 2);
  assert.equal(cycleSpeed(2), 1);
  assert.equal(cycleSpeed(1.25), 1.5);
  assert.equal(formatSpeed(1.25), "1.25×");
  assert.equal(formatSpeed(1), "1×");
});

test("time formatting and clamping", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(455.4), "7:35");
  assert.equal(formatTime(1889.25), "31:29");
  assert.equal(formatTime(3725), "1:02:05");
  assert.equal(clamp(-3, 0, 10), 0);
  assert.equal(clamp(13, 0, 10), 10);
});

test("saved progress is validated against the recording", () => {
  assert.deepEqual(parsePosition('{"t":120.5,"scrollY":900}', 455), { t: 120.5, scrollY: 900, savedAt: null });
  assert.equal(parsePosition('{"t":455}', 455), null, "finished => start over");
  assert.equal(parsePosition("not json", 455), null);
  assert.equal(parsePosition('{"t":-1}', 455), null);
  assert.deepEqual(parsePrefs('{"rate":1.5,"follow":false,"lastDoc":"leverage"}'), { rate: 1.5, follow: false, lastDoc: "leverage" });
  assert.deepEqual(parsePrefs('{"rate":3}'), { rate: 1, follow: true, lastDoc: null });
});

test("typing targets are left alone", () => {
  assert.equal(isTypingTarget({ tagName: "INPUT" }), true);
  assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isTypingTarget({ tagName: "BUTTON" }), false);
  assert.equal(isTypingTarget(null), false);
});

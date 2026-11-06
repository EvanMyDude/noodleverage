// Pure timing logic for the reader. No DOM access here, so it is testable under `node --test`.
// The only clock is the caller-supplied playback position (audio.currentTime).

export const SPEEDS = [1, 1.25, 1.5, 1.75, 2];
export const HIGHLIGHT_LEAD = 0.04; // seconds the highlight may lead the voice (feels in sync)
export const SEEK_LEAD = 0.03;      // seconds before a word's onset that a click lands
export const SILENCE_HOLD = 0.8;    // keep a word lit through gaps shorter than this

// Word rows from alignment.json: [text, page, flags, quality, start?, end?, group?]
export const Q = { WORD: "w", APPROX: "a", NONE: "n", SYMBOL: "x" };

/** Build a sorted timeline of the timed words (word-level and approximate). */
export function buildTimeline(words) {
  const idx = [];
  for (let i = 0; i < words.length; i++) {
    const q = words[i][3];
    if ((q === Q.WORD || q === Q.APPROX) && Number.isFinite(words[i][4])) idx.push(i);
  }
  idx.sort((a, b) => words[a][4] - words[b][4] || a - b);
  const starts = new Float64Array(idx.length);
  const ends = new Float64Array(idx.length);
  idx.forEach((wi, k) => { starts[k] = words[wi][4]; ends[k] = words[wi][5]; });
  const groups = new Map(); // approximate group id -> word indices
  for (const wi of idx) {
    const g = words[wi][6];
    if (g != null) { if (!groups.has(g)) groups.set(g, []); groups.get(g).push(wi); }
  }
  return { idx, starts, ends, groups };
}

/** Index of the last element of `arr` that is <= x, or -1. */
export function lastAtOrBefore(arr, x) {
  let lo = 0, hi = arr.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/**
 * Word index (into the document's word list) being spoken at playback time `t`, or -1 when
 * nothing is (before the first word, or inside a pause longer than SILENCE_HOLD).
 */
export function findActive(tl, t, lead = HIGHLIGHT_LEAD, hold = SILENCE_HOLD) {
  if (!tl.starts.length || !Number.isFinite(t)) return -1;
  const k = lastAtOrBefore(tl.starts, t + lead);
  if (k < 0) return -1;
  const nextStart = k + 1 < tl.starts.length ? tl.starts[k + 1] : Infinity;
  if (t > tl.ends[k] + hold && nextStart > t + lead) return -1;
  return tl.idx[k];
}

/** Words to light up for an active word: its approximate group, or just itself. */
export function wordsToLight(tl, words, wi) {
  if (wi < 0) return [];
  const g = words[wi][6];
  return g != null && tl.groups.has(g) ? tl.groups.get(g) : [wi];
}

/** Sentence index for every word (sentences are [w0, w1, start?, end?]). */
export function sentenceIndex(sentences, wordCount) {
  const out = new Int32Array(wordCount).fill(-1);
  sentences.forEach((s, si) => { for (let w = s[0]; w < s[1]; w++) out[w] = si; });
  return out;
}

/** The timed word to seek to for a click on word `wi`, or null if it has no audio. */
export function seekTargetFor(words, wi) {
  const w = words[wi];
  if (!w || (w[3] !== Q.WORD && w[3] !== Q.APPROX) || !Number.isFinite(w[4])) return null;
  return Math.max(0, w[4] - SEEK_LEAD);
}

/** Start time of the sentence containing (or last before) playback time t. */
export function sentenceStartAt(sentences, sentOfWord, tl, words, t) {
  const k = lastAtOrBefore(tl.starts, t + HIGHLIGHT_LEAD);
  if (k < 0) return 0;
  const si = sentOfWord[tl.idx[k]];
  const s = sentences[si];
  return s && s.length >= 4 ? Math.max(0, s[2] - SEEK_LEAD) : Math.max(0, words[tl.idx[k]][4] - SEEK_LEAD);
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function stepSpeed(current, dir) {
  let i = SPEEDS.findIndex((s) => Math.abs(s - current) < 1e-6);
  if (i < 0) i = 0;
  return SPEEDS[clamp(i + dir, 0, SPEEDS.length - 1)];
}

export function cycleSpeed(current) {
  const i = SPEEDS.findIndex((s) => Math.abs(s - current) < 1e-6);
  return SPEEDS[(i + 1) % SPEEDS.length];
}

export function formatSpeed(r) {
  return `${Number(r.toFixed(2))}×`;
}

export function formatTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

// ---- persistence (pure helpers; the app wraps storage access in try/catch)
export const PREFS_KEY = "insightReader:v1:prefs";
export const posKey = (pairId) => `insightReader:v1:pos:${pairId}`;

export function parsePrefs(raw) {
  const d = { rate: 1, follow: true, lastDoc: null };
  try {
    const o = JSON.parse(raw);
    if (o && SPEEDS.includes(o.rate)) d.rate = o.rate;
    if (o && typeof o.follow === "boolean") d.follow = o.follow;
    if (o && typeof o.lastDoc === "string") d.lastDoc = o.lastDoc;
  } catch { /* defaults */ }
  return d;
}

/** Saved position, validated against this recording's duration. Finished => start over. */
export function parsePosition(raw, duration) {
  try {
    const o = JSON.parse(raw);
    if (!o || !Number.isFinite(o.t) || o.t <= 0) return null;
    if (Number.isFinite(duration) && o.t >= duration - 1) return null;
    return { t: o.t, scrollY: Number.isFinite(o.scrollY) ? o.scrollY : null, savedAt: o.savedAt || null };
  } catch { return null; }
}

/** Keyboard events that belong to the page, not to a text field or the browser. */
export function isTypingTarget(el) {
  if (!el) return false;
  const tag = (el.tagName || "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return true; // includes the seek slider: its arrow keys stay native
  return Boolean(el.isContentEditable);
}

// Insight Reader: renders aligned documents and keeps highlighting locked to the audio clock.
import {
  buildTimeline, findActive, wordsToLight, sentenceIndex, seekTargetFor, sentenceStartAt,
  clamp, cycleSpeed, stepSpeed, formatSpeed, formatTime,
  PREFS_KEY, posKey, parsePrefs, parsePosition, isTypingTarget, Q,
} from "./timeline.js";

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const el = {
  docs: $("docs"), switcher: $("doc-switch"), topbar: $("topbar"), banner: $("banner"),
  seek: $("seek"), elapsed: $("t-elapsed"), total: $("t-total"),
  play: $("btn-play"), back: $("btn-back"), fwd: $("btn-fwd"), speed: $("btn-speed"), follow: $("btn-follow"),
  pill: $("return-pill"), toast: $("toast"), sr: $("sr-status"), player: $("player"),
  toc: $("toc"), popContents: $("pop-contents"), popHelp: $("pop-help"), alignInfo: $("align-info"),
};
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// Storage can be unavailable (private windows, blocked site data): never let it break the app.
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

const state = {
  library: [],
  views: new Map(),        // id -> rendered document view
  cur: null,
  prefs: parsePrefs(store.get(PREFS_KEY)),
  active: -1, sentence: -1, lit: [], anchor: null,
  detached: false, autoScrollUntil: 0, userScrollAt: 0,
  scrubbing: false, raf: 0, pendingSeek: null,
  ready: false, audioError: false, lastSave: 0, lastClick: null,
};

// ---------------------------------------------------------------- helpers
function setText(node, text) { node.textContent = text; }
function announce(msg) { setText(el.sr, msg); }
let toastTimer = 0;
function toast(message, action) {
  clearTimeout(toastTimer);
  el.toast.replaceChildren(document.createTextNode(message));
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.addEventListener("click", () => { action.run(); el.toast.hidden = true; });
    el.toast.append(b);
  }
  el.toast.hidden = false;
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, action ? 6000 : 2400);
}
function banner(parts) {
  if (!parts) { el.banner.hidden = true; el.banner.replaceChildren(); return; }
  el.banner.replaceChildren(...parts.map((p) => {
    if (typeof p === "string") return document.createTextNode(p);
    const c = document.createElement("code"); c.textContent = p.code; return c;
  }));
  el.banner.hidden = false;
}
function savePrefs() { store.set(PREFS_KEY, JSON.stringify(state.prefs)); }
const topbarH = () => el.topbar.getBoundingClientRect().height;
// alignment time = playback time + offset (the playback copy is verified sample-identical, offset 0)
const offset = () => (state.cur && state.cur.entry.playbackOffset) || 0;
const mediaUrl = (path) => path.split("/").map(encodeURIComponent).join("/");
const playerTop = () => el.player.getBoundingClientRect().top;

// ---------------------------------------------------------------- rendering
function renderDocument(entry, data) {
  const art = document.createElement("article");
  art.className = "doc";
  art.dataset.doc = entry.id;
  art.lang = "en";
  const W = data.words;
  const sentOf = sentenceIndex(data.sentences, W.length);
  const wordEls = new Array(W.length);
  const sentEls = new Map();
  const headings = [];
  let lists = [];      // open list stack: [{el, ordered, lastLi}]
  let lastPage = 0;
  const blockText = (b) => W.slice(b.w[0], b.w[1]).map((r) => r[0]).join(" ");

  // runs of >= 3 unnarrated words get one small "not in audio" tag
  const nRunStart = new Set();
  for (let i = 0; i < W.length; i++) {
    if (W[i][3] === Q.NONE && (i === 0 || W[i - 1][3] !== Q.NONE)) {
      let j = i; while (j < W.length && (W[j][3] === Q.NONE || W[j][3] === Q.SYMBOL)) j++;
      if (j - i >= 3) nRunStart.add(i);
    }
  }

  data.blocks.forEach((b, bi) => {
    let box;
    if (b.type === "li") {
      const level = b.level || 1, ordered = !!b.ordered;
      while (lists.length > level) lists.pop();
      if (lists.length === level && lists[level - 1].ordered !== ordered) lists.pop();
      while (lists.length < level) {
        const parent = lists[lists.length - 1];
        const list = document.createElement(ordered ? "ol" : "ul");
        (parent && parent.lastLi ? parent.lastLi : art).appendChild(list);
        lists.push({ el: list, ordered, lastLi: null });
      }
      box = document.createElement("li");
      const n = parseInt(b.marker, 10);
      if (ordered && Number.isFinite(n)) box.value = n;
      lists[level - 1].el.appendChild(box);
      lists[level - 1].lastLi = box;
    } else {
      lists = [];
      if (b.type === "h1" && (b.title || bi === 0)) { box = document.createElement("h1"); box.className = "doc-title"; }
      else if (b.type === "h1") { box = document.createElement("h2"); box.className = "section"; headings.push({ box, level: 1, text: blockText(b) }); }
      else if (b.type === "h2" || b.type === "h3") { box = document.createElement("h3"); headings.push({ box, level: 2, text: blockText(b) }); }
      else if (b.type === "h4") { box = document.createElement("h4"); box.className = "label"; }
      else if (b.type === "callout") { box = document.createElement("div"); box.className = "callout blk"; }
      else { box = document.createElement("p"); box.className = "blk"; }
      art.appendChild(box);
    }

    let sentSpan = null, curSent = -2;
    for (let wi = b.w[0]; wi < b.w[1]; wi++) {
      const row = W[wi], si = sentOf[wi];
      if (si !== curSent) {
        if (wi > b.w[0]) box.appendChild(document.createTextNode(" "));
        sentSpan = document.createElement("span");
        sentSpan.className = "s";
        box.appendChild(sentSpan);
        if (!sentEls.has(si)) sentEls.set(si, []);
        sentEls.get(si).push(sentSpan);
        curSent = si;
      } else {
        sentSpan.appendChild(document.createTextNode(" "));
      }
      if (row[1] !== lastPage) {
        const pg = document.createElement("span");
        pg.className = "pg";
        pg.setAttribute("aria-label", `Page ${row[1]}`);
        pg.textContent = row[1];
        sentSpan.appendChild(pg);
        lastPage = row[1];
      }
      if (nRunStart.has(wi)) {
        const tag = document.createElement("span");
        tag.className = "nn";
        tag.textContent = "not in audio";
        tag.title = "This passage is not in the narration";
        sentSpan.appendChild(tag);
      }
      const w = document.createElement("span");
      const q = row[3];
      w.className = `w ${q}` + (q === Q.WORD || q === Q.APPROX ? " t" : "") + (row[2] & 1 ? " b" : "") + (row[2] & 2 ? " i" : "");
      w.dataset.i = wi;
      if (q === Q.APPROX) w.title = "Approximate timing";
      else if (q === Q.NONE) w.title = "Not in the narration";
      w.textContent = row[0];
      sentSpan.appendChild(w);
      wordEls[wi] = w;
    }
  });
  const minLevel = Math.min(...headings.map((h) => h.level), 9);
  headings.forEach((h) => { h.level = h.level - minLevel + 1; });
  return { art, wordEls, sentEls, sentOf, headings };
}

function renderError(entry, title, lines) {
  const art = document.createElement("article");
  art.className = "doc";
  art.dataset.doc = entry ? entry.id : "error";
  const box = document.createElement("div");
  box.className = "fatal";
  const h = document.createElement("h1"); h.textContent = title; box.append(h);
  for (const ln of lines) {
    if (typeof ln === "string") { const p = document.createElement("p"); p.textContent = ln; box.append(p); }
    else { const pre = document.createElement("pre"); pre.textContent = ln.code; box.append(pre); }
  }
  art.append(box);
  return art;
}

async function loadView(entry) {
  const view = { id: entry.id, entry, data: null, synced: false, syncNote: "", memPos: null, ready: false };
  try {
    const res = await fetch(entry.data, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    view.data = await res.json();
  } catch (err) {
    view.art = renderError(entry, "Alignment data not found", [
      `The reader could not load ${entry.data} (${err.message}). The narration can still play, but there is no text to show.`,
      "Rebuild it from the project folder:",
      { code: ".venv/bin/python tools/build_alignment.py" },
    ]);
    view.tl = buildTimeline([]);
    el.docs.append(view.art);
    return view;
  }
  const r = renderDocument(entry, view.data);
  Object.assign(view, r);
  view.tl = buildTimeline(view.data.words);
  view.synced = view.data.sync === "word";
  if (!view.synced) view.syncNote = "The narration differs too much from this text, so word sync is off. Audio still plays.";
  r.art.addEventListener("click", onWordClick);
  r.art.addEventListener("dblclick", onWordDblClick);
  el.docs.append(r.art);
  return view;
}

// ---------------------------------------------------------------- documents
function renderSwitcher() {
  el.switcher.replaceChildren(...state.library.map((d) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.id = d.id;
    b.title = d.fullTitle;
    const name = document.createElement("span"); name.textContent = d.title;
    const pos = document.createElement("span"); pos.className = "pos";
    b.append(name, pos);
    b.addEventListener("click", () => openDoc(d.id));
    return b;
  }));
  refreshSwitcher();
}
function refreshSwitcher() {
  for (const b of el.switcher.children) {
    const d = state.library.find((x) => x.id === b.dataset.id);
    const isCur = state.cur && state.cur.id === d.id;
    b.setAttribute("aria-current", isCur ? "true" : "false");
    const pos = parsePosition(store.get(posKey(d.pairId)), d.duration);
    b.querySelector(".pos").textContent = pos ? formatTime(pos.t) : "";
    b.setAttribute("aria-label", `${d.fullTitle}${pos ? `, resume at ${formatTime(pos.t)}` : ""}`);
  }
}

async function openDoc(id) {
  const entry = state.library.find((d) => d.id === id) || state.library[0];
  if (!entry || (state.cur && state.cur.id === entry.id)) return;
  if (state.cur) {
    savePosition();
    audio.pause();
    state.cur.art.hidden = true;
  }
  clearHighlight();
  state.ready = false;
  state.audioError = false;
  state.detached = false;
  banner(null);
  let view = state.views.get(entry.id);
  if (!view) { view = await loadView(entry); state.views.set(entry.id, view); }
  state.cur = view;
  view.art.hidden = false;
  view.art.classList.toggle("synced", view.synced);
  if (view.syncNote) banner([view.syncNote]);
  history.replaceState(null, "", `#${entry.id}`);
  document.title = `${entry.title} · Insight Reader`;
  state.prefs.lastDoc = entry.id; savePrefs();
  refreshSwitcher();
  buildToc();
  renderAlignInfo();

  // audio: restore (never autoplay) once metadata is known
  view.restore = view.memPos || parsePosition(store.get(posKey(entry.pairId)), entry.duration);
  window.scrollTo({ top: view.restore && view.restore.scrollY != null ? view.restore.scrollY : 0, behavior: "auto" });
  setControls(false);
  setLoading(true);
  updateSeekUI(view.restore ? view.restore.t : 0, entry.duration);
  audio.dataset.doc = entry.id;
  audio.src = mediaUrl(entry.playback || entry.audio);
  audio.load();
  announce(`Opened ${entry.fullTitle}`);
}

function savePosition() {
  const v = state.cur;
  if (!v || !state.ready || !Number.isFinite(audio.duration)) return;
  const t = audio.ended ? audio.duration : audio.currentTime;
  const pos = { t, scrollY: Math.round(window.scrollY), savedAt: Date.now() };
  v.memPos = audio.ended ? null : { t, scrollY: pos.scrollY };
  store.set(posKey(v.entry.pairId), JSON.stringify(pos));
  state.lastSave = performance.now();
  refreshSwitcher();
}

// ---------------------------------------------------------------- audio wiring
function setControls(on) {
  for (const b of [el.play, el.back, el.fwd]) b.disabled = !on;
  el.seek.disabled = !on;
}
function setLoading(on) { el.play.classList.toggle("loading", on); }
function setPlayIcon() {
  const playing = !audio.paused && !audio.ended;
  el.play.classList.toggle("playing", playing);
  el.play.classList.toggle("ended", audio.ended);
  const label = audio.ended ? "Replay from the start" : playing ? "Pause" : "Play";
  el.play.setAttribute("aria-label", label);
  el.play.title = `${label} (Space)`;
}
function applyRate() {
  const r = state.prefs.rate;
  audio.defaultPlaybackRate = r;
  audio.playbackRate = r;
  // keep the voice natural at higher speeds (prefixed for older engines)
  audio.preservesPitch = true; audio.mozPreservesPitch = true; audio.webkitPreservesPitch = true;
  el.speed.textContent = formatSpeed(r);
  el.speed.setAttribute("aria-label", `Playback speed ${formatSpeed(r)}`);
}
function setRate(r, viaKey) {
  state.prefs.rate = r; savePrefs(); applyRate();
  if (viaKey) toast(`Speed ${formatSpeed(r)}`);
}

audio.addEventListener("loadedmetadata", () => {
  const v = state.cur;
  if (!v || audio.dataset.doc !== v.id) return;
  const expected = v.data ? v.data.audio.duration : v.entry.duration;
  if (v.data && Math.abs(audio.duration - expected) > 0.25) {
    v.synced = false;
    v.art.classList.remove("synced");
    banner([`This recording is ${formatTime(audio.duration)} long but the alignment was built for ${formatTime(expected)}, so word sync is off. Rebuild with `,
      { code: ".venv/bin/python tools/build_alignment.py" }, "."]);
  }
  el.seek.max = String(audio.duration);
  setText(el.total, formatTime(audio.duration));
  applyRate();
  const r = v.restore;
  if (r && r.t > 0 && r.t < audio.duration - 0.5) {
    audio.currentTime = r.t;
    if (!v.memPos) {
      toast(`Resumed at ${formatTime(r.t)}`, { label: "Start over", run: () => { seekTo(0); scrollToTop(); } });
    }
  }
  state.ready = true;
  setControls(true);
  updateSeekUI(audio.currentTime);
  syncToAudio({ scrollIfNoSaved: r && r.scrollY == null });
  setPlayIcon();
});
audio.addEventListener("canplay", () => setLoading(false));
audio.addEventListener("playing", () => { setLoading(false); setPlayIcon(); startLoop(); announce("Playing"); });
audio.addEventListener("waiting", () => { if (!audio.paused) setLoading(true); });
audio.addEventListener("play", () => { setPlayIcon(); startLoop(); });
audio.addEventListener("pause", () => { setPlayIcon(); syncToAudio(); savePosition(); if (!audio.ended) announce("Paused"); });
audio.addEventListener("seeked", () => { syncToAudio(); if (state.ready) savePosition(); });
audio.addEventListener("seeking", () => syncToAudio());
audio.addEventListener("timeupdate", () => { if (audio.paused) syncToAudio(); });
audio.addEventListener("ratechange", () => syncToAudio());
audio.addEventListener("ended", () => {
  setPlayIcon(); clearHighlight(); savePosition(); updateSeekUI(audio.duration);
  toast("End of narration", { label: "Replay", run: () => { seekTo(0); play(); } });
  announce("End of narration");
});
audio.addEventListener("error", () => {
  const v = state.cur;
  if (!v || audio.dataset.doc !== v.id || !audio.getAttribute("src")) return;
  setLoading(false); setControls(false);
  state.audioError = true;
  v.art.classList.remove("synced");
  const code = audio.error ? audio.error.code : 0;
  const why = code === 4 ? "was not found or can't be decoded" : code === 2 ? "couldn't be downloaded" : "couldn't be played";
  banner([`The narration file `, { code: v.entry.playback || v.entry.audio }, ` ${why}. The text is still here to read. If you replaced the audio, rebuild with `, { code: ".venv/bin/python tools/build_alignment.py" }, "."]);
  announce("Narration unavailable");
});

function play() {
  if (!state.ready) return;
  if (audio.ended || audio.currentTime >= audio.duration - 0.05) audio.currentTime = 0;
  const p = audio.play();
  if (p && p.catch) p.catch(() => toast("Playback was blocked by the browser. Press play again."));
}
function togglePlay() {
  if (audio.paused || audio.ended) {
    // pressing Play means "I'm listening": resume following (when auto-follow is on)
    if (state.detached) { state.detached = false; if (state.anchor) follow(state.anchor, { force: state.prefs.follow }); }
    play();
  } else audio.pause();
}

function seekTo(t) {
  if (!state.ready) return;
  const target = clamp(t, 0, audio.duration);
  audio.currentTime = target;
  updateSeekUI(target);
  syncToAudio();
  setPlayIcon();
}
function skip(delta) { if (state.ready) seekTo(audio.currentTime + delta); }

// ---------------------------------------------------------------- sync (audio clock only)
function startLoop() { if (!state.raf) state.raf = requestAnimationFrame(tick); }
function tick() {
  state.raf = 0;
  if (state.pendingSeek != null) { audio.currentTime = state.pendingSeek; state.pendingSeek = null; }
  syncToAudio();
  if (!audio.paused && !audio.ended) {
    if (performance.now() - state.lastSave > 2000) savePosition();
    state.raf = requestAnimationFrame(tick);
  }
}

function syncToAudio(opts = {}) {
  const v = state.cur;
  const t = audio.currentTime;
  if (!state.scrubbing) updateSeekUI(t);
  if (!v || !v.synced || !state.ready) return;
  const at = t + offset();
  const wi = audio.ended ? -1 : findActive(v.tl, at);
  setActive(v, wi, at, opts);
}

function setActive(v, wi, t, opts = {}) {
  if (wi !== state.active) {
    for (const k of state.lit) v.wordEls[k] && v.wordEls[k].classList.remove("on");
    state.lit = wordsToLight(v.tl, v.data.words, wi);
    for (const k of state.lit) v.wordEls[k] && v.wordEls[k].classList.add("on");
    state.active = wi;
    if (wi >= 0) state.anchor = v.wordEls[wi];
  }
  // keep the sentence tint through pauses; clear it before the first word and at the end
  const beforeStart = !v.tl.starts.length || t < v.tl.starts[0] - 0.05;
  const si = wi >= 0 ? v.sentOf[wi] : (beforeStart || audio.ended ? -1 : state.sentence);
  if (si !== state.sentence) {
    (v.sentEls.get(state.sentence) || []).forEach((s) => s.classList.remove("cur"));
    (v.sentEls.get(si) || []).forEach((s) => s.classList.add("cur"));
    state.sentence = si;
  }
  if (wi >= 0) follow(state.anchor, { instant: opts.scrollIfNoSaved });
  else updatePill();
}

function clearHighlight() {
  const v = state.cur;
  if (v && v.wordEls) {
    for (const k of state.lit) v.wordEls[k] && v.wordEls[k].classList.remove("on");
    (v.sentEls.get(state.sentence) || []).forEach((s) => s.classList.remove("cur"));
  }
  state.lit = []; state.active = -1; state.sentence = -1; state.anchor = null;
  updatePill();
}

function updateSeekUI(t, durationHint) {
  const d = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (durationHint || 0);
  if (!state.scrubbing) el.seek.value = String(t);
  const pct = d ? (clamp(t, 0, d) / d) * 100 : 0;
  el.seek.style.setProperty("--pct", `${pct}%`);
  setText(el.elapsed, formatTime(t));
  if (d) setText(el.total, formatTime(d));
  el.seek.setAttribute("aria-valuetext", `${formatTime(t)} of ${formatTime(d)}`);
}

// ---------------------------------------------------------------- auto-follow
function band() {
  const top = topbarH(), bottom = playerTop();
  const h = bottom - top;
  return { top: top + h * 0.12, bottom: top + h * 0.7, target: top + h * 0.3, viewTop: top, viewBottom: bottom };
}
function follow(node, { force = false, instant = false } = {}) {
  if (!node) return updatePill();
  if (!force && (!state.prefs.follow || state.detached)) return updatePill();
  const r = node.getBoundingClientRect(), b = band();
  if (force || r.top < b.top || r.bottom > b.bottom) {
    const smooth = !instant && !reducedMotion.matches && Math.abs(r.top - b.target) < window.innerHeight * 2.5;
    state.autoScrollUntil = performance.now() + (smooth ? 900 : 120);
    window.scrollTo({ top: Math.max(0, window.scrollY + r.top - b.target), behavior: smooth ? "smooth" : "auto" });
  }
  updatePill();
}
function returnToCurrent() {
  state.detached = false;
  const node = state.anchor || (state.cur && state.cur.sentEls && (state.cur.sentEls.get(state.sentence) || [])[0]);
  if (node) follow(node, { force: true });
  updatePill();
}
function updatePill() {
  const node = state.anchor;
  if (!node || !state.ready || !node.isConnected) { el.pill.hidden = true; return; }
  const r = node.getBoundingClientRect(), b = band();
  const visible = r.bottom > b.viewTop + 4 && r.top < b.viewBottom - 4;
  if (state.detached && r.top >= b.top && r.bottom <= b.bottom) state.detached = false; // scrolled back by hand
  const show = !visible && (state.detached || !state.prefs.follow);
  el.pill.hidden = !show;
  if (show) el.pill.classList.toggle("up", r.bottom <= b.viewTop + 4);
}
function onUserScrollIntent() { state.userScrollAt = performance.now(); }
window.addEventListener("wheel", onUserScrollIntent, { passive: true });
window.addEventListener("touchmove", onUserScrollIntent, { passive: true });
let scrollSaveTimer = 0;
window.addEventListener("scroll", () => {
  el.topbar.classList.toggle("scrolled", window.scrollY > 4);
  const now = performance.now();
  const programmatic = now < state.autoScrollUntil && now - state.userScrollAt > 120;
  if (!programmatic && state.anchor && state.prefs.follow) {
    const r = state.anchor.getBoundingClientRect(), b = band();
    if (r.top < b.top || r.bottom > b.bottom) state.detached = true; // you scrolled away: stop following
  }
  updatePill();
  clearTimeout(scrollSaveTimer);
  scrollSaveTimer = setTimeout(savePosition, 500);
}, { passive: true });
window.addEventListener("resize", () => updatePill());
function scrollToTop() { state.autoScrollUntil = performance.now() + 900; window.scrollTo({ top: 0, behavior: reducedMotion.matches ? "auto" : "smooth" }); }

function setFollow(on) {
  state.prefs.follow = on; savePrefs();
  el.follow.setAttribute("aria-pressed", String(on));
  el.follow.title = `Auto-follow ${on ? "on" : "off"} (F)`;
  if (on) returnToCurrent(); else updatePill();
}

// ---------------------------------------------------------------- word clicks
function onWordClick(e) {
  const w = e.target.closest(".w");
  if (!w || e.detail > 1) return;
  const sel = window.getSelection();
  if (sel && !sel.isCollapsed && sel.toString().trim()) return; // selecting text, not seeking
  const v = state.cur, wi = Number(w.dataset.i);
  if (!v || !v.data) return;
  if (!v.synced) { toast("Word sync is off for this document."); return; }
  const target = seekTargetFor(v.data.words, wi);
  if (target == null) {
    if (v.data.words[wi][3] === Q.NONE) toast("This passage isn't in the narration.");
    return;
  }
  if (!state.ready) { toast(state.audioError ? "The narration file couldn't be loaded." : "The narration is still loading."); return; }
  state.lastClick = { at: performance.now(), prevTime: audio.currentTime, wasPlaying: !audio.paused && !audio.ended };
  state.detached = false;
  seekTo(target - offset());
  play();
}
function onWordDblClick() {
  // a double-click selects a word; undo the seek its first click triggered
  const c = state.lastClick;
  if (c && performance.now() - c.at < 700) {
    seekTo(c.prevTime);
    if (!c.wasPlaying) audio.pause();
  }
  state.lastClick = null;
}

// ---------------------------------------------------------------- contents & help
function buildToc() {
  const v = state.cur;
  el.toc.replaceChildren();
  if (!v || !v.headings) return;
  for (const h of v.headings) {
    const li = document.createElement("li");
    li.className = `lv${h.level}`;
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = h.text;
    b.title = h.text;
    b.addEventListener("click", () => {
      el.popContents.hidePopover();
      if (state.anchor && !audio.paused) state.detached = true;
      state.autoScrollUntil = performance.now() + 900;
      h.box.scrollIntoView({ block: "start", behavior: reducedMotion.matches ? "auto" : "smooth" });
    });
    li.append(b);
    el.toc.append(li);
  }
  if (!v.headings.length) {
    const li = document.createElement("li"); li.textContent = "No headings in this document."; el.toc.append(li);
  }
}
function renderAlignInfo() {
  const v = state.cur;
  el.alignInfo.replaceChildren();
  if (!v || !v.data) return;
  const s = v.data.stats, narrated = s.words - s.x;
  const p = (txt) => { const x = document.createElement("p"); x.textContent = txt; el.alignInfo.append(x); };
  p(`${v.data.fullTitle}: ${v.data.source.pages} pages, ${formatTime(v.data.audio.duration)} of narration.`);
  p(`Word-level timing for ${s.w.toLocaleString()} of ${narrated.toLocaleString()} words (${s.wordLevelPct}%), measured from the recording itself by forced alignment.`);
  p(`${s.a} word${s.a === 1 ? " has" : "s have"} approximate timing; ${s.n} word${s.n === 1 ? " is" : "s are"} not in the narration.`);
  if (/OCR|Vision/i.test(v.data.source.text)) p("This PDF stores its text as shapes, so the words were recognized on this Mac (OCR) and checked against the page images.");
  if (!v.synced) p("Word sync is currently off for this document.");
  const lg = document.createElement("div"); lg.className = "legend";
  for (const [cls, sample, label] of [["w", "word", "spoken now"], ["a", "group", "approximate timing"], ["n", "dimmed", "not in the narration"]]) {
    const sw = document.createElement("span"); sw.className = `sw ${cls}`; sw.textContent = sample;
    const lb = document.createElement("span"); lb.textContent = label;
    lg.append(sw, lb);
  }
  el.alignInfo.append(lg);
}

// ---------------------------------------------------------------- controls & keyboard
el.play.addEventListener("click", togglePlay);
el.back.addEventListener("click", () => skip(-10));
el.fwd.addEventListener("click", () => skip(10));
el.speed.addEventListener("click", () => setRate(cycleSpeed(state.prefs.rate)));
el.follow.addEventListener("click", () => setFollow(!state.prefs.follow));
el.pill.addEventListener("click", returnToCurrent);

el.seek.addEventListener("pointerdown", () => { state.scrubbing = true; });
el.seek.addEventListener("input", () => {
  if (!state.ready) return;
  state.scrubbing = true;
  const t = Number(el.seek.value);
  updateSeekUI(t);
  state.pendingSeek = t;              // applied on the next frame (coalesces fast drags)
  if (audio.paused) { audio.currentTime = t; state.pendingSeek = null; }
  else startLoop();
});
const endScrub = () => {
  if (!state.scrubbing) return;
  state.scrubbing = false;
  if (state.ready) seekTo(Number(el.seek.value));
};
el.seek.addEventListener("change", endScrub);
el.seek.addEventListener("pointerup", endScrub);
el.seek.addEventListener("pointercancel", endScrub);

function repeatSentence() {
  const v = state.cur;
  if (!v || !v.synced || !state.ready) return;
  seekTo(sentenceStartAt(v.data.sentences, v.sentOf, v.tl, v.data.words, audio.currentTime + offset()) - offset());
  state.detached = false;
  play();
}
function nextDoc() {
  if (state.library.length < 2 || !state.cur) return;
  const i = state.library.findIndex((d) => d.id === state.cur.id);
  openDoc(state.library[(i + 1) % state.library.length].id);
}

document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
  if (isTypingTarget(e.target)) return;
  const onButton = e.target && e.target.tagName === "BUTTON";
  const k = e.key;
  const handlers = {
    " ": () => { if (!onButton) togglePlay(); else return false; },
    k: togglePlay, K: togglePlay,
    ArrowLeft: () => skip(-10), j: () => skip(-10), J: () => skip(-10),
    ArrowRight: () => skip(10), l: () => skip(10), L: () => skip(10),
    "[": () => setRate(stepSpeed(state.prefs.rate, -1), true),
    "]": () => setRate(stepSpeed(state.prefs.rate, 1), true),
    f: () => { setFollow(!state.prefs.follow); toast(`Auto-follow ${state.prefs.follow ? "on" : "off"}`); },
    c: returnToCurrent, r: repeatSentence,
    t: () => el.popContents.togglePopover(), d: nextDoc, "?": () => el.popHelp.togglePopover(),
  };
  handlers.F = handlers.f; handlers.C = handlers.c; handlers.R = handlers.r; handlers.T = handlers.t; handlers.D = handlers.d;
  const h = handlers[k];
  if (!h) return;
  if (h() === false) return;
  e.preventDefault();
});

window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id) openDoc(id); });
window.addEventListener("pagehide", savePosition);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") savePosition(); });

// ---------------------------------------------------------------- start
async function start() {
  el.follow.setAttribute("aria-pressed", String(state.prefs.follow));
  applyRate();
  try {
    const res = await fetch("data/library.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.library = (await res.json()).documents || [];
    if (!state.library.length) throw new Error("no documents listed");
  } catch (err) {
    el.docs.append(renderError(null, "No reader data yet", [
      `data/library.json could not be loaded (${err.message}).`,
      "Build the alignment data from the project folder, then reload:",
      { code: ".venv/bin/python tools/build_alignment.py" },
    ]));
    el.player.hidden = true;
    return;
  }
  renderSwitcher();
  const fromHash = location.hash.slice(1);
  const id = [fromHash, state.prefs.lastDoc].find((x) => x && state.library.some((d) => d.id === x)) || state.library[0].id;
  await openDoc(id);
}

// read-only handle for automated checks
window.__reader = {
  state, audio,
  activeFor: (t) => (state.cur && state.cur.tl ? findActive(state.cur.tl, t + offset()) : -1),
  words: () => (state.cur && state.cur.data ? state.cur.data.words : []),
};
start();

# Insight Reader

A private, dark-mode reader for your notes. It shows the PDF's text as a comfortable,
reflowable page, plays the narration MP3, and highlights each word as it is spoken.
Click any word to listen from there. Everything runs on this Mac; nothing is uploaded.

Two document/audio pairs are set up (switch with the toggle at the top or `D`):

| Reader | PDF | Narration |
|---|---|---|
| Lessons | `Top Lessons and Actions from April - June 2026.pdf` | `April - June 2026 Top Lessons and Actions Audio.mp3` (7:35) |
| Leverage | `NoodLeverage.pdf` | `NoodLeverage Audio.mp3` (31:29) |

## Open it

**Hosted** (after GitHub Pages is enabled for this repo): https://evanmydude.github.io/noodleverage/

**Locally:**

```bash
python3 serve.py
```

That opens http://127.0.0.1:8765 in your browser (only reachable from this Mac). Stop it with
Ctrl+C. Chrome is the browser the timing was measured in (see *Verification*).

## Use it

- **Click a word** to play from it. Dragging to select text never moves playback, and a
  double-click to select a word puts playback back where it was.
- **Controls**: play/pause, back/forward 10 s, a draggable seek bar, and the speed chip
  (1× → 1.25× → 1.5× → 1.75× → 2×, pitch preserved).
- **Auto-follow** (left button in the bar) keeps the spoken line in view. Scroll away and it
  stops following; a *Back to current passage* pill (or `C`, or pressing Play) brings you back.
- **Contents** (top right, or `T`) jumps to a heading without touching playback.
- **Shortcuts** (`?` shows them): Space/K play · ←/J −10 s · →/L +10 s · `[` `]` speed ·
  R repeat sentence · F auto-follow · C current passage · T contents · D switch document.
  Shortcuts are ignored while you type in a field or hold ⌘/Ctrl/Alt.
- **Progress** is remembered per document *and recording* (position, scroll, speed, auto-follow).
  Reopening resumes where you left off, paused. Switching documents keeps each one's place.

### What the markings mean

- Blue pill: the word being spoken. Faint band: its sentence.
- Dotted underline when active: **approximate** timing. The narration differed there (a
  mis-heard name, a word the aligner barely heard), so the group is timed to the real span of
  speech at that spot instead of a precise word time.
- Dimmed text with a *not in audio* tag: the narrator didn't read it. It isn't clickable.

## How the timing is made (and why you can trust it)

`tools/build_alignment.py` runs once per recording and saves everything to `data/`:

1. **Text**: Lessons has a real tagged text layer (headings, lists, and bold come from the PDF
   tags). NoodLeverage stores its text as vector shapes, so it is read with macOS's built-in
   Vision OCR. Bold and italic come from the PDF's own hidden OCR layer, and every word the OCR and
   the narration disagreed on was checked against the page image
   (`sources/NoodLeverage.ocr-corrections.json` lists each fix).
2. **Speech recognition**: faster-whisper (`small.en`, local CPU) hears what was actually said.
3. **Forced alignment**: wav2vec2 CTC pins each spoken word to ~20 ms frames.
4. **Matching**: spoken words are matched to the displayed words in order (difflib anchors, then
   fuzzy alignment inside gaps), so repeated phrases can't jump to the wrong occurrence.
   Nothing is ever spread evenly or estimated from word counts.
5. **Second pass over gaps**: wherever no spoken word matched, the *displayed* words are
   force-aligned against that stretch of audio. Words the recognizer skipped (it dropped a whole
   sentence on page 5 of NoodLeverage) are recovered with real timing; words that were never
   said only fit onto silence, score near zero, and stay marked *not in audio*.
6. **Seek-accurate playback copy**: your MP3s are variable-bitrate, and browsers seek those through
   a coarse table. Measured in Chrome, a seek landed up to **1.2 s** away from where the player
   said it was. The build therefore makes a constant-bitrate copy of the *same recording*
   (`data/<id>/narration.mp3`), verifies that its timeline is sample-identical to your original,
   and the app plays that. Your original MP3s are never modified.

The app's only clock is the audio element's `currentTime`; there is no separate timer.

## Replace the files / regenerate

One-time setup (downloads ~1–2 GB of packages and speech models, then works offline):

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
```

Then, after replacing a PDF or MP3 (or editing `sources.json` to point at new ones):

```bash
.venv/bin/python tools/build_alignment.py
```

```bash
.venv/bin/python tools/verify_alignment.py
```

- Build one document only with `--only lessons` or `--only leverage`; use `--model medium.en` if
  the report shows many approximate words.
- Each stage is cached by file content (`data/cache/`), so rebuilding after a small change is fast.
- New files get a new identity, so old saved progress is not applied to a different recording.
- Read `data/<id>/alignment-report.md` afterwards: coverage, unnarrated passages, spoken-only
  passages, OCR notes, and timestamps to spot-check by ear. `verification.md` re-transcribes
  clips cut at the saved times as an independent check.
- For an outlined/scanned PDF, review the "Displayed word ≠ what was heard" list and add
  image-verified fixes to a corrections file referenced from `sources.json`.

## Tests

```bash
npm test
```

(Runs `node --test tests/timeline.test.mjs` and `.venv/bin/python -m pytest -q tests`.)

## Files

```
index.html, css/reader.css, js/app.js   the reader
js/timeline.js                          timing logic (unit-tested)
serve.py                                local server with HTTP Range support (needed for seeking)
sources.json                            which PDF goes with which MP3
sources/*.ocr-corrections.json          image-verified OCR fixes
tools/build_alignment.py                text + speech + alignment + playback copy → data/
tools/verify_alignment.py               independent re-transcription check
tools/align/                            the pipeline stages
data/library.json, data/<id>/           generated reader data, reports, playback copies
tests/                                  Python and JS tests
```

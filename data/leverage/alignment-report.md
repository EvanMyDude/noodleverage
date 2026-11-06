# Alignment report: NoodLeverage

- PDF: `NoodLeverage.pdf` (12 pages, text via Apple Vision VNRecognizeTextRequest (accurate, en-US))
- Audio: `NoodLeverage Audio.mp3` (31:29.25); the reader plays `data/leverage/narration.mp3` (timeline offset -0.0 ms vs the original)
- Method: faster-whisper 1.1.1 (small.en) → torchaudio 2.7.1 WAV2VEC2_ASR_BASE_960H + numpy Viterbi → difflib anchors + fuzzy Needleman-Wunsch in gaps
- Built: 2026-11-06T02:40:08+00:00

## Coverage

| Word-level | Approximate | Not narrated | Symbols (silent) | Total |
|---|---|---|---|---|
| 4456 (99.44%) | 15 | 10 | 12 | 4493 |

## Recovered by the second pass

Words the speech recognizer skipped, found by force-aligning the displayed text against the audio.

- 0:34.49 p1: “go?".”
- 2:32.26 p2: “of”
- 4:53.27 p2: “#Hormozi”
- 6:34.00 p3: “stays”
- 6:54.92 p3: “and”
- 11:32.83 p5: “around”
- 11:33.79 p5: “Fort”
- 11:34.07 p5: “Worth.”
- 11:35.05 p5: “Next:”
- 11:35.90 p5: “Pray”
- 11:36.20 p5: “on”
- 11:36.32 p5: “it."”
- 11:37.36 p5: “This”
- 11:37.58 p5: “is”
- 11:37.80 p5: “priority”
- 13:42.46 p5: “of”
- 13:42.58 p5: “my”
- 13:58.55 p5: “"Okay,”
- 14:26.82 p6: “go?"):”
- 18:30.92 p7: “moat”
- 25:45.91 p10: “Audit:”
- 29:48.14 p11: “to”

## Not in the narration

Shown in the reader, dimmed, and not clickable.

- p1: “NoodLeverage ↳ to build clarity with frfr”
- p5: “(pg. 2, 4, 7)”

## Approximate timing

Highlighted as a group over the real audio span of what was said there.

- 2:03.51–2:03.53 p1: “a”
- 3:24.41–3:24.43 p2: “a”
- 5:07.13–5:07.22 p2: “I'm”
- 6:33.93–6:34.27 p3: “a”
- 9:12.97–9:12.99 p4: “a”
- 10:54.91–10:54.96 p4: “a”
- 11:37.64–11:37.80 p5: “a”
- 14:49.85–14:50.13 p6: “mirror”
- 18:37.01–18:37.11 p7: “C:”
- 22:14.07–22:14.09 p8: “I”
- 23:44.33–23:44.35 p9: “I”
- 25:24.89–25:25.01 p9: “C. I”
- 26:55.59–26:55.61 p10: “I”
- 29:16.33–29:16.43 p11: “a”

## Spoken but not in the text

Narration with no counterpart in the PDF (list numbers, “hashtag”, page asides…).

- 0:33.10: “slash,”
- 2:24.70: “slash,”
- 4:43.64: “on page 2, 3, 9, 13 to 14.”
- 4:51.37: “Hashtag”
- 4:53.03: “Hashtag hormonesy.”
- 4:57.00: “Hashtag”
- 4:59.35: “Hashtag”
- 5:36.45: “slash,”
- 5:42.99: “1.”
- 5:52.88: “2.”
- 6:03.87: “3.”
- 6:27.68: “slash”
- 6:29.95: “slash”
- 7:29.22: “slash”
- 8:01.11: “slash”
- 8:12.73: “My synthesis of, am I really committed to stand -up? Actually, mirror work through the looking glass. What's a minimal productive protocol? And define when for each next step on page two, five, 11, 15, 17.”
- 9:10.44: “page 517,”
- 9:18.86: “slash”
- 11:51.22: “slash”
- 13:45.96: “1.”
- 14:22.21: “2.”
- 14:44.87: “slash”
- 14:52.30: “slash”
- 14:55.27: “one.”
- 15:34.39: “Two,”
- 24:00.99: “pea”
- 24:05.81: “One”
- 24:21.44: “–”
- 27:41.23: “This”
- 29:57.51: “One.”
- 30:19.03: “1.”
- 30:40.32: “1.”
- 31:02.53: “One.”

## Displayed word ≠ what was heard

Near-spellings and substitutions. Check these against the page if the source is OCR.

- p2: shown “listed”, heard “enlisted,” (fuzzy)
- p2: shown “#Hormozi”, heard “Hashtag” (sub)
- p3: shown “humor,”, heard “humour,” (fuzzy)
- p3: shown “a”, heard “Stazer” (sub)
- p6: shown “mirror”, heard “neural” (sub)
- p6: shown “with”, heard “within” (fuzzy)
- p6: shown “Enverus,”, heard “Verus,” (fuzzy)
- p7: shown “moat”, heard “mode” (sub)
- p7: shown “Enverus.”, heard “Inverus.” (fuzzy)
- p9: shown “Ptitties,”, heard “titties,” (fuzzy)
- p9: shown “Nooding.”, heard “nuding.” (fuzzy)
- p9: shown “cataloging”, heard “cataloguing” (fuzzy)
- p10: shown “neighbors).”, heard “Neighbours.” (fuzzy)

## OCR notes

- p1: '4' → '↳' (real text-layer glyph at this position)
- correction “Al” → “AI” applied 12× — Inter's capital I read as lowercase l; page shows AI (all occurrences, e.g. 'AI systems', '(AI).')
- correction “l” → “I” applied 2× — standalone lowercase l is the pronoun I on the page ('once I', 'know I')
- correction “|” → “I” applied 1× — page: 'what I am building toward'
- correction “|” → “I” applied 1× — page: 'bottleneck I can automate'
- correction “|” → “I” applied 1× — page: 'mindset I moved to Fort Worth'
- correction “|” → “I” applied 1× — page: 'note that I define steps well'
- correction “/” → “I” applied 1× — page: 'as hard as I worked on'
- correction “1” → “I” applied 1× — page: 'on the day I run out'
- correction “NextTime/OpenTheNote"” → “NextTimeIOpenTheNote"” applied 1× — page: '#When - NextTimeIOpenTheNote"'
- correction “complexity"to” → “complexity" to” applied 1× — page has a space: 'complexity" to turn chaos'
- correction “PRI'),” → “PRI"),” applied 1× — page closes the quote with a double quote: 'PRI"),'
- correction “go?"'” → “go?"” applied 1× — no stray apostrophe on the page: 'to go?" on page 3'
- correction “"Seriously,,” → “"Seriously,,,” applied 1× — page shows three commas: '"Seriously,,, during the day'

## Spot checks to confirm by ear

Click these words in the reader (or seek to the time) and confirm the voice starts on them.

- 0:42.04 p1: “unanswered questions: "What am I still…”
- 6:10.47 p3: “work, dropping Backyard Baseball, and ruling…”
- 12:17.12 p5: “conversation with Zach, a text to…”
- 18:27.79 p7: “enthusiasm, while trading away my existing…”
- 24:59.18 p9: “instead of present action. Option C:…”
- 30:24.97 p11: “about running out of medication early.…”

#!/usr/bin/env python3
"""Independent check of the saved timestamps, using only the audio.

For each document: cut the recording at the *computed* boundaries and re-transcribe each
clip in isolation (no context from the full-file pass).
  * sentence clips  [start, end]       -> transcript should equal the sentence (low WER),
                                          and must not start/end with a neighbouring word
  * word-onset clips [start, start+1.4s] -> transcript should begin with that word
                                          (this is what a click-to-seek lands on)

    .venv/bin/python tools/verify_alignment.py
"""
from __future__ import annotations

import difflib
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.audio import decode_audio  # noqa: E402

from tools.align.common import DATA, ROOT, read_json  # noqa: E402
from tools.align.normalize import spoken_tokens  # noqa: E402

SR = 16000


def toks(text: str) -> list[str]:
    return [s for w in text.split() for s in spoken_tokens(w)]


def wer(ref: list[str], hyp: list[str]) -> float:
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    errs = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    return errs / max(1, len(ref))


def close(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def main() -> None:
    model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=8)
    lib = read_json(DATA / "library.json")["documents"]
    rng = random.Random(7)
    overall_ok = True
    for entry in lib:
        doc = read_json(ROOT / entry["data"])
        W, S = doc["words"], doc["sentences"]
        audio = decode_audio(str(ROOT / doc["audio"]["file"]), sampling_rate=SR)
        lines = [f"# Independent timing check: {doc['fullTitle']}", "",
                 "Clips are cut at the saved timestamps and re-transcribed in isolation.", ""]

        def transcribe(s, e):
            clip = audio[int(max(0, s) * SR): int(e * SR)]
            segs, _ = model.transcribe(clip, language="en", beam_size=5, condition_on_previous_text=False,
                                       vad_filter=False, without_timestamps=True)
            return " ".join(x.text.strip() for x in segs)

        timed = [s for s in S if len(s) == 4 and all(W[k][3] == "w" for k in range(s[0], s[1]) if W[k][3] != "x")
                 and s[1] - s[0] >= 5]
        picks = [timed[int(f * (len(timed) - 1))] for f in (0.02, 0.5, 0.97)] + rng.sample(timed, 5)
        lines += ["## Sentences", "", "| where | time | WER | edges clean | text |", "|---|---|---|---|---|"]
        sent_ok = 0
        for s in picks:
            w0, w1, ts, te = s
            ref = toks(" ".join(W[k][0] for k in range(w0, w1) if W[k][3] != "n"))
            hyp_text = transcribe(ts - 0.03, te + 0.08)
            hyp = toks(hyp_text)
            e = wer(ref, hyp)
            edges = bool(hyp) and close(hyp[0], ref[0]) and close(hyp[-1], ref[-1])
            ok = e <= 0.15 and edges
            sent_ok += ok
            lines.append(f"| p{W[w0][1]} | {ts:.2f}–{te:.2f}s | {e:.0%} | {'yes' if edges else 'NO'} | "
                         f"{' '.join(W[k][0] for k in range(w0, w1))[:70]} |")
        word_picks = rng.sample([k for k, w in enumerate(W) if w[3] == "w" and len(spoken_tokens(w[0])) == 1
                                 and len(w[0]) >= 4], 12)
        lines += ["", "## Click landing (clip starts at the saved word start)", "",
                  "| word | time | clip begins with | ok |", "|---|---|---|---|"]
        word_ok = 0
        for k in sorted(word_picks):
            target = spoken_tokens(W[k][0])[0]
            hyp = toks(transcribe(W[k][4] - 0.03, W[k][4] + 1.4))
            ok = bool(hyp) and close(hyp[0], target)
            word_ok += ok
            lines.append(f"| {W[k][0]} | {W[k][4]:.2f}s | {' '.join(hyp[:4])} | {'yes' if ok else 'NO'} |")
        # every "not in the narration" claim, re-heard in isolation
        lines += ["", "## “Not in the narration” claims", "",
                  "The audio between the neighbouring spoken words is re-transcribed on its own.", "",
                  "| passage | window | heard there | verdict |", "|---|---|---|---|"]
        absent_ok, absent_n, k = 0, 0, 0
        while k < len(W):
            if W[k][3] != "n":
                k += 1; continue
            j = k
            while j < len(W) and W[j][3] in ("n", "x"):
                j += 1
            run = " ".join(W[i][0] for i in range(k, j))
            lo = next((W[i][5] for i in range(k - 1, -1, -1) if len(W[i]) > 5), 0.0)
            hi = next((W[i][4] for i in range(j, len(W)) if len(W[i]) > 4), len(audio) / SR)
            heard = toks(transcribe(max(0, lo - 0.3), hi + 0.3)) if hi - lo > -0.3 else []
            want = toks(run)
            found = sum(1 for t in want if any(close(t, h) for h in heard))
            spoken = bool(want) and found / len(want) >= 0.5
            absent_n += 1; absent_ok += not spoken
            lines.append(f"| p{W[k][1]}: {run[:60]} | {lo:.2f}–{hi:.2f}s | {' '.join(heard[:12]) or '(silence)'} | "
                         f"{'absent ✓' if not spoken else 'SPOKEN?'} |")
            k = j
        summary = (f"{entry['id']}: sentences {sent_ok}/{len(picks)} clean (WER ≤ 15%, no neighbour bleed); "
                   f"click landings {word_ok}/{len(word_picks)} start on the target word; "
                   f"not-narrated claims confirmed {absent_ok}/{absent_n}")
        lines += ["", f"**{summary}**", ""]
        (DATA / entry["id"] / "verification.md").write_text("\n".join(lines), encoding="utf-8")
        print(summary)
        overall_ok &= sent_ok >= len(picks) - 1 and word_ok >= len(word_picks) - 2 and absent_ok == absent_n
    print("PASS" if overall_ok else "CHECK REPORTS")


if __name__ == "__main__":
    main()

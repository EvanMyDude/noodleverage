#!/usr/bin/env python3
"""Build reader data for every document/audio pair in sources.json.

    .venv/bin/python tools/build_alignment.py            # all documents
    .venv/bin/python tools/build_alignment.py --only leverage
    .venv/bin/python tools/build_alignment.py --model medium.en   # heavier ASR model

Stages are cached under data/cache by content hash, so re-running after editing a
corrections file only redoes the cheap matching step. The reader app never touches audio.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.align import extract_ocr, extract_text  # noqa: E402
from tools.align.common import CACHE, DATA, ROOT, read_json, sha256_file, write_json  # noqa: E402
from tools.align.match import align, apply_fill, flatten_asr, gap_requests, sentences  # noqa: E402
from tools.align.playback import ensure_playback_copy  # noqa: E402

PY = sys.executable
SWIFT = "swift"


def run(cmd: list[str]) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    print("  $", " ".join(c if " " not in c else repr(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)


def text_layer_is_real(pdf: Path) -> bool:
    """True when most characters use embedded (subset) fonts, i.e. real text rather than an
    invisible OCR overlay in a non-embedded base font."""
    import pdfplumber
    with pdfplumber.open(pdf) as doc:
        chars = [c for p in doc.pages for c in p.chars if not c["text"].isspace()]
    if len(chars) < 200:
        return False
    embedded = sum(bool(re.match(r"^[A-Z]{6}\+", c["fontname"])) for c in chars)
    return embedded / len(chars) > 0.9


def apply_corrections(blocks: list[dict], path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    fixes = read_json(path).get("fixes", [])
    applied = []
    flat = [t for b in blocks for t in b["tokens"]]
    for fx in fixes:
        pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(fx["find"]) + r"(?![A-Za-z0-9])")
        n = 0
        for k, t in enumerate(flat):
            if fx.get("page") and t["p"] != fx["page"]:
                continue
            if fx.get("token") and t["t"] != fx["find"]:
                continue  # exact-token fixes never touch "(1)" when fixing a bare "1"
            if not pat.search(t["t"]):
                continue
            if fx.get("context"):
                window = " ".join(x["t"] for x in flat[max(0, k - 6): k + 7])
                if fx["context"] not in window:
                    continue
            t["t"] = pat.sub(fx["replace"], t["t"], count=1)
            n += 1
        applied.append({**fx, "count": n})
        if n == 0:
            print(f"  ! correction matched nothing: {fx}")
    # A fix may restore a missing space ('complexity"to' -> 'complexity" to'): re-split tokens.
    for b in blocks:
        new = []
        for t in b["tokens"]:
            parts = t["t"].split()
            new += [{**t, "t": p} for p in parts] if len(parts) > 1 else [t]
        b["tokens"] = new
    return applied


def build_doc(doc: dict, model: str) -> dict:
    pdf, audio = ROOT / doc["pdf"], ROOT / doc["audio"]
    for p in (pdf, audio):
        if not p.exists():
            raise SystemExit(f"[{doc['id']}] missing source file: {p.name}")
    print(f"\n== {doc['id']}: {pdf.name} + {audio.name}")
    pdf_hash, audio_hash = sha256_file(pdf), sha256_file(audio)
    pair_id = pdf_hash[:12] + audio_hash[:12]

    # 1. text
    mode = doc.get("extract", "auto")
    if mode == "auto":
        mode = "text" if text_layer_is_real(pdf) else "ocr"
    ocr_review: list = []
    if mode == "text":
        blocks = extract_text.extract(pdf)
        engine = "pdfplumber text layer (tagged PDF)"
    else:
        ocr_json = CACHE / f"ocr-{pdf_hash[:16]}.json"
        if not ocr_json.exists():
            run([SWIFT, "tools/align/ocr_pdf.swift", str(pdf), str(ocr_json), str(CACHE / f"pages-{pdf_hash[:16]}"), "3"])
        blocks = extract_ocr.extract(pdf, ocr_json, ocr_review)
        engine = read_json(ocr_json)["engine"]
    corrections = apply_corrections(blocks, ROOT / doc["corrections"] if doc.get("corrections") else None)

    # 2. speech recognition (cached per audio + model)
    asr_json = CACHE / f"asr-{audio_hash[:16]}-{model}.json"
    if not asr_json.exists():
        run([PY, "-m", "tools.align.asr", str(audio), str(asr_json), "--model", model])
    asr = read_json(asr_json)
    # 3. forced alignment (separate process: torch and ctranslate2 must not share one)
    ctc_json = CACHE / f"ctc-{audio_hash[:16]}-{model}.json"
    if not ctc_json.exists():
        run([PY, "-m", "tools.align.ctc", str(audio), str(asr_json), str(ctc_json)])
    ctc = read_json(ctc_json)

    # 4. seek-accurate playback copy (VBR MP3s seek imprecisely in browsers)
    pb = ensure_playback_copy(audio, DATA / doc["id"] / "narration.mp3")
    pb_file = pb["file"] if pb["file"] == audio.name else Path(pb["file"]).relative_to(ROOT).as_posix()

    # 5. match
    asr_words = flatten_asr(asr, ctc)
    duration = asr["duration"]
    res = align(blocks, asr_words, duration)
    timing = res["timing"]

    # 6. second pass: force-align the displayed text of every gap against its stretch of audio
    flat = [t for b in blocks for t in b["tokens"]]
    reqs = gap_requests(flat, timing, duration)
    res["recovered"] = []
    if reqs:
        import hashlib
        key = hashlib.sha256(json.dumps([audio_hash, reqs], sort_keys=True).encode()).hexdigest()[:16]
        req_json, fill_json = CACHE / f"fillreq-{key}.json", CACHE / f"fill-{key}.json"
        if not fill_json.exists():
            write_json(req_json, {"requests": reqs})
            run([PY, "-m", "tools.align.ctc", str(audio), str(req_json), str(fill_json), "--fill"])
        res["recovered"] = apply_fill(timing, reqs, read_json(fill_json)["results"])

    words_out, blocks_out, base = [], [], 0
    for b in blocks:
        n = len(b["tokens"])
        bo = {"type": b["type"], "w": [base, base + n]}
        for key in ("level", "marker", "ordered", "title"):
            if b.get(key):
                bo[key] = b[key]
        blocks_out.append(bo)
        for k, t in enumerate(b["tokens"]):
            tm = timing[base + k]
            flags = (1 if t.get("b") else 0) | (2 if t.get("i") else 0)
            row = [t["t"], t["p"], flags, tm["q"]]
            if "s" in tm:
                row += [tm["s"], tm["e"]]
                if tm["q"] == "a":
                    row.append(tm["g"])
            words_out.append(row)
        base += n
    sents = []
    for w0, w1 in sentences(blocks):
        ts = [timing[k] for k in range(w0, w1) if "s" in timing[k]]
        sents.append([w0, w1, min(t["s"] for t in ts), max(t["e"] for t in ts)] if ts else [w0, w1])

    q = [t["q"] for t in timing]
    spoken_words = sum(1 for x in q if x != "x")
    stats = {k: q.count(k) for k in ("w", "a", "n", "x")}
    stats["words"] = len(q)
    stats["wordLevelPct"] = round(100 * stats["w"] / max(1, spoken_words), 2)
    stats["timedPct"] = round(100 * (stats["w"] + stats["a"]) / max(1, spoken_words), 2)
    status = "word" if stats["wordLevelPct"] >= 50 else "disabled"

    out = {
        "version": 1,
        "id": doc["id"],
        "title": doc.get("title", doc["id"]),
        "fullTitle": doc.get("fullTitle", pdf.stem),
        "pairId": pair_id,
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"pdf": pdf.name, "pdfSha256": pdf_hash, "pages": max(t["p"] for b in blocks for t in b["tokens"]),
                   "text": engine},
        "audio": {"file": audio.name, "sha256": audio_hash, "duration": duration,
                  "playback": pb_file, "playbackOffset": pb["offset"], "playbackCheck": pb["check"]},
        "method": {"asr": f"{asr['engine']} ({asr['model']})", "align": ctc["engine"],
                   "match": "difflib anchors + fuzzy Needleman-Wunsch in gaps", "minConfidence": 0.25, "onsetBackoff": 0.08},
        "sync": status,
        "stats": stats,
        "blocks": blocks_out,
        "sentences": sents,
        "words": words_out,
    }
    out_dir = DATA / doc["id"]
    write_json(out_dir / "alignment.json", out, compact=True)
    write_report(out_dir / "alignment-report.md", out, res, ocr_review, corrections, mode)
    print(f"  words {stats['words']}: word-level {stats['w']} ({stats['wordLevelPct']}%), approx {stats['a']}, "
          f"not narrated {stats['n']}, symbols {stats['x']}; spoken-only runs {len(res['insertions'])}; "
          f"recovered by 2nd pass {len(res['recovered'])}")
    return {"id": doc["id"], "title": out["title"], "fullTitle": out["fullTitle"], "data": f"data/{doc['id']}/alignment.json",
            "audio": audio.name, "playback": pb_file, "playbackOffset": pb["offset"],
            "duration": duration, "pairId": pair_id, "sync": status}


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def write_report(path: Path, out: dict, res: dict, ocr_review: list, corrections: list, mode: str) -> None:
    W = out["words"]
    s = out["stats"]
    lines = [f"# Alignment report: {out['fullTitle']}", "",
             f"- PDF: `{out['source']['pdf']}` ({out['source']['pages']} pages, text via {out['source']['text']})",
             f"- Audio: `{out['audio']['file']}` ({fmt(out['audio']['duration'])}); the reader plays "
             f"`{out['audio']['playback']}` (timeline offset {out['audio']['playbackOffset']*1000:.1f} ms vs the original)",
             f"- Method: {out['method']['asr']} → {out['method']['align']} → {out['method']['match']}",
             f"- Built: {out['built']}", "",
             "## Coverage", "",
             f"| Word-level | Approximate | Not narrated | Symbols (silent) | Total |",
             f"|---|---|---|---|---|",
             f"| {s['w']} ({s['wordLevelPct']}%) | {s['a']} | {s['n']} | {s['x']} | {s['words']} |", ""]

    def runs(kind):
        out_runs, cur = [], []
        for k, w in enumerate(W):
            if w[3] == kind:
                cur.append(k)
            elif cur and w[3] != "x":
                out_runs.append(cur); cur = []
        if cur:
            out_runs.append(cur)
        return out_runs

    if res.get("recovered"):
        lines += ["## Recovered by the second pass", "",
                  "Words the speech recognizer skipped, found by force-aligning the displayed text against the audio.", ""]
        lines += [f"- {fmt(W[k][4])} p{W[k][1]}: “{W[k][0]}”" for k in res["recovered"]]
        lines += [""]
    lines += ["## Not in the narration", "", "Shown in the reader, dimmed, and not clickable.", ""]
    for r in runs("n"):
        lines.append(f"- p{W[r[0]][1]}: “{' '.join(W[k][0] for k in range(r[0], r[-1] + 1))}”")
    lines += ["", "## Approximate timing", "", "Highlighted as a group over the real audio span of what was said there.", ""]
    for r in runs("a"):
        w0 = W[r[0]]
        lines.append(f"- {fmt(w0[4])}–{fmt(W[r[-1]][5])} p{w0[1]}: “{' '.join(W[k][0] for k in range(r[0], r[-1] + 1))}”")
    lines += ["", "## Spoken but not in the text", "", "Narration with no counterpart in the PDF (list numbers, “hashtag”, page asides…).", ""]
    for ins in res["insertions"]:
        lines.append(f"- {fmt(ins['s'])}: “{ins['text']}”")
    lines += ["", "## Displayed word ≠ what was heard", "",
              "Near-spellings and substitutions. Check these against the page if the source is OCR.", ""]
    for rv in res["review"]:
        lines.append(f"- p{rv['page']}: shown “{rv['text']}”, heard “{rv['heard']}” ({', '.join(rv['kinds'])})")
    if mode == "ocr":
        lines += ["", "## OCR notes", ""]
        lines += [f"- p{r['page']}: {r['ocr']!r} → {r['fixed']!r} ({r['why']})" for r in ocr_review]
        lines += [f"- correction “{c['find']}” → “{c['replace']}” applied {c['count']}× — {c.get('note','')}" for c in corrections]
    timed = [k for k, w in enumerate(W) if w[3] == "w"]
    if timed:
        lines += ["", "## Spot checks to confirm by ear", "",
                  "Click these words in the reader (or seek to the time) and confirm the voice starts on them.", ""]
        for frac in (0.02, 0.2, 0.4, 0.6, 0.8, 0.97):
            k = timed[int(frac * (len(timed) - 1))]
            ctx = " ".join(W[j][0] for j in range(k, min(len(W), k + 6)))
            lines.append(f"- {fmt(W[k][4])} p{W[k][1]}: “{ctx}…”")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--model", default="small.en")
    a = ap.parse_args()
    cfg = read_json(ROOT / "sources.json")
    lib_path = DATA / "library.json"
    library = read_json(lib_path)["documents"] if lib_path.exists() else []
    by_id = {d["id"]: d for d in library}
    for doc in cfg["documents"]:
        if a.only and doc["id"] != a.only:
            continue
        by_id[doc["id"]] = build_doc(doc, a.model)
    order = [d["id"] for d in cfg["documents"]]
    write_json(lib_path, {"documents": [by_id[i] for i in order if i in by_id]})
    print(f"\nlibrary: {lib_path}")


if __name__ == "__main__":
    main()

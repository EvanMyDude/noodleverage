"""Rebuild a reflowable block structure for a PDF whose visible text is vector outlines.

Inputs
  * Vision OCR JSON from ocr_pdf.swift (words, boxes, stroke widths) -- the wording source.
  * The PDF itself via pdfplumber, for two things only:
      - real embedded glyphs (arrows such as ↳ →, the running footer), which beat OCR;
      - an invisible OCR text layer, if present (here: Adobe Acrobat "Paper Capture", drawn in
        non-embedded Helvetica / -Bold / -Oblique). Its words are lower quality than Vision's,
        but its font choice tells us which words are bold or italic, and its text is a second
        opinion when reviewing OCR disagreements.

Geometry (PDF points, top-left origin) drives structure: footer band, heading size from
average glyph width, list level from the text column, new items/paragraphs from line pitch.
"""
from __future__ import annotations

import logging
import re
import statistics as st
from pathlib import Path

import pdfplumber

try:
    from .docmodel import BlockBuilder
    from .common import read_json
except ImportError:  # pragma: no cover
    from docmodel import BlockBuilder
    from common import read_json

logging.getLogger("pdfminer").setLevel(logging.ERROR)

MARKERS = {"•", "◦", "▪", "·", "o"}
ARROWS = set("↳→←↑↓↘↗")
LINE_PITCH_BREAK = 21.0     # body line pitch is ~17.6pt; items/paragraphs are >= ~23pt apart
COLUMN_STEP = 22.0          # list nesting step between text columns
TERMINAL = tuple('.?!:"”')


def _words_from_hidden(page) -> list[dict]:
    hidden = [c for c in page.chars if not re.match(r"^[A-Z]{6}\+", c["fontname"])]
    return hidden


def _style_for(box, hidden_chars) -> tuple[bool, bool, int]:
    x, y, w, h = box
    bold = ital = n = 0
    for c in hidden_chars:
        cx, cy = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
        if x - 1 <= cx <= x + w + 1 and y - 2 <= cy <= y + h + 2 and not c["text"].isspace():
            n += 1
            bold += "Bold" in c["fontname"]
            ital += "Oblique" in c["fontname"] or "Italic" in c["fontname"]
    return (n > 0 and bold * 2 > n), (n > 0 and ital * 2 > n), n


def _merge_physical_lines(lines: list[dict]) -> list[dict]:
    """Vision sometimes splits one printed line into several observations; merge by y-overlap."""
    lines = sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0]))
    out: list[dict] = []
    for l in lines:
        y, h = l["bbox"][1], l["bbox"][3]
        if out:
            py, ph = out[-1]["bbox"][1], out[-1]["bbox"][3]
            if abs((y + h / 2) - (py + ph / 2)) < min(h, ph) * 0.45:
                prev = out[-1]
                words = sorted(prev["words"] + l["words"], key=lambda w: w["bbox"][0] if w["bbox"] else 0)
                x0 = min(prev["bbox"][0], l["bbox"][0])
                prev.update(words=words, text=" ".join(w["text"] for w in words),
                            bbox=[x0, min(py, y), max(prev["bbox"][0] + prev["bbox"][2], l["bbox"][0] + l["bbox"][2]) - x0,
                                  max(ph, h)])
                continue
        out.append({**l, "words": list(l["words"])})
    return out


def extract(pdf_path: Path, ocr_json: Path, review: list | None = None) -> list[dict]:
    """Return blocks; append cross-check notes to `review` when given."""
    ocr = read_json(ocr_json)
    bb = BlockBuilder()
    plumber = pdfplumber.open(pdf_path)

    # Body text column and stroke baseline, measured over the whole document.
    all_x = [l["bbox"][0] for p in ocr["pages"] for l in p["lines"]]
    body_x = st.median([x for x in all_x if x < 90]) if all_x else 72.0
    strokes = [w["stroke"] for p in ocr["pages"] for l in p["lines"] for w in l["words"] if w["stroke"] > 0]
    stroke_regular = st.median(strokes) if strokes else 4.3

    item_col = None          # text column of the open list item
    last_bottom = None       # top y of the previous line on this page
    for page in ocr["pages"]:
        pno = page["index"]
        ppage = plumber.pages[pno - 1]
        hidden = _words_from_hidden(ppage)
        real = [c for c in ppage.chars if re.match(r"^[A-Z]{6}\+", c["fontname"])]
        footer_top = min([c["top"] for c in real if c["size"] < 9] or [page["height"] - 45]) - 4
        arrows = [c for c in real if c["text"] in ARROWS]

        lines = [l for l in _merge_physical_lines(page["lines"]) if l["bbox"][1] < footer_top]
        last_bottom = None
        for line in lines:
            words = [dict(w) for w in line["words"] if w["text"]]
            # Real arrow glyphs replace whatever OCR guessed at that spot ("4" for "↳").
            for a in arrows:
                ax = (a["x0"] + a["x1"]) / 2
                hit = next((w for w in words if w["bbox"] and w["bbox"][0] - 2 <= ax <= w["bbox"][0] + w["bbox"][2] + 2
                            and abs(w["bbox"][1] + w["bbox"][3] / 2 - (a["top"] + a["bottom"]) / 2) < 8), None)
                if hit is not None and len(hit["text"]) <= 2:
                    if review is not None and hit["text"] != a["text"]:
                        review.append({"page": pno, "kind": "glyph", "ocr": hit["text"], "fixed": a["text"],
                                       "why": "real text-layer glyph at this position"})
                    hit["text"] = a["text"]
            marker = None
            if words and words[0]["text"] in MARKERS and len(words) > 1:
                marker = "•"
                words = words[1:]
            elif (len(words) > 1 and re.fullmatch(r"\d+\.", words[0]["text"]) and words[0]["bbox"]
                  and words[0]["bbox"][0] > body_x + 3):
                marker = words[0]["text"]
                words = words[1:]
            if not words:
                continue
            if marker is not None:
                # Vision's box for the word after a marker swallows the gap, so place the item's
                # text column from the marker instead: markers sit ~6pt right of their column.
                marker_x = line["bbox"][0]
                level = max(1, round((marker_x - body_x - 6) / COLUMN_STEP) + 1)
                text_x = body_x + level * COLUMN_STEP
            else:
                text_x = words[0]["bbox"][0] if words[0]["bbox"] else line["bbox"][0]
                level = max(0, round((text_x - body_x) / COLUMN_STEP))

            core = [w for w in words if w["bbox"] and re.search(r"[A-Za-z0-9]", w["text"])]
            cw = (sum(w["bbox"][2] for w in core) / max(1, sum(len(w["text"]) for w in core))) if core else 0
            for w in words:
                b, i, n = _style_for(w["bbox"], hidden) if w["bbox"] else (False, False, 0)
                if n == 0:  # no hidden-layer evidence: fall back to stroke width
                    b = w["stroke"] > stroke_regular * 1.38
                w["b"], w["i"], w["t"] = b, i, w["text"]
            _smooth(words)
            all_bold = bool(core) and all(w["b"] for w in core)
            semibold = bool(core) and (sum(w["stroke"] for w in core) / len(core)) > stroke_regular * 1.3

            # Line pitch measured between box centres (quote marks make some boxes taller):
            # ~17.6pt inside a paragraph/item, >= ~23pt between blocks.
            centre = line["bbox"][1] + line["bbox"][3] / 2
            pitch = (centre - last_bottom) if last_bottom is not None else None  # None = first line on page
            last_bottom = centre
            gap_break = pitch is not None and pitch > LINE_PITCH_BREAK
            cur = bb.cur
            cur_core = [t for t in (cur["tokens"] if cur else []) if re.search(r"[A-Za-z0-9]", t["t"])]
            same_weight = (bool(cur_core) and all(t["b"] for t in cur_core)) == all_bold
            if cw >= 15:
                bb.open("h1", title=True); item_col = None
            elif cw >= 11:
                bb.open("h1"); item_col = None
            elif cur and cur["type"] == "h2" and level == 0 and marker is None and pitch is not None and not gap_break and semibold:
                item_col = None  # wrapped heading line (its last line can be short and narrow)
            elif cw >= 7.55 and level == 0 and marker is None and semibold:
                bb.open("h2")
                item_col = None
            elif marker is not None:
                bb.open("li", level=max(1, level), marker=marker, ordered=marker[0].isdigit())
                item_col = text_x
            elif cur and cur["type"] == "li" and item_col is not None and abs(text_x - item_col) < 6 and \
                    (not gap_break or pitch is None):
                pass  # wrapped line of the open item (pitch None = first line on a new page)
            elif cur and cur["type"] in ("p", "callout") and level == 0 and pitch is not None and not gap_break \
                    and same_weight:
                pass  # wrapped paragraph line
            elif cur and cur["type"] == "p" and pitch is None and level == 0 and cur["tokens"] and \
                    (not cur["tokens"][-1]["t"].endswith(TERMINAL) or words[0]["t"][:1].islower()):
                pass  # paragraph continuing across a page break
            elif words[0]["t"] in ARROWS:
                bb.open("callout"); item_col = None
            elif level > 0 and pitch is None and cur and cur["type"] == "li":
                pass  # list continuation at the top of a page at a different column
            else:
                bb.open("p", level=level) if level > 0 else bb.open("p")
                item_col = None
            bb.add_words([{"t": w["t"], "b": w["b"], "i": w["i"]} for w in words], pno)

    bb.close()
    blocks = _promote_labels(bb.blocks)
    plumber.close()
    return blocks


def _smooth(words: list[dict]) -> None:
    """Fill single-token holes in bold/italic runs ("Whether **I** need" where OCR boxes drift),
    and let punctuation-only tokens inherit the style of the run they sit in."""
    for key in ("b", "i"):
        flags = [w[key] for w in words]
        for k in range(1, len(words) - 1):
            if not flags[k] and flags[k - 1] and flags[k + 1] and (len(words[k]["t"]) <= 3 or
                                                                    not re.search(r"[A-Za-z0-9]", words[k]["t"])):
                words[k][key] = True


def _promote_labels(blocks: list[dict]) -> list[dict]:
    """A short, single-line, fully bold paragraph with no closing punctuation is a sub-heading."""
    for b in blocks:
        if b["type"] == "p" and not b.get("level") and 0 < len(b["tokens"]) <= 9 and all(t["b"] for t in b["tokens"]) \
                and not b["tokens"][-1]["t"].endswith((".", ":")) and len({t["p"] for t in b["tokens"]}) == 1:
            b["type"] = "h4"
            for t in b["tokens"]:
                t["b"] = False  # heading style carries the weight
    for b in blocks:
        if b["type"] in ("h1", "h2"):
            for t in b["tokens"]:
                t["b"] = False
    return blocks


def hidden_layer_words(pdf_path: Path) -> list[tuple[int, str]]:
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            chars = _words_from_hidden(p)
            if not chars:
                continue
            words = pdfplumber.utils.extract_words(chars)
            out += [(p.page_number, w["text"]) for w in words]
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys
    rv: list = []
    for b in extract(Path(sys.argv[1]), Path(sys.argv[2]), rv):
        tag = b["type"] + (f"{b.get('level','')}[{b.get('marker')}]" if b["type"] == "li" else "")
        pages = sorted({t["p"] for t in b["tokens"]})
        txt = " ".join(("**" + t["t"] + "**") if t["b"] else ("_" + t["t"] + "_") if t.get("i") else t["t"] for t in b["tokens"])
        print(f"{tag:9} p{pages} {txt[:150]}")
    for r in rv:
        print("REVIEW", r)

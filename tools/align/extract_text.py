"""Extract a reflowable block structure from a PDF that has a real (tagged) text layer.

Headings come from font size, list items from the PDF's `Lbl` structure tags, bold from
`Strong` tags (or a bold fallback font), and running footers are dropped by their small size.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

try:
    from .docmodel import BlockBuilder
except ImportError:  # pragma: no cover
    from docmodel import BlockBuilder

logging.getLogger("pdfminer").setLevel(logging.ERROR)

FOOTER_MAX_SIZE = 9.0


def has_text_layer(pdf_path: Path, min_words_per_page: int = 40) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        words = sum(len((p.extract_text() or "").split()) for p in pdf.pages)
        return words >= min_words_per_page * max(1, len(pdf.pages))


def _struct_mcids(page) -> tuple[set, set]:
    """Return (label mcids, strong mcids) from the page's structure tree."""
    labels, strong = set(), set()

    def walk(node, path):
        t = node.get("type")
        for m in node.get("mcids") or []:
            if "Lbl" in path + [t]:
                labels.add(m)
            if "Strong" in path + [t]:
                strong.add(m)
        for c in node.get("children") or []:
            walk(c, path + [t])

    try:
        for n in page.structure_tree:
            walk(n, [])
    except Exception:  # untagged PDF: fall back to geometry only
        pass
    return labels, strong


def _lines(page):
    """Group body chars into physical lines of words (top-to-bottom, left-to-right)."""
    labels, strong = _struct_mcids(page)
    chars = [c for c in page.chars if c["size"] > FOOTER_MAX_SIZE]
    chars.sort(key=lambda c: (round(c["top"] / 3), c["x0"]))
    lines: list[list[dict]] = []
    for c in chars:
        if lines and abs(lines[-1][-1]["top"] - c["top"]) < c["size"] * 0.5:
            lines[-1].append(c)
        else:
            lines.append([c])
    out = []
    carry_bold = False  # untagged chars (mcid None) continue the previous char's weight
    for line in lines:
        line.sort(key=lambda c: c["x0"])
        words, cur, prev = [], None, None
        for c in line:
            if c.get("mcid") is None:
                c_bold = "Bold" in c["fontname"] or (carry_bold and "Liberation" not in c["fontname"])
            else:
                c_bold = c["mcid"] in strong or "Bold" in c["fontname"]
            carry_bold = c_bold if not c["text"].isspace() else carry_bold
            gap = c["x0"] - prev["x1"] if prev else 0
            if c["text"].isspace() or (prev and gap > c["size"] * 0.22):
                if cur:
                    words.append(cur)
                cur = None
                if c["text"].isspace():
                    prev = c
                    continue
            is_label = c.get("mcid") in labels
            if cur is None:
                cur = {"t": "", "x0": c["x0"], "size": c["size"], "b": c_bold, "lbl": is_label}
            cur["t"] += c["text"]
            cur["b"] = cur["b"] or c_bold
            prev = c
        if cur:
            words.append(cur)
        if words:
            out.append({"top": line[0]["top"], "x0": words[0]["x0"], "size": max(w["size"] for w in words), "words": words})
    return out


def extract(pdf_path: Path) -> list[dict]:
    bb = BlockBuilder()
    item_text_x = None
    with pdfplumber.open(pdf_path) as pdf:
        sizes = sorted({round(c["size"]) for p in pdf.pages for c in p.chars if c["size"] > FOOTER_MAX_SIZE})
        body = min(sizes)
        heading_sizes = sorted([s for s in sizes if s > body * 1.15], reverse=True)
        level_of = {s: f"h{min(i + 1, 4)}" for i, s in enumerate(heading_sizes)}
        prev_line = None
        for page in pdf.pages:
            pno = page.page_number
            for line in _lines(page):
                size = round(line["size"])
                words = line["words"]
                if size in level_of:
                    htype = level_of[size]
                    same_heading = (bb.cur and bb.cur["type"] == htype and prev_line is not None
                                    and round(prev_line["size"]) == size)
                    if not same_heading:
                        bb.open(htype)
                    bb.add_words(words, pno)
                    item_text_x = None
                elif words[0]["lbl"]:
                    marker_words = [w for w in words if w["lbl"]]
                    rest = [w for w in words if not w["lbl"]]
                    marker = "".join(w["t"] for w in marker_words)
                    bb.open("li", level=1, marker=marker, ordered=marker[:1].isdigit())
                    bb.add_words(rest, pno)
                    item_text_x = rest[0]["x0"] if rest else None
                elif bb.cur and bb.cur["type"] == "li" and item_text_x is not None and line["x0"] >= item_text_x - 3:
                    bb.add_words(words, pno)  # wrapped line (also across a page break)
                else:
                    if not (bb.cur and bb.cur["type"] == "p" and prev_line is not None
                            and line["top"] - prev_line["top"] < line["size"] * 1.8):
                        bb.open("p")
                    bb.add_words(words, pno)
                    item_text_x = None
                prev_line = line
            prev_line = None  # page boundary: never merge headings across pages
    bb.close()
    if bb.blocks and bb.blocks[0]["type"] == "h1":
        bb.blocks[0]["title"] = True
    return bb.blocks


def raw_body_words(pdf_path: Path) -> list[str]:
    """All words of the text layer minus footers, for the no-omission test."""
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in _lines(page):
                out += [w["t"] for w in line["words"]]
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys
    for b in extract(Path(sys.argv[1])):
        tag = b["type"] + (f"[{b.get('marker')}]" if b["type"] == "li" else "")
        pages = sorted({t["p"] for t in b["tokens"]})
        bold = " ".join(t["t"] for t in b["tokens"] if t["b"])
        print(f"{tag:7} p{pages} {' '.join(t['t'] for t in b['tokens'])[:110]}" + (f"  **{bold[:40]}**" if bold else ""))

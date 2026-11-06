"""Shared document model produced by both extractors (text layer and OCR).

A document is a list of blocks in reading order:

    {"type": "h1"|"h2"|"h3"|"h4"|"p"|"li"|"callout",
     "level": 1,            # list nesting depth (li only)
     "marker": "1." | "•",  # source list marker (li only), shown but never aligned
     "tokens": [{"t": "word", "p": page, "b": bold}, ...]}

Tokens are whitespace-delimited display words with their punctuation attached, exactly as
they appear in the source after line-wrap repair. Nothing here paraphrases or drops words.
"""
from __future__ import annotations

import re

CLOSE_PUNCT = re.compile(r"^[,.;:!?)\]”’]+$")
# Encoding-level glyph variants that render identically; normalizing them changes no wording.
GLYPH_FIXES = str.maketrans({"ʼ": "’"})  # MODIFIER LETTER APOSTROPHE -> RIGHT SINGLE QUOTATION MARK


class BlockBuilder:
    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.cur: dict | None = None

    def open(self, btype: str, **extra) -> dict:
        self.close()
        self.cur = {"type": btype, "tokens": [], **extra}
        return self.cur

    def add_words(self, words: list[dict], page: int) -> None:
        """Append words from one physical line, repairing the wrap from the previous line."""
        assert self.cur is not None, "add_words without an open block"
        toks = self.cur["tokens"]
        for i, w in enumerate(words):
            text = w["t"].translate(GLYPH_FIXES)
            if not text:
                continue
            # A hyphen at the end of the previous line joins compound words split by wrapping
            # ("5-" + "minute" -> "5-minute"). The hyphen itself is kept: it is in the source.
            if i == 0 and toks and toks[-1]["t"].endswith("-") and len(toks[-1]["t"]) > 1 and toks[-1].get("eol"):
                toks[-1]["t"] += text
                toks[-1]["b"] = toks[-1]["b"] or w.get("b", False)
                continue
            # Stray spaces before closing punctuation ("building ." -> "building.").
            if CLOSE_PUNCT.match(text) and toks:
                toks[-1]["t"] += text
                continue
            tok = {"t": text, "p": page, "b": bool(w.get("b", False))}
            if w.get("i"):
                tok["i"] = True
            toks.append(tok)
        for t in toks:
            t.pop("eol", None)
        if toks:
            toks[-1]["eol"] = True

    def close(self) -> None:
        if self.cur is not None:
            for t in self.cur["tokens"]:
                t.pop("eol", None)
            if self.cur["tokens"]:
                self.blocks.append(self.cur)
        self.cur = None


def block_text(block: dict) -> str:
    return " ".join(t["t"] for t in block["tokens"])


def doc_words(blocks: list[dict]) -> list[str]:
    return [t["t"] for b in blocks for t in b["tokens"]]

"""Shared spoken-form normalizer: the same function runs on displayed words and ASR words,
so formatting differences (digits vs words, hyphens, hashtags, curly quotes) never cause
false mismatches. A display word maps to zero or more spoken sub-tokens.
"""
from __future__ import annotations

import re
import unicodedata

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
        "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# Written forms a narrator reads differently. Applied to both sides after splitting.
ALIASES = {
    "pg": ["page"], "pgs": ["pages"], "vs": ["versus"], "ok": ["okay"], "&": ["and"], "+": ["plus"],
    "%": ["percent"], "≠": ["not", "equal"], "=": ["equals"], "w/": ["with"], "approx": ["approximately"],
}
SYMBOLS_SILENT = set("↳→←↑↓|/\\-–—•◦*~^_…")


def number_words(n: int, year_style: bool = False) -> list[str]:
    if year_style and 1100 <= n <= 2099 and not (2000 <= n <= 2009):
        hi, lo = divmod(n, 100)
        if lo == 0:
            return number_words(hi) + ["hundred"]
        return number_words(hi) + (["oh"] if lo < 10 else []) + number_words(lo)
    if n < 20:
        return [ONES[n]]
    if n < 100:
        t, o = divmod(n, 10)
        return [TENS[t]] + ([ONES[o]] if o else [])
    if n < 1000:
        h, r = divmod(n, 100)
        return [ONES[h], "hundred"] + (number_words(r) if r else [])
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        return number_words(th) + ["thousand"] + (number_words(r) if r else [])
    return [str(n)]


def _camel_split(s: str) -> list[str]:
    # "#NoLukewarm" -> ["No", "Lukewarm"]; "NextTimeIOpenTheNote" -> ["Next","Time","I","Open","The","Note"]
    return re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|'[a-z]+", s) or [s]


def spoken_tokens(word: str) -> list[str]:
    """Display/ASR word -> spoken sub-tokens (lowercase ASCII-ish), possibly empty."""
    w = unicodedata.normalize("NFKC", word)
    w = w.replace("’", "'").replace("‘", "'").replace("ʼ", "'").replace("“", '"').replace("”", '"')
    if w in ALIASES:
        return list(ALIASES[w])
    w = re.sub(r"(?<=\d),(?=\d{3}\b)", "", w)  # 1,000 -> 1000
    dollars = bool(re.match(r"^[\"'(]*\$\d", w))
    w = w.replace("$", "")
    out: list[str] = []
    # Split on characters that are never part of a spoken word.
    for piece in re.split(r"[\s/\\|–—\-…]+|\.{2,}", w):
        if not piece:
            continue
        hashtag = piece.startswith("#")
        piece = piece.strip("\"'()[]{}.,;:!?*_`#<>")
        if not piece:
            continue
        if piece in ALIASES:
            out += ALIASES[piece]
            continue
        if piece.startswith("#") or hashtag and piece.isdigit():
            out.append("number")
            piece = piece.lstrip("#")
        parts = _camel_split(piece) if (hashtag or re.search(r"[a-z][A-Z]", piece)) else re.findall(r"[A-Za-z']+|\d+|[≠=&%+]", piece)
        for p in parts:
            lp = p.lower().strip("'")
            if not lp:
                continue
            if lp.isdigit():
                n = int(lp)
                out += number_words(n, year_style=len(lp) == 4)
            elif lp in ALIASES:
                out += ALIASES[lp]
            else:
                out.append(lp.replace("'", ""))
    if dollars:
        out.append("dollars")
    return out


def ctc_text(word: str) -> str:
    """Characters wav2vec2's English CTC vocabulary can align (A-Z and apostrophe), '|' between parts."""
    toks = spoken_tokens(word)
    return "|".join(re.sub(r"[^A-Z']", "", t.upper()) for t in toks if re.sub(r"[^A-Z']", "", t.upper()))

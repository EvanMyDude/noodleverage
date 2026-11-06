"""OCR-based structure for NoodLeverage: every recognized word survives, structure is sane."""
import re
from collections import Counter
from pathlib import Path

import pytest

from tools.align import extract_ocr
from tools.align.common import CACHE, read_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "NoodLeverage.pdf"


def _ocr_json():
    if not PDF.exists():
        return None
    p = CACHE / f"ocr-{sha256_file(PDF)[:16]}.json"
    return p if p.exists() else None


OCR = _ocr_json()
pytestmark = pytest.mark.skipif(OCR is None, reason="run tools/build_alignment.py first (OCR cache missing)")


@pytest.fixture(scope="module")
def blocks():
    return extract_ocr.extract(PDF, OCR)


def text(b):
    return " ".join(t["t"] for t in b["tokens"])


def test_opening_structure(blocks):
    assert blocks[0]["type"] == "h1" and text(blocks[0]) == "NoodLeverage"
    assert blocks[1]["type"] == "callout" and text(blocks[1]).startswith("↳ to build clarity")
    assert [text(b) for b in blocks if b["type"] == "h1"] == ["NoodLeverage", "Engineered Output", "EE Raw Output"]


def test_footers_removed(blocks):
    joined = " ".join(text(b) for b in blocks)
    assert joined.count("NoodLeverage") == 1


def test_sub_headings_are_labels_not_paragraphs(blocks):
    labels = {text(b) for b in blocks if b["type"] == "h4"}
    assert {"Evidence from my journal", "Best next move", "Why this deserves attention now",
            "Enough clarity to move forward", "What I already know / what remains unresolved"} <= labels
    # a heading never swallows the label that starts the next page
    assert not any(text(b).endswith("Evidence from my journal") for b in blocks if b["type"] == "h2")


def test_nested_lists(blocks):
    lv2 = [b for b in blocks if b["type"] == "li" and b.get("level") == 2]
    assert any(text(b).startswith("Option A:") for b in lv2)
    assert any(text(b).startswith("Hidden Assumption:") for b in lv2)


def test_every_ocr_word_is_kept_in_order(blocks):
    """Structure building may join lines and attach punctuation, but never drop OCR text."""
    ocr = read_json(OCR)
    raw = []
    for p in ocr["pages"]:
        for line in sorted(extract_ocr._merge_physical_lines(p["lines"]), key=lambda l: (l["bbox"][1], l["bbox"][0])):
            if line["bbox"][1] > p["height"] - 50:
                continue  # footer band
            raw.append(re.sub(r"[^A-Za-z0-9]", "", line["text"]))
    ours = re.sub(r"[^A-Za-z0-9]", "", "".join(text(b) + b.get("marker", "") for b in blocks))
    missing = Counter("".join(raw)) - Counter(ours)
    extra = Counter(ours) - Counter("".join(raw))
    # the only allowed difference: OCR's "4" where the PDF has a real "↳" glyph
    assert missing == Counter({"4": 1}) and not extra


def test_styles_recovered(blocks):
    toks = [t for b in blocks for t in b["tokens"]]
    assert sum(1 for t in toks if t.get("i")) > 300   # italic journal quotes
    assert sum(1 for t in toks if t["b"]) > 200        # bold run-in labels
    inline = next(b for b in blocks if "bathroom mirror" in text(b))
    assert any(t["b"] and t["t"] == "bathroom" for t in inline["tokens"])

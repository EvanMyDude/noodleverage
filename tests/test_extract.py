"""Text-layer extraction of the Lessons PDF: structure, footer removal, and no omissions."""
import re
from pathlib import Path

import pytest

from tools.align import extract_text
from tools.align.docmodel import GLYPH_FIXES

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "Top Lessons and Actions from April - June 2026.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="source PDF not present")


@pytest.fixture(scope="module")
def blocks():
    return extract_text.extract(PDF)


def text(b):
    return " ".join(t["t"] for t in b["tokens"])


def test_headings_in_reading_order(blocks):
    assert [text(b) for b in blocks if b["type"] == "h1"] == ["Top Lessons and Actions from April - June 2026"]
    assert [text(b) for b in blocks if b["type"] == "h2"] == [
        "April 2026 Top 3 Lessons:", "Top 3 Actions:", "May 2026 Top 3 Lessons:", "Top 3 Actions:",
        "June 2026 Top 3 Lessons:", "Top 3 Actions:", "Top 3 Actions from before, when they were still lessons:",
    ]


def test_every_section_has_three_numbered_items(blocks):
    items = [b for b in blocks if b["type"] == "li"]
    assert len(items) == 21
    assert [b["marker"] for b in items] == ["1.", "2.", "3."] * 7


def test_footer_and_page_numbers_removed(blocks):
    joined = " ".join(text(b) for b in blocks)
    assert joined.count("Top Lessons and Actions from April - June 2026") == 1  # the title only
    # running page numbers ("… 2026   1") would surface as a bare digit ending a block
    assert not any(re.fullmatch(r"\d+", b["tokens"][-1]["t"]) for b in blocks if not b["type"].startswith("h"))


def test_item_split_across_page_break_is_joined(blocks):
    item = next(b for b in blocks if "can mean returning consistently" in text(b))
    assert {t["p"] for t in item["tokens"]} == {2, 3}


def test_no_characters_dropped_or_added(blocks):
    raw = "".join(w for w in extract_text.raw_body_words(PDF)).translate(GLYPH_FIXES)
    raw = raw.replace(" ", "")
    ours = "".join(t["t"] for b in blocks for t in b["tokens"]) + "".join(b.get("marker", "") for b in blocks)
    assert sorted(raw) == sorted(ours)  # same multiset of characters
    assert len(raw) == len(ours)


def test_bold_lead_sentences(blocks):
    bold = [" ".join(t["t"] for t in b["tokens"] if t["b"]) for b in blocks if b["type"] == "li"]
    assert "Don’t let the to-do list be an idol." in bold
    assert "“Doing the work” ≠ making it look pretty." in bold
    assert bold[0] == ""  # April items are regular weight


def test_apostrophes_normalized(blocks):
    joined = " ".join(text(b) for b in blocks)
    assert "ʼ" not in joined and "doesn’t" in joined

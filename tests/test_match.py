"""Matcher behaviour on synthetic narrations: never invents timing, keeps repeats in order."""
from tools.align.match import align, sentences
from tools.align.normalize import spoken_tokens


def doc(*paragraphs):
    return [{"type": "p", "tokens": [{"t": w, "p": 1, "b": False} for w in para.split()]} for para in paragraphs]


def asr(words, start=0.0, step=0.4, conf=1.0):
    out, t = [], start
    for w in words.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3), "c": conf, "src": "ctc"})
        t += step
    return out


def q(res):
    return "".join(t["q"] for t in res["timing"])


def test_verbatim_reading_is_word_level_with_audio_times():
    res = align(doc("Do nothing when the urge hits."), asr("Do nothing when the urge hits."), 10)
    assert q(res) == "wwwwww"
    assert [t["s"] for t in res["timing"]] == [0.0, 0.4, 0.8, 1.2, 1.6, 2.0]


def test_numbers_hyphens_and_hashtags_match_spoken_forms():
    res = align(doc("try your own 10-second pause #NoLukewarm in 2026"),
                asr("try your own ten second pause hashtag no lukewarm in twenty twenty six"), 20)
    assert set(q(res)) == {"w"}


def test_repeated_phrase_matches_in_sequence():
    d = doc("pick one fruit now. later pick one fruit again.")
    a = asr("pick one fruit now later pick one fruit again")
    res = align(d, a, 10)
    first, second = res["timing"][0]["s"], res["timing"][5]["s"]
    assert first == 0.0 and second == 2.0  # the 2nd "pick" gets the 2nd occurrence's time


def test_skipped_words_are_not_narrated_not_interpolated():
    words = asr("one four")
    words[1]["s"], words[1]["e"] = 0.35, 0.6  # no time left for "two three"
    res = align(doc("one two three four"), words, 5)
    assert q(res) == "wnnw"
    assert "s" not in res["timing"][1]


def test_unnarrated_title_before_first_anchor():
    res = align(doc("My Title", "Body text here"), asr("body text here", start=3.0), 10)
    assert q(res) == "nnwww"


def test_substitution_gets_real_span_as_approximate():
    res = align(doc("the Hormozi plan works"), asr("the hormonesy plan works"), 10)
    assert q(res) == "wawww"[:4]
    assert res["timing"][1]["s"] == 0.4 and res["timing"][1]["e"] == 0.7


def test_spoken_insertions_are_ignored_and_reported():
    res = align(doc("Top 3 Lessons: Do nothing"), asr("Top three lessons one do nothing"), 10)
    assert set(q(res)) == {"w"}
    assert [i["text"] for i in res["insertions"]] == ["one"]


def test_compound_words():
    res = align(doc("I can NOT become trade-offs and Tradeoffs"), asr("I cannot become trade offs and trade-offs"), 10)
    assert set(q(res)) == {"w"}


def test_weakly_heard_words_are_approximate():
    a = asr("a b c")
    a[1]["c"] = 0.05
    res = align(doc("alpha b c".replace("alpha", "a")), a, 5)
    assert q(res) == "waw"


def test_symbols_have_no_timing():
    res = align(doc("know / what"), asr("know slash what"), 5)
    assert q(res) == "wxw"


def test_sentence_split_ignores_abbreviations():
    d = doc("See pg. 12 for this. Then act!")
    assert sentences(d) == [(0, 5), (5, 7)]


def test_normalizer_examples():
    assert spoken_tokens("“#NoLukewarm”") == ["no", "lukewarm"]
    assert spoken_tokens("1,000") == ["one", "thousand"]
    assert spoken_tokens("≠") == ["not", "equal"]
    assert spoken_tokens("↳") == []

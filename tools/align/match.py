"""Stage 3: map audio-derived word times onto the displayed words.

1. Expand displayed words and ASR words into spoken sub-tokens (same normalizer).
2. Anchor with difflib's longest-matching-block search (monotonic, so repeated phrases are
   matched in order and can never jump to another occurrence).
3. Inside each unmatched gap, a small fuzzy Needleman-Wunsch pairs near-spellings
   ("weather"/"whether") and records positional substitutions ("Hormozi"/"hormonesy").
4. Classify every displayed word:
     w  word-level: its sub-tokens matched words the CTC aligner heard clearly
     a  approximate: substitution / weak / unrecognized speech between anchors; timed to the
        real audio span of that speech and highlighted as a group
     n  not narrated: no speech between the neighbouring anchors
     x  symbol with no spoken form (↳, |, -): never timed, never seeks
No timestamp is ever interpolated from word counts.
"""
from __future__ import annotations

import difflib
import re

try:
    from .normalize import spoken_tokens
except ImportError:  # pragma: no cover
    from normalize import spoken_tokens

MIN_CONF = 0.25          # CTC peak-probability below this = the aligner did not clearly hear it
FUZZY = 0.72             # similarity for a near-spelling match
ABBREV = re.compile(r"^(pg|pgs|vs|e\.g|i\.e|etc|mr|mrs|dr|st|no)\.$", re.I)
SENT_END = re.compile(r"[.!?][\"'”’)\]]*$")


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) <= 2:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _nw(d: list[str], a: list[str]) -> list[tuple[int | None, int | None, str]]:
    """Global alignment of two short token lists -> [(i, j, kind)], kind in exact|fuzzy|sub|del|ins."""
    n, m = len(d), len(a)
    if n == 0:
        return [(None, j, "ins") for j in range(m)]
    if m == 0:
        return [(i, None, "del") for i in range(n)]
    if n * m > 400_000:  # pathological gap: give up pairing rather than guess
        return [(i, None, "del") for i in range(n)] + [(None, j, "ins") for j in range(m)]
    GAP = -1.0
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    P = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        S[i][0], P[i][0] = i * GAP, 1
    for j in range(1, m + 1):
        S[0][j], P[0][j] = j * GAP, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = _sim(d[i - 1], a[j - 1])
            diag = S[i - 1][j - 1] + (2.0 if s == 1.0 else 1.0 if s >= FUZZY else -0.6)
            up, left = S[i - 1][j] + GAP, S[i][j - 1] + GAP
            cands = [(diag, 0), (up, 1), (left, 2)]
            # compounds: "can"+"not" = "cannot"; "tradeoffs" = "trade"+"offs"
            if i >= 2 and d[i - 2] + d[i - 1] == a[j - 1]:
                cands.append((S[i - 2][j - 1] + 4.0, 3))
            if j >= 2 and d[i - 1] == a[j - 2] + a[j - 1]:
                cands.append((S[i - 1][j - 2] + 4.0, 4))
            best, move = max(cands, key=lambda c: c[0])
            S[i][j], P[i][j] = best, move
    out, i, j = [], n, m
    while i > 0 or j > 0:
        p = P[i][j] if i > 0 and j > 0 else (1 if i > 0 else 2)
        if p == 3:
            out += [(i - 1, j - 1, "exact"), (i - 2, j - 1, "exact")]
            i, j = i - 2, j - 1
            continue
        if p == 4:
            out += [(i - 1, j - 1, "exact"), (i - 1, j - 2, "exact")]
            i, j = i - 1, j - 2
            continue
        if p == 0:
            s = _sim(d[i - 1], a[j - 1])
            out.append((i - 1, j - 1, "exact" if s == 1.0 else "fuzzy" if s >= FUZZY else "sub"))
            i, j = i - 1, j - 1
        elif p == 1:
            out.append((i - 1, None, "del")); i -= 1
        else:
            out.append((None, j - 1, "ins")); j -= 1
    out.reverse()
    return out


ONSET_BACKOFF = 0.08  # CTC spikes land on the most confident frame, a little after the onset


def flatten_asr(asr: dict, ctc: dict) -> list[dict]:
    words = [w for seg in asr["segments"] for w in seg["words"]]
    assert len(words) == len(ctc["words"]), "ASR/CTC word count mismatch"
    out, last_s, last_e = [], 0.0, 0.0
    for w, c in zip(words, ctc["words"]):
        s = max(c["s"], last_s)            # tiny overlaps between segment windows
        if c["src"] == "ctc":
            # onset compensation: move into the gap before the word (at most half of it, so a short
            # preceding word keeps its tail) and never into the previous word
            s = max(last_e, s - min(ONSET_BACKOFF, max(0.0, s - last_e) / 2), last_s)
        e = max(c["e"], s + 0.02)
        out.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3), "c": c["c"], "src": c["src"]})
        last_s, last_e = s, e
    return out


def align(blocks: list[dict], asr_words: list[dict], duration: float) -> dict:
    words = [t for b in blocks for t in b["tokens"]]
    D = [(gi, s) for gi, t in enumerate(words) for s in spoken_tokens(t["t"])]
    A = [(ai, s) for ai, w in enumerate(asr_words) for s in spoken_tokens(w["w"])]

    pairs: list[tuple[int, int, str]] = []          # (d_sub_index, a_sub_index, kind)
    a_used: set[int] = set()
    sm = difflib.SequenceMatcher(None, [s for _, s in D], [s for _, s in A], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            pairs += [(i1 + k, j1 + k, "exact") for k in range(i2 - i1)]
        else:
            for i, j, kind in _nw([s for _, s in D[i1:i2]], [s for _, s in A[j1:j2]]):
                if i is not None and j is not None:
                    pairs.append((i1 + i, j1 + j, kind))
    by_word: dict[int, list[tuple[int, str]]] = {}
    for di, aj, kind in pairs:
        by_word.setdefault(D[di][0], []).append((A[aj][0], kind))
        if kind in ("exact", "fuzzy"):
            a_used.add(A[aj][0])
    subs_per_word: dict[int, int] = {}
    for gi, _ in D:
        subs_per_word[gi] = subs_per_word.get(gi, 0) + 1

    # --- per-word classification
    timing: list[dict] = []
    for gi, t in enumerate(words):
        n_sub = subs_per_word.get(gi, 0)
        if n_sub == 0:
            timing.append({"q": "x"}); continue
        got = by_word.get(gi, [])
        good = [ai for ai, k in got if k in ("exact", "fuzzy")]
        clear = [ai for ai in good if asr_words[ai]["c"] >= MIN_CONF]
        if good and len(good) * 2 >= n_sub and clear:
            ss = [asr_words[ai] for ai in good]
            timing.append({"q": "w", "s": min(w["s"] for w in ss), "e": max(w["e"] for w in ss)})
        elif got:
            ss = [asr_words[ai] for ai, _ in got]
            timing.append({"q": "a", "s": min(w["s"] for w in ss), "e": max(w["e"] for w in ss), "why": "substitution" if not good else "weak"})
            a_used.update(ai for ai, _ in got)
        else:
            timing.append({"q": "?"})

    # --- unresolved runs: look for real speech between the neighbouring anchors
    i = 0
    while i < len(timing):
        if timing[i]["q"] != "?":
            i += 1; continue
        j = i
        while j < len(timing) and timing[j]["q"] in ("?", "x"):
            j += 1
        run = [k for k in range(i, j) if timing[k]["q"] == "?"]
        left = next((timing[k] for k in range(i - 1, -1, -1) if "s" in timing[k]), None)
        right = next((timing[k] for k in range(j, len(timing)) if "s" in timing[k]), None)
        lo = left["e"] if left else 0.0
        hi = right["s"] if right else duration
        heard = [ai for ai, w in enumerate(asr_words) if ai not in a_used and w["s"] >= lo - 0.05 and w["e"] <= hi + 0.05]
        if heard:
            s, e = asr_words[heard[0]]["s"], asr_words[heard[-1]]["e"]
            for k in run:
                timing[k] = {"q": "a", "s": s, "e": e, "why": "unrecognized"}
            a_used.update(heard)
        elif left and right and len(run) <= 2 and (hi - lo) >= 0.18 * len(run):
            for k in run:
                timing[k] = {"q": "a", "s": round(lo, 3), "e": round(hi, 3), "why": "gap"}
        else:
            for k in run:
                timing[k] = {"q": "n"}
        i = j

    violations = finalize(timing)

    insertions = []
    run: list[int] = []
    for ai in range(len(asr_words) + 1):
        if ai < len(asr_words) and ai not in a_used:
            run.append(ai)
        elif run:
            insertions.append({"s": asr_words[run[0]]["s"], "e": asr_words[run[-1]]["e"],
                               "text": " ".join(asr_words[k]["w"] for k in run)})
            run = []

    review = []  # spoken form differs from displayed text (OCR / homophone / narration)
    for gi, got in by_word.items():
        kinds = {k for _, k in got}
        if kinds & {"fuzzy", "sub"}:
            review.append({"word": gi, "text": words[gi]["t"], "page": words[gi]["p"],
                           "heard": " ".join(asr_words[ai]["w"] for ai, _ in got), "kinds": sorted(kinds)})
    return {"timing": timing, "insertions": insertions, "review": review, "orderFixes": violations}


def finalize(timing: list[dict]) -> int:
    """Group approximate words that share one audio span; demote out-of-order words."""
    g = 0
    for k, tm in enumerate(timing):
        tm.pop("g", None)
        if tm["q"] == "a":
            prev = timing[k - 1] if k else None
            if not (prev and prev["q"] == "a" and prev["s"] == tm["s"] and prev["e"] == tm["e"]):
                g += 1
            tm["g"] = g
    # a click must never land earlier than the previous word
    last, violations = -1.0, 0
    for tm in timing:
        if "s" in tm:
            if tm["s"] < last - 0.05 and tm["q"] == "w":
                tm["q"] = "a"; tm["why"] = "order"; violations += 1
                g += 1; tm["g"] = g
            last = max(last, tm["s"])
    return violations


FILL_MIN_CONF = 0.5   # second pass: every character must be clearly heard on average
FILL_ANCHORS = 2


def gap_requests(words: list[dict], timing: list[dict], duration: float) -> list[dict]:
    """Windows for the second pass: each run of not-word-level words plus anchors."""
    reqs, k = [], 0
    while k < len(timing):
        if timing[k]["q"] not in ("n", "a"):
            k += 1; continue
        j = k
        while j < len(timing) and timing[j]["q"] in ("n", "a", "x"):
            j += 1
        left = [i for i in range(k - 1, -1, -1) if timing[i]["q"] == "w"][:FILL_ANCHORS][::-1]
        right = [i for i in range(j, len(timing)) if timing[i]["q"] == "w"][:FILL_ANCHORS]
        idx = left + [i for i in range(k, j) if timing[i]["q"] != "x"] + right
        if len(idx) - len(left) - len(right) <= 80:
            s0 = timing[left[0]]["s"] - 0.15 if left else 0.0
            e0 = timing[right[-1]]["e"] + 0.15 if right else duration
            reqs.append({"id": len(reqs), "s": round(max(0.0, s0), 3), "e": round(min(duration, e0), 3),
                         "idx": idx, "words": [words[i]["t"] for i in idx]})
        k = j
    return reqs


def apply_fill(timing: list[dict], requests: list[dict], results: list[dict]) -> list[int]:
    """Accept second-pass times for clearly heard words; returns indices newly word-level."""
    recovered = []
    by_id = {r["id"]: r for r in results}
    for rq in requests:
        res = (by_id.get(rq["id"]) or {}).get("words")
        if not res:
            continue
        for i, r in zip(rq["idx"], res):
            if r is None:
                continue
            s, e, c = r
            if c < FILL_MIN_CONF or e - s < 0.03:
                continue
            if timing[i]["q"] == "w":
                timing[i].update(s=s, e=e)            # anchors re-timed with the full text present
            else:
                timing[i] = {"q": "w", "s": s, "e": e, "why": "second-pass"}
                recovered.append(i)
    # a lone short word left between two spoken neighbours (e.g. a reduced "a") was said in
    # passing: time it to the gap between them, marked approximate rather than "not narrated"
    for i, tm in enumerate(timing):
        if tm["q"] != "n":
            continue
        lft = next((timing[k] for k in range(i - 1, -1, -1) if timing[k]["q"] != "x"), None)
        rgt = next((timing[k] for k in range(i + 1, len(timing)) if timing[k]["q"] != "x"), None)
        if lft and rgt and lft["q"] == "w" and rgt["q"] == "w" and 0 <= rgt["s"] - lft["e"] <= 1.0:
            timing[i] = {"q": "a", "s": lft["e"], "e": max(rgt["s"], lft["e"] + 0.02), "why": "between-neighbours"}
    finalize(timing)
    return recovered


def sentences(blocks: list[dict]) -> list[tuple[int, int]]:
    """Sentence ranges [w0, w1) over the flat word list. Headings are one sentence each."""
    out, base = [], 0
    for b in blocks:
        toks = b["tokens"]
        start = 0
        for k, t in enumerate(toks):
            last = k == len(toks) - 1
            if last or (b["type"] not in ("h1", "h2", "h3", "h4") and SENT_END.search(t["t"]) and not ABBREV.match(t["t"])):
                out.append((base + start, base + k + 1))
                start = k + 1
        base += len(toks)
    return out

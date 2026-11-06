"""Stage 2: refine Whisper's word times with wav2vec2 CTC forced alignment (~20 ms frames).

For each Whisper segment we take the audio window (padded), compute wav2vec2 character
emissions, and force-align the *spoken* words Whisper heard with a small Viterbi pass.
Each ASR word gets [start, end, confidence]; words that can't be aligned keep Whisper's
own timing and are flagged so later stages treat them as lower-precision.

Runs in its own process (no ctranslate2 import here) to avoid the macOS OpenMP clash.

    python -m tools.align.ctc <audio.mp3> <asr.json> <out.json>
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import av
import numpy as np
import torch
import torchaudio

try:
    from .common import read_json, write_json
    from .normalize import ctc_text
except ImportError:  # pragma: no cover
    from common import read_json, write_json
    from normalize import ctc_text

SR = 16000
PAD = 0.35  # seconds of context around each Whisper segment


def decode_audio(path: Path) -> np.ndarray:
    """Same decode path faster-whisper uses (PyAV -> s16 mono 16 kHz), so timelines match."""
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SR)
    chunks = []
    with av.open(str(path), mode="r", metadata_errors="ignore") as container:
        for frame in container.decode(audio=0):
            frame.pts = None
            for f in resampler.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
    return (np.concatenate(chunks).astype(np.float32) / 32768.0) if chunks else np.zeros(0, np.float32)


def viterbi(emission: np.ndarray, tokens: list[int], blank: int = 0):
    """Standard CTC forced alignment over the blank-interleaved label sequence.

    Leading/trailing silence and pauses are absorbed by blank states, so a word's span covers
    only frames where its own characters are emitted. Returns [(token_index, frame, prob)] for
    every frame whose best path sits on a (non-blank) character state.
    """
    T, L = emission.shape[0], len(tokens)
    if L == 0:
        return None
    S = 2 * L + 1
    ext = np.full(S, blank, dtype=np.int64)
    ext[1::2] = tokens
    # skip transition s-2 -> s allowed for a character that differs from the one two back
    skip = np.zeros(S, dtype=bool)
    skip[3::2] = ext[3::2] != ext[1:-2:2]
    if T < L + int((~skip[1::2]).sum()) - 1:  # not enough frames for the label sequence
        return None
    NEG = np.float32(-1e30)
    score = np.full(S, NEG, dtype=np.float32)
    score[0] = emission[0, blank]
    score[1] = emission[0, ext[1]]
    back = np.zeros((T, S), dtype=np.int8)  # 0 stay, 1 from s-1, 2 from s-2
    for t in range(1, T):
        prev1 = np.concatenate(([NEG], score[:-1]))
        prev2 = np.concatenate(([NEG, NEG], score[:-2]))
        prev2 = np.where(skip, prev2, NEG)
        cand = np.stack([score, prev1, prev2])
        arg = cand.argmax(axis=0)
        score = cand[arg, np.arange(S)] + emission[t, ext]
        back[t] = arg
    s = S - 1 if score[S - 1] >= score[S - 2] else S - 2
    if score[s] <= NEG / 2:
        return None
    path = []
    for t in range(T - 1, -1, -1):
        if s % 2 == 1:
            path.append(((s - 1) // 2, t, float(np.exp(emission[t, ext[s]]))))
        s -= int(back[t, s])
    path.reverse()
    return path


def load_model():
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().eval()
    index = {c: i for i, c in enumerate(bundle.get_labels())}  # ('-', '|', 'E', 'T', ...)
    return model, index


def align_window(model, index, wave: np.ndarray, w0: float, w1: float, texts: list[str]):
    """Force-align `texts` (one entry per word) inside [w0, w1] seconds.
    Returns [(start, end, confidence) | None per word], or None if alignment is impossible."""
    w0, w1 = max(0.0, w0), min(len(wave) / SR, w1)
    clip = torch.from_numpy(wave[int(w0 * SR): int(w1 * SR)]).unsqueeze(0)
    if clip.shape[1] < SR // 10:
        return None
    with torch.inference_mode():
        emis, _ = model(clip)
        emis = torch.log_softmax(emis, dim=-1)[0].numpy()
    stride = (w1 - w0) / emis.shape[0]
    tokens, owner = [], []
    for wi, w in enumerate(texts):
        txt = ctc_text(w)
        if not txt:
            continue
        if tokens:
            tokens.append(index["|"]); owner.append(-1)
        for ch in txt:
            tokens.append(index.get(ch, index["|"])); owner.append(wi)
    path = viterbi(emis, tokens) if tokens else None
    if path is None:
        return None
    spans: dict[int, list] = {}
    for j, t, p in path:
        wi = owner[j]
        if wi < 0:
            continue
        sp = spans.setdefault(wi, [t, t, {}])
        sp[0], sp[1] = min(sp[0], t), max(sp[1], t)
        sp[2][j] = max(sp[2].get(j, 0.0), p)  # peak emission prob per character
    out = []
    for wi in range(len(texts)):
        if wi in spans:
            f0, f1, peaks = spans[wi]
            out.append((round(w0 + f0 * stride, 3), round(w0 + (f1 + 1) * stride, 3), round(float(np.mean(list(peaks.values()))), 3)))
        else:
            out.append(None)
    return out


def align(audio_path: Path, asr: dict) -> dict:
    model, index = load_model()
    wave = decode_audio(audio_path)
    if abs(len(wave) / SR - asr["duration"]) > 0.05:
        raise SystemExit(f"decoded length {len(wave)/SR:.3f}s differs from ASR stage {asr['duration']}s")
    out_words = []
    t0 = time.time()
    for seg in asr["segments"]:
        words = seg["words"]
        if not words:
            continue
        w0 = min(seg["start"], words[0]["s"]) - PAD
        w1 = max(seg["end"], words[-1]["e"]) + PAD
        res = align_window(model, index, wave, w0, w1, [w["w"] for w in words]) or [None] * len(words)
        for w, r in zip(words, res):
            # sanity: CTC and Whisper should agree to within a second
            if r is None or (abs(r[0] - w["s"]) > 1.0 and abs(r[1] - w["e"]) > 1.0):
                out_words.append({"s": w["s"], "e": w["e"], "c": 0.0, "src": "whisper"})
            else:
                out_words.append({"s": r[0], "e": r[1], "c": r[2], "src": "ctc"})
    return {
        "engine": f"torchaudio {torchaudio.__version__} WAV2VEC2_ASR_BASE_960H + numpy Viterbi",
        "elapsedSec": round(time.time() - t0, 1),
        "words": out_words,
    }


def fill(audio_path: Path, requests: list[dict]) -> list[dict]:
    """Second pass: force-align the *displayed* words of each gap (plus anchor words on both
    sides) against the audio between the anchors. Spoken words get confident character
    emissions; words that were never said are forced onto silence and score near zero."""
    model, index = load_model()
    wave = decode_audio(audio_path)
    out = []
    for rq in requests:
        res = align_window(model, index, wave, rq["s"], rq["e"], rq["words"])
        out.append({"id": rq["id"], "words": res})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("asr", type=Path, help="ASR JSON, or a gap-request JSON with --fill")
    ap.add_argument("out", type=Path)
    ap.add_argument("--fill", action="store_true", help="second pass over gaps in the displayed text")
    a = ap.parse_args()
    torch.set_num_threads(8)
    if a.fill:
        res = fill(a.audio, read_json(a.asr)["requests"])
        write_json(a.out, {"results": res})
        print(f"ctc fill: {len(res)} gap windows -> {a.out}")
        return
    res = align(a.audio, read_json(a.asr))
    write_json(a.out, res)
    n = len(res["words"]); ok = sum(w["src"] == "ctc" for w in res["words"])
    print(f"ctc: {ok}/{n} words force-aligned in {res['elapsedSec']}s -> {a.out}")


if __name__ == "__main__":
    main()

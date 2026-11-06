"""Stage 1: transcribe the narration with faster-whisper (local, CPU) and keep word timestamps.

Run as its own process (see build_alignment.py): ctranslate2 and torch each bundle an
OpenMP runtime on macOS, and loading both in one interpreter can abort.

    python -m tools.align.asr <audio.mp3> <out.json> [--model small.en]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from faster_whisper import WhisperModel, __version__ as fw_version
from faster_whisper.audio import decode_audio

try:  # allow both `python -m tools.align.asr` and direct execution
    from .common import write_json
except ImportError:  # pragma: no cover
    from common import write_json

SR = 16000


def transcribe(audio_path: Path, model_name: str) -> dict:
    audio = decode_audio(str(audio_path), sampling_rate=SR)  # PyAV's bundled FFmpeg; no system ffmpeg
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=8)
    t0 = time.time()
    segments, info = model.transcribe(
        audio,
        language="en",
        beam_size=5,
        word_timestamps=True,
        condition_on_previous_text=False,  # avoids runaway repetition on long files
        vad_filter=False,  # keep the original timeline untouched
    )
    out_segments = []
    for seg in segments:
        out_segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [
                {"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}
                for w in (seg.words or [])
                if w.word.strip()
            ],
        })
    return {
        "engine": f"faster-whisper {fw_version}",
        "model": model_name,
        "sampleRate": SR,
        "samples": int(len(audio)),
        "duration": round(len(audio) / SR, 3),
        "elapsedSec": round(time.time() - t0, 1),
        "segments": out_segments,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", default="small.en")
    a = ap.parse_args()
    result = transcribe(a.audio, a.model)
    write_json(a.out, result)
    n = sum(len(s["words"]) for s in result["segments"])
    print(f"asr: {a.audio.name}: {len(result['segments'])} segments, {n} words, "
          f"{result['duration']}s audio in {result['elapsedSec']}s -> {a.out}")


if __name__ == "__main__":
    main()

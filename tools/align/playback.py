"""Seek-accurate playback copy of the narration.

Browsers seek VBR MP3s through the Xing table of contents, which only has 100 coarse
entries: measured in Chrome on these files, a seek landed up to ±1.2 s away from the
reported currentTime. A constant-bitrate MP3 is byte-addressable, so seeks land on the
exact frame (measured error <= one 26 ms frame).

This module re-encodes the *same recording* to CBR (PyAV's bundled libmp3lame), then
verifies that its decoded timeline matches the original sample-for-sample before the app
is allowed to use it. Word timestamps still refer to the original MP3's timeline.
"""
from __future__ import annotations

from pathlib import Path

import av
import numpy as np

CBR_BITRATE = 256_000


def mp3_is_vbr(path: Path) -> bool:
    if path.suffix.lower() != ".mp3":
        return False
    head = path.read_bytes()[:16384]
    return b"Xing" in head or b"VBRI" in head


def encode_cbr(src: Path, dst: Path, bitrate: int = CBR_BITRATE) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.mp3")
    with av.open(str(src)) as inp, av.open(str(tmp), "w", format="mp3") as out:
        ist = inp.streams.audio[0]
        ost = out.add_stream("libmp3lame", rate=ist.rate)
        ost.layout = ist.layout.name if ist.layout else "stereo"
        ost.bit_rate = bitrate
        for frame in inp.decode(ist):
            frame.pts = None
            for p in ost.encode(frame):
                out.mux(p)
        for p in ost.encode(None):
            out.mux(p)
    tmp.replace(dst)


def _decode16k(path: Path) -> np.ndarray:
    res = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(str(path)) as c:
        for f in c.decode(audio=0):
            f.pts = None
            chunks += [x.to_ndarray().reshape(-1) for x in res.resample(f)]
        chunks += [x.to_ndarray().reshape(-1) for x in res.resample(None)]
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def timeline_offset(original: Path, copy: Path, probes=(0.07, 0.5, 0.93)) -> dict:
    """Offset (s) of the copy's content relative to the original at several points."""
    a, b = _decode16k(original), _decode16k(copy)
    out = []
    for frac in probes:
        t = int(len(a) * frac)
        x = a[t: t + 32000]
        best = (-2.0, 0)
        for lag in range(-800, 801):
            y = b[t + lag: t + lag + 32000]
            if len(y) < len(x):
                continue
            c = float(np.dot(x, y) / np.sqrt(np.dot(x, x) * np.dot(y, y) + 1e-12))
            if c > best[0]:
                best = (c, lag)
        out.append({"at": round(t / 16000, 1), "offsetMs": round(best[1] / 16, 2), "corr": round(best[0], 4)})
    return {"lengthDiffMs": round((len(b) - len(a)) / 16, 2), "probes": out}


def ensure_playback_copy(src: Path, dst: Path) -> dict:
    """Return {'file', 'offset', 'check'} for the app; uses the original when it is not VBR."""
    if not mp3_is_vbr(src):
        return {"file": src.name, "offset": 0.0, "check": "original file is constant-bitrate or not MP3; used as-is"}
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        print(f"  making seek-accurate CBR copy of {src.name} ...", flush=True)
        encode_cbr(src, dst)
    chk = timeline_offset(src, dst)
    offs = [p["offsetMs"] for p in chk["probes"]]
    if max(offs) - min(offs) > 1.0 or min(p["corr"] for p in chk["probes"]) < 0.95:
        raise SystemExit(f"playback copy does not track the original timeline: {chk}")
    return {"file": dst.as_posix(), "offset": round(-float(np.median(offs)) / 1000, 4), "check": chk}

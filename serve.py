#!/usr/bin/env python3
"""Serve the Insight Reader locally (127.0.0.1 only), with HTTP Range support.

    python3 serve.py            # http://127.0.0.1:8765, opens your browser
    python3 serve.py --port 9000 --no-open

Python's built-in http.server ignores Range requests; browsers need 206 Partial Content
responses to seek inside audio, so this handler implements single-range requests.
Standard library only: no virtualenv needed to read.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")
PRIVATE = (".venv", "tools", "tests", "data/cache", ".git", "__pycache__")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("READER_LOG"):
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _blocked(self) -> bool:
        rel = self.path.split("?", 1)[0].lstrip("/")
        return any(rel == p or rel.startswith(p + "/") for p in PRIVATE)

    def do_HEAD(self):
        if self._blocked():
            return self.send_error(HTTPStatus.NOT_FOUND)
        return super().do_HEAD()

    def do_GET(self):
        if self._blocked():
            return self.send_error(HTTPStatus.NOT_FOUND)
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().do_GET()
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        size = path.stat().st_size
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return super().do_GET()
        if m.group(1) == "":           # suffix range: last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        if start >= size or start > end:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser cancelled this range (normal while scrubbing)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if not (ROOT / "data" / "library.json").exists():
        print("note: data/library.json is missing; run  .venv/bin/python tools/build_alignment.py  first.")
    server = ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(ROOT)))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Insight Reader: {url}   (Ctrl+C to stop)", flush=True)
    if not a.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

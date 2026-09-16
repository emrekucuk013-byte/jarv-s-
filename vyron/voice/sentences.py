"""Turn a stream of text deltas into whole sentences, so speech can start early."""

from __future__ import annotations

import re

_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


class SentenceChunker:
    def __init__(self, min_chars: int = 12):
        self.buffer = ""
        self.min_chars = min_chars  # boundaries closer than this (e.g. "Dr.") are ignored

    def feed(self, delta: str) -> list[str]:
        self.buffer += delta
        out: list[str] = []
        start = 0
        for m in _BOUNDARY.finditer(self.buffer):
            if m.start() - start >= self.min_chars:
                out.append(self.buffer[start:m.start()].strip())
                start = m.end()
        self.buffer = self.buffer[start:]
        return [s for s in out if s]

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []

"""cchardet via faust-cchardet: the C uchardet binding (the original cchardet stops building at Python 3.10)."""

from __future__ import annotations

from typing import Final

import cchardet

REQUIREMENTS = ("faust-cchardet>=2.1.19",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with cchardet's uchardet engine."""
    cchardet.detect(data)


def _encoding_chunks(case: tuple[int, bytes]) -> None:
    chunk_size, data = case
    detector: Final = cchardet.UniversalDetector()
    for start in range(0, len(data), chunk_size):
        detector.feed(data[start : start + chunk_size])
    detector.close()


def _encoding_stream(data: bytes) -> None:
    detector: Final = cchardet.UniversalDetector()
    detector.feed(data)
    detector.close()


OPERATIONS = {
    "encoding-result-stream": (_encoding_stream, "faust-cchardet"),
    "encoding-chunks": (_encoding_chunks, "faust-cchardet"),
    "encoding": (encoding, "faust-cchardet"),
    "encoding-result": (encoding, "faust-cchardet"),
}

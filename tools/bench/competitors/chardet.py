"""chardet: the pure-Python universal character encoding detector."""

from __future__ import annotations

from typing import Final

import chardet

REQUIREMENTS = ("chardet>=5.2",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's character encoding with chardet's prober ensemble."""
    chardet.detect(data)


def _encoding_chunks(case: tuple[int, bytes]) -> None:
    chunk_size, data = case
    detector: Final = chardet.UniversalDetector()
    for start in range(0, len(data), chunk_size):
        detector.feed(data[start : start + chunk_size])
    detector.close()


def _encoding_stream(data: bytes) -> None:
    detector: Final = chardet.UniversalDetector()
    detector.feed(data)
    detector.close()


OPERATIONS = {
    "encoding-result-stream": (_encoding_stream, "chardet"),
    "encoding-chunks": (_encoding_chunks, "chardet"),
    "encoding": (encoding, "chardet"),
    "encoding-result": (encoding, "chardet"),
}

"""charset-normalizer: the chaos/coherence-scoring detector requests depends on."""

from __future__ import annotations

from typing import Final

from charset_normalizer import from_bytes

_REQUIREMENTS: Final[tuple[str, ...]] = ("charset-normalizer>=3.4",)


def encoding(data: bytes) -> None:
    # Detect a byte stream's character encoding with charset-normalizer's best match.
    from_bytes(data).best()


OPERATIONS = {"encoding": (encoding, "charset-normalizer"), "encoding-result": (encoding, "charset-normalizer")}

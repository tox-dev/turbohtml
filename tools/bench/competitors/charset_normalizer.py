"""charset-normalizer: the chaos/coherence-scoring detector requests depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from charset_normalizer import from_bytes

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("charset-normalizer>=3.4",)


def encoding(data: bytes) -> None:
    # Detect a byte stream's character encoding with charset-normalizer's best match.
    from_bytes(data).best()


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "encoding": (encoding, "charset-normalizer"),
    "encoding-result": (encoding, "charset-normalizer"),
}

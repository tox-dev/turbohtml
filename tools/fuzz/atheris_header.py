"""
Every Atheris input starts with libxml2's ``[opts][failure_pos][max_chunk]`` header of three big-endian 4-byte fields.

libxml2's fuzzers read the same fields before the document
(https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/xml.c#L46-L56), and the bridge's
custom mutator keeps them aligned (``atheris_bridge.c``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AnyStr, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_FIELD_SIZE: Final[int] = 4
HEADER_SIZE: Final[int] = 3 * _FIELD_SIZE


def split_header(data: bytes) -> tuple[Header, bytes]:
    # Read each field as ``xmlFuzzReadInt(4)`` does, so a short input yields fewer bytes and a smaller value.
    opts, failure_pos, max_chunk = (
        int.from_bytes(data[start : start + _FIELD_SIZE], "big") for start in range(0, HEADER_SIZE, _FIELD_SIZE)
    )
    size: Final = len(data)
    # libxml2 bounds the failure position by the size plus 100 and the chunk by the size plus an eighth, reading 0 as 1,
    # so random fields land near the input's allocation count and length
    # (https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/xml.c#L52-L56).
    return Header(opts, failure_pos % (size + 100), max(max_chunk % (size + size // 8 + 1), 1)), data[HEADER_SIZE:]


def feed_chunks(feed: Callable[[AnyStr], object], data: AnyStr, max_chunk: int) -> None:
    # Feed ``data`` in ``max_chunk`` pieces with an empty feed before and after each, which must change nothing.
    feed(data[:0])
    for chunk in _chunks(data, max_chunk):
        feed(chunk)
        feed(chunk[:0])


def _chunks(data: AnyStr, max_chunk: int) -> Iterator[AnyStr]:
    # libxml2's push loop takes max_chunk units per piece until 50 + len/100 pieces, then the rest, which bounds the
    # feed count of a large input with a small chunk
    # (https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/xml.c#L137-L157)
    start = 0
    for _ in range(50 + len(data) // 100 - 1):
        if len(data) - start <= max_chunk:
            break
        yield data[start : start + max_chunk]
        start += max_chunk
    yield data[start:]


class Header(NamedTuple):
    # A ``failure_pos`` of 0 injects no allocation failure.
    opts: int
    failure_pos: int
    max_chunk: int

    def encode(self) -> bytes:
        # Seed files carry the header too.
        return b"".join(field.to_bytes(_FIELD_SIZE, "big") for field in self)


# libxml2's seed generator writes options 0, no failure and a 256-byte push chunk
# (https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/genSeed.c#L133-L141).
SEED_HEADER: Final[bytes] = Header(0, 0, 256).encode()
# One unit per feed cuts inside every multibyte sequence.
BYTEWISE: Final[Header] = Header(0, 0, 1)

__all__ = [
    "BYTEWISE",
    "HEADER_SIZE",
    "SEED_HEADER",
    "Header",
    "feed_chunks",
    "split_header",
]

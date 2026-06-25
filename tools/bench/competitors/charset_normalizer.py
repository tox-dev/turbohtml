"""charset-normalizer: the pure-Python detector ``requests`` pulls in as a transitive dependency."""

from __future__ import annotations

from charset_normalizer import from_bytes

REQUIREMENTS = ("charset-normalizer>=3.4",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's encoding with charset-normalizer's best-match search."""
    from_bytes(data).best()


OPERATIONS = {"encoding": (encoding, "charset-normalizer")}

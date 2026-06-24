"""resiliparse: parse with the same lexbor engine selectolax wraps."""

from __future__ import annotations

from resiliparse.parse.html import HTMLTree  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs

REQUIREMENTS = ("resiliparse>=1.0.8",)


def parse(text: str) -> None:
    """Parse a whole document with resiliparse."""
    HTMLTree.parse(text)


OPERATIONS = {"parse": (parse, "resiliparse")}

"""resiliparse: parse with the same lexbor engine selectolax wraps."""

from __future__ import annotations

from resiliparse.extract.html2text import (  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
    extract_plain_text,
)
from resiliparse.parse.html import HTMLTree  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs

REQUIREMENTS = ("resiliparse>=1.0.8",)


def parse(text: str) -> None:
    """Parse a whole document with resiliparse."""
    HTMLTree.parse(text)


def text_render(text: str) -> None:
    """Extract visible text with resiliparse, off the lexbor tree it parses to."""
    extract_plain_text(text)


def text_main(text: str) -> None:
    """Extract the boilerplate-stripped main text with resiliparse's main_content mode."""
    extract_plain_text(text, main_content=True)


OPERATIONS = {
    "parse": (parse, "resiliparse"),
    "text-render": (text_render, "resiliparse"),
    "text-main": (text_main, "resiliparse"),
}

"""readability-lxml: the Arc90 Readability port that scores the content body over lxml."""

from __future__ import annotations

from typing import Final

from readability import Document

_REQUIREMENTS: Final[tuple[str, ...]] = ("readability-lxml>=0.8.1",)


def article(text: str) -> None:
    # Extract the content body and title with readability-lxml.
    document = Document(text)
    document.summary()
    document.short_title()


OPERATIONS = {"article": (article, "readability-lxml")}

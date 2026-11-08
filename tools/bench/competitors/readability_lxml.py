"""readability-lxml: the Arc90 Readability port that scores the content body over lxml."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from readability import Document

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("readability-lxml>=0.8.1",)


def article(text: str) -> None:
    # Extract the content body and title with readability-lxml.
    document = Document(text)
    document.summary()
    document.short_title()


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"article": (article, "readability-lxml")}

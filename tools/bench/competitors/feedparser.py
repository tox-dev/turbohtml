"""feedparser: the canonical Python RSS/Atom/RDF feed parser, the competitor to turbohtml's feed surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import feedparser

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("feedparser>=6.0",)


def syndication(text: str) -> None:
    # Parse the feed with feedparser, which runs its own SAX-based scanner and normalizes every item.
    feedparser.parse(text)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"syndication": (syndication, "feedparser")}

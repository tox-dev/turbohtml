"""feedparser: the canonical Python RSS/Atom/RDF feed parser, the competitor to turbohtml's feed surface."""

from __future__ import annotations

import feedparser

REQUIREMENTS = ("feedparser>=6.0",)


def syndication(text: str) -> None:
    """Parse the feed with feedparser, which runs its own SAX-based scanner and normalizes every item."""
    feedparser.parse(text)


OPERATIONS = {"syndication": (syndication, "feedparser")}

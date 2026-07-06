"""trafilatura: the lxml-backed main-text and metadata extractor turbohtml.article succeeds."""

from __future__ import annotations

import trafilatura

REQUIREMENTS = ("trafilatura>=1.12",)


def article(text: str) -> None:
    """Extract the content body and metadata with trafilatura, on an lxml tree."""
    trafilatura.bare_extraction(text, with_metadata=True)


def text_main(text: str) -> None:
    """Extract the boilerplate-stripped main text with trafilatura's extract."""
    trafilatura.extract(text)


def date(text: str) -> None:
    """Read the publication date off trafilatura's metadata, its dedicated metadata pass."""
    _ = trafilatura.extract_metadata(text).date


OPERATIONS = {
    "article": (article, "trafilatura"),
    "text-main": (text_main, "trafilatura"),
    "date": (date, "trafilatura"),
}

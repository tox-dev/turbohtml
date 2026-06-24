"""trafilatura: the lxml-backed main-text and metadata extractor turbohtml.article succeeds."""

from __future__ import annotations

import trafilatura

REQUIREMENTS = ("trafilatura>=1.12",)


def article(text: str) -> None:
    """Extract the content body and metadata with trafilatura, on an lxml tree."""
    trafilatura.bare_extraction(text, with_metadata=True)


OPERATIONS = {"article": (article, "trafilatura")}

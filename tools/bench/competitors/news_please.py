"""news-please: the crawler-grade news extractor turbohtml.article succeeds for in-hand HTML."""

from __future__ import annotations

# not a bench group dependency: its newspaper4k requirement clobbers newspaper3k's newspaper module
from newsplease import NewsPlease  # ty: ignore[unresolved-import]

REQUIREMENTS = ("news-please>=1.6",)

_URL = "https://example.com/article"


def article(text: str) -> None:
    """Extract title, main text, and metadata with news-please's extractor ensemble."""
    NewsPlease.from_html(text, url=_URL)


OPERATIONS = {"article": (article, "news-please")}

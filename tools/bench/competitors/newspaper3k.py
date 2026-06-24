"""newspaper3k: the news scraper turbohtml.article succeeds (pins old dependencies, often unresolvable)."""

from __future__ import annotations

from newspaper import Article

REQUIREMENTS = ("newspaper3k>=0.2.8",)


def article(text: str) -> None:
    """Extract content and metadata with newspaper3k, from pre-set HTML."""
    parsed = Article(url="")
    parsed.set_html(text)
    parsed.parse()


OPERATIONS = {"article": (article, "newspaper3k")}

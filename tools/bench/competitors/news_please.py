"""news-please: the news crawler whose article body and metadata turbohtml.article succeeds."""

from __future__ import annotations

from newsplease import NewsPlease

REQUIREMENTS = ("news-please>=1.6",)


def article(text: str) -> None:
    """Extract the article body and metadata with news-please, from pre-set HTML."""
    NewsPlease.from_html(text, url=None)


OPERATIONS = {"article": (article, "news-please")}

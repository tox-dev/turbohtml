"""newspaper3k: the news scraper turbohtml.article succeeds (pins old dependencies, often unresolvable)."""

from __future__ import annotations

from newspaper import Article

# lxml-html-clean restores the lxml.html.clean module newspaper imports, split out of lxml 5.2
REQUIREMENTS = ("newspaper3k>=0.2.8", "lxml-html-clean>=0.4.5")


def article(text: str) -> None:
    """Extract content and metadata with newspaper3k, from pre-set HTML."""
    parsed = Article(url="")
    parsed.set_html(text)
    parsed.parse()


OPERATIONS = {"article": (article, "newspaper3k")}

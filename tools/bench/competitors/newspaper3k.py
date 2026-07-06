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


def text_main(text: str) -> None:
    """Read the main text off newspaper3k's article, its only path is the full parse."""
    parsed = Article(url="")
    parsed.set_html(text)
    parsed.parse()
    _ = parsed.text


def date(text: str) -> None:
    """Read the publish date off newspaper3k's article, its only path is the full parse."""
    parsed = Article(url="")
    parsed.set_html(text)
    parsed.parse()
    _ = parsed.publish_date


OPERATIONS = {
    "article": (article, "newspaper3k"),
    "text-main": (text_main, "newspaper3k"),
    "date": (date, "newspaper3k"),
}

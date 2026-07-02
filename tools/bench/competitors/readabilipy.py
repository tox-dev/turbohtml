"""readabilipy: the Readability.js wrapper turbohtml.article succeeds, in its pure-Python mode."""

from __future__ import annotations

from readabilipy import simple_json_from_html_string

REQUIREMENTS = ("readabilipy>=0.3",)


def article(text: str) -> None:
    """Extract title, byline, date, and text blocks with readabilipy's Python mode (html5lib on BeautifulSoup)."""
    simple_json_from_html_string(text, use_readability=False)


OPERATIONS = {"article": (article, "readabilipy")}

"""readabilipy: Mozilla Readability wrapped for Python, whose article turbohtml.article succeeds."""

from __future__ import annotations

from readabilipy import simple_json_from_html_string

REQUIREMENTS = ("readabilipy>=0.3",)


def article(text: str) -> None:
    """Extract the article content and metadata with readabilipy's pure-Python path."""
    simple_json_from_html_string(text, use_readability=False)


OPERATIONS = {"article": (article, "readabilipy")}

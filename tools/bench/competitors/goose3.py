"""goose3: the article scraper whose content body and metadata turbohtml.article succeeds."""

from __future__ import annotations

from goose3 import Goose

REQUIREMENTS = ("goose3>=3.1.19",)

_GOOSE = Goose()


def article(text: str) -> None:
    """Extract the content body and metadata with goose3, from pre-set HTML."""
    _GOOSE.extract(raw_html=text)


OPERATIONS = {"article": (article, "goose3")}

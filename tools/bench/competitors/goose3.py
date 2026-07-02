"""goose3: the lxml-backed article extractor turbohtml.article succeeds."""

from __future__ import annotations

from goose3 import Goose

REQUIREMENTS = ("goose3>=3.1.19",)

_GOOSE = Goose({"enable_image_fetching": False})


def article(text: str) -> None:
    """Extract the content body and metadata with goose3, cleaning an lxml tree."""
    _GOOSE.extract(raw_html=text)


OPERATIONS = {"article": (article, "goose3")}

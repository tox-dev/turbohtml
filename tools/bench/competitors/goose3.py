"""goose3: the lxml-backed article extractor turbohtml.article succeeds."""

from __future__ import annotations

from goose3 import Goose

REQUIREMENTS = ("goose3>=3.1.19",)

_GOOSE = Goose({"enable_image_fetching": False})


def article(text: str) -> None:
    """Extract the content body and metadata with goose3, cleaning an lxml tree."""
    _GOOSE.extract(raw_html=text)


def date(text: str) -> None:
    """Read the publish date off goose3's article, its only path is the full extract."""
    _ = _GOOSE.extract(raw_html=text).publish_date


def text_main(text: str) -> None:
    """Read the cleaned main text off goose3's article, its only path is the full extract."""
    _ = _GOOSE.extract(raw_html=text).cleaned_text


def socialcard(text: str) -> None:
    """Read the OpenGraph card off goose3's article, its only path is the full extract."""
    _ = _GOOSE.extract(raw_html=text).opengraph


OPERATIONS = {
    "article": (article, "goose3"),
    "date": (date, "goose3"),
    "text-main": (text_main, "goose3"),
    "socialcard": (socialcard, "goose3"),
}

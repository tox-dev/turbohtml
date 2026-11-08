"""boilerpy3: the boilerpipe-port block classifier turbohtml.extract.boilerplate succeeds."""

from __future__ import annotations

from typing import Final

from boilerpy3 import extractors

REQUIREMENTS: Final[tuple[str, ...]] = ("boilerpy3>=1.0.7",)

_EXTRACTOR: Final[extractors.ArticleExtractor] = extractors.ArticleExtractor(raise_on_failure=False)


def boilerplate(text: str) -> None:
    # Classify every text block content or boilerplate with boilerpy3's ArticleExtractor.
    _EXTRACTOR.get_doc(text)


def text_main(text: str) -> None:
    # Extract the boilerplate-removed main text with boilerpy3's ArticleExtractor.
    _EXTRACTOR.get_content(text)


OPERATIONS = {
    "boilerplate": (boilerplate, "boilerpy3"),
    "text-main": (text_main, "boilerpy3"),
}

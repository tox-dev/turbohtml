"""boilerpy3: the boilerpipe-port block classifier turbohtml.extract.boilerplate succeeds."""

from __future__ import annotations

from boilerpy3 import extractors

REQUIREMENTS = ("boilerpy3>=1.0.7",)

_EXTRACTOR = extractors.ArticleExtractor(raise_on_failure=False)


def boilerplate(text: str) -> None:
    """Classify every text block content or boilerplate with boilerpy3's ArticleExtractor."""
    _EXTRACTOR.get_doc(text)


OPERATIONS = {"boilerplate": (boilerplate, "boilerpy3")}

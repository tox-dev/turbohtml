"""boilerpy3: the Boilerpipe port whose ArticleExtractor turbohtml.extract.boilerplate succeeds."""

from __future__ import annotations

from boilerpy3 import extractors

REQUIREMENTS = ("boilerpy3>=1.0.7",)

_EXTRACTOR = extractors.ArticleExtractor()


def article(text: str) -> None:
    """Extract the article content with boilerpy3's ArticleExtractor, on an lxml tree."""
    _EXTRACTOR.get_content(text)


OPERATIONS = {"article": (article, "boilerpy3")}

"""justext: the per-paragraph good/bad classifier turbohtml.extract.boilerplate reproduces over its C scoring."""

from __future__ import annotations

import justext

REQUIREMENTS = ("justext>=3.0",)

_STOPLIST = justext.get_stoplist("English")


def article(text: str) -> None:
    """Classify every paragraph as content or boilerplate with justext, on an lxml tree."""
    justext.justext(text, _STOPLIST)


OPERATIONS = {"article": (article, "justext")}

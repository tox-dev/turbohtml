"""justext: the stopword-density paragraph classifier turbohtml.extract.boilerplate succeeds."""

from __future__ import annotations

import justext

REQUIREMENTS = ("justext>=3.0",)

_STOPLIST = justext.get_stoplist("English")


def boilerplate(text: str) -> None:
    """Classify every paragraph good or boilerplate with justext, segmenting an lxml tree."""
    justext.justext(text, _STOPLIST)


OPERATIONS = {"boilerplate": (boilerplate, "justext")}

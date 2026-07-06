"""justext: the stopword-density paragraph classifier turbohtml.extract.boilerplate succeeds."""

from __future__ import annotations

import justext

REQUIREMENTS = ("justext>=3.0",)

_STOPLIST = justext.get_stoplist("English")


def boilerplate(text: str) -> None:
    """Classify every paragraph good or boilerplate with justext, segmenting an lxml tree."""
    justext.justext(text, _STOPLIST)


def text_main(text: str) -> None:
    """Join justext's non-boilerplate paragraphs into the main text, over the same classification."""
    "\n".join(p.text for p in justext.justext(text, _STOPLIST) if not p.is_boilerplate)


OPERATIONS = {
    "boilerplate": (boilerplate, "justext"),
    "text-main": (text_main, "justext"),
}

"""w3lib: replace_entities, the closest competitor to turbohtml.unescape."""

from __future__ import annotations

import w3lib.html

REQUIREMENTS = ("w3lib>=2.4.1",)


def unescape(text: str) -> None:
    """Resolve character references with w3lib's regex-based replace_entities."""
    w3lib.html.replace_entities(text)


def strip_tags(text: str) -> None:
    """Strip the code/a/q tags but keep their text with w3lib's regex remove_tags."""
    w3lib.html.remove_tags(text, which_ones=("code", "a", "q"))


OPERATIONS = {"unescape": (unescape, "w3lib"), "strip-tags": (strip_tags, "w3lib")}

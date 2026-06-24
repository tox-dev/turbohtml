"""w3lib: replace_entities, the closest competitor to turbohtml.unescape."""

from __future__ import annotations

import w3lib.html

REQUIREMENTS = ("w3lib>=2.4.1",)


def unescape(text: str) -> None:
    """Resolve character references with w3lib's regex-based replace_entities."""
    w3lib.html.replace_entities(text)


OPERATIONS = {"unescape": (unescape, "w3lib")}

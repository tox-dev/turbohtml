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


_URL_HINT_BASE = "http://site.com/"


def extract_url(case: tuple[str, str]) -> None:
    """Read a document's own URL hint with w3lib's regex passes: the base URL or the meta refresh."""
    kind, text = case
    if kind == "base":
        w3lib.html.get_base_url(text, _URL_HINT_BASE)
    else:
        w3lib.html.get_meta_refresh(text, _URL_HINT_BASE)


OPERATIONS = {
    "unescape": (unescape, "w3lib"),
    "strip-tags": (strip_tags, "w3lib"),
    "extract-url": (extract_url, "w3lib"),
}

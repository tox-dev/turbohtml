"""w3lib: replace_entities, the closest competitor to turbohtml.unescape, plus the w3lib.url canonicalizers."""

from __future__ import annotations

import w3lib.html
import w3lib.url

REQUIREMENTS = ("w3lib>=2.4.1",)


def unescape(text: str) -> None:
    """Resolve character references with w3lib's regex-based replace_entities."""
    w3lib.html.replace_entities(text)


def strip_tags(text: str) -> None:
    """Strip the code/a/q tags but keep their text with w3lib's regex remove_tags."""
    w3lib.html.remove_tags(text, which_ones=("code", "a", "q"))


def strip_remove(text: str) -> None:
    """Drop the code/a/q tags and their content with w3lib's regex remove_tags_with_content."""
    w3lib.html.remove_tags_with_content(text, which_ones=("code", "a", "q"))


_URL_HINT_BASE = "http://site.com/"


def extract_url(case: tuple[str, str]) -> None:
    """Read a document's own URL hint with w3lib's regex passes: the base URL or the meta refresh."""
    kind, text = case
    if kind == "base":
        w3lib.html.get_base_url(text, _URL_HINT_BASE)
    else:
        w3lib.html.get_meta_refresh(text, _URL_HINT_BASE)


def urls_clean(case: tuple[str, tuple[str, ...]]) -> None:
    """Run w3lib's safe_url_string (or canonicalize_url) over the shared URL batch, by case kind."""
    kind, batch = case
    transform = w3lib.url.safe_url_string if kind == "clean" else w3lib.url.canonicalize_url
    for url in batch:
        transform(url)


OPERATIONS = {
    "unescape": (unescape, "w3lib"),
    "strip-tags": (strip_tags, "w3lib"),
    "strip-remove": (strip_remove, "w3lib"),
    "extract-url": (extract_url, "w3lib"),
    "urls-clean": (urls_clean, "w3lib"),
}

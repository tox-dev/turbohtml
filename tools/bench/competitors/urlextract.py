"""urlextract: finds URLs in plain text by locating IANA TLDs and expanding around them."""

from __future__ import annotations

from urlextract import URLExtract

REQUIREMENTS = ("urlextract>=1.9",)

_EXTRACTOR = URLExtract(extract_email=True)


def detect(case: tuple[str, str]) -> None:
    """Scan plain text for URLs with urlextract: find_urls for the list, has_urls for the presence test."""
    kind, text = case
    if kind == "find":
        _EXTRACTOR.find_urls(text)
    else:
        _EXTRACTOR.has_urls(text)


# urlextract only finds URLs in plain text; it never rewrites HTML, so it maps to detect, not the parse-and-rewrite
# linkify op.
OPERATIONS = {"detect": (detect, "urlextract")}

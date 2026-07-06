"""lxml-html-clean: the blocklist Cleaner externalized from lxml.html.clean."""

from __future__ import annotations

import lxml_html_clean

REQUIREMENTS = ("lxml-html-clean>=0.4.5", "lxml>=6.1.1")

_CLEANER = lxml_html_clean.Cleaner()


def sanitize(text: str) -> None:
    """Sanitize with lxml-html-clean's blocklist Cleaner."""
    _CLEANER.clean_html(text)


def linkify(text: str) -> None:
    """Auto-link URLs in HTML with lxml-html-clean's autolink_html, on an lxml tree."""
    lxml_html_clean.autolink_html(text)


OPERATIONS = {
    "sanitize": (sanitize, "lxml-html-clean"),
    "linkify": (linkify, "lxml-html-clean"),
}

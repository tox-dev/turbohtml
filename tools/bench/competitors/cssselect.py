"""cssselect: the CSS-selector to XPath translator lxml/parsel/pyquery wrap, timed on the translate operation."""

from __future__ import annotations

from cssselect import HTMLTranslator

REQUIREMENTS = ("cssselect>=1.4",)

_TRANSLATOR = HTMLTranslator()


def translate(selector: str) -> None:
    """Translate a CSS selector to XPath with cssselect's HTMLTranslator, the same job turbohtml.convert does."""
    _TRANSLATOR.css_to_xpath(selector)


OPERATIONS = {
    "translate": (translate, "cssselect"),
}

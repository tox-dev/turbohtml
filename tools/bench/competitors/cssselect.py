"""cssselect: the CSS-to-XPath translator behind lxml.cssselect, parsel, and pyquery."""

from __future__ import annotations

from cssselect import HTMLTranslator, parse

REQUIREMENTS = ("cssselect>=1.4",)

_TRANSLATOR = HTMLTranslator()


def translate(selector: str) -> None:
    """Translate one CSS selector to XPath 1.0 with cssselect's HTML translator."""
    _TRANSLATOR.css_to_xpath(selector)


def specificity(selector: str) -> None:
    """Weigh each selector in a list with cssselect, the (a, b, c) triple per parsed arm."""
    [parsed.specificity() for parsed in parse(selector)]


OPERATIONS = {"translate": (translate, "cssselect"), "specificity": (specificity, "cssselect")}

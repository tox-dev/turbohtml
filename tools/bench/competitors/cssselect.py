"""cssselect: the CSS-to-XPath translator behind lxml.cssselect, parsel, and pyquery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from cssselect import HTMLTranslator, parse

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("cssselect>=1.4",)

_TRANSLATOR: Final[HTMLTranslator] = HTMLTranslator()


def translate(selector: str) -> None:
    # Translate one CSS selector to XPath 1.0 with cssselect's HTML translator.
    _TRANSLATOR.css_to_xpath(selector)


def specificity(selector: str) -> None:
    # Weigh each selector in a list with cssselect, the (a, b, c) triple per parsed arm.
    for parsed in parse(selector):
        parsed.specificity()


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "translate": (translate, "cssselect"),
    "specificity": (specificity, "cssselect"),
}

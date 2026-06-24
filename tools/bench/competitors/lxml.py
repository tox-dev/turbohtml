"""lxml: the ElementTree-style constructor build and lxml.builder's nested ``E`` factory."""

from __future__ import annotations

from lxml import html as lxml_html
from lxml.builder import E

REQUIREMENTS = ("lxml>=6.1.1",)


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with lxml's Element factory and ``.text``, then serialize."""
    ul = lxml_html.Element("ul")
    for index in range(count):
        li = lxml_html.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = lxml_html.tostring(ul)


def build_e(count: int) -> None:
    """Build the same ``<ul>`` with lxml.builder's nested ``E`` calls and serialize the tree."""
    rows = (E.li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count))
    _ = lxml_html.tostring(E.ul(*rows))


OPERATIONS = {"build": (build, "lxml"), "build-e": (build_e, "lxml.builder")}

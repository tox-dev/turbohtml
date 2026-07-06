"""resiliparse: parse with the same lexbor engine selectolax wraps."""

from __future__ import annotations

import functools

from resiliparse.extract.html2text import (  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
    extract_plain_text,
)
from resiliparse.parse.html import HTMLTree  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs

REQUIREMENTS = ("resiliparse>=1.0.8",)


def parse(text: str) -> None:
    """Parse a whole document with resiliparse."""
    HTMLTree.parse(text)


@functools.cache
def _parsed(text: str) -> HTMLTree:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return HTMLTree.parse(text)


def text_render(text: str) -> None:
    """Extract visible text with resiliparse, off the lexbor tree it parses to."""
    extract_plain_text(text)


def text_main(text: str) -> None:
    """Extract the boilerplate-stripped main text with resiliparse's main_content mode."""
    extract_plain_text(text, main_content=True)


def find(text: str) -> None:
    """Collect every anchor with resiliparse's get_elements_by_tag_name."""
    if (body := _parsed(text).body) is not None:
        body.get_elements_by_tag_name("a")


def select(text: str) -> None:
    """Run the CSS selector with resiliparse's query_selector_all."""
    _parsed(text).document.query_selector_all("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector with resiliparse's query_selector_all."""
    _parsed(text).document.query_selector_all("div:has(a)")


def text_content(text: str) -> None:
    """Collect the document's raw text with resiliparse's text property, distinct from the layout-aware render."""
    tree = _parsed(text)
    if (node := tree.body or tree.document) is not None:
        _ = node.text


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with resiliparse's document html property."""
    _ = _parsed(text).document.html


def navigate(text: str) -> None:
    """Walk every element with resiliparse's get_elements_by_tag_name wildcard."""
    if (body := _parsed(text).body) is not None:
        for _node in body.get_elements_by_tag_name("*"):
            pass


OPERATIONS = {
    "parse": (parse, "resiliparse"),
    "text-render": (text_render, "resiliparse"),
    "text-main": (text_main, "resiliparse"),
    "find": (find, "resiliparse"),
    "select": (select, "resiliparse"),
    "select-has": (select_has, "resiliparse"),
    "text-content": (text_content, "resiliparse"),
    "serialize": (serialize, "resiliparse"),
    "navigate": (navigate, "resiliparse"),
}

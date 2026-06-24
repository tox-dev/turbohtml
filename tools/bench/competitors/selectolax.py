"""selectolax: parse with the lexbor C engine, plus the read-path queries over its tree."""

from __future__ import annotations

import functools

from selectolax.lexbor import LexborHTMLParser

REQUIREMENTS = ("selectolax>=0.4.10",)


def parse(text: str) -> None:
    """Parse a whole document with lexbor through selectolax (its native input is UTF-8 bytes)."""
    LexborHTMLParser(text.encode())


@functools.cache
def _parsed(text: str) -> LexborHTMLParser:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return LexborHTMLParser(text.encode())


def find(text: str) -> None:
    """Collect every anchor with selectolax's css."""
    _parsed(text).css("a")


def select(text: str) -> None:
    """Run the CSS selector with selectolax's css."""
    _parsed(text).css("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector with selectolax's css."""
    _parsed(text).css("div:has(a)")


def text_content(text: str) -> None:
    """Collect the document's visible text with selectolax's text() method."""
    tree = _parsed(text)
    if (node := tree.body or tree.root) is not None:
        node.text(deep=True)


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with selectolax's html property."""
    _ = _parsed(text).html


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with selectolax's strip_tags, then serialize."""
    tree = LexborHTMLParser(text)
    tree.strip_tags(["code", "a", "q"])
    _ = tree.html


OPERATIONS = {
    "parse": (parse, "selectolax"),
    "find": (find, "selectolax"),
    "select": (select, "selectolax"),
    "select-has": (select_has, "selectolax"),
    "text-content": (text_content, "selectolax"),
    "serialize": (serialize, "selectolax"),
    "strip-remove": (strip_remove, "selectolax"),
}

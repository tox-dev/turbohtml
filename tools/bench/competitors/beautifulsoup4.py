"""BeautifulSoup: parse, the constructor build, and the read-path queries."""

from __future__ import annotations

import functools
import re

from bs4 import BeautifulSoup

REQUIREMENTS = ("beautifulsoup4>=4.15",)

_FIND_TEXT_PATTERN = re.compile(r"test")
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"


def parse(text: str) -> None:
    """Parse a whole document with BeautifulSoup over its stdlib html.parser backend."""
    BeautifulSoup(text, "html.parser")


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with BeautifulSoup's ``new_tag`` and ``.string``, then serialize (the workload)."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    _ = ul.decode()


def construct(count: int) -> None:
    """Construct ``count`` elements with attributes and text, in isolation from serialization."""
    soup = BeautifulSoup("", "html.parser")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"


@functools.cache
def _tree(count: int) -> object:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    return ul


def emit(count: int) -> None:
    """Emit a pre-built ``count``-row tree with ``.decode()``."""
    _ = _tree(count).decode()  # ty: ignore[unresolved-attribute]  # bs4 Tag has no stubs


@functools.cache
def _parsed(text: str) -> BeautifulSoup:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return BeautifulSoup(text, "html.parser")


def find(text: str) -> None:
    """Collect every anchor with BeautifulSoup's find_all."""
    _parsed(text).find_all("a")


def select(text: str) -> None:
    """Run the CSS selector with BeautifulSoup's soupsieve select."""
    _parsed(text).select("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector with BeautifulSoup's soupsieve."""
    _parsed(text).select("div:has(a)")


def find_text(text: str) -> None:
    """Collect every matching string with BeautifulSoup's find_all(string=...)."""
    _parsed(text).find_all(string=_FIND_TEXT_PATTERN)


def text_content(text: str) -> None:
    """Collect the document's visible text with BeautifulSoup's get_text()."""
    _parsed(text).get_text()


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with BeautifulSoup's decode."""
    _parsed(text).decode()


def edit(text: str) -> None:
    """Tag every link with rel=nofollow through BeautifulSoup's item assignment."""
    for anchor in _parsed(text).find_all("a"):
        anchor["rel"] = "nofollow"


def set_html(text: str) -> None:
    """Clear the body and append a reparsed fragment, BeautifulSoup's inner-HTML shape."""
    body = _parsed(text).find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(_SET_HTML, "html.parser").children):
        body.append(node)


def navigate(text: str) -> None:
    """Walk every descendant with BeautifulSoup's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


OPERATIONS = {
    "parse": (parse, "BeautifulSoup"),
    "build": (build, "BeautifulSoup"),
    "construct": (construct, "BeautifulSoup"),
    "emit": (emit, "BeautifulSoup"),
    "find": (find, "BeautifulSoup"),
    "select": (select, "BeautifulSoup"),
    "select-has": (select_has, "BeautifulSoup"),
    "find-text": (find_text, "BeautifulSoup"),
    "text-content": (text_content, "BeautifulSoup"),
    "serialize": (serialize, "BeautifulSoup"),
    "edit": (edit, "BeautifulSoup"),
    "set-html": (set_html, "BeautifulSoup"),
    "navigate": (navigate, "BeautifulSoup"),
}

"""
turbohtml's own timing for every operation: the shared baseline each competitor divides into.

This module imports turbohtml and nothing else, so it loads only in the turbohtml-only ``core`` venv. ``OPERATIONS``
maps each operation to ``(timing function, label)``; the function takes the same case input the competitor receives.
"""

from __future__ import annotations

import functools
import re

import turbohtml
from turbohtml import sanitizer as _sanitizer
from turbohtml.build import E
from turbohtml.query import Query as _Query

_SANITIZER = _sanitizer.Sanitizer(_sanitizer.Policy.relaxed())
_LINKS_BASE = "https://example.com/base/"
_FIND_TEXT_PATTERN = re.compile(r"test")  # ubiquitous in the wpt corpus, so the predicate does real work
_CSS = "div a[href]"  # a descendant combinator with an attribute test, common in scrapers
_HAS = "div:has(a)"  # the :has() relational pseudo-class
_STRIP = "code, a, q"  # a bulk set of tags to drop or unwrap
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with turbohtml's element constructors and serialize it (the aggregate workload)."""
    ul = turbohtml.Element("ul")
    for index in range(count):
        li = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = ul.html


def build_e(count: int) -> None:
    """Build the same ``<ul>`` with the terse :data:`turbohtml.build.E` factory and serialize it."""
    rows = [E.li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = E.ul(*rows).serialize()


def construct(count: int) -> None:
    """Construct ``count`` elements with attributes and text, in isolation from serialization."""
    for index in range(count):
        element = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        element.text = f"item {index}"


@functools.cache
def _tree(count: int) -> turbohtml.Element:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step."""
    ul = turbohtml.Element("ul")
    for index in range(count):
        li = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    return ul


def emit(count: int) -> None:
    """Emit a pre-built ``count``-row tree, in isolation from construction."""
    _ = _tree(count).html


def parse(text: str) -> None:
    """Parse a whole document into a navigable tree through turbohtml.parse()."""
    turbohtml.parse(text)


def fragment(text: str) -> None:
    """Parse a fragment in its container context with turbohtml.parse_fragment."""
    turbohtml.parse_fragment(text, context="tbody")


def escape(text: str) -> None:
    """Escape text with turbohtml.escape."""
    turbohtml.escape(text)


def unescape(text: str) -> None:
    """Resolve character references with turbohtml.unescape."""
    turbohtml.unescape(text)


def tokenize(text: str) -> None:
    """Consume turbohtml's token stream so lazy Token construction is included."""
    for _ in turbohtml.tokenize(text):
        pass


@functools.cache
def _parsed(text: str) -> turbohtml.Document:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return turbohtml.parse(text)


def find(text: str) -> None:
    """Collect every anchor with turbohtml's find_all."""
    _parsed(text).find_all("a")


def select(text: str) -> None:
    """Run the CSS selector with turbohtml's select."""
    _parsed(text).select(_CSS)


def select_has(text: str) -> None:
    """Run the :has() relational selector with turbohtml's select."""
    _parsed(text).select(_HAS)


def find_text(text: str) -> None:
    """Collect every element whose collected text matches the regex with turbohtml's find_all."""
    _parsed(text).find_all(text=_FIND_TEXT_PATTERN)


def text_content(text: str) -> None:
    """Collect the document's visible text with turbohtml's text property."""
    _ = _parsed(text).text


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with turbohtml's html property."""
    _ = _parsed(text).html


def edit(text: str) -> None:
    """Tag every link with rel=nofollow through turbohtml's live attribute mapping."""
    for anchor in _parsed(text).find_all("a"):
        anchor.attrs["rel"] = "nofollow"


def class_edit(text: str) -> None:
    """Add then drop a class token on every link with turbohtml's classList mutators."""
    for anchor in _parsed(text).find_all("a"):
        anchor.add_class("seen").remove_class("seen")


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with turbohtml's bulk remove, then serialize."""
    _ = turbohtml.parse(text).remove(_STRIP).html


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with turbohtml's strip_tags, then serialize."""
    _ = turbohtml.parse(text).strip_tags(_STRIP).html


def set_html(text: str) -> None:
    """Replace the body's children by reparsing a fragment in context with turbohtml's set_inner_html."""
    _parsed(text).find_all("body")[0].set_inner_html(_SET_HTML)


def set_text(text: str) -> None:
    """Replace the body's children with one verbatim text node through turbohtml's set_text."""
    _parsed(text).find_all("body")[0].set_text(_SET_TEXT)


def navigate(text: str) -> None:
    """Walk every descendant node with turbohtml's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


def chain(text: str) -> None:
    """Run a fluent jQuery-style chain with turbohtml's Query wrapper."""
    _Query(_parsed(text))("a").filter("[href]").eq(0).add_class("seen").attr("href")


def links_extract(text: str) -> None:
    """Collect every link with turbohtml's links()."""
    _parsed(text).links()


def links_absolutize(text: str) -> None:
    """Resolve every relative link against a base with turbohtml's resolve_links()."""
    _parsed(text).resolve_links(_LINKS_BASE)


def links_rewrite(text: str) -> None:
    """Rewrite every link through a callback with turbohtml's rewrite_links()."""
    _parsed(text).rewrite_links(lambda url: url)


def socialcard(text: str) -> None:
    """Read the OpenGraph/Twitter card tags with turbohtml (parse plus one C walk)."""
    turbohtml.parse(text).opengraph()


def structured(text: str) -> None:
    """Extract JSON-LD, Microdata, and OpenGraph with turbohtml in one C walk."""
    turbohtml.parse(text).structured_data()


def sanitize(text: str) -> None:
    """Sanitize with turbohtml's relaxed policy, reusing a prebuilt sanitizer."""
    _SANITIZER.sanitize(text)


OPERATIONS: dict[str, tuple[object, str]] = {
    "build": (build, "turbohtml"),
    "build-e": (build_e, "turbohtml"),
    "construct": (construct, "turbohtml"),
    "emit": (emit, "turbohtml"),
    "parse": (parse, "turbohtml"),
    "fragment": (fragment, "turbohtml"),
    "escape": (escape, "turbohtml"),
    "unescape": (unescape, "turbohtml"),
    "tokenize": (tokenize, "turbohtml"),
    "find": (find, "turbohtml"),
    "select": (select, "turbohtml"),
    "select-has": (select_has, "turbohtml"),
    "find-text": (find_text, "turbohtml"),
    "text-content": (text_content, "turbohtml"),
    "serialize": (serialize, "turbohtml"),
    "edit": (edit, "turbohtml"),
    "class-edit": (class_edit, "turbohtml"),
    "strip-remove": (strip_remove, "turbohtml"),
    "strip-tags": (strip_tags, "turbohtml"),
    "set-html": (set_html, "turbohtml"),
    "set-text": (set_text, "turbohtml"),
    "navigate": (navigate, "turbohtml"),
    "chain": (chain, "turbohtml"),
    "links-extract": (links_extract, "turbohtml"),
    "links-absolutize": (links_absolutize, "turbohtml"),
    "links-rewrite": (links_rewrite, "turbohtml"),
    "socialcard": (socialcard, "turbohtml"),
    "structured": (structured, "turbohtml"),
    "sanitize": (sanitize, "turbohtml"),
}

"""BeautifulSoup: parse, the constructor build, and the read-path queries."""

from __future__ import annotations

import functools
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, UnicodeDammit
from bs4.element import AttributeValueList

from bench.timing import Mutating

REQUIREMENTS = ("beautifulsoup4>=4.15",)

_FIND_TEXT_PATTERN = re.compile(r"test")
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_STRIP = ("code", "a", "q")
_LINKS_BASE = "https://example.com/base/"


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


def _fresh(text: str) -> BeautifulSoup:
    """Parse a fresh document for the mutating operations, which each iteration must run on an unmodified tree."""
    return BeautifulSoup(text, "html.parser")


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


def class_edit(text: str) -> None:
    """Add then drop a class token on every link through BeautifulSoup's multi-valued class list (a net no-op)."""
    for anchor in _parsed(text).find_all("a"):
        tokens = anchor.get_attribute_list("class")
        anchor["class"] = AttributeValueList([*tokens, "seen"])
        anchor["class"] = tokens


def extract_attr(text: str) -> None:
    """Read every anchor's href by selecting once and reading the attribute off each node."""
    for anchor in _parsed(text).select("a"):
        anchor.get("href")


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading get_text off each node."""
    for anchor in _parsed(text).select("a"):
        anchor.get_text()


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with BeautifulSoup's decompose on a fresh parse, then serialize."""
    soup = _fresh(text)
    for tag in list(soup.find_all(_STRIP)):
        tag.decompose()
    _ = soup.decode()


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with BeautifulSoup's unwrap on a fresh parse, then emit."""
    soup = _fresh(text)
    for tag in list(soup.find_all(_STRIP)):
        tag.unwrap()
    _ = soup.decode()


def encoding(data: bytes) -> None:
    """Detect a byte stream's encoding with BeautifulSoup's UnicodeDammit sniffer."""
    _ = UnicodeDammit(data).original_encoding


def edit(soup: BeautifulSoup) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through BeautifulSoup's item assignment."""
    for anchor in soup.find_all("a"):
        anchor["rel"] = "nofollow"


def set_html(soup: BeautifulSoup) -> None:
    """Clear a freshly parsed body and append a reparsed fragment, BeautifulSoup's inner-HTML shape."""
    body = soup.find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(_SET_HTML, "html.parser").children):
        body.append(node)


def set_text(soup: BeautifulSoup) -> None:
    """Replace a freshly parsed body's children with one verbatim text node through BeautifulSoup's .string."""
    soup.find_all("body")[0].string = _SET_TEXT


def navigate(text: str) -> None:
    """Walk every descendant with BeautifulSoup's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


def match(text: str) -> None:
    """Test every anchor against a selector with BeautifulSoup's soupsieve-backed css.match."""
    for anchor in _parsed(text).find_all("a"):
        anchor.css.match("div a[href]")


def links_extract(text: str) -> None:
    """Collect every anchor's href with BeautifulSoup, the shape a scraper reads links in."""
    _ = [anchor.get("href") for anchor in _parsed(text).find_all("a")]


def links_rewrite(text: str) -> None:
    """Rewrite every link with an identity map, idempotent so the cached tree stays reusable."""
    for anchor in _parsed(text).find_all("a"):
        if (href := anchor.get("href")) is not None:
            anchor["href"] = href


def links_filter(text: str) -> None:
    """Collect the anchor hrefs and keep the on-page links, mirroring the cleaned-link filter."""
    _ = [
        href
        for anchor in _parsed(text).find_all("a")
        if (href := anchor.get("href")) and not str(href).startswith(("#", "javascript:"))
    ]


def socialcard(text: str) -> None:
    """Read every meta property and content off a freshly parsed tree, BeautifulSoup's take on card extraction."""
    for meta in _fresh(text).find_all("meta"):
        meta.get("property")
        meta.get("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a freshly parsed document's own URL hint with BeautifulSoup: the base href or the meta refresh."""
    kind, text = case
    soup = _fresh(text)
    if kind == "base":
        if (base := soup.find("base")) is not None:
            base.get("href")
    elif (refresh := soup.find("meta", attrs={"http-equiv": "refresh"})) is not None:
        refresh.get("content")


def links_absolutize(soup: BeautifulSoup) -> None:
    """Resolve every relative link on a freshly parsed tree against a base with BeautifulSoup's item assignment."""
    for anchor in soup.find_all("a"):
        if isinstance(href := anchor.get("href"), str):
            anchor["href"] = urljoin(_LINKS_BASE, href)


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
    "class-edit": (class_edit, "BeautifulSoup"),
    "extract-attr": (extract_attr, "BeautifulSoup"),
    "extract-text": (extract_text, "BeautifulSoup"),
    "strip-remove": (strip_remove, "BeautifulSoup"),
    "strip-tags": (strip_tags, "BeautifulSoup"),
    "encoding": (encoding, "BeautifulSoup"),
    "edit": (Mutating(_fresh, edit), "BeautifulSoup"),
    "set-html": (Mutating(_fresh, set_html), "BeautifulSoup"),
    "set-text": (Mutating(_fresh, set_text), "BeautifulSoup"),
    "navigate": (navigate, "BeautifulSoup"),
    "match": (match, "BeautifulSoup"),
    "links-extract": (links_extract, "BeautifulSoup"),
    "links-rewrite": (links_rewrite, "BeautifulSoup"),
    "links-filter": (links_filter, "BeautifulSoup"),
    "socialcard": (socialcard, "BeautifulSoup"),
    "extract-url": (extract_url, "BeautifulSoup"),
    "links-absolutize": (Mutating(_fresh, links_absolutize), "BeautifulSoup"),
}

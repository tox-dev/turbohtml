"""selectolax: parse with the lexbor C engine, plus the read-path queries over its tree."""

from __future__ import annotations

import functools
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from bench.timing import Mutating

REQUIREMENTS = ("selectolax>=0.4.10",)

_STRIP_TAGS = ["code", "a", "q"]  # the bulk tag set turbohtml drops/unwraps, as a lexbor tag list
_LINKS_BASE = "https://example.com/base/"  # the base turbohtml resolves relative hrefs against


def parse(text: str) -> None:
    """Parse a whole document with lexbor through selectolax (its native input is UTF-8 bytes)."""
    LexborHTMLParser(text.encode())


@functools.cache
def _parsed(text: str) -> LexborHTMLParser:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return LexborHTMLParser(text.encode())


def _fresh(text: str) -> LexborHTMLParser:
    """Parse a fresh, un-cached tree so operations that mutate or must include parse time get their own copy."""
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


def navigate(text: str) -> None:
    """Walk every node with selectolax's traverse, text nodes included to match a full descendant walk."""
    if (root := _parsed(text).root) is not None:
        for _node in root.traverse(include_text=True):
            pass


def links_extract(text: str) -> None:
    """Collect every anchor's href with selectolax, the shape a scraper reads links in."""
    _ = [anchor.attributes.get("href") for anchor in _parsed(text).css("a")]


def extract_attr(text: str) -> None:
    """Read every anchor's href by selecting once and reading the attribute off each node."""
    _ = [anchor.attributes.get("href") for anchor in _parsed(text).css("a")]


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading text off each node."""
    _ = [anchor.text() for anchor in _parsed(text).css("a")]


def links_rewrite(text: str) -> None:
    """Rewrite every link with an identity map, idempotent so the cached tree stays reusable."""
    for anchor in _parsed(text).css("a"):
        anchor.attrs["href"] = anchor.attrs.get("href")


def links_filter(text: str) -> None:
    """Collect the hrefs of anchors that carry one, selectolax's closest analog to a link filter."""
    _ = [anchor.attributes.get("href") for anchor in _parsed(text).css("a[href]")]


def class_edit(text: str) -> None:
    """Add then drop a class token on every link, a net no-op so the cached tree stays valid across iterations."""
    for anchor in _parsed(text).css("a"):
        original = anchor.attrs.get("class") or ""
        anchor.attrs["class"] = f"{original} seen".strip()
        anchor.attrs["class"] = original


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with selectolax's strip_tags, then serialize."""
    tree = _fresh(text)
    tree.strip_tags(_STRIP_TAGS)
    _ = tree.html


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with selectolax's unwrap_tags, then serialize."""
    tree = _fresh(text)
    tree.unwrap_tags(_STRIP_TAGS)
    _ = tree.html


def socialcard(text: str) -> None:
    """Read every meta property and content off a freshly parsed tree, selectolax's take on card extraction."""
    for meta in _fresh(text).css("meta"):
        meta.attributes.get("property")
        meta.attributes.get("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a document's own URL hint by parsing the string: the base href or the meta-refresh target."""
    kind, text = case
    tree = _fresh(text)
    if kind == "base":
        if (base := tree.css_first("base")) is not None:
            base.attributes.get("href")
    elif (refresh := tree.css_first("meta[http-equiv=refresh]")) is not None:
        refresh.attributes.get("content")


def edit(tree: LexborHTMLParser) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through selectolax's live attribute mapping."""
    for anchor in tree.css("a"):
        anchor.attrs["rel"] = "nofollow"


def links_absolutize(tree: LexborHTMLParser) -> None:
    """Resolve every relative link on a freshly parsed tree against the base with selectolax's attribute mapping."""
    for anchor in tree.css("a"):
        if (href := anchor.attrs.get("href")) is not None:
            anchor.attrs["href"] = urljoin(_LINKS_BASE, href)


def match(text: str) -> None:
    """Test every anchor against a selector with selectolax's per-node css_matches."""
    for anchor in _parsed(text).css("a"):
        anchor.css_matches("div a[href]")


OPERATIONS = {
    "parse": (parse, "selectolax"),
    "find": (find, "selectolax"),
    "select": (select, "selectolax"),
    "select-has": (select_has, "selectolax"),
    "text-content": (text_content, "selectolax"),
    "serialize": (serialize, "selectolax"),
    "navigate": (navigate, "selectolax"),
    "links-extract": (links_extract, "selectolax"),
    "links-rewrite": (links_rewrite, "selectolax"),
    "links-filter": (links_filter, "selectolax"),
    "extract-attr": (extract_attr, "selectolax"),
    "extract-text": (extract_text, "selectolax"),
    "class-edit": (class_edit, "selectolax"),
    "strip-remove": (strip_remove, "selectolax"),
    "strip-tags": (strip_tags, "selectolax"),
    "socialcard": (socialcard, "selectolax"),
    "extract-url": (extract_url, "selectolax"),
    "edit": (Mutating(_fresh, edit), "selectolax"),
    "links-absolutize": (Mutating(_fresh, links_absolutize), "selectolax"),
    "match": (match, "selectolax"),
}

"""resiliparse: parse with the same lexbor engine selectolax wraps."""

from __future__ import annotations

import functools
from urllib.parse import urljoin

from resiliparse.extract.html2text import (  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
    extract_plain_text,
)
from resiliparse.parse.encoding import (  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
    detect_encoding,
)
from resiliparse.parse.html import HTMLTree  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs

from bench.timing import Mutating

REQUIREMENTS = ("resiliparse>=1.0.8",)

_LINKS_BASE = "https://example.com/base/"  # the base turbohtml resolves relative hrefs against


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


def encoding(data: bytes) -> None:
    """Detect a byte stream's encoding with resiliparse's chardet-style detect_encoding."""
    detect_encoding(data)


def extract_attr(text: str) -> None:
    """Read every anchor's href by selecting once and reading the attribute off each node."""
    _ = [anchor.getattr("href") for anchor in _parsed(text).document.query_selector_all("a")]


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading text off each node."""
    _ = [anchor.text for anchor in _parsed(text).document.query_selector_all("a")]


def links_extract(text: str) -> None:
    """Collect every anchor's href with resiliparse, the shape a scraper reads links in."""
    _ = [anchor.getattr("href") for anchor in _parsed(text).document.query_selector_all("a")]


def links_rewrite(text: str) -> None:
    """Rewrite every link with an identity map, idempotent so the cached tree stays reusable."""
    for anchor in _parsed(text).document.query_selector_all("a"):
        if (href := anchor.getattr("href")) is not None:
            anchor.setattr("href", href)


def class_edit(text: str) -> None:
    """Add then drop a class token on every link, a net no-op so the cached tree stays valid across iterations."""
    for anchor in _parsed(text).document.query_selector_all("a"):
        original = anchor.getattr("class") or ""
        anchor.setattr("class", f"{original} seen".strip())
        anchor.setattr("class", original)


def socialcard(text: str) -> None:
    """Read every meta property and content off a freshly parsed tree, resiliparse's take on card extraction."""
    for meta in HTMLTree.parse(text).document.query_selector_all("meta"):
        meta.getattr("property")
        meta.getattr("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a document's own URL hint by parsing the string: the base href or the meta-refresh target."""
    kind, text = case
    document = HTMLTree.parse(text).document
    if kind == "base":
        if (base := document.query_selector("base")) is not None:
            base.getattr("href")
    elif (refresh := document.query_selector("meta[http-equiv=refresh]")) is not None:
        refresh.getattr("content")


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with resiliparse's decompose on a fresh parse, then serialize."""
    tree = HTMLTree.parse(text)
    for node in tree.document.query_selector_all("code, a, q"):
        node.decompose()
    _ = tree.document.html


def edit(tree: HTMLTree) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through resiliparse's setattr."""
    for anchor in tree.document.query_selector_all("a"):
        anchor.setattr("rel", "nofollow")


def links_absolutize(tree: HTMLTree) -> None:
    """Resolve every relative link on a freshly parsed tree against the base with resiliparse's setattr."""
    for anchor in tree.document.query_selector_all("a"):
        if (href := anchor.getattr("href")) is not None:
            anchor.setattr("href", urljoin(_LINKS_BASE, href))


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
    "encoding": (encoding, "resiliparse"),
    "extract-attr": (extract_attr, "resiliparse"),
    "extract-text": (extract_text, "resiliparse"),
    "links-extract": (links_extract, "resiliparse"),
    "links-rewrite": (links_rewrite, "resiliparse"),
    "class-edit": (class_edit, "resiliparse"),
    "socialcard": (socialcard, "resiliparse"),
    "extract-url": (extract_url, "resiliparse"),
    "strip-remove": (strip_remove, "resiliparse"),
    "edit": (Mutating(HTMLTree.parse, edit), "resiliparse"),
    "links-absolutize": (Mutating(HTMLTree.parse, links_absolutize), "resiliparse"),
}

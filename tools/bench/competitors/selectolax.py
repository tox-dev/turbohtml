"""selectolax: parse with the lexbor C engine, plus the read-path queries over its tree."""

from __future__ import annotations

import functools
from typing import Final, cast
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from bench.timing import Mutating
from bench.tree_text import PRESERVE_TAGS, SPACE_RUN

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


def _select_relative(case: tuple[str, str]) -> None:
    _parsed(case[1]).css(case[0])


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
    """
    Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does.

    Reading the raw href off each anchor is not the same operation: it leaves the on-page and script targets in,
    resolves nothing against the base, and returns a link once per occurrence. A dict keyed by the resolved URL
    dedupes in insertion order, which is what a caller replacing extract_links has to write for themselves.
    """
    seen: dict[str, None] = {}
    for anchor in LexborHTMLParser(text.encode()).css("a[href]"):
        if href := anchor.attributes.get("href"):
            seen[urljoin(_LINKS_BASE, href)] = None
    _ = list(seen)


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


def _serialize_inner(text: str) -> str:
    return cast("str", _body(text).inner_html)


@functools.cache
def _body(text: str) -> LexborNode:
    return cast("LexborNode", _parsed(text).body)


def _encode_inner(text: str) -> bytes:
    return _serialize_inner(text).encode()


def _fresh_transform(text: str) -> LexborHTMLParser:
    tree: Final = _fresh(text)
    if tree.css("template"):
        message: Final = "selectolax does not expose template contents for mutation"
        raise ValueError(message)
    return tree


def _collapse_whitespace(tree: LexborHTMLParser) -> None:
    for node in list(cast("LexborNode", tree.root).traverse(include_text=True)):
        if not node.is_text_node:
            continue
        parent = node.parent
        while parent is not None and parent.tag not in PRESERVE_TAGS:
            parent = parent.parent
        if parent is not None:
            continue
        text: Final = cast("str", node.text_content)
        collapsed = SPACE_RUN.sub(" ", text)
        previous = node.prev
        while previous is not None and previous.is_text_node and not previous.text_content:
            previous = previous.prev
        if previous is not None and previous.is_text_node and cast("str", previous.text_content).endswith(" "):
            collapsed = collapsed.removeprefix(" ")
        if collapsed != text:
            node.replace_with(collapsed)


def _transform_tree(tree: LexborHTMLParser) -> None:
    _strip_comments(tree)
    _collapse_whitespace(tree)


def _strip_comments(tree: LexborHTMLParser) -> None:
    for node in list(cast("LexborNode", tree.root).traverse(include_text=True)):
        if node.is_comment_node:
            node.remove()


OPERATIONS = {
    "collapse-whitespace": (Mutating(_fresh_transform, _collapse_whitespace), "selectolax"),
    "transform-tree": (Mutating(_fresh_transform, _transform_tree), "selectolax"),
    "serialize-inner": (_serialize_inner, "selectolax"),
    "encode-inner": (_encode_inner, "selectolax"),
    "strip-comments": (Mutating(_fresh_transform, _strip_comments), "selectolax"),
    "parse": (parse, "selectolax"),
    "parse-formatting": (parse, "selectolax"),
    "parse-foster": (parse, "selectolax"),
    "parse-crlf": (parse, "selectolax"),
    "parse-nul": (parse, "selectolax"),
    "parse-afe": (parse, "selectolax"),
    "parse-scope": (parse, "selectolax"),
    "find": (find, "selectolax"),
    "select": (select, "selectolax"),
    "select-nth": (_select_relative, "selectolax"),
    "select-relative": (_select_relative, "selectolax"),
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

"""BeautifulSoup: parse, the constructor build, and the read-path queries."""

from __future__ import annotations

import functools
import re
from typing import Final
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, UnicodeDammit
from bs4.element import AttributeValueList, CData, NavigableString, Script, Stylesheet, Tag, TemplateString

from bench.timing import Mutating
from bench.tree_text import PRESERVE_TAGS, SPACE_RUN

# UnicodeDammit only sniffs when an optional detector backend is installed. Without one it answers windows-1252 for
# every high-byte stream and utf-8 for Shift_JIS, which is fast and wrong; chardet is the backend bs4 documents first.
REQUIREMENTS = ("beautifulsoup4[chardet]>=4.15",)

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


def _find_text_exact(case: tuple[str, str]) -> None:
    _ = [
        node
        for node in _parsed(case[0]).find_all("div")
        if node.get_text(types=(NavigableString, CData, Script, Stylesheet, TemplateString)) == case[1]
    ]


def _find_attr_presence(case: tuple[str, bool]) -> None:
    _parsed(case[0]).find_all("p", attrs={"data-x": case[1]})


def _select_default(case: tuple[str, str]) -> None:
    if case[0] == ":default" and "<button>" in case[1]:
        unsupported: Final = "SoupSieve requires explicit type=submit for default buttons"
        raise NotImplementedError(unsupported)
    _select_relative(case)


def _select_relative(case: tuple[str, str]) -> None:
    _parsed(case[1]).select(case[0])


def text_content(text: str) -> None:
    """Collect the document's visible text with BeautifulSoup's get_text()."""
    _parsed(text).get_text()


def _serialize_named(text: str) -> str:
    return _parsed(text).decode(formatter="html")


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


def rewrite(text: str) -> None:
    """Full parse, mutate, serialize -- the DOM round-trip the streamer skips: rel=nofollow, lazy img, drop comments."""
    soup = BeautifulSoup(text, "html.parser")
    for anchor in soup.select("a[href]"):
        anchor["rel"] = "nofollow"
    for image in soup.find_all("img"):
        image["loading"] = "lazy"
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    _ = str(soup)


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
    """Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does."""
    seen: dict[str, None] = {}
    for anchor in BeautifulSoup(text, "html.parser").find_all("a"):
        if href := anchor.get("href"):
            seen[urljoin(_LINKS_BASE, str(href))] = None
    _ = list(seen)


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


def _serialize_inner(text: str) -> str:
    return _body(text).decode_contents()


@functools.cache
def _body(text: str) -> Tag:
    return _parsed(text).find_all("body")[0]


def _encode_inner(text: str) -> bytes:
    return _body(text).encode_contents()


def _serialize_inner_indent(text: str) -> str:
    return _body(text).decode_contents(indent_level=0)


def _encode_inner_indent(text: str) -> bytes:
    return _body(text).encode_contents(indent_level=0)


def _collapse_whitespace(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment) or any(parent.name in PRESERVE_TAGS for parent in node.parents):
            continue
        collapsed = SPACE_RUN.sub(" ", str(node))
        previous = node.previous_sibling
        while isinstance(previous, NavigableString) and not isinstance(previous, Comment) and not previous:
            previous = previous.previous_sibling
        if isinstance(previous, NavigableString) and not isinstance(previous, Comment) and previous.endswith(" "):
            collapsed = collapsed.removeprefix(" ")
        if collapsed != node:
            node.replace_with(collapsed)


def _transform_tree(soup: BeautifulSoup) -> None:
    _strip_comments(soup)
    _collapse_whitespace(soup)


def _strip_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()


def _node_equals(case: tuple[int, str]) -> bool:
    left, right = _equality_pair(*case)
    return left == right


@functools.cache
def _equality_pair(count: int, variant: str) -> tuple[Tag, Tag]:
    if variant == "duplicates":
        message: Final = "constructor does not normalize case variants to duplicate attribute names"
        raise ValueError(message)
    left: Final = BeautifulSoup("", "html.parser").new_tag("div")
    right: Final = BeautifulSoup("", "html.parser").new_tag("div")
    right.attrs["data-seed"] = ""
    del right.attrs["data-seed"]
    names: Final = [f"data-{index}" for index in range(count)]
    for name in names:
        left.attrs[name] = "é水😀"
    order: Final = names[count // 2 :] + names[: count // 2] if variant == "rotated" else names
    for name in reversed(order) if variant == "reversed" else order:
        right.attrs[name] = "é水😀"
    if variant == "early-value":
        right.attrs[names[0]] = "different"
    elif variant == "late-value":
        right.attrs[names[-1]] = "different"
    elif variant == "disjoint":
        del right.attrs[names[-1]]
        right.attrs["data-missing"] = "é水😀"
    return left, right


OPERATIONS = {
    "node-equals": (_node_equals, "BeautifulSoup (html.parser)"),
    "collapse-whitespace": (Mutating(_fresh, _collapse_whitespace), "BeautifulSoup (html.parser)"),
    "transform-tree": (Mutating(_fresh, _transform_tree), "BeautifulSoup (html.parser)"),
    "serialize-inner": (_serialize_inner, "BeautifulSoup (html.parser)"),
    "encode-inner": (_encode_inner, "BeautifulSoup (html.parser)"),
    "serialize-inner-indent": (_serialize_inner_indent, "BeautifulSoup (html.parser)"),
    "encode-inner-indent": (_encode_inner_indent, "BeautifulSoup (html.parser)"),
    "strip-comments": (Mutating(_fresh, _strip_comments), "BeautifulSoup (html.parser)"),
    "parse": (parse, "BeautifulSoup (html.parser)"),
    "parse-formatting": (parse, "BeautifulSoup (html.parser)"),
    "parse-foster": (parse, "BeautifulSoup (html.parser)"),
    "parse-crlf": (parse, "BeautifulSoup (html.parser)"),
    "parse-nul": (parse, "BeautifulSoup (html.parser)"),
    "parse-afe": (parse, "BeautifulSoup (html.parser)"),
    "parse-scope": (parse, "BeautifulSoup (html.parser)"),
    "build": (build, "BeautifulSoup (html.parser)"),
    "construct": (construct, "BeautifulSoup (html.parser)"),
    "emit": (emit, "BeautifulSoup (html.parser)"),
    "find": (find, "BeautifulSoup (html.parser)"),
    "select": (select, "BeautifulSoup (html.parser)"),
    "select-relative": (_select_relative, "BeautifulSoup (html.parser)"),
    "select-default": (_select_default, "BeautifulSoup (html.parser)"),
    "select-has": (select_has, "BeautifulSoup (html.parser)"),
    "find-text": (find_text, "BeautifulSoup (html.parser)"),
    "find-text-exact": (_find_text_exact, "BeautifulSoup (html.parser)"),
    "find-attr-presence": (_find_attr_presence, "BeautifulSoup (html.parser)"),
    "text-content": (text_content, "BeautifulSoup (html.parser)"),
    "serialize": (serialize, "BeautifulSoup (html.parser)"),
    "class-edit": (class_edit, "BeautifulSoup (html.parser)"),
    "extract-attr": (extract_attr, "BeautifulSoup (html.parser)"),
    "extract-text": (extract_text, "BeautifulSoup (html.parser)"),
    "strip-remove": (strip_remove, "BeautifulSoup (html.parser)"),
    "strip-tags": (strip_tags, "BeautifulSoup (html.parser)"),
    "rewrite": (rewrite, "BeautifulSoup (html.parser)"),
    "encoding": (encoding, "BeautifulSoup (html.parser)"),
    "encoding-result": (encoding, "BeautifulSoup (html.parser)"),
    "edit": (Mutating(_fresh, edit), "BeautifulSoup (html.parser)"),
    "set-html": (Mutating(_fresh, set_html), "BeautifulSoup (html.parser)"),
    "set-text": (Mutating(_fresh, set_text), "BeautifulSoup (html.parser)"),
    "navigate": (navigate, "BeautifulSoup (html.parser)"),
    "match": (match, "BeautifulSoup (html.parser)"),
    "links-extract": (links_extract, "BeautifulSoup (html.parser)"),
    "links-rewrite": (links_rewrite, "BeautifulSoup (html.parser)"),
    "links-filter": (links_filter, "BeautifulSoup (html.parser)"),
    "socialcard": (socialcard, "BeautifulSoup (html.parser)"),
    "extract-url": (extract_url, "BeautifulSoup (html.parser)"),
    "links-absolutize": (Mutating(_fresh, links_absolutize), "BeautifulSoup (html.parser)"),
    "serialize-named": (_serialize_named, "BeautifulSoup (html.parser)"),
}

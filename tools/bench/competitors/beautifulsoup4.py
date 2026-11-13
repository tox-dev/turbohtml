"""BeautifulSoup: parse, the constructor build, and the read-path queries."""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, UnicodeDammit
from bs4.element import AttributeValueList, CData, NavigableString, Script, Stylesheet, Tag, TemplateString

from bench._attributes import fill_equality_pair  # simplint: ignore[private-import-path]  # shared benchmark helper
from bench._beautifulsoup import (  # simplint: ignore[private-import-path]  # shared competitor helpers
    body,
    collapse_whitespace,
    encode_inner,
    encode_inner_indent,
    find,
    fresh,
    parsed,
    serialize_inner_indent,
    socialcard,
)
from bench.timing import Mutating

if TYPE_CHECKING:
    from bench.operations import Timed

# UnicodeDammit only sniffs when an optional detector backend is installed. Without one it answers windows-1252 for
# every high-byte stream and utf-8 for Shift_JIS, which is fast and wrong; chardet is the backend bs4 documents first.
_REQUIREMENTS: Final[tuple[str, ...]] = ("beautifulsoup4[chardet]>=4.15",)

_FIND_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(r"test")
_STRIP: Final[tuple[str, ...]] = ("code", "a", "q")
_LINKS_BASE: Final[str] = "https://example.com/base/"


def parse(text: str) -> None:
    # Parse a whole document with BeautifulSoup over its stdlib html.parser backend.
    BeautifulSoup(text, "html.parser")


def build(count: int) -> None:
    # Build a ``<ul>`` of rows with BeautifulSoup's ``new_tag`` and ``.string``, then serialize (the workload).
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    _ = ul.decode()


def construct(count: int) -> None:
    # Construct ``count`` elements with attributes and text, in isolation from serialization.
    soup = BeautifulSoup("", "html.parser")
    for index in range(count):
        soup.new_tag("li", attrs={"class": "item", "data-i": str(index)}).string = f"item {index}"


@functools.cache
def _tree(count: int) -> Tag:
    # Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step.
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    return ul


def emit(count: int) -> None:
    # Emit a pre-built ``count``-row tree with ``.decode()``.
    _ = _tree(count).decode()


def select(text: str) -> None:
    # Run the CSS selector with BeautifulSoup's soupsieve select.
    parsed("html.parser", text).select("div a[href]")


def select_has(text: str) -> None:
    # Run the :has() relational selector with BeautifulSoup's soupsieve.
    parsed("html.parser", text).select("div:has(a)")


def find_text(text: str) -> None:
    # Collect every matching string with BeautifulSoup's find_all(string=...).
    parsed("html.parser", text).find_all(string=_FIND_TEXT_PATTERN)


def _find_text_exact(case: tuple[str, str]) -> None:
    _ = [
        node
        for node in parsed("html.parser", case[0]).find_all("div")
        if node.get_text(types=(NavigableString, CData, Script, Stylesheet, TemplateString)) == case[1]
    ]


def _find_attr_presence(case: tuple[str, bool]) -> None:
    parsed("html.parser", case[0]).find_all("p", attrs={"data-x": case[1]})


def _select_default(case: tuple[str, str]) -> None:
    if case[0] == ":default" and "<button>" in case[1]:
        unsupported: Final = "SoupSieve requires explicit type=submit for default buttons"
        raise NotImplementedError(unsupported)
    _select_relative(case)


def _select_relative(case: tuple[str, str]) -> None:
    parsed("html.parser", case[1]).select(case[0])


def text_content(text: str) -> None:
    # Collect the document's visible text with BeautifulSoup's get_text().
    parsed("html.parser", text).get_text()


def _serialize_named(text: str) -> str:
    return parsed("html.parser", text).decode(formatter="html")


def serialize(text: str) -> None:
    # Serialize a parsed document back to HTML with BeautifulSoup's decode.
    parsed("html.parser", text).decode()


def class_edit(text: str) -> None:
    # Add then drop a class token on every link through BeautifulSoup's multi-valued class list (a net no-op).
    for anchor in parsed("html.parser", text).find_all("a"):
        tokens = anchor.get_attribute_list("class")
        anchor["class"] = AttributeValueList([*tokens, "seen"])
        anchor["class"] = tokens


def extract_attr(text: str) -> None:
    # Read every anchor's href by selecting once and reading the attribute off each node.
    for anchor in parsed("html.parser", text).select("a"):
        anchor.get("href")


def extract_text(text: str) -> None:
    # Read every anchor's visible text by selecting once and reading get_text off each node.
    for anchor in parsed("html.parser", text).select("a"):
        anchor.get_text()


def strip_remove(text: str) -> None:
    # Drop every code/a/q subtree with BeautifulSoup's decompose on a fresh parse, then serialize.
    soup = fresh("html.parser", text)
    for tag in list(soup.find_all(_STRIP)):
        tag.decompose()
    _ = soup.decode()


def strip_tags(text: str) -> None:
    # Unwrap every code/a/q element keeping its content with BeautifulSoup's unwrap on a fresh parse, then emit.
    soup = fresh("html.parser", text)
    for tag in list(soup.find_all(_STRIP)):
        tag.unwrap()
    _ = soup.decode()


def encoding(data: bytes) -> None:
    # Detect a byte stream's encoding with BeautifulSoup's UnicodeDammit sniffer.
    _ = UnicodeDammit(data).original_encoding


def rewrite(text: str) -> None:
    # Full parse, mutate, serialize -- the DOM round-trip the streamer skips: rel=nofollow, lazy img, drop comments.
    soup = BeautifulSoup(text, "html.parser")
    for anchor in soup.select("a[href]"):
        anchor["rel"] = "nofollow"
    for image in soup.find_all("img"):
        image["loading"] = "lazy"
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    _ = str(soup)


def edit(soup: BeautifulSoup) -> None:
    # Tag every link with rel=nofollow on a freshly parsed tree through BeautifulSoup's item assignment.
    for anchor in soup.find_all("a"):
        anchor["rel"] = "nofollow"


def set_html(soup: BeautifulSoup) -> None:
    # Clear a freshly parsed body and append a reparsed fragment, BeautifulSoup's inner-HTML shape.
    markup: str = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
    body = soup.find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(markup, "html.parser").children):
        body.append(node)


def set_text(soup: BeautifulSoup) -> None:
    # Replace a freshly parsed body's children with one verbatim text node through BeautifulSoup's .string.
    soup.find_all("body")[0].string = "Replacement text, escaped & verbatim."


def navigate(text: str) -> None:
    # Walk every descendant with BeautifulSoup's descendants iterator.
    for _node in parsed("html.parser", text).descendants:
        pass


def match(text: str) -> None:
    # Test every anchor against a selector with BeautifulSoup's soupsieve-backed css.match.
    for anchor in parsed("html.parser", text).find_all("a"):
        anchor.css.match("div a[href]")


def links_extract(text: str) -> None:
    # Collect every anchor's href with BeautifulSoup, the shape a scraper reads links in.
    _ = [anchor.get("href") for anchor in parsed("html.parser", text).find_all("a")]


def links_rewrite(text: str) -> None:
    # Rewrite every link with an identity map, idempotent so the cached tree stays reusable.
    for anchor in parsed("html.parser", text).find_all("a"):
        if (href := anchor.get("href")) is not None:
            anchor["href"] = href


def links_filter(text: str) -> None:
    # Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does.
    seen: dict[str, None] = {}
    for anchor in BeautifulSoup(text, "html.parser").find_all("a"):
        if href := anchor.get("href"):
            seen[urljoin(_LINKS_BASE, str(href))] = None
    _ = list(seen)


def extract_url(case: tuple[str, str]) -> None:
    # Read a freshly parsed document's own URL hint with BeautifulSoup: the base href or the meta refresh.
    kind, text = case
    soup = fresh("html.parser", text)
    if kind == "base":
        if (base := soup.find("base")) is not None:
            base.get("href")
    elif (refresh := soup.find("meta", attrs={"http-equiv": "refresh"})) is not None:
        refresh.get("content")


def links_absolutize(soup: BeautifulSoup) -> None:
    # Resolve every relative link on a freshly parsed tree against a base with BeautifulSoup's item assignment.
    for anchor in soup.find_all("a"):
        if isinstance(href := anchor.get("href"), str):
            anchor["href"] = urljoin(_LINKS_BASE, href)


def _serialize_inner(text: str) -> str:
    return body("html.parser", text).decode_contents()


def _transform_tree(soup: BeautifulSoup) -> None:
    _strip_comments(soup)
    collapse_whitespace(soup)


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
    fill_equality_pair(left.attrs, right.attrs, count, variant)
    return left, right


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "node-equals": (_node_equals, "BeautifulSoup (html.parser)"),
    "collapse-whitespace": (
        Mutating(setup=functools.partial(fresh, "html.parser"), run=collapse_whitespace),
        "BeautifulSoup (html.parser)",
    ),
    "transform-tree": (
        Mutating(setup=functools.partial(fresh, "html.parser"), run=_transform_tree),
        "BeautifulSoup (html.parser)",
    ),
    "serialize-inner": (_serialize_inner, "BeautifulSoup (html.parser)"),
    "encode-inner": (functools.partial(encode_inner, "html.parser"), "BeautifulSoup (html.parser)"),
    "serialize-inner-indent": (functools.partial(serialize_inner_indent, "html.parser"), "BeautifulSoup (html.parser)"),
    "encode-inner-indent": (functools.partial(encode_inner_indent, "html.parser"), "BeautifulSoup (html.parser)"),
    "strip-comments": (
        Mutating(setup=functools.partial(fresh, "html.parser"), run=_strip_comments),
        "BeautifulSoup (html.parser)",
    ),
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
    "find": (functools.partial(find, "html.parser"), "BeautifulSoup (html.parser)"),
    "select": (select, "BeautifulSoup (html.parser)"),
    "select-nth": (_select_relative, "BeautifulSoup (html.parser)"),
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
    "edit": (Mutating(setup=functools.partial(fresh, "html.parser"), run=edit), "BeautifulSoup (html.parser)"),
    "set-html": (Mutating(setup=functools.partial(fresh, "html.parser"), run=set_html), "BeautifulSoup (html.parser)"),
    "set-text": (Mutating(setup=functools.partial(fresh, "html.parser"), run=set_text), "BeautifulSoup (html.parser)"),
    "navigate": (navigate, "BeautifulSoup (html.parser)"),
    "match": (match, "BeautifulSoup (html.parser)"),
    "links-extract": (links_extract, "BeautifulSoup (html.parser)"),
    "links-rewrite": (links_rewrite, "BeautifulSoup (html.parser)"),
    "links-filter": (links_filter, "BeautifulSoup (html.parser)"),
    "socialcard": (functools.partial(socialcard, "html.parser"), "BeautifulSoup (html.parser)"),
    "extract-url": (extract_url, "BeautifulSoup (html.parser)"),
    "links-absolutize": (
        Mutating(setup=functools.partial(fresh, "html.parser"), run=links_absolutize),
        "BeautifulSoup (html.parser)",
    ),
    "serialize-named": (_serialize_named, "BeautifulSoup (html.parser)"),
}
